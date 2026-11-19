"""gst decompile: turn the game's code into readable source under decomp/ (then index it)."""

from __future__ import annotations

from pathlib import Path

from gst import workspace
from gst.common import GsError, emit, human_size

BACKENDS = ("dotnet", "godot", "java", "lua", "native", "il2cpp")


def register(sub, parent) -> None:
    p = sub.add_parser("decompile", parents=[parent], help="decompile the game's code into decomp/ and index it",
                       description=__doc__ + " Backends: dotnet (Unity Mono, Godot .NET, XNA/FNA and other .NET "
                                             "games: ilspycmd), godot (GDScript: GDRE Tools), java (jars: Vineflower), "
                                             "lua (plain scripts copied, bytecode through unluac), native (exe/dll: "
                                             "Ghidra headless with the shipped PDB, decompiled C by namespace), il2cpp "
                                             "(Unity IL2CPP: metadata, dummy DLLs by Cpp2IL/Il2CppDumper/Il2CppInspector, "
                                             "C# stubs with RVAs by ilspycmd in decomp/cs-il2cpp).")
    p.add_argument("backend", nargs="?", default="auto", choices=["auto", *BACKENDS],
                   help="auto = from the engine found by recon (default)")
    p.add_argument("names", nargs="*", help="dotnet: assemblies; native: modules; java: jars or class packages "
                                            "(default: the game's own; see --list)")
    p.add_argument("--list", action="store_true", help="dotnet/native/java: show the assemblies, modules or jars found, then stop")
    p.add_argument("--all", action="store_true", help="dotnet/il2cpp: also framework and third-party assemblies; "
                                                      "java: every package and the library jars")
    p.add_argument("--maps", action="store_true", help="il2cpp: also the name maps for Ghidra (Il2CppInspector, Il2CppDumper)")
    p.add_argument("--jobs", type=int, default=2, help="dotnet: assemblies decompiled in parallel (default 2)")
    p.add_argument("--force", action="store_true", help="dotnet/java/lua: decompile again even if the input did not change")
    p.add_argument("--only", action="append", help="java: package prefix to decompile (repeatable), e.g. zombie/core/ "
                                                   "(gst study --opt java.filter=zombie/core/)")
    p.add_argument("--max-functions", type=int, default=0,
                   help="native: functions decompiled per module, named ones first (default 0 = all); the rest are listed")
    p.add_argument("--functions", help="native: regex on qualified names to decompile (re-exports without re-analysis)")
    p.add_argument("--timeout", type=int, default=None, help="native: Ghidra analysis limit per module in seconds (default: none)")
    p.add_argument("--function-timeout", type=int, default=None,
                   help="native: decompiler limit per function in seconds (default: none, every function runs to the end)")
    p.add_argument("--no-index", action="store_true", help="skip `gst index` afterwards")
    p.set_defaults(func=cmd_decompile)


def is_godot_dotnet(install: Path) -> bool:
    return any(d.is_dir() and any(d.glob("GodotSharp*.dll")) for d in install.glob("data_*"))


def backends_for(game: dict) -> list[str]:
    """The engine's backend, plus ``lua`` whenever the install holds Lua (it ships next to any engine) and ``java``
    when it holds a game jar or class tree."""
    from gst.decompile import java, lua
    from gst.recon import fingerprint

    key = (game.get("engine") or {}).get("key") or "native"
    backend = fingerprint.ENGINES.get(key, fingerprint.ENGINES["native"])[3]
    install = Path(game["install_dir"])
    plain, compiled = lua.scan(install)
    extra = ["java"] if backend != "java" and any(u["game"] for u in java.units(install, key)) else []
    extra += ["lua"] if backend != "lua" and (plain or compiled) else []
    if backend == "dotnet":
        return ["dotnet"] + extra
    if backend == "godot":
        return ["godot"] + (["dotnet"] if is_godot_dotnet(install) else []) + extra
    if backend in ("java", "lua", "native", "il2cpp"):
        return [backend] + extra
    if extra:
        return extra
    hint = ("Unreal Blueprint logic: gst assets extract --name <BP path regex> --bytecode (Kismet bytecode in JSON); "
            "the C++: gst decompile native <Game>-Win64-Shipping.exe (big: hours)" if backend == "ue"
            else "the managed part of any game: gst decompile dotnet; native modules: gst decompile native --list")
    raise GsError(f"no decompile backend for {key} ({backend}) in this version yet; available: {', '.join(BACKENDS)} ({hint})")


