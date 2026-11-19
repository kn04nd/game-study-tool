"""``gst analyze <name>``: analyses over what a study already extracted -> ``analysis/<name>.json`` + ``.md``.

An analysis is a function registered with :func:`analysis`. It reads the workspace (Unity inventory tables,
the code index, other analyses, the install read-only), streams over its inputs and returns a
:class:`Report`; the framework checks its requirements, writes both files atomically with a common header
and records the run. Each analysis module is listed in :data:`PROVIDERS`.

Writing one (see docs/INTERFACES.md, section 6)::

    from gst.analysis import Report, analysis, md_table

    @analysis("prefabs", engines=("unity-mono", "unity-il2cpp"), needs=("unity.inventory",),
              uses=("unity.install",), description="prefab roots and their trees", version=1,
              options={"marker": "MonoBehaviour class names that mark a root (repeatable)"})
    def prefabs(ctx):
        for row in ctx.rows(types=("GameObject",)):   # streams the inventory, never loads it all
            ...
        return Report(data={...}, markdown="...", summary={"prefabs": n})

Contract:

- ``analysis/<name>.json`` = ``{"header": {...}, "summary": {...}, "data": ...}``; ``analysis/<name>.md`` = the
  same header as a list, then the report's Markdown (human tables). Bigger per-item outputs go under
  ``analysis/<name>/`` through :meth:`Context.side_path` / :meth:`Context.write_side_json`.
- Header: analysis, version, game, slug, build, engine, inputs used (label, path, bytes, mtime), tool versions,
  options, generated_at, seconds. Everything else is deterministic: analyses sort what they emit (use
  :func:`counted`), so two runs over the same inputs differ only in ``generated_at``/``seconds``.
- Streaming: read the Unity inventory with :meth:`Context.rows` (``inventory.iter_rows``), open one
  serialized file at a time (:meth:`Context.open_unity`), never load every object of a game.
- No ceilings: limits only as explicit options.
"""

from __future__ import annotations

import collections
import importlib
import json
import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

ANALYSIS_DIR = "analysis"
ANY = "*"
UNITY = ("unity-mono", "unity-il2cpp")

# Modules that register analyses when imported. One line per module.
PROVIDERS: list[str] = [
    "gst.analysis.unity.prefabs",       # prefab roots + per-prefab trees
    "gst.analysis.unity.mono_values",   # MonoBehaviour field values per script
    "gst.analysis.unity.catalogs",      # ScriptableObject catalogs, name families, Addressables
    "gst.analysis.systems",             # candidate gameplay systems (code index + prefabs + REA)
    "gst.analysis.unity.controller",    # AnimatorControllers, overrides, #hash names
    "gst.analysis.unity.animation",     # AnimationClips, CRC32 path maps, events, root motion
    "gst.analysis.unity.avatar",        # Avatars, humanoid mapping
    "gst.analysis.unity.rig",           # SMR rigs, influences, deform/helper bones, IK constraints
    "gst.analysis.unity.joints",        # physics joints per prefab, ragdoll components
    "gst.analysis.gltf_summary",        # batch summary of every glb/gltf + families
    "gst.analysis.dcc",                 # DCC/origin from names and transform signatures
    "gst.analysis.unity.shaders",       # shaders (props, keywords, passes) + Material usage
    "gst.analysis.unity.textures",      # texture sizes/formats, normal maps, atlas hints, per family
    "gst.analysis.unity.audio_refs",    # who references each AudioClip (typetree or binary PPtr scan)
    "gst.analysis.audio_catalog",       # sound catalog (Unity refs / Godot scenes + code), assets/audio/catalog.json
    "gst.analysis.godot.pck",           # PCK directory (stdlib) cross-checked with the GDRE inventory
    "gst.analysis.godot.scenes",        # .tscn/.tres -> JSON, node types, scripts, signals, instances, tunables
    "gst.analysis.godot.animation",     # Animation resources, AnimationTree graphs, skeletons, ragdoll/joints
    "gst.analysis.godot.shaders",       # .gdshader + embedded/visual shaders, uniforms, materials, users
    "gst.analysis.unreal",              # Unreal families by folder, DataTable rows, Blueprint parents, script objects
    "gst.analysis.unreal_deep",         # Unreal default values (unreal_values), skeleton/anim/physics (unreal_animation)
]
ENV_PROVIDERS = "GST_ANALYSIS_PROVIDERS"  # extra modules (comma-separated), e.g. test fixtures


