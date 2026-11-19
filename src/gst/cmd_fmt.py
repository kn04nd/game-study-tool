"""gst fmt: reverse engineering of file formats - identify, look (bounded), compare, write a reader, prove it.

Paths are taken as given; inside a study workspace, a relative path that does not exist there is also
looked up in the game's install folder (read-only).
"""

from __future__ import annotations

import glob
from pathlib import Path

from gst import workspace
from gst.common import GsError, emit, human_size


def register(sub, parent) -> None:
    p = sub.add_parser("fmt", parents=[parent], help="file formats: identify, hexdump, entropy, strings, diff, readers",
                       description=__doc__)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")

    i = s.add_parser("id", parents=[parent], help="identify files by signature, with entropy")
    i.add_argument("paths", nargs="+", help="files or globs")
    i.set_defaults(func=cmd_id)

    h = s.add_parser("hex", parents=[parent], help="hexdump of a range (at most 512 bytes)")
    h.add_argument("path")
    h.add_argument("--offset", type=lambda v: int(v, 0), default=0, help="start (0x.. accepted; negative = from the end)")
    h.add_argument("--length", type=lambda v: int(v, 0), default=256, help="bytes to show (max 512)")
    h.set_defaults(func=cmd_hex)

    e = s.add_parser("entropy", parents=[parent], help="entropy per block: text, structure, compressed/encrypted regions")
    e.add_argument("path")
    e.add_argument("--block", type=int, default=4096)
    e.set_defaults(func=cmd_entropy)

    t = s.add_parser("strings", parents=[parent], help="ASCII and UTF-16 strings with offsets")
    t.add_argument("path")
    t.add_argument("--min", type=int, default=5, dest="minimum")
    t.add_argument("--limit", type=int, default=100)
    t.add_argument("--grep", help="regex filter on the strings")
    t.set_defaults(func=cmd_strings)

    c = s.add_parser("scan", parents=[parent], help="survey a folder of unknown files: extensions, kinds, sizes, entropy")
    c.add_argument("folder")
    c.set_defaults(func=cmd_scan)

    d = s.add_parser("diff", parents=[parent], help="byte ranges that differ between two versions of a file")
    d.add_argument("a")
    d.add_argument("b")
    d.add_argument("--limit", type=int, default=20)
    d.set_defaults(func=cmd_diff)

    pt = s.add_parser("pattern", parents=[parent], help="apply an ImHex pattern (.hexpat) to files: decoded tree as JSON")
    pt.add_argument("pattern", help="a .hexpat file (e.g. notes/formats/<name>.hexpat)")
    pt.add_argument("paths", nargs="+", help="files or globs")
    pt.add_argument("--lines", type=int, default=60, help="JSON lines printed for the first file (default 60)")
    pt.set_defaults(func=cmd_pattern)

    n = s.add_parser("new-reader", parents=[parent], help="start notes/formats/<name>.py (dissect.cstruct) and <name>.md")
    n.add_argument("name")
    n.add_argument("--sample", help="a sample file: fills the magic, size and first bytes")
    n.set_defaults(func=cmd_new_reader)

    r = s.add_parser("roundtrip", parents=[parent], help="parse and rewrite samples with a reader: bytes must match")
    r.add_argument("reader", help="notes/formats/<name>.py")
    r.add_argument("paths", nargs="+", help="sample files or globs")
    r.set_defaults(func=cmd_roundtrip)


def _resolve(raw: str, *, many: bool = False) -> list[Path]:
    """Files for an argument: as given, then (inside a workspace) under the game install."""
    bases = [Path.cwd()]
    ws, game = workspace.current(required=False)
    if ws is not None and game and game.get("install_dir"):
        bases.append(Path(game["install_dir"]))
    for base in bases:
        pattern = str(Path(raw) if Path(raw).is_absolute() else base / raw)
        hits = [Path(h) for h in sorted(glob.glob(pattern, recursive=True)) if Path(h).is_file()] if any(
            ch in raw for ch in "*?[") else ([Path(pattern)] if Path(pattern).is_file() else [])
        if hits:
            return hits if many else hits[:1]
    raise GsError(f"no file matches {raw}")


def _one(raw: str) -> Path:
    return _resolve(raw)[0]


def cmd_id(args) -> int:
    from gst import formats

    rows = [formats.identify(p) for raw in args.paths for p in _resolve(raw, many=True)]
    emit(rows, args.json, "\n".join(f"{r['kind']:<16} {human_size(r['size']):>9}  H={r['entropy']:<4} {r['path']}\n"
                                    f"{'':<16} {r['detail']}; first bytes {r['magic']}" for r in rows))
    return 0


def cmd_hex(args) -> int:
    from gst import formats

    path = _one(args.path)
    data = formats.read_range(path, args.offset, args.length)
    start = args.offset if args.offset >= 0 else max(0, path.stat().st_size + args.offset)
    emit({"path": str(path), "offset": start, "length": len(data), "hex": data.hex()}, args.json,
         formats.hexdump(data, start) + ("\n(max 512 bytes per call)" if args.length > formats.MAX_HEX else ""))
    return 0


