"""gst maps: the game's maps and levels - find the level files it ships (install and asset inventories)
and render raw grids found inside them (heightmaps, tile/collision layers) as PNG. The format and the
load flow are study work (study-maps skill): gst fmt for the container, the code for who reads each part."""

from __future__ import annotations

from pathlib import Path

from gst import policy, workspace
from gst.common import GsError, emit


def register(sub, parent) -> None:
    p = sub.add_parser("maps", parents=[parent], help="maps and levels: find level files, render heightmaps/grids as PNG",
                       description=__doc__)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")

    ls = s.add_parser("list", parents=[parent], help="level files in the install and in assets/**/inventory*.jsonl -> dumps/maps/levels.json")
    ls.set_defaults(func=cmd_list)

    ra = s.add_parser("raster", parents=[parent], help="raw grid (heightmap, tiles, collision) -> assets/maps/<name>.png + sidecar")
    ra.add_argument("file", help="file inside the workspace (e.g. a decompressed map in dumps/) or relative to the install")
    ra.add_argument("--width", type=int, required=True)
    ra.add_argument("--height", type=int, required=True)
    ra.add_argument("--offset", default="0", help="byte offset of the first value (0x.. accepted)")
    ra.add_argument("--dtype", default="u8", help="u8, i8, u16le, u16be, i16le, i16be, u32le, f32le, f32be (default u8)")
    ra.add_argument("--flip-y", action="store_true", help="flip vertically (row 0 is the south edge in many engines)")
    ra.add_argument("--name", help="output name (default <file>_<offset>)")
    ra.set_defaults(func=cmd_raster)


def cmd_list(args) -> int:
    from gst.assets import levels

    ws, game = workspace.current()
    install = Path(game["install_dir"]) if game.get("install_dir") else None
    s = levels.find(ws, install)
    workspace.journal(ws, f"gst maps list: {s['levels']} level files ({', '.join(f'{k} {v}' for k, v in list(s['by_kind'].items())[:4])})")
    if s["levels"]:
        text = (f"{s['levels']} level files ({s['seconds']} s)\n" + "\n".join(f"  {k}: {v}" for k, v in s["by_kind"].items())
                + "\nfolders: " + ", ".join(f"{k} ({v})" for k, v in list(s["folders"].items())[:6])
                + f"\nwritten: {s['out']}\nnext: gst fmt id <one map> to see the container, then the study-maps skill")
    else:
        text = ("no level files by name: levels may live inside archives (list them with gst assets list or a container "
                "reader, then run gst maps list again) or inside data the code builds; look for the loader in the code")
    emit(s, args.json, text)
    return 0


def cmd_raster(args) -> int:
    from gst.assets import levels

    ws, game = workspace.current()
    src = Path(args.file)
    if not src.is_absolute():
        src = ws / args.file if (ws / args.file).exists() else Path(game.get("install_dir") or ".") / args.file
    if not src.is_file():
        raise GsError(f"no such file: {args.file} (looked in the workspace and the install)")
    decision = policy.check(game, "extract")
    if not decision.allowed and not src.resolve().is_relative_to(ws.resolve()):
        raise GsError(decision.reason, 3)
    r = levels.raster(ws, src, width=args.width, height=args.height, offset=int(args.offset, 0), dtype=args.dtype,
                      flip_y=args.flip_y, name=args.name)
    workspace.journal(ws, f"gst maps raster {Path(args.file).name} @{args.offset} {args.width}x{args.height} {args.dtype} -> {r['out']}")
    emit(r, args.json, f"{r['out']}: {args.width}x{args.height} {args.dtype}, values {r['min']}..{r['max']} -> 0..255"
                       "\nlook at it (Read the PNG): terrain should look like terrain; if it looks like noise, the width, "
                       "offset or dtype is wrong")
    return 0