@dataclass(frozen=True)
class Spec:
    name: str
    func: Callable[["Context"], "Report"]
    engines: tuple[str, ...] = (ANY,)
    needs: tuple[str, ...] = ()      # required inputs (see NEEDS): missing -> the run is "skipped" with the reason
    uses: tuple[str, ...] = ()       # optional inputs: used and recorded when present
    description: str = ""
    version: int = 1                 # bump when the output changes (reruns it in gst study)
    options: dict[str, str] = field(default_factory=dict)  # option name -> help (documentation + --list)
    module: str = ""
    category: str = "other"          # COVERAGE.md row of its gst study job (extractors.base.CATEGORIES)

    def applies_to(self, engine_key: str | None) -> bool:
        return ANY in self.engines or (engine_key in self.engines)

    def deps(self) -> list[str]:
        """Other analyses this one reads (``analysis:<name>`` in needs/uses): they run first."""
        return [n.split(":", 1)[1] for n in self.needs + self.uses if n.startswith("analysis:")]


_REGISTRY: dict[str, Spec] = {}


def analysis(name: str, *, engines: Iterable[str] = (ANY,), needs: Iterable[str] = (), uses: Iterable[str] = (),
             description: str = "", version: int = 1, options: dict[str, str] | None = None, category: str = "other"):
    """Decorator: register ``func(ctx: Context) -> Report`` as ``gst analyze <name>``."""

    def deco(func):
        if name in _REGISTRY and _REGISTRY[name].func is not func:
            raise ValueError(f"analysis '{name}' already registered by {_REGISTRY[name].module}")
        for need in tuple(needs) + tuple(uses):
            if need not in NEEDS and not need.startswith("analysis:"):
                raise ValueError(f"analysis '{name}': unknown requirement '{need}' (known: {', '.join(sorted(NEEDS))}, "
                                 "analysis:<name>)")
        doc_line = ((func.__doc__ or "").strip().splitlines() or [""])[0]
        _REGISTRY[name] = Spec(name=name, func=func, engines=tuple(engines), needs=tuple(needs), uses=tuple(uses),
                               description=description or doc_line, version=version, options=dict(options or {}),
                               module=func.__module__, category=category)
        return func

    return deco


def load_providers() -> None:
    extra = [m.strip() for m in os.environ.get(ENV_PROVIDERS, "").split(",") if m.strip()]
    for module in PROVIDERS + extra:
        importlib.import_module(module)


def all_specs() -> dict[str, Spec]:
    load_providers()
    return dict(sorted(_REGISTRY.items()))


def get(name: str) -> Spec:
    specs = all_specs()
    if name not in specs:
        from gst.common import GsError

        raise GsError(f"no analysis '{name}' (gst analyze --list): {', '.join(specs)}")
    return specs[name]


def for_engine(engine_key: str | None) -> list[Spec]:
    """Analyses that apply to an engine, in dependency order (an analysis after those it reads)."""
    return ordered([s for s in all_specs().values() if s.applies_to(engine_key)])


def ordered(specs: list[Spec]) -> list[Spec]:
    by_name = {s.name: s for s in specs}
    out: list[Spec] = []
    seen: set[str] = set()

    def visit(s: Spec, stack: tuple = ()) -> None:
        if s.name in seen or s.name in stack:
            return
        for dep in s.deps():
            if dep in by_name:
                visit(by_name[dep], stack + (s.name,))
        seen.add(s.name)
        out.append(s)

    for s in sorted(specs, key=lambda x: x.name):
        visit(s)
    return out


def _clear() -> None:
    """Tests only."""
    _REGISTRY.clear()


# --- requirements --------------------------------------------------------------------------------

def _inventory_dir(ws: Path) -> Path:
    return Path(ws) / "assets" / "unity" / "_inventory"


def _need_unity_inventory(ws: Path, game: dict):
    inv = _inventory_dir(ws)
    tables = list(inv.glob("*.jsonl.gz"))[:1] or [p for p in inv.glob("*.jsonl") if not p.name.startswith("_")][:1]
    if tables:
        return True, "", inv
    return False, "no Unity inventory (gst study runs unity.inventory first)", inv


def _need_unity_install(ws: Path, game: dict):
    install = Path(game.get("install_dir") or "")
    if install.is_dir() and any(install.glob("*_Data")):
        return True, "", install
    return False, f"install folder with a <Game>_Data folder not found: {install}", install


def _need_code_index(ws: Path, game: dict):
    db = Path(ws) / "index" / "code.db"
    return (db.is_file(), "" if db.is_file() else "no code index (gst decompile / gst index)", db)


