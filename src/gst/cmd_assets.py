"""gst assets: inventory and convert a game's assets into readable files under assets/ (install untouched)."""

from __future__ import annotations

import argparse
from pathlib import Path

from gst import policy, workspace
from gst.common import GsError, emit, human_size


def register(sub, parent) -> None:
    p = sub.add_parser("assets", parents=[parent], help="inventory and convert game assets into assets/ (Unity, Godot, Unreal 4/5; audio: any engine)",
                       description=__doc__ + " Unity: UnityPy/AssetRipper. Godot: GDRE. Unreal: repak/uas. Any engine: audio (vgmstream).")
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")

    ls = s.add_parser("list", parents=[parent], help="every object or packed file (INVENTORY.md + inventory.jsonl); Unity: or a filtered view")
    _filters(ls)
    ls.set_defaults(func=cmd_list)

    ex = s.add_parser("extract", parents=[parent], help="convert selected assets (Unity: PNG/WAV/text/OBJ/JSON; Godot: scenes/textures; Unreal: --types json|textures|audio|models|raw)")
    _filters(ex)
    ex.add_argument("--all", action="store_true", help="no limit (ignores --limit; without --limit there is none)")
    ex.add_argument("--no-thumbs", action="store_true", help="skip 256 px thumbnails of large textures")
    ex.add_argument("--bytecode", action="store_true", help="Unreal json: include Blueprint (Kismet) bytecode")
    ex.add_argument("--all-types", action="store_true", help="Unreal json: also the classes uas skips by default "
                    "(SkeletalMesh, AnimMontage...), for gst analyze unreal_animation")
    ex.add_argument("--ue", help="Unreal: engine version for CUE4Parse, e.g. 4.27 or 5.3 (default: guessed)")
    ex.set_defaults(func=cmd_extract)

    tl = s.add_parser("to-legacy", parents=[parent], help="Unreal IoStore: retoc to-legacy into dumps/unreal/legacy/, then uas "
                      "(--types) into assets/unreal/iostore/<mode>/")
    tl.add_argument("--filter", action="append", default=[], help="path substring to convert (repeatable; default: every package)")
    tl.add_argument("--types", action=_AppendCsv, help="uas modes after the conversion (default: json)")
    tl.add_argument("--bytecode", action="store_true", help="json: include Blueprint (Kismet) bytecode")
    tl.add_argument("--all-types", action="store_true", help="json: also the classes uas skips by default (SkeletalMesh, AnimMontage...)")
    tl.add_argument("--ue", help="engine version, e.g. 5.3 (default: guessed)")
    tl.set_defaults(func=cmd_to_legacy)

    au = s.add_parser("audio", parents=[parent], help="any engine: audio banks/containers to WAV with vgmstream")
    au.add_argument("patterns", nargs="*", help="files or globs relative to the install (default: every known audio container)")
    au.add_argument("--list", action="store_true", help="only list containers and their streams")
    au.add_argument("--name", help="regex on stream names")
    au.add_argument("--limit", type=int, default=None, help="streams per run (default: all)")
    au.add_argument("--all", action="store_true", help="no limit")
    au.set_defaults(func=cmd_audio)

    pr = s.add_parser("project", parents=[parent], help="whole project (Unity: AssetRipper, scenes/prefabs as YAML; Godot: GDRE); large")
    pr.add_argument("--mode", choices=["project", "primary"], default="project",
                    help="project = Unity project with scenes and prefabs as YAML (default); primary = content only")
    pr.set_defaults(func=cmd_project)

    ix = s.add_parser("index", parents=[parent], help="rebuild assets/INDEX.md from the sidecars")
    ix.set_defaults(func=cmd_index)


class _AppendCsv(argparse.Action):
    """``--types a,b --types c`` -> "a,b,c" (repeatable; every value kept, in order, no duplicates)."""

    def __call__(self, parser, namespace, values, option_string=None):
        old = [t for t in (getattr(namespace, self.dest, None) or "").split(",") if t]
        new = [t.strip() for t in str(values).split(",") if t.strip()]
        setattr(namespace, self.dest, ",".join(dict.fromkeys(old + new)))


def _filters(p) -> None:
    p.add_argument("--types", action=_AppendCsv, help="comma-separated, repeatable. Unity types, e.g. Texture2D,Sprite,AudioClip,"
                   "TextAsset,Font,Mesh,MonoBehaviour; Unreal modes json,textures,models,audio,animations,raw")
    p.add_argument("--name", help="regex on object names (Godot: res:// path; Unreal: packed path)")
    p.add_argument("--container", help="regex on container paths (AssetBundles, Resources)")
    p.add_argument("--file", help="regex on the source data file (e.g. sharedassets0)")
    p.add_argument("--limit", type=int, help="maximum objects (list: shown; extract: exported)")


