"""``code.java`` (Vineflower, one unit per jar or loose class tree) and ``code.lua`` (plain scripts copied,
bytecode through unluac, one unit per script folder), stage 20, followed by ``code.index``.

Both apply to any engine whose install holds jars/classes or Lua (Project Zomboid, Minecraft-likes, LÖVE, Lua
next to a native or Unity engine...). ``gst decompile java|lua`` runs the same extractors in this process
(:func:`run_inprocess`), so both commands write the same outputs and manifests.

Outputs: ``decomp/java/<jar stem>/``, ``decomp/java/classes/<package>/`` and ``decomp/lua/<path in the install>``,
a ``<out>.log`` per Java unit, one manifest fragment per unit in ``decomp/<java|lua>/_manifest/`` (with the unit's
fingerprint: ``gst decompile`` reuses a unit whose fingerprint did not change), merged into
``decomp/<java|lua>/MANIFEST.json`` by ``finalize``.

Options (``gst study --opt key=value``): ``java.filter=zombie/core/,...`` (package prefixes), ``java.all=1`` (every
package and the library jars), ``java.units=<jar or package>,...`` (decompile these even when they look like
libraries), ``java.threads=N``. The options are part of each unit's fingerprint: changing them reruns the units.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from gst.common import read_json, write_json, write_text
from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.registry import register

JAVA_DIR = Path("decomp") / "java"
LUA_DIR = Path("decomp") / "lua"
FRAGMENTS = "_manifest"

_UNITS: dict[tuple[str, str, str], list[dict]] = {}  # (kind, install, engine) -> units: one scan per process


def clear_cache() -> None:
    """Forget the scans (tests that change an install between two plans)."""
    _UNITS.clear()


def _engine(ctx: Context) -> str:
    return str((ctx.game.get("engine") or {}).get("key") or "")


def _java_units(ctx: Context) -> list[dict]:
    from gst.decompile import java

    key = ("java", str(Path(ctx.install_dir).resolve()), _engine(ctx))
    if key not in _UNITS:
        _UNITS[key] = java.units(ctx.install_dir, _engine(ctx))
    return _UNITS[key]


def _lua_units(ctx: Context) -> list[dict]:
    from gst.decompile import lua

    key = ("lua", str(Path(ctx.install_dir).resolve()), "")
    if key not in _UNITS:
        _UNITS[key] = lua.units(ctx.install_dir)
    return _UNITS[key]


def _tool_version(tool_id: str) -> str:
    from gst import tools

    try:
        return tools.installed_version(tools.get(tool_id)) or ""
    except Exception:
        return ""


def _blocked(ctx: Context, target: str) -> str | None:
    from gst import policy

    decision = policy.check(ctx.game, "decompile", target)
    return None if decision.allowed else f"{decision.reason} (guardrails.md rule {decision.rule})"


def _fingerprint(ctx: Context, relpaths: list[str], extra: dict) -> str:
    from gst.pipeline.jobs import fingerprint_of, stat_inputs

    return fingerprint_of(stat_inputs(ctx.install_dir, relpaths), str(ctx.game.get("buildid") or "?"),
                          json.dumps(extra, sort_keys=True))


def _fragment_path(ctx: Context, root: Path, name: str) -> Path:
    safe = name.strip("/").replace("/", "__") or "_root"
    return ctx.workspace / root / FRAGMENTS / f"{'_root' if safe == '.' else safe}.json"


def _merge(ctx: Context, root: Path, header: dict, key: str) -> Result | None:
    frags = sorted((ctx.workspace / root / FRAGMENTS).glob("*.json"))
    if not frags:
        return None
    entries = {}
    for frag in frags:
        data = read_json(frag, {}) or {}
        entries[data.get("out") or data.get("unit") or frag.stem] = data
    manifest = ctx.workspace / root / "MANIFEST.json"
    write_json(manifest, {**header, "game_build": ctx.game.get("buildid"), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          key: entries})
    return Result(outputs=[manifest.relative_to(ctx.workspace).as_posix()], out_bytes=manifest.stat().st_size,
                  tool="gst", notes=[f"{len(entries)} units in {manifest.relative_to(ctx.workspace).as_posix()}"])


# --- Java ---------------------------------------------------------------------------------------

@register()
class JavaCode(Extractor):
    name = "code.java"
    category = "code"
    resource = "heavy"  # Vineflower on a fat jar takes every core and GBs of heap: one at a time
    stage = 20
    decomp_dir = JAVA_DIR

    def applies(self, ctx: Context) -> bool:
        return bool(_java_units(ctx))

    def discover(self, ctx: Context):
        from gst.decompile import java

        version = _tool_version("vineflower")
        for u in _java_units(ctx):
            meta = {k: v for k, v in u.items() if k not in ("relpaths", "size")}
            extra = {"backend": "java", "export": java.EXPORT_VERSION, "tool": version, "run": java.wanted(u, ctx.options),
                     "only": java.prefixes_for(u, ctx.options)}
            yield Unit(id=u["id"], extractor=self.name, relpaths=u["relpaths"], size=u["size"],
                       fingerprint=_fingerprint(ctx, u["relpaths"], extra), meta=meta)

    def fragment(self, ctx: Context, unit: Unit) -> Path:
        return _fragment_path(ctx, JAVA_DIR, unit.meta["out"])

    def outputs_exist(self, ctx: Context, unit: Unit) -> bool:
        return (ctx.workspace / JAVA_DIR / unit.meta["out"]).is_dir()

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.common import GsError
        from gst.decompile import java
        from gst.pipeline.worker import commit, partial_dir, tree_bytes

        meta = {**unit.meta, "relpaths": unit.relpaths}
        if not java.wanted(meta, ctx.options):
            what = "library jar" if meta["kind"] == "jar" else "library class tree"
            return Result(status="skipped", notes=[f"{unit.id}: {what} ({', '.join(meta.get('library_roots') or [meta['name']])}), "
                                                   "not the game's code (gst study --opt java.all=1, or java.units=<name>)"])
        if meta.get("error"):
            return Result(status="failed", error=f"{unit.id} is not a readable jar: {meta['error']}", tool="vineflower")
        source = ctx.install_dir / unit.relpaths[0]
        reason = _blocked(ctx, str(source))
        if reason:
            return Result(status="blocked-policy", error=reason)
        prefixes = java.prefixes_for(meta, ctx.options)
        threads = int(ctx.options.get("java.threads") or 0) or None
        staging = partial_dir(ctx, unit)
        version = _tool_version("vineflower")
        try:
            facts = java.run_unit(ctx.install_dir, meta, staging, prefixes, threads)
        except GsError as exc:
            return Result(status="failed", error=str(exc), tool="vineflower", tool_version=version)
        if not facts["matched"]:
            return Result(status="skipped", tool="vineflower", tool_version=version,
                          notes=[f"{unit.id}: no class under {', '.join(prefixes)} (java.filter)"])
        log = facts.pop("log")
        if facts["exit_code"] != 0 and not facts["files"]:
            return Result(status="failed", error=f"Vineflower exited with {facts['exit_code']} on {unit.id}\n{log}",
                          tool="vineflower", tool_version=version)
        final = ctx.workspace / JAVA_DIR / meta["out"]
        produced = staging / "out"
        if meta["kind"] == "classes" and meta.get("package"):  # out = classes/<package>: its sources start at <package>/
            produced = produced / meta["package"]
            produced.mkdir(parents=True, exist_ok=True)
        commit(produced, final)
        write_text(final.parent / f"{final.name}.log", log)
        from gst.decompile.dotnet import sha256_of

        fragment = {"unit": unit.id, "out": meta["out"], **facts, "fingerprint": unit.fingerprint, "tool": "vineflower",
                    "tool_version": version, "sha256": sha256_of(source) if meta["kind"] == "jar" else None,
                    "main": meta.get("main"), "game": meta.get("game")}
        write_json(self.fragment(ctx, unit), fragment)
        notes = [f"{facts['files']} Java files from {facts['matched']} of {facts['classes']} classes"
                 + (f" under {', '.join(prefixes)}" if prefixes else "")]
        if facts["exit_code"] != 0:
            notes.append(f"Vineflower exited with {facts['exit_code']} (see {final.relative_to(ctx.workspace).as_posix()}.log)")
        return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=tree_bytes(final), tool="vineflower",
                      tool_version=version, notes=notes)

    def finalize(self, ctx: Context) -> Result | None:
        return _merge(ctx, JAVA_DIR, {"backend": "java", "tool": "vineflower", "tool_version": _tool_version("vineflower")},
                      "jars")


# --- Lua ----------------------------------------------------------------------------------------

@register()
class LuaCode(Extractor):
    name = "code.lua"
    category = "code"
    resource = "io"  # mostly copies; unluac runs once per compiled chunk
    stage = 20
    decomp_dir = LUA_DIR

    def applies(self, ctx: Context) -> bool:
        return bool(_lua_units(ctx))

    def discover(self, ctx: Context):
        for u in _lua_units(ctx):
            relpaths = u["plain"] + u["bytecode"]
            extra = {"backend": "lua"}
            if u["bytecode"]:  # a new unluac or another verdict changes what the bytecode gives
                extra.update({"tool": _tool_version("unluac"), "verdict": ctx.game.get("verdict")})
            yield Unit(id=u["id"], extractor=self.name, relpaths=relpaths, size=u["size"],
                       fingerprint=_fingerprint(ctx, relpaths, extra), meta={k: v for k, v in u.items() if k != "size"})

    def fragment(self, ctx: Context, unit: Unit) -> Path:
        return _fragment_path(ctx, LUA_DIR, unit.id)

    def outputs_exist(self, ctx: Context, unit: Unit) -> bool:
        prev = read_json(self.fragment(ctx, unit), {}) or {}
        return all((ctx.workspace / p).exists() for p in prev.get("outputs") or [])

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.decompile import lua
        from gst.pipeline.worker import commit, partial_dir, rmtree_force, tree_bytes

        meta = {**unit.meta, "id": unit.id}
        staging = partial_dir(ctx, unit) / "out"
        facts = lua.run_unit(ctx.install_dir, ctx.game, meta, staging)
        target = ctx.workspace / LUA_DIR / ("" if unit.id == "." else unit.id)
        frag_path = self.fragment(ctx, unit)
        previous = (read_json(frag_path, {}) or {}).get("outputs") or []
        if meta["recursive"]:  # the unit owns its whole folder
            commit(staging, target)
            outputs = [target.relative_to(ctx.workspace).as_posix()]
        else:  # only its own files: the folder may hold other units' subfolders
            outputs = []
            for item in sorted(staging.iterdir()):
                commit(item, target / item.name)
                outputs.append((target / item.name).relative_to(ctx.workspace).as_posix())
            for stale in set(previous) - set(outputs):
                path = ctx.workspace / stale
                if path.is_dir():
                    rmtree_force(path)
                else:
                    path.unlink(missing_ok=True)
        bytecode = facts["bytecode"]
        decompiled = sum(1 for b in bytecode if b.get("decompiled"))
        blocked = [b for b in bytecode if b.get("blocked")]
        version = _tool_version("unluac") if bytecode else ""
        write_json(frag_path, {**facts, "outputs": outputs, "fingerprint": unit.fingerprint, "tool": "copy + unluac",
                               "unluac_version": version or None})
        notes = [f"{facts['plain_files']} plain scripts copied"]
        notes += [f"bytecode {b['file']} ({b.get('kind')} {b.get('version')}): "
                  + ("decompiled" if b.get("decompiled") else f"NOT decompiled: {b.get('error')}")
                  + (f"; source twin {b['source_twin']}" if b.get("source_twin") else "") for b in bytecode]
        out_bytes = sum(tree_bytes(ctx.workspace / o) for o in outputs)
        tool = "copy + unluac" if bytecode else "copy"
        if facts["plain_files"] or decompiled or not bytecode:
            return Result(outputs=outputs, out_bytes=out_bytes, tool=tool, tool_version=version, notes=notes)
        if len(blocked) == len(bytecode):
            return Result(status="blocked-policy", error=blocked[0]["error"], outputs=outputs, notes=notes)
        return Result(status="unsupported", error="\n".join(f"{b['file']}: {b.get('stderr') or b.get('error')}" for b in bytecode),
                      outputs=outputs, out_bytes=out_bytes, tool=tool, tool_version=version, notes=notes)

    def finalize(self, ctx: Context) -> Result | None:
        result = _merge(ctx, LUA_DIR, {"backend": "lua", "tool": "copy + unluac",
                                       "unluac_version": _tool_version("unluac") or None}, "units")
        if result is not None:  # the totals the older single-run manifest had
            manifest = ctx.workspace / LUA_DIR / "MANIFEST.json"
            data = read_json(manifest, {}) or {}
            units = data.get("units", {}).values()
            data["plain_files"] = sum(u.get("plain_files", 0) for u in units)
            data["bytecode"] = [b for u in units for b in u.get("bytecode", [])]
            write_json(manifest, data)
        return result


# --- gst decompile: the same extractors in this process --------------------------------------------

def run_inprocess(ws: Path, game: dict, cls: type[Extractor], options: dict | None = None, *, select=None,
                  force: bool = False) -> list[dict]:
    """Discover and run every unit of ``cls`` here (``gst decompile``), then its ``finalize``. A unit whose fragment
    records the same fingerprint, with its outputs present, is reused unless ``force``. One row per unit:
    ``{unit, out, status, reused, error, notes, outputs, facts}`` (``facts`` = the unit's manifest fragment)."""
    from gst.pipeline.worker import context_for, partial_path, rmtree_force

    ext = cls()
    ctx = context_for(Path(ws), game, options)
    if not ext.applies(ctx):
        return []
    rows: list[dict] = []
    ran = False
    for unit in ext.discover(ctx):
        if select is not None and not select(unit):
            continue
        frag = ext.fragment(ctx, unit)
        prev = read_json(frag, None) if frag.exists() else None
        out = unit.meta.get("out") or unit.id
        if not force and prev and prev.get("fingerprint") == unit.fingerprint and ext.outputs_exist(ctx, unit):
            rows.append({"unit": unit.id, "out": out, "status": "done", "reused": True, "error": None, "notes": [],
                         "outputs": [], "facts": prev})
            continue
        try:
            result = ext.run(unit, ctx)
        finally:
            rmtree_force(partial_path(ctx.out_root, unit.extractor, unit.id))
        ran = ran or result.status == "done"
        facts = read_json(frag, None) if result.status in ("done", "unsupported", "blocked-policy") and frag.exists() else None
        rows.append({"unit": unit.id, "out": out, "status": result.status, "reused": False, "error": result.error,
                     "notes": result.notes, "outputs": result.outputs, "facts": facts or {}})
    partial_root = ctx.out_root / ".partial"
    if partial_root.is_dir() and not any(partial_root.iterdir()):
        partial_root.rmdir()
    if ran or (rows and not (ctx.workspace / ext.decomp_dir / "MANIFEST.json").exists()):
        ext.finalize(ctx)
    return rows