def _need_rea(ws: Path, game: dict):
    rea = Path(ws) / "dumps" / "rea"
    found = rea.is_dir() and any(rea.rglob("*.json"))
    return found, "" if found else "no REA evidence in dumps/rea", rea


# requirement name -> check(ws, game) -> (ok, reason when missing, path)
NEEDS: dict[str, Callable[[Path, dict], tuple[bool, str, Path]]] = {
    "unity.inventory": _need_unity_inventory,
    "unity.install": _need_unity_install,
    "code.index": _need_code_index,
    "dumps.rea": _need_rea,
    "godot.project": lambda ws, game: _godot().need_project(ws, game),
    "godot.pack": lambda ws, game: _godot().need_pack(ws, game),
    "unreal.inventory": lambda ws, game: _need_unreal_inventory(ws, game),
    "unreal.json": lambda ws, game: _need_unreal_json(ws, game),
}


def _need_unreal_inventory(ws: Path, game: dict):
    inv = Path(ws) / "assets" / "inventory.jsonl"
    return inv.is_file(), "" if inv.is_file() else "no Unreal inventory (gst assets list / gst study runs unreal.inventory)", inv


def _need_unreal_json(ws: Path, game: dict):
    root = Path(ws) / "assets" / "unreal"
    found = root.is_dir() and any(p.name != "containers.json" and not p.name.endswith(".gs.json") and not p.name.startswith(("_", "."))
                                  for p in root.rglob("*.json"))
    return found, "" if found else "no uas JSON under assets/unreal (gst assets extract --name <regex> --types json)", root


def _godot():
    from gst.analysis import godot

    return godot


def check_need(ws: Path, game: dict, need: str) -> tuple[bool, str, Path | None]:
    if need.startswith("analysis:"):
        other = need.split(":", 1)[1]
        p = Path(ws) / ANALYSIS_DIR / f"{other}.json"
        return p.is_file(), "" if p.is_file() else f"analysis/{other}.json missing (gst analyze {other})", p
    return NEEDS[need](Path(ws), game)


# --- running -------------------------------------------------------------------------------------

