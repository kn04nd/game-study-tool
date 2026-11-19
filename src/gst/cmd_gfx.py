"""gst gfx: how a frame is drawn - capture one with RenderDoc (from the sandbox copy, offline) and summarize
it into frame.md: passes, render targets, shaders with their constant buffers and disassembly."""

from __future__ import annotations

from pathlib import Path

from gst import workspace
from gst.common import GsError, emit, human_size


def register(sub, parent) -> None:
    p = sub.add_parser("gfx", parents=[parent], help="graphics: RenderDoc frame capture (sandbox) and frame.md summary",
                       description=__doc__)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")

    c = s.add_parser("capture", parents=[parent], help="run the game from the sandbox under RenderDoc and capture a frame")
    c.add_argument("--after", type=float, default=5.0, help="seconds after the graphics device starts (default 5)")
    c.add_argument("--exe", help="executable in the sandbox (default: the game's)")
    c.add_argument("--args", default="", help="arguments for the game, e.g. '-windowed'")
    c.add_argument("--manual", action="store_true", help="you press F12/PrtScr in the game; ends when the game exits")
    c.add_argument("--no-export", action="store_true", help="skip the frame.md summary")
    c.set_defaults(func=cmd_capture)

    e = s.add_parser("export", parents=[parent], help="summarize a capture: frame.md, targets/*.png, shaders/*.txt")
    e.add_argument("capture", nargs="?", help="a .rdc (default: the latest in traces/gfx)")
    e.add_argument("--images", type=int, default=16, help="render targets saved as PNG (default 16)")
    e.set_defaults(func=cmd_export)

    ls = s.add_parser("list", parents=[parent], help="captures in traces/gfx")
    ls.set_defaults(func=cmd_list)


def _latest(ws: Path) -> Path:
    from gst import gfx

    found = gfx.captures(ws)
    if not found:
        raise GsError("no capture yet: gst gfx capture")
    return Path(found[-1]["capture"])


def _export(ws: Path, capture: Path, images: int, as_json: bool) -> int:
    from gst import gfx

    result = gfx.export(ws, capture, max_images=images)
    workspace.journal(ws, f"gst gfx export {capture.name}: {result['passes']} passes, {result['draws']} draws, "
                          f"{result['shaders']} shaders")
    md = Path(result["frame_md"])
    emit(result, as_json, md.read_text(encoding="utf-8") + f"\nwritten: {md}")
    return 0


def cmd_capture(args) -> int:
    from gst import gfx

    ws, game = workspace.current()
    result = gfx.capture(ws, game, after=args.after, exe=args.exe, args=args.args, manual=args.manual)
    captures = [Path(c) for c in result["captures"]]
    workspace.journal(ws, f"gst gfx capture: {', '.join(c.name for c in captures) or 'no capture'}")
    if not captures:
        emit(result, args.json, "no capture (in --manual mode press F12 or PrtScr in the game before closing it)")
        return 1
    text = "\n".join(f"captured: {c} ({human_size(c.stat().st_size)})" for c in captures)
    if result.get("api"):
        text += f"\nAPI: {result['api']}; the game (pid {result['pid']}) was {'stopped' if result['stopped'] else 'left running'}"
    if args.no_export:
        emit(result, args.json, text)
        return 0
    if not args.json:
        print(text)
    return _export(ws, captures[-1], 16, args.json)


def cmd_export(args) -> int:
    ws, _game = workspace.current()
    capture = Path(args.capture) if args.capture else _latest(ws)
    if not capture.is_absolute():
        capture = (ws / capture) if (ws / capture).exists() else capture
    return _export(ws, capture, args.images, args.json)


def cmd_list(args) -> int:
    from gst import gfx

    ws, _game = workspace.current()
    rows = gfx.captures(ws)
    emit(rows, args.json, "\n".join(f"{r['size_h']:>9}  {'frame.md' if r['exported'] else '        '}  {r['capture']}"
                                    for r in rows) or "no captures (gst gfx capture)")
    return 0