def _dotnet(ws: Path, game: dict, args) -> tuple[list[str], list[str]]:
    from gst.decompile import dotnet

    result = dotnet.decompile(ws, game, names=args.names or None, everything=args.all, jobs=args.jobs, force=args.force)
    done = result["assemblies"]
    files = sum(v.get("files", 0) for v in done.values())
    workspace.journal(ws, f"gst decompile dotnet: {len(done)} assemblies, {files} files"
                          + (f", failed: {', '.join(result['failed'])}" if result["failed"] else ""))
    lines = [f"dotnet -> {result['out']}"] + [
        f"  {name:<40} {v.get('files', 0):>6} files  {v.get('errors', 0):>4} errors  "
        f"{'reused' if v.get('reused') else str(v.get('seconds')) + ' s'}" + ("  (PDB names)" if v.get("pdb") else "")
        for name, v in done.items()]
    if result["skipped"]:
        lines.append(f"  skipped {len(result['skipped'])} framework/third-party assemblies (--list; name one to include it)")
    failures = [f"dotnet {name} (see decomp/cs/{name}.log)" for name in result["failed"]]
    return lines, failures


def _godot(ws: Path, game: dict, args) -> tuple[list[str], list[str]]:
    from gst.decompile import godot

    result = godot.decompile(ws, game)
    workspace.journal(ws, f"gst decompile godot: {result['scripts']} GDScript files from {Path(result['pack']).name}")
    lines = [f"godot -> {result['out']}: {result['scripts']} GDScript files from {Path(result['pack']).name} "
             f"({result['seconds']} s)" + (f", e.g. {', '.join(result['examples'][:3])}" if result["examples"] else "")]
    return lines, []


def _java(ws: Path, game: dict, args) -> tuple[list[str], list[str]]:
    from gst.decompile import java

    result = java.decompile(ws, game, jars=args.names or None, only=args.only, everything=args.all, force=args.force)
    lines = [f"java -> {result['out']}"]
    for name, v in result["jars"].items():
        roots = ", ".join(v.get("only") or ["(everything)"])
        lines.append(f"  {name:<32} {v.get('files', 0):>6} files  "
                     f"{'reused' if v.get('reused') else str(v.get('seconds')) + ' s'}  packages: {roots}"
                     + ("  FAILED" if v.get("status") == "failed" else ""))
        if v.get("skipped_roots"):
            lines.append(f"    other roots not decompiled: {', '.join(v['skipped_roots'])} (--only <prefix>/ or --all)")
    for s in result["skipped"]:
        lines.append(f"  skipped {s['unit']}: {s['why']}")
    workspace.journal(ws, "gst decompile java: " + ", ".join(f"{k} {v.get('files', 0)} files" for k, v in result["jars"].items()))
    return lines, [f"java {name} (see decomp/java/{name}.log)" for name in result["failed"]]


def _lua(ws: Path, game: dict, args) -> tuple[list[str], list[str]]:
    from gst.decompile import lua

    result = lua.decompile(ws, game, force=args.force)
    lines = [f"lua -> {result['out']}: {result['copied']} plain scripts in {result['units']} folders "
             f"({result['reused']} reused, {result['seconds']} s)"]
    for b in result["bytecode"]:
        what = (f"{b.get('kind')} {b.get('version')}" + (", big-endian" if b.get("big_endian") else ""))
        status = "decompiled" if b.get("decompiled") else f"NOT decompiled ({b.get('error')})"
        twin = f"; source twin: {b['source_twin']}" if b.get("source_twin") else ""
        lines.append(f"  bytecode {b['file']}: {what}: {status}{twin}")
    workspace.journal(ws, f"gst decompile lua: {result['copied']} scripts, {len(result['bytecode'])} bytecode chunks")
    return lines, []


def _native(ws: Path, game: dict, args) -> tuple[list[str], list[str]]:
    from gst.decompile import native

    result = native.decompile(ws, game, names=args.names or None, max_functions=args.max_functions, only=args.functions,
                              analysis_timeout=args.timeout, seconds=args.function_timeout or 0, force=args.force)
    lines = [f"native -> {result['out']}"]
    for name, v in result["modules"].items():
        if v.get("failed"):
            lines.append(f"  {name:<32} FAILED (exit {v.get('exit_code')}); see logs/ghidra-{name}.log")
            continue
        lines.append(f"  {name:<32} {v.get('decompiled', 0):>6} of {v.get('functions', 0)} functions decompiled  "
                     f"{'reused' if v.get('reused') else str(v.get('seconds')) + ' s'}"
                     + ("  (PDB names)" if v.get("pdb") else "  (no PDB: FUN_<address> names)")
                     + (f"  {v['listed']} listed only (--functions <regex>)" if v.get("listed") else ""))
    if result["skipped"]:
        lines.append(f"  {len(result['skipped'])} other native modules not analyzed (--list; name one to include it)")
    workspace.journal(ws, "gst decompile native: " + ", ".join(f"{k} {v.get('decompiled', 0)} functions"
                                                               for k, v in result["modules"].items()))
    return lines, [f"native {name} (see logs/ghidra-{name}.log)" for name in result["failed"]]