@dataclass
class Report:
    """What an analysis returns: ``data`` (full, JSON-able, deterministic order), ``markdown`` (human tables,
    without the header), ``summary`` (a few counts for the CLI, the job notes and the JSON)."""

    data: Any
    markdown: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class Context:
    """Everything an analysis gets: the workspace, game.json, the install (read-only), its options and helpers
    to stream inputs and record what it used."""

    def __init__(self, ws: Path, game: dict, spec: Spec, options: dict | None = None, log: Callable[[str], None] | None = None):
        self.workspace = Path(ws)
        self.game = game or {}
        self.spec = spec
        self.name = spec.name
        self.options = dict(options or {})
        self.install_dir = Path(self.game["install_dir"]) if self.game.get("install_dir") else None
        self.out_dir = self.workspace / ANALYSIS_DIR
        self.inputs: dict[str, dict] = {}
        self.tools: dict[str, str] = {}
        self.outputs: list[str] = []
        self._log = log or (lambda _msg: None)
        try:
            from gst import __version__ as gst_version
        except ImportError:
            gst_version = "?"
        self.tools["gst"] = gst_version

    # inputs / provenance
    def use(self, label: str, path: Path | str | None = None, **extra) -> None:
        """Record an input in the header (label, path relative to the workspace when inside it, bytes, mtime)."""
        entry: dict[str, Any] = {"label": label}
        if path is not None:
            p = Path(path)
            try:
                entry["path"] = p.relative_to(self.workspace).as_posix()
            except ValueError:
                try:  # install files: relative to the install folder
                    entry["path"] = "<install>/" + p.relative_to(self.install_dir).as_posix() if self.install_dir else p.as_posix()
                except ValueError:
                    entry["path"] = p.as_posix()
            try:
                st = p.stat()
                if p.is_file():
                    entry["bytes"] = st.st_size
                entry["mtime"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))
            except OSError:
                pass
        entry.update({k: v for k, v in extra.items() if v is not None})
        self.inputs[label] = entry

    def tool(self, name: str, version: str) -> None:
        self.tools[name] = version or "?"

    def log(self, msg: str) -> None:
        self._log(f"{self.name}: {msg}")

    def opt(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def opt_list(self, key: str) -> list[str]:
        """An option given once, repeated or comma-separated, as a list."""
        value = self.options.get(key)
        if value in (None, "", []):
            return []
        items = value if isinstance(value, (list, tuple)) else [value]
        return [v.strip() for item in items for v in str(item).split(",") if v.strip()]

    # Unity helpers
    def inventory_dir(self) -> Path:
        return _inventory_dir(self.workspace)

    def rows(self, *, files=None, types=None, name: str | None = None):
        """Stream Unity inventory rows (``inventory.iter_rows``); records the inventory as an input."""
        from gst.engines.unity import inventory

        inv = self.inventory_dir()
        if "unity.inventory" not in self.inputs:
            self.use("unity.inventory", inv / inventory.INDEX if (inv / inventory.INDEX).is_file() else inv)
        return inventory.iter_rows(inv, files=files, types=types, name=name)

    def inventory_files(self, types: Iterable[str] | None = None) -> list[str]:
        """Install relpaths of the inventoried files holding any of ``types`` (all when None), sorted."""
        from gst.engines.unity import inventory

        wanted = set(types or ())
        out = []
        for s in inventory.summaries(self.inventory_dir()):
            if not wanted or wanted & set(s.get("types", {})):
                out.append(s["file"])
        return sorted(out)

    def open_unity(self, relpath: str):
        """Open one install file (serialized file or bundle) read-only, streaming; close with ``loader.close_env``."""
        from gst.engines.unity import loader

        self.tool("UnityPy", loader.unitypy_version())
        if self.install_dir is None:
            from gst.common import GsError

            raise GsError("game.json has no install_dir")
        return loader.open_env(self.install_dir / relpath)

    # other analyses
    def load(self, name: str) -> dict | None:
        """The JSON of another analysis (``{"header", "summary", "data"}``), recorded as an input; None if absent."""
        p = self.out_dir / f"{name}.json"
        if not p.is_file():
            return None
        self.use(f"analysis:{name}", p)
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    # outputs
    def side_path(self, *parts: str) -> Path:
        """A path under ``analysis/<name>/`` for per-item outputs (created folders, recorded output)."""
        from gst.assets import safe_name

        p = self.out_dir / self.name
        for part in parts:
            p = p / safe_name(str(part), 120)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.outputs.append(p.relative_to(self.workspace).as_posix())
        return p

    def write_side_json(self, data: Any, *parts: str) -> Path:
        p = self.side_path(*parts)
        _atomic_write(p, json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n")
        return p

    def side_dir(self) -> Path:
        return self.out_dir / self.name


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".partial-{os.getpid()}")
    # lone surrogates (badly decoded game strings) become backslash escapes: still valid JSON, never a crash
    with open(tmp, "w", encoding="utf-8", newline="\n", errors="backslashreplace") as fh:
        fh.write(text)
    os.replace(tmp, path)


def header(ctx: Context, seconds: float) -> dict:
    g = ctx.game
    return {"analysis": ctx.name, "version": ctx.spec.version, "game": g.get("name"), "slug": g.get("slug"),
            "build": str(g.get("buildid") or "?"), "engine": (g.get("engine") or {}).get("key"),
            "engine_version": (g.get("engine") or {}).get("version"),
            "inputs": [ctx.inputs[k] for k in sorted(ctx.inputs)], "tools": dict(sorted(ctx.tools.items())),
            "options": {k: ctx.options[k] for k in sorted(ctx.options)},
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(seconds, 2)}


def header_md(h: dict, title: str) -> str:
    install = [i["label"].split(":", 1)[1] for i in h["inputs"] if i["label"].startswith("install:")]
    inputs = "; ".join(f"`{i.get('path', i['label'])}`" + (f" ({i['label']})" if i.get("path") else "")
                       for i in h["inputs"] if not i["label"].startswith("install:"))
    if install:
        inputs += ("; " if inputs else "") + f"install files ({len(install)}): " + ", ".join(f"`{r}`" for r in install)
    tools = ", ".join(f"{k} {v}" for k, v in h["tools"].items())
    opts = ", ".join(f"{k}={v}" for k, v in h["options"].items()) or "-"
    return "\n".join([f"# {title}", "",
                      f"- Game: {h.get('game') or '?'} (`{h.get('slug') or '?'}`), build {h['build']}, "
                      f"engine {h.get('engine') or '?'} {h.get('engine_version') or ''}".rstrip(),
                      f"- Generated by `gst analyze {h['analysis']}` (v{h['version']}) at {h['generated_at']} in {h['seconds']} s",
                      f"- Inputs: {inputs or '-'}", f"- Tools: {tools}", f"- Options: {opts}",
                      f"- Full data: `analysis/{h['analysis']}.json`", ""])


def run(ws: Path, game: dict, name: str, options: dict | None = None, *, log: Callable[[str], None] | None = None) -> dict:
    """Run one analysis. Returns ``{"name", "status" (done|skipped|failed), "reason", "json", "md", "outputs",
    "summary", "seconds"}``; never raises for a broken analysis (the traceback goes in ``reason``)."""
    spec = get(name)
    ws = Path(ws)
    t0 = time.time()
    out = {"name": name, "status": "done", "reason": "", "json": None, "md": None, "outputs": [], "summary": {},
           "seconds": 0.0}
    engine = (game.get("engine") or {}).get("key")
    if not spec.applies_to(engine):
        out.update(status="skipped", reason=f"does not apply to engine {engine} (engines: {', '.join(spec.engines)})")
        return out
    for need in spec.needs:
        ok, reason, _path = check_need(ws, game, need)
        if not ok:
            out.update(status="skipped", reason=f"needs {need}: {reason}")
            return out
    ctx = Context(ws, game, spec, options, log)
    for need in spec.needs + spec.uses:
        ok, _reason, path = check_need(ws, game, need)
        if ok and not need.startswith("analysis:") and need != "unity.install":
            ctx.use(need, path)
    try:
        report = spec.func(ctx)
        if not isinstance(report, Report):
            raise TypeError(f"analysis {name} returned {type(report).__name__}, not Report")
    except Exception:
        out.update(status="failed", reason=traceback.format_exc(), seconds=round(time.time() - t0, 2))
        return out
    h = header(ctx, time.time() - t0)
    doc = {"header": h, "summary": report.summary, "data": report.data}
    if report.notes:
        doc["notes"] = report.notes
    jpath, mpath = ws / ANALYSIS_DIR / f"{name}.json", ws / ANALYSIS_DIR / f"{name}.md"
    try:
        _atomic_write(jpath, json.dumps(doc, ensure_ascii=False, indent=1, default=str) + "\n")
        md = header_md(h, spec.description or name)
        if report.notes:
            md += "\n".join(f"> {n}" for n in report.notes) + "\n\n"
        _atomic_write(mpath, md + (report.markdown or "").rstrip() + "\n")
    except Exception:
        out.update(status="failed", reason=traceback.format_exc(), seconds=round(time.time() - t0, 2))
        return out
    rel = [p.relative_to(ws).as_posix() for p in (jpath, mpath)]
    side = sorted(set(ctx.outputs))
    out.update(json=rel[0], md=rel[1], outputs=rel + ([f"{ANALYSIS_DIR}/{name}/"] if side else []), summary=report.summary,
               notes=report.notes, seconds=round(time.time() - t0, 2), side_files=len(side))
    return out


def run_all(ws: Path, game: dict, options: dict | None = None, *, names: list[str] | None = None,
            log: Callable[[str], None] | None = None) -> list[dict]:
    """Every applicable analysis (or ``names``) in dependency order; options ``<name>.<key>`` go to that analysis."""
    engine = (game.get("engine") or {}).get("key")
    specs = for_engine(engine) if names is None else ordered([get(n) for n in names])
    return [run(ws, game, s.name, options_for(s.name, options or {}), log=log) for s in specs]


def options_for(name: str, options: dict) -> dict:
    """``{"prefabs.marker": "X", "file": "..."}`` -> options of ``prefabs``: its own keys win over shared ones."""
    shared = {k: v for k, v in options.items() if "." not in k}
    own = {k.split(".", 1)[1]: v for k, v in options.items() if k.startswith(name + ".")}
    return {**shared, **own}


# --- helpers for analyses ------------------------------------------------------------------------

def counted(counter: dict | collections.Counter) -> dict:
    """A counter as a dict sorted by count (desc) then key: deterministic JSON."""
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0]))))


def md_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    def cell(v: Any) -> str:
        if v is None:
            return "-"
        if isinstance(v, float):
            v = f"{v:.4g}"
        return str(v).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def stats(values: list[float]) -> dict:
    """min / median / p90 / max / sum of a list of numbers (empty -> {})."""
    if not values:
        return {}
    v = sorted(values)
    n = len(v)

    def pct(p: float):
        return v[min(n - 1, max(0, int(round(p * (n - 1)))))]

    med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    return {"n": n, "min": v[0], "median": med, "p90": pct(0.9), "max": v[-1], "sum": sum(v)}
