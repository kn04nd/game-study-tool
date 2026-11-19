"""Ghidra headless on an IL2CPP game's GameAssembly with the names of :mod:`gst.engines.il2cpp.names`.

Same layout as ``gst decompile native`` (``decomp/ghidra/GameAssembly.dll/`` + the project in
``decomp/ghidra/_projects/``, an entry in ``decomp/ghidra/MANIFEST.json``), so ``gst index`` reads the export through
its ``.c.map.json`` sidecars and ``gst decompile native GameAssembly.dll --functions <regex>`` re-exports without
re-analysis. Every function is analyzed and decompiled (no function count, size or time limit).

Scripts, in order: ``GsPdbPrescript`` (no PDB: PDB analysis off) and ``GstIl2cppNames pre`` before the analysis (labels
in a namespace per type + entry points at every method RVA: the analysis creates the functions there, with their
names and boundaries), then ``GstIl2cppNames post`` (creates and names what the analysis missed, plate comments with
the C# signature; writes ``il2cpp_names.json`` with the counts) and ``GsExport``. Without any names source Ghidra runs
unnamed and the result says so. When only the names changed (same GameAssembly, same analysis options), the saved
analysis is reused: ``post`` applies the new names on it, then the export runs again.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from gst import policy, tools
from gst.common import BAT_UNSAFE, GsError, check_bat_args, read_json, write_json  # noqa: F401 (re-exported)
from gst.decompile import native
from gst.engines.il2cpp import names as il2cpp_names
from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.registry import register

SCRIPT = "GstIl2cppNames.java"
NAMES_VERSION = 1  # bump when GstIl2cppNames.java changes what it applies: names re-applied on the saved analysis
REPORT = "il2cpp_names.json"
PRE_REPORT = "il2cpp_names.pre.json"
# analyzeHeadless is a .bat, so every argument passes through cmd.exe: gst.common.check_bat_args refuses
# anything cmd.exe would reinterpret.


def command(headless: Path, project: Path, module: Path, out: Path, *, names: Path | None, log: Path, reuse: bool,
            apply_names: bool, flat: bool = False, max_functions: int = 0, seconds: int = 0) -> list[str]:
    """analyzeHeadless arguments. ``reuse`` = open the saved analysis (``-process -noanalysis``); ``apply_names`` with
    reuse runs only the ``post`` pass (names changed); a fresh import runs ``pre`` before the analysis too."""
    style = "flat" if flat else "ns"
    cmd = [str(headless), str(project.parent), project.name]
    if reuse:
        cmd += ["-process", module.name, "-noanalysis"]
        if not apply_names:
            cmd.append("-readOnly")
    else:
        cmd += ["-import", str(module), "-overwrite", "-preScript", "GsPdbPrescript.java", "-"]
        if names is not None:
            cmd += ["-preScript", SCRIPT, str(names), "pre", str(out / PRE_REPORT), style]
    cmd += ["-scriptPath", str(native.scripts_dir())]
    if names is not None and (apply_names or not reuse):
        cmd += ["-postScript", SCRIPT, str(names), "post", str(out / REPORT), style]
    cmd += ["-postScript", "GsExport.java", str(out), str(max_functions), str(seconds), "-",
            "-max-cpu", str(max(1, (os.cpu_count() or 4) - 2)), "-log", str(log),
            "-scriptlog", str(log.with_suffix(".script.log"))]
    check_bat_args(cmd, "analyzeHeadless.bat")
    return cmd


def cpp2il_diffable(ws: Path, install: Path) -> bool:
    """Fallback names source when the workspace has none: Cpp2IL ``--output-as diffable-cs`` with the attribute injector
    (method RVAs as ``[Address(RVA = ..)]``; minutes, reads the install only) into ``decomp/il2cpp/cpp2il-diffable/``.
    False when Cpp2IL is not installed or fails (the log stays in ``logs/cpp2il-diffable.log``)."""
    import subprocess

    try:
        exe = tools.bin_path("cpp2il")
    except Exception:  # not in this build's tool catalog
        return False
    if exe is None:
        return False
    out = ws / il2cpp_names.CPP2IL_DIRS[1]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    cmd = [str(exe), "--game-path", str(install), "--use-processor", "attributeinjector", "--output-as", "diffable-cs",
           "--output-to", str(out)]
    (ws / "logs").mkdir(exist_ok=True)
    with open(ws / "logs" / "cpp2il-diffable.log", "w", encoding="utf-8", errors="replace") as fh:
        code = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(out)).returncode
    return code == 0 and any(out.rglob("*.cs"))


def run(ws: Path, game: dict, *, force: bool = False, flat: bool = False, seconds: int = 0, max_functions: int = 0,
        reanalyze: bool = False, index: bool = True) -> dict:
    """Analyze + name + export GameAssembly; returns the module summary (also stored in decomp/ghidra/MANIFEST.json)."""
    ws = Path(ws)
    install = Path(game["install_dir"])
    binary = il2cpp_names.game_assembly(install)
    if binary is None:
        raise GsError(f"no GameAssembly in {install}: not an IL2CPP build")
    decision = policy.check(game, "decompile", str(binary))
    if not decision.allowed:
        raise GsError(f"{decision.reason} (guardrails.md rule {decision.rule})", 3)
    headless = tools.bin_path("ghidra")
    if headless is None:
        raise GsError("Ghidra is not installed (gst tools install ghidra)")
    env = native.environment()

    names_info = il2cpp_names.build(ws, install)
    if not names_info.get("path") and cpp2il_diffable(ws, install):
        names_info = il2cpp_names.build(ws, install)
    names_path = ws / names_info["path"] if names_info.get("path") else None
    names_sha = names_info.get("entries_sha256") if names_path else None

    name = native.safe_name(binary.name)
    root = ws / native.OUT_DIR
    root.mkdir(parents=True, exist_ok=True)
    (ws / "logs").mkdir(exist_ok=True)
    manifest_path = root / "MANIFEST.json"
    manifest = read_json(manifest_path, {}) or {}
    prev = (manifest.get("modules") or {}).get(name) or {}
    sha = (names_info.get("game_assembly") or {}).get("sha256") or native.sha256_of(binary)
    out = root / name
    project = root / "_projects" / name / name
    analyzed = (prev.get("sha256") == sha and prev.get("analysis_version") == native.ANALYSIS_VERSION
                and (project.parent / f"{name}.gpr").is_file())
    same_names = prev.get("names_sha256") == names_sha and prev.get("names_version") == NAMES_VERSION \
        and prev.get("names_style") == ("flat" if flat else "ns")
    same_export = prev.get("script_version") == native.SCRIPT_VERSION and prev.get("max_functions") == max_functions
    if not force and analyzed and same_names and same_export and (out / "meta.json").is_file():
        return {**prev, "reused": True, "names": names_info}
    reuse = analyzed and not force and not reanalyze
    apply_names = names_path is not None and (not same_names or not reuse)

    work = root / "_input"
    work.mkdir(exist_ok=True)
    local = work / name
    if not reuse:
        shutil.copy2(binary, local)  # the install stays untouched; gst-made path for the command line
        if project.parent.exists():
            shutil.rmtree(project.parent)
    project.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    log = ws / "logs" / f"ghidra-{name}.log"
    cmd = command(Path(headless), project, local, out, names=names_path, log=log, reuse=reuse,
                  apply_names=apply_names, flat=flat, max_functions=max_functions, seconds=seconds)
    what = ("re-exporting" if not apply_names else "applying new names to the saved analysis of") if reuse else "analyzing"
    sys.stderr.write(f"gst: Ghidra {what} {binary.name} ({binary.stat().st_size >> 20} MB, "
                     f"{'names from ' + ', '.join(names_info['sources']) if names_path else 'no IL2CPP names source'}); "
                     "hours for a big GameAssembly...\n")
    t0 = time.time()
    code = native._run(cmd, env, ws / "logs" / f"ghidra-{name}.console.txt", None)
    if local.exists():
        local.unlink()
    if work.is_dir() and not any(work.iterdir()):
        work.rmdir()
    meta = read_json(out / "meta.json", None)
    report = read_json(out / REPORT, None)
    pre = read_json(out / PRE_REPORT, None)
    notes = []
    if names_path is None:
        notes.append("no IL2CPP names source (Il2CppDumper script.json, Il2CppInspector JSON, Cpp2IL diffable C#, REA "
                     "evidence with binary_path): GameAssembly analyzed without names")
    notes += names_info.get("notes") or []
    entry: dict = {"source": str(binary), "sha256": sha, "exit_code": code, "seconds": round(time.time() - t0, 1),
                   "log": str(log), "reused_analysis": reuse, "names_sha256": names_sha, "names_version": NAMES_VERSION,
                   "names_style": "flat" if flat else "ns", "names_sources": sorted(names_info.get("sources") or {}),
                   "names_counts": names_info.get("counts"), "il2cpp_names": (report or {}).get("counts"),
                   "il2cpp_names_pre": (pre or {}).get("counts"), "notes": notes}
    if meta is None:
        entry["failed"] = True
    else:
        entry.update({"pdb": False, "functions": meta.get("functions"), "decompiled": meta.get("decompiled"),
                      "listed": meta.get("listed"), "failed_functions": meta.get("failed"), "imports": meta.get("imports"),
                      "strings": meta.get("strings"), "max_functions": max_functions, "only": None,
                      "script_version": native.SCRIPT_VERSION, "analysis_version": native.ANALYSIS_VERSION,
                      "ghidra": meta.get("ghidra"), "il2cpp": True})
    manifest = read_json(manifest_path, {}) or {}
    manifest.update({"backend": "native", "tool": "ghidra", "tool_version": tools.installed_version(tools.get("ghidra")),
                     "game_build": game.get("buildid"), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    manifest.setdefault("modules", {})[name] = entry
    write_json(manifest_path, manifest)
    if index and meta is not None:
        from gst import index as code_index

        entry["index"] = {k: v for k, v in code_index.build(ws).items() if k in ("files", "symbols", "failures", "seconds")}
    return {**entry, "names": names_info, "out": str(out), "reused": False}


# --- gst study extractor --------------------------------------------------------------------------

# Producers of names.jsonl sources (the IL2CPP tools, then REA). Names, not imports: an extractor missing from
# this build is simply not waited for.
NAME_PRODUCERS = ("code.il2cpp.metadata", "code.il2cpp.dummydll", "code.il2cpp.maps", "code.il2cpp.decompile",
                  "rea.il2cpp_metadata")


def _sources_state(ws: Path) -> list[list]:
    """(source, path, files, bytes, newest mtime) of every names source: the study job reruns when one changes."""
    out = []
    for source, path in il2cpp_names.find_sources(ws):
        files = total = newest = 0
        for p in (path.rglob("*") if path.is_dir() else [path]):
            try:
                st = p.stat()
            except OSError:
                continue
            if p.is_file():
                files += 1
                total += st.st_size
                newest = max(newest, st.st_mtime_ns)
        out.append([source, path.relative_to(ws).as_posix(), files, total, newest])
    return out


@register("unity-il2cpp")
class Il2cppNativeCode(Extractor):
    """Ghidra on GameAssembly with the IL2CPP names (every function, no time limit), exported and indexed."""

    name = "code.il2cpp.native"
    category = "code"
    resource = "heavy"
    stage = 70
    after = NAME_PRODUCERS

    def applies(self, ctx: Context) -> bool:
        return il2cpp_names.game_assembly(ctx.install_dir) is not None

    def discover(self, ctx: Context):
        from gst.pipeline.jobs import fingerprint_of, stat_inputs

        install = ctx.install_dir.resolve()
        binary = il2cpp_names.game_assembly(ctx.install_dir)
        relpaths = [binary.resolve().relative_to(install).as_posix()]
        relpaths += [p.resolve().relative_to(install).as_posix()
                     for p in sorted(ctx.install_dir.glob("*_Data/il2cpp_data/Metadata/global-metadata.dat"))]
        state = stat_inputs(ctx.install_dir, relpaths) + _sources_state(ctx.workspace)
        yield Unit(id=relpaths[0], extractor=self.name, relpaths=relpaths, size=binary.stat().st_size,
                   fingerprint=fingerprint_of(state, ctx.game.get("buildid"), f"il2cpp-names-{NAMES_VERSION}"),
                   meta={"name": binary.name})

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.pipeline.worker import tree_bytes

        def opt(key: str) -> bool:
            return str(ctx.options.get(key, "")).lower() in {"1", "true", "yes", "on"}

        version = tools.installed_version(tools.get("ghidra")) or ""
        game = {**ctx.game, "install_dir": str(ctx.install_dir)}
        try:
            info = run(ctx.workspace, game, flat=opt("il2cpp.flat"), reanalyze=opt("il2cpp.reanalyze"), force=opt("il2cpp.force"))
        except GsError as exc:
            return Result(status="blocked-policy" if exc.code == 3 else "failed", error=str(exc), tool="ghidra", tool_version=version)
        name = native.safe_name(unit.meta["name"])
        out = ctx.workspace / native.OUT_DIR / name
        if info.get("failed"):
            console = ctx.workspace / "logs" / f"ghidra-{name}.console.txt"
            text = console.read_text(encoding="utf-8", errors="replace") if console.exists() else ""
            return Result(status="failed", error=f"Ghidra produced no export for {unit.meta['name']} (exit {info.get('exit_code')}); "
                                                 f"log {info.get('log')}\n{text}", tool="ghidra", tool_version=version)
        applied = info.get("il2cpp_names") or {}
        named = sum(applied.get(k, 0) for k in ("already_named", "renamed_default", "renamed_other"))
        sources = info.get("names_sources") or []
        notes = [f"{info.get('decompiled')} of {info.get('functions')} functions decompiled; "
                 + (f"{named} named from {', '.join(sources)}" if sources else "no IL2CPP names source: unnamed")]
        notes += [f"{k}: {applied[k]}" for k in ("created_method", "created_inside_other_function", "failed_method", "errors")
                  if applied.get(k)]
        notes += info.get("notes") or []
        outputs = [out.relative_to(ctx.workspace).as_posix()]
        if (info.get("names") or {}).get("path"):
            outputs.append(info["names"]["path"])
        return Result(outputs=outputs, out_bytes=tree_bytes(out), tool="ghidra", tool_version=version, notes=notes)