def _il2cpp(ws: Path, game: dict, args) -> tuple[list[str], list[str]]:
    from gst.engines.il2cpp import extractors

    options = {"il2cpp.all": "1" if args.all else "", "timeout": args.timeout}
    if args.force:
        from gst.engines.il2cpp import decompile

        (ws / decompile.OUT_DIR / "MANIFEST.json").unlink(missing_ok=True)
    result = extractors.run_all(ws, game, options, maps=args.maps)
    lines, failures = ["il2cpp -> decomp/il2cpp, decomp/cs-il2cpp"], []
    for name, units in result.items():
        for r in units:
            lines.append(f"  {name:<24} {r['unit'] or '-':<18} {r['status']:<11} " + "; ".join(r.get("notes") or []))
            first = (r.get("error") or "").strip().splitlines()[:1]
            if r["status"] == "failed":
                failures.append(f"il2cpp {name} {r['unit']}: {first[0] if first else ''}")
            elif r["status"] == "unsupported" and first:
                lines.append(f"    {first[0]}")
    workspace.journal(ws, "gst decompile il2cpp: " + ", ".join(f"{k} {'/'.join(r['status'] for r in v)}" for k, v in result.items()))
    return lines, failures


def _list(game: dict, backends: list[str], as_json: bool) -> int:
    install = Path(game["install_dir"])
    if backends[:1] == ["java"]:
        from gst.decompile import java

        engine = (game.get("engine") or {}).get("key")
        found = [{k: v for k, v in u.items() if k != "relpaths"} for u in java.units(install, engine)]
        lines = [f"{'game' if u['game'] else '    '}  {u['id']:<44} {human_size(u['size']):>9}  {u['classes']:>6} classes"
                 + (f"  packages: {', '.join(u['roots'])}" if u.get("roots") else "") for u in found]
        emit(found, as_json, "\n".join(lines) + f"\n\n{sum(u['game'] for u in found)} decompiled by default of {len(found)} "
             "jars/class trees (gst decompile java <name> ... to choose; --all for every one)")
        return 0
    if "native" in backends:
        from gst.decompile import native

        found = native.find_modules(install, game)
        lines = [f"{'game' if m['game'] else '    '}  {m['name']:<44} {human_size(m['size']):>9}{'  +pdb' if m['pdb'] else ''}"
                 f"{'  large' if m['large'] else ''}{'  third-party' if m['third_party'] else ''}" for m in found]
        emit(found, as_json, "\n".join(lines) + f"\n\n{sum(m['game'] for m in found)} analyzed by default of {len(found)} "
             "native modules (gst decompile native <name> ... to choose)")
        return 0
    from gst.decompile import dotnet

    found = dotnet.find_assemblies(install)
    lines = [f"{'game' if a['game'] else '    '}  {a['name']:<44} {human_size(a['size']):>9}{'  +pdb' if a['pdb'] else ''}"
             for a in found]
    emit(found, as_json, "\n".join(lines) + f"\n\n{sum(a['game'] for a in found)} game assemblies of {len(found)}"
         " (gst decompile dotnet <Name> ... to choose; --all for every one)")
    return 0


def cmd_decompile(args) -> int:
    ws, game = workspace.current()
    chosen = backends_for(game) if args.backend == "auto" else [args.backend]
    if args.list:
        return _list(game, chosen, args.json)
    lines, failures = [], []
    for backend in chosen:
        more, failed = {"dotnet": _dotnet, "godot": _godot, "java": _java, "lua": _lua, "native": _native,
                        "il2cpp": _il2cpp}[backend](ws, game, args)
        lines += more
        failures += failed
    game["phase"] = "code" if game.get("phase") in {None, "recon"} else game["phase"]
    workspace.save(ws, game)
    index_summary = None
    if not args.no_index:
        from gst import index

        index_summary = index.build(ws)
        workspace.journal(ws, f"gst index: {index_summary['files']} files, {index_summary['symbols']} symbols")
        lines.append(f"index: {index_summary['files']} files, {index_summary['symbols']} symbols, {index_summary['refs']} refs "
                     f"({index_summary['seconds']} s). Next: gst q def <Type>, gst q outline <Type>, gst q callers <Type.Method>")
    if failures:
        lines.append("FAILED: " + "; ".join(failures))
    emit({"backends": chosen, "index": index_summary, "failed": failures}, args.json, "\n".join(lines))
    return 1 if failures and len(failures) >= len(chosen) else 0