def _engine_game() -> tuple[Path, dict, Path, str]:
    """(workspace, game, install, backend) where backend is `unity`, `godot` or `unreal`."""
    from gst.assets import backend_for

    ws, game = workspace.current()
    backend = backend_for((game.get("engine") or {}).get("key", ""))
    if backend is None:
        raise GsError(f"gst assets list/extract handle Unity, Godot and Unreal 4/5 games in this version; {game.get('name')} is "
                      f"{(game.get('engine') or {}).get('label')}. Audio of any engine: gst assets audio")
    decision = policy.check(game, "extract")
    if not decision.allowed:
        raise GsError(decision.reason, 3)
    return ws, game, Path(game["install_dir"]), backend


def _unity_game() -> tuple[Path, dict, Path]:
    ws, game, install, backend = _engine_game()
    if backend != "unity":
        raise GsError("this option is Unity-only (Unity games in this version)")
    return ws, game, install


def _types(arg: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(t.strip() for t in arg.split(",") if t.strip()) if arg else default


def cmd_list(args) -> int:
    from gst.assets import unity

    ws, game, install, backend = _engine_game()
    if backend == "unreal":
        from gst.assets import unreal

        if args.name:
            paths, total = unreal.search(ws, install, game, args.name, limit=args.limit)
            full = ws / "assets" / "search-unreal.txt"  # the whole list on disk, a summary on screen
            from gst.common import write_text

            write_text(full, "\n".join(paths) + "\n")
            shown = paths if args.json or len(paths) <= 40 else paths[:40]
            more = f"\n... {len(paths) - len(shown)} more: full list in {full}" if len(paths) > len(shown) else ""
            emit({"total": total, "paths": paths, "file": str(full)}, args.json, ("\n".join(shown) + more) if paths else
                 f"no packed path matches {args.name!r} (regex, case-insensitive; paths look like FSD/Content/Folder/BP_Name.uasset)")
            return 0
        s = unreal.inventory(ws, install, game)
        workspace.journal(ws, f"gst assets list: {s['files']} Unreal files from {s['source']}")
        text = (f"{s['containers']} containers, {s['files']} files listed from {s['source']} ({s['seconds']} s); "
                f"Blueprints {s['blueprints']}\n" + ", ".join(f"{k or '-'} {v}" for k, v in s["types"].items())
                + "\nwritten: assets/INVENTORY.md, assets/inventory.jsonl")
        if s["encrypted"]:
            text += (f"\nencrypted: {', '.join(s['encrypted'][:6])}: contents stay closed (rule 4); study the structure from "
                     "the paths, the configs on disk and public documentation")
        else:
            text += f"\nnext: gst assets extract --name \"<regex>\" (JSON; --types textures|audio|models|raw; --bytecode for Blueprints)"
        emit(s, args.json, text)
        return 0
    if backend == "godot":
        from gst.assets import godot

        summary = godot.inventory(ws, install, game.get("name") or "")
        workspace.journal(ws, f"gst assets list: {summary['files']} files in the Godot pack ({summary['seconds']} s)")
        top = ", ".join(f"{t} {n}" for t, n in list(summary["types"].items())[:10])
        emit(summary, args.json, f"Godot {summary['godot_version']}: {summary['files']} files in {Path(summary['pack']).name} "
                                 f"({summary['seconds']} s)\n{top}\nwritten: assets/INVENTORY.md, assets/inventory.jsonl\n"
                                 "next: gst assets extract --types scene,texture --name <regex> --limit 50")
        return 0
    if any([args.types, args.name, args.container, args.file]):
        rows = unity.search(install, types=_types(args.types, ()), name=args.name, container=args.container,
                            file=args.file, limit=args.limit, ws=ws)
        shown = rows[:40]  # screen summary only; --json has every row
        text = "\n".join(f"{r['type']:<14} {r['name'] or '-':<48} {r['file']}#{r['path_id']}  "
                         f"{human_size(r['size'])}" + (f"  {r['container']}" if r["container"] else "")
                         for r in shown) or "no objects match"
        if len(rows) > len(shown):
            text += f"\n... {len(rows) - len(shown)} more (--json for all)"
        emit(rows, args.json, text)
        return 0
    summary = unity.inventory(ws, install, game.get("name") or "")
    workspace.journal(ws, f"gst assets list: {summary['objects']} Unity objects ({summary['seconds']} s)")
    top = ", ".join(f"{t} {n}" for t, n in list(summary["types"].items())[:10])
    emit(summary, args.json, f"Unity {summary['unity_version']}: {summary['objects']} objects in {summary['files']} files "
                             f"({summary['seconds']} s)\n{top}\nwritten: assets/INVENTORY.md, "
                             "assets/unity/_inventory/ (INDEX.json + one .jsonl.gz per file)\n"
                             "next: gst assets list --types Texture2D --name <regex>; gst assets extract --types Texture2D --name <regex>")
    return 0


def cmd_extract(args) -> int:
    from gst.assets import build_index, unity

    ws, game, install, backend = _engine_game()
    if backend == "unreal":
        from gst.assets import unreal

        if not args.name:
            raise GsError("Unreal paks hold tens of thousands of assets: pass --name <regex on the path> "
                          "(see assets/INVENTORY.md, e.g. --name '/Character/BP_')")
        modes = [m.strip().lower() for m in (args.types or "json").split(",") if m.strip()]  # every mode, not the first
        results = [unreal.extract(ws, install, game, name=args.name, mode=m, bytecode=args.bytecode, ue=args.ue,
                                  limit=None if args.all else args.limit, all_types=args.all_types) for m in modes]
        result = {**results[-1], "modes": {m: r for m, r in zip(modes, results)},
                  "matched": max(r["matched"] for r in results)}
        mode = ",".join(modes)
        if not result["matched"]:
            raise GsError(f"no packed asset matches {args.name!r}; find paths with gst assets list --name <regex> "
                          "(or assets/INVENTORY.md)")
        index = build_index(ws)
        workspace.journal(ws, f"gst assets extract (unreal {mode}): {result['matched']} assets matching {args.name!r}")
        emit({**result, "index": index}, args.json,
             f"exported {result['matched']} matching assets as {mode} into {result['out']} ({result['seconds']} s, {result.get('game')})"
             f"\nassets/INDEX.md: {index['files']} files in {index['folders']} folders")
        return 0
    if backend == "godot":
        from gst.assets import godot

        if args.container or args.file:
            raise GsError("--container/--file are Unity filters; for Godot use --name (regex on the res:// path)")
        result = godot.extract(ws, install, types=_types(args.types, godot.DEFAULT_TYPES), name=args.name,
                               limit=None if args.all else args.limit)
        index = build_index(ws)
        done = sum(result["exported"].values())
        workspace.journal(ws, f"gst assets extract (godot): {done} files ({', '.join(f'{k} {v}' for k, v in result['exported'].items())})")
        text = (f"recovered {done} of {result['matched']} matching files into assets/godot/extract/ ({result['seconds']} s): "
                + (", ".join(f"{k} {v}" for k, v in result["exported"].items()) or "-"))
        if result["limited"]:
            text += "\nstopped at the limit: narrow with --name or --types, raise --limit, or --all"
        emit({**result, "index": index}, args.json, text + f"\nassets/INDEX.md: {index['files']} files in {index['folders']} folders")
        return 0
    result = unity.extract(ws, install, types=_types(args.types, unity.DEFAULT_TYPES), name=args.name,
                           container=args.container, file=args.file,
                           limit=None if args.all else args.limit, thumbs=not args.no_thumbs)
    index = build_index(ws)
    done = sum(result["exported"].values())
    workspace.journal(ws, f"gst assets extract: {done} objects ({', '.join(f'{k} {v}' for k, v in result['exported'].items())})")
    text = (f"exported {done} of {result['matched']} matching objects into assets/unity/ ({result['seconds']} s): "
            + (", ".join(f"{k} {v}" for k, v in result["exported"].items()) or "-"))
    if result["limited"]:
        text += "\nstopped at the limit: narrow with --name/--container/--file, raise --limit, or --all"
    if result["failures"]:
        text += f"\n{result['failures']} failed, e.g. {result['failed'][0]}"
    text += f"\nassets/INDEX.md: {index['files']} files in {index['folders']} folders"
    emit({**result, "index": index}, args.json, text)
    return 0


def cmd_to_legacy(args) -> int:
    import hashlib

    from gst.assets import build_index
    from gst.assets import unreal

    ws, game, install, backend = _engine_game()
    if backend != "unreal":
        raise GsError("to-legacy is for Unreal IoStore containers (.utoc/.ucas)")
    filters = [f for f in args.filter if f.strip()]
    tag = hashlib.sha1("\n".join(sorted(filters)).encode("utf-8")).hexdigest()[:8] if filters else "all"
    modes = [m.strip().lower() for m in (args.types or "json").split(",") if m.strip()]
    result = unreal.to_legacy(ws, install, game, filters=filters or None, modes=modes, bytecode=args.bytecode, ue=args.ue,
                              name=f"legacy-{tag}", all_types=args.all_types)
    index = build_index(ws)
    conv = result["legacy"]
    workspace.journal(ws, f"gst assets to-legacy: {conv['extracted']} packages ({', '.join(filters) or 'all'}), "
                          f"{result['exported']} files exported")
    emit({**result, "index": index}, args.json,
         f"retoc to-legacy: {conv['extracted']} packages ({conv['failed']} failed) into {', '.join(conv['paks']) or '-'}\n"
         + (f"left out (encrypted without a usable published key): {', '.join(b['name'] for b in result['blocked'])}\n"
            if result.get("blocked") else "")
         + "\n".join(f"uas {m}: {e['exported']} files in {e['out']}" for m, e in result["exports"].items())
         + f"\n({result['seconds']} s) assets/INDEX.md: {index['files']} files in {index['folders']} folders")
    return 0


def cmd_audio(args) -> int:
    from gst.assets import audio, build_index

    ws, game = workspace.current()
    install = Path(game["install_dir"])
    files = audio.find(install, args.patterns or None)
    if not files:
        raise GsError("no audio containers found (pass files or globs relative to the install)")
    if args.list:
        rows = []
        for f in files:
            listed = audio.streams(f)
            rows.append({"file": str(f.relative_to(install)) if f.is_relative_to(install) else str(f), "streams": len(listed),
                         "minutes": round(sum(s["seconds"] for s in listed) / 60, 1),
                         "examples": [s["name"] for s in listed[:4]]})
        emit(rows, args.json, "\n".join(f"{r['file']:<60} {r['streams']:>6} streams {r['minutes']:>7} min  "
                                       f"e.g. {', '.join(r['examples'])}" for r in rows))
        return 0
    result = audio.convert(ws, files, name=args.name, limit=None if args.all else args.limit)
    index = build_index(ws)
    done = sum(result["exported"].values())
    workspace.journal(ws, f"gst assets audio: {done} streams from {len(result['exported'])} containers")
    text = f"converted {done} of {result['matched']} matching streams into assets/audio/ ({result['seconds']} s)"
    if result["limited"]:
        text += "\nstopped at the limit: narrow with --name, raise --limit, or --all"
    if result["failures"]:
        text += f"\n{result['failures']} failed, e.g. {result['failed'][0]}"
    emit({**result, "index": index}, args.json, text + f"\nassets/INDEX.md: {index['files']} files in {index['folders']} folders")
    return 0


def cmd_project(args) -> int:
    import sys

    from gst.assets import assetripper, build_index

    ws, _game, install, backend = _engine_game()
    if backend == "unreal":
        raise GsError("no whole-project export for Unreal (a single pak holds 100k+ files): use gst assets extract --name <regex>")
    if backend == "godot":
        from gst.assets import godot

        sys.stderr.write(f"gst: GDRE recovery of the whole project from {install} (minutes)...\n")
        result = godot.project(ws, install)
        index = build_index(ws)
        workspace.journal(ws, f"gst assets project (godot): {result['files']} files, {human_size(result['bytes'])}")
        emit({**result, "index": index}, args.json,
             f"Godot project: {result['files']} files, {human_size(result['bytes'])} in {result['out']} ({result['seconds']} s); "
             f"C# sources come from gst decompile\nassets/INDEX.md: {index['files']} files in {index['folders']} folders")
        return 0
    sys.stderr.write(f"gst: AssetRipper {args.mode} export of {install} (minutes; output can be GBs)...\n")
    result = assetripper.export(ws, install, mode=args.mode)
    index = build_index(ws)
    workspace.journal(ws, f"gst assets project --mode {args.mode}: {result['files']} files, {human_size(result['bytes'])}")
    emit({**result, "index": index}, args.json,
         f"AssetRipper {args.mode}: {result['files']} files, {human_size(result['bytes'])} in {result['out']} "
         f"({result['seconds']} s)\nassets/INDEX.md: {index['files']} files in {index['folders']} folders")
    return 0


def cmd_index(args) -> int:
    from gst.assets import build_index

    ws, _game = workspace.current()
    summary = build_index(ws)
    emit(summary, args.json, f"assets/INDEX.md: {summary['files']} files in {summary['folders']} folders")
    return 0