def cmd_entropy(args) -> int:
    from gst import formats

    m = formats.entropy_map(_one(args.path), block=args.block)
    lines = [f"{m['path']}: {human_size(m['size'])}, mean entropy {m['overall']} bits/byte (blocks of {m['block']} B)",
             f"[{m['spark']}]"]
    lines += [f"  {r['start']:#010x}-{r['end']:#010x}  {r['class']:<26} max {r['max']}" for r in m["regions"][:40]]
    if len(m["regions"]) > 40:
        lines.append(f"  ... {len(m['regions']) - 40} more regions (--json for all)")
    emit(m, args.json, "\n".join(lines))
    return 0


def cmd_strings(args) -> int:
    import re

    from gst import formats

    rows = formats.strings(_one(args.path), minimum=args.minimum, limit=10_000 if args.grep else args.limit)
    if args.grep:
        rx = re.compile(args.grep, re.I)
        rows = [r for r in rows if rx.search(r["text"])][:args.limit]
    emit(rows, args.json, "\n".join(f"{r['offset']:#010x} {'u16' if r['encoding'] != 'ascii' else '   '} {r['text']}"
                                    for r in rows) or "no strings")
    return 0


def cmd_scan(args) -> int:
    from gst import formats

    folder = Path(args.folder)
    if not folder.is_dir():
        ws, game = workspace.current(required=False)
        folder = Path(game["install_dir"]) / args.folder if game else folder
    if not folder.is_dir():
        raise GsError(f"no folder {args.folder}")
    result = formats.scan(folder)
    lines = [f"{result['files']} files under {result['root']}" + (" (stopped at the limit)" if result["truncated"] else ""),
             f"{'ext':<12} {'files':>7} {'size':>10}  H    kinds"]
    lines += [f"{g['ext']:<12} {g['files']:>7} {human_size(g['bytes']):>10}  {g['entropy']:<4} "
              + ", ".join(f"{k} {n}" for k, n in g["kinds"].items()) + f"   e.g. {g['example']}" for g in result["groups"][:40]]
    emit(result, args.json, "\n".join(lines))
    return 0


def cmd_diff(args) -> int:
    from gst import formats

    d = formats.diff(_one(args.a), _one(args.b), limit=args.limit)
    if d["identical"]:
        text = "identical"
    else:
        text = (f"sizes {d['size_a']} vs {d['size_b']}; common prefix {d['common_prefix']} bytes, "
                f"common suffix {d['common_suffix']} bytes")
        if d["same_size"]:
            text += "\n" + "\n".join(f"  {r['start']:#010x}-{r['end']:#010x}  a: {r['a']}\n{'':>27}b: {r['b']}" for r in d["ranges"])
            if d["more_ranges"]:
                text += f"\n  ... {d['more_ranges']} more ranges"
        else:
            text += "\n  different sizes: bytes were inserted or removed after the common prefix"
    emit(d, args.json, text)
    return 0


def cmd_pattern(args) -> int:
    import tempfile

    from gst import formats

    pattern = _one(args.pattern)
    ws, _game = workspace.current(required=False)
    out_dir = ws / "dumps" / "formats" / pattern.stem if ws is not None else Path(tempfile.mkdtemp(prefix="gs-pattern-"))
    results = [formats.run_pattern(pattern, path, out_dir / f"{path.name}.json")
               for raw in args.paths for path in _resolve(raw, many=True)]
    first = Path(results[0]["json"]).read_text(encoding="utf-8", errors="replace").splitlines()
    text = "\n".join(first[:args.lines]) + (f"\n... {len(first) - args.lines} more lines" if len(first) > args.lines else "")
    text += "\n" + "\n".join(f"written: {r['json']} ({human_size(r['bytes'])})" for r in results)
    emit(results, args.json, text)
    return 0


def cmd_new_reader(args) -> int:
    from gst.formats import readers

    ws, _game = workspace.current()
    sample = _one(args.sample) if args.sample else None
    out = readers.new_reader(ws, args.name, sample)
    workspace.journal(ws, f"gst fmt new-reader {args.name}" + (f" (sample {sample.name})" if sample else ""))
    emit(out, args.json, f"reader: {out['reader']}\nnotes:  {out['note']}\n"
                         f"next: map the layout in the reader, then gst fmt roundtrip notes/formats/{args.name}.py <samples>")
    return 0


def cmd_roundtrip(args) -> int:
    from gst.formats import readers

    reader = Path(args.reader)
    ws, _game = workspace.current(required=False)
    if not reader.is_file() and ws is not None and (ws / args.reader).is_file():
        reader = ws / args.reader
    files = [p for raw in args.paths for p in _resolve(raw, many=True)]
    result = readers.roundtrip(reader, files)
    lines = []
    for r in result["results"]:
        status = "identical" if r.get("ok") else (f"ERROR {r['error']}" if "error" in r else
                                                  f"DIFFERS at {r['first_difference']:#x} ({r['rewritten_size']} vs {r['size']} bytes)")
        extra = "  " + ", ".join(f"{k}={v}" for k, v in (r.get("describe") or {}).items()) if r.get("describe") else ""
        lines.append(f"{status:<40} {r['sha256'][:12]}  {Path(r['file']).name}{extra}")
    lines.append(f"{result['identical']} of {result['files']} files round-trip byte for byte")
    if ws is not None:
        workspace.journal(ws, f"gst fmt roundtrip {reader.name}: {result['identical']}/{result['files']} identical")
    emit(result, args.json, "\n".join(lines))
    return 0 if result["identical"] == result["files"] else 1
