"""gst trace / gst sandbox: observe the running game - its processes, read-only value scans, who writes an
address (hardware watchpoint), what an action changes on disk. Only for games whose verdict allows runtime
work (offline-ok), only on the game's own processes, never writing to its memory.
"""

from __future__ import annotations

import time
from pathlib import Path

from gst import workspace
from gst.common import GsError, emit, human_size, write_json


def register(sub, parent) -> None:
    p = sub.add_parser("trace", parents=[parent], help="runtime: processes, value scans, who writes an address, disk snapshots",
                       description=__doc__)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")

    ps = s.add_parser("ps", parents=[parent], help="running processes of the studied game")
    ps.set_defaults(func=cmd_ps)

    sc = s.add_parser("scan", parents=[parent], help="first scan: addresses holding a value (read-only)")
    sc.add_argument("value", help="the value now shown in the game, e.g. 100 or 37.5")
    sc.add_argument("--type", default="int32", choices=["int8", "uint8", "int16", "int32", "uint32", "int64", "float", "double"])
    sc.add_argument("--pid", type=int)
    sc.set_defaults(func=cmd_scan)

    nx = s.add_parser("next", parents=[parent], help="next scan: keep the candidates that now hold a value or changed")
    nx.add_argument("value", nargs="?", help="the new value (or use one of the flags)")
    for flag in ("changed", "unchanged", "increased", "decreased"):
        nx.add_argument(f"--{flag}", dest="mode", action="store_const", const=flag)
    nx.set_defaults(func=cmd_next)

    w = s.add_parser("watch", parents=[parent], help="who writes an address: hardware watchpoint, hits named by function")
    w.add_argument("address", help="0x... address, `scan` (the last candidate left) or scan:N")
    w.add_argument("--size", type=int, default=None, help="bytes watched: 1, 2, 4 or 8 (default: the scan type)")
    w.add_argument("--hits", type=int, default=10, help="stop after this many writes (default 10)")
    w.add_argument("--seconds", type=float, default=30, help="stop after this long (default 30)")
    w.add_argument("--pid", type=int)
    w.set_defaults(func=cmd_watch)

    sn = s.add_parser("snapshot", parents=[parent], help="manifest + copies of save/config folders (before an action)")
    sn.add_argument("name")
    sn.add_argument("--path", action="append", help="folder to include (repeatable; default: user-data folders from recon)")
    sn.add_argument("--force", action="store_true")
    sn.set_defaults(func=cmd_snapshot)

    df = s.add_parser("diff", parents=[parent], help="what changed between two snapshots (files and byte ranges)")
    df.add_argument("a")
    df.add_argument("b")
    df.set_defaults(func=cmd_diff)

    sb = sub.add_parser("sandbox", parents=[parent], help="copy of the install for tools that inject (offline only)",
                        description="The sandbox (workspace/sandbox) is a copy of the install: tools that inject or drop "
                                    "files next to the game run only there (guardrails rule 5).")
    sb.add_argument("action", choices=["create", "remove", "status"])
    sb.set_defaults(func=cmd_sandbox)


def _traces(ws: Path) -> Path:
    return ws / "traces"


def cmd_ps(args) -> int:
    from gst import runtime

    _ws, game = workspace.current()
    rows = runtime.game_processes(game)
    emit(rows, args.json, "\n".join(f"{p['pid']:>7}  {p['name']:<32} {p['path']}" for p in rows)
         or f"{game.get('name')} is not running")
    return 0


def cmd_scan(args) -> int:
    from gst import runtime
    from gst.runtime import memory, win

    ws, game = workspace.current()
    proc = runtime.target(game, args.pid)
    handle = memory.open_process(proc["pid"])
    try:
        t0 = time.time()
        hits, scanned = memory.first_scan(handle, args.value, args.type)
        size = memory.TYPES[args.type][1]
        values = {a: memory.read(handle, a, size) or b"" for a in list(hits)[:200_000]}
    finally:
        win.CloseHandle(handle)
    memory.save_state(_traces(ws), proc["pid"], args.type, hits, values, [f"scan {args.value} ({args.type}): {len(hits)}"])
    workspace.journal(ws, f"gst trace scan {args.value} ({args.type}) in pid {proc['pid']}: {len(hits)} addresses")
    emit({"pid": proc["pid"], "candidates": len(hits), "scanned_bytes": scanned}, args.json,
         f"{len(hits)} addresses hold {args.value} ({args.type}); {human_size(scanned)} read in {time.time() - t0:.1f} s\n"
         "next: change the value in the game, then gst trace next <new value> (or --decreased/--changed)")
    return 0


def _show(handle, addresses, kind: str, ws: Path, pid: int) -> list[dict]:
    from gst import runtime
    from gst.runtime import memory

    sym = runtime.Symbolizer(ws, runtime.modules(handle))
    rows = []
    for address in list(addresses)[:20]:
        raw = memory.read(handle, address, memory.TYPES[kind][1])
        where = sym.describe(address)
        rows.append({"address": hex(address), "value": memory.decode(raw, kind) if raw else None,
                     "where": where.get("where") if "module" in where else "heap/stack"})
    return rows


def cmd_next(args) -> int:
    from gst import runtime
    from gst.runtime import memory, win

    ws, game = workspace.current()
    meta, candidates, previous = memory.load_state(_traces(ws))
    proc = runtime.target(game, meta["pid"])
    handle = memory.open_process(proc["pid"])
    try:
        kept, values = memory.next_scan(handle, candidates, meta["type"], value=args.value, mode=args.mode or "",
                                        previous=previous)
        rows = _show(handle, kept, meta["type"], ws, proc["pid"])
    finally:
        win.CloseHandle(handle)
    step = f"next {args.value if args.value is not None else '--' + str(args.mode)}: {len(kept)}"
    memory.save_state(_traces(ws), proc["pid"], meta["type"], kept, values, meta.get("history", []) + [step])
    workspace.journal(ws, f"gst trace {step} candidates")
    text = f"{len(kept)} of {len(candidates)} candidates left" + "".join(
        f"\n  {r['address']}  = {r['value']}  ({r['where']})" for r in rows)
    if len(kept) == 1:
        text += "\nnext: gst trace watch scan (who writes it)"
    elif kept:
        text += "\nnext: change the value again and gst trace next ..., until one or a few are left"
    emit({"left": len(kept), "candidates": rows}, args.json, text)
    return 0


def _address(ws: Path, raw: str) -> tuple[int, str | None]:
    from gst.runtime import memory

    if raw.startswith("scan"):
        meta, candidates, _values = memory.load_state(_traces(ws))
        index = int(raw.split(":", 1)[1]) if ":" in raw else 0
        if raw == "scan" and len(candidates) != 1:
            raise GsError(f"{len(candidates)} candidates left: narrow with gst trace next, or pick one with scan:N")
        if index >= len(candidates):
            raise GsError(f"only {len(candidates)} candidates")
        return candidates[index], meta["type"]
    try:
        return int(raw, 16), None
    except ValueError as exc:
        raise GsError("address: 0x..., scan or scan:N") from exc


def cmd_watch(args) -> int:
    from gst import runtime
    from gst.runtime import memory, watch, win

    ws, game = workspace.current()
    address, kind = _address(ws, args.address)
    size = args.size or (memory.TYPES[kind][1] if kind else 4)
    proc = runtime.target(game, args.pid)
    result = watch.watch(proc["pid"], address, size, max_hits=args.hits, seconds=args.seconds)
    handle = memory.open_process(proc["pid"]) if result["stopped_by"] != "process exited" else None
    try:
        sym = runtime.Symbolizer(ws, runtime.modules(handle) if handle else [])
        grouped: dict[int, dict] = {}
        for hit in result["hits"]:
            g = grouped.setdefault(hit["rip"], {**sym.describe(hit["rip"], inside_previous=True), "count": 0, "values": []})
            g["count"] += 1
            if hit["bytes"] and kind:
                g["values"].append(memory.decode(bytes.fromhex(hit["bytes"]), kind))
            elif hit["bytes"]:
                g["values"].append(hit["bytes"])
    finally:
        if handle:
            win.CloseHandle(handle)
    writers = list(grouped.values())
    out = {**result, "writers": writers, "process": proc["name"]}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    write_json(_traces(ws) / f"watch-{stamp}.json", out)
    names = ", ".join(w.get("function") or w.get("where", "?") for w in writers) or "no writes"
    workspace.journal(ws, f"gst trace watch {hex(address)} ({size} B): {len(result['hits'])} writes by {names}")
    lines = [f"{len(result['hits'])} writes to {hex(address)} in {result['seconds']} s (stopped: {result['stopped_by']})"]
    for w in writers:
        what = w.get("function") or w.get("where")
        lines.append(f"  {w['count']}x  {what}  [{w.get('where', w['address'])}]"
                     + (f"  -> {w['file']}:{w['line']}" if w.get("file") else "")
                     + (f"  values {w['values'][:8]}" if w["values"] else ""))
        if w.get("hint"):
            lines.append(f"       {w['hint']}")
    lines.append(f"written: traces/watch-{stamp}.json")
    emit(out, args.json, "\n".join(lines))
    return 0


def cmd_snapshot(args) -> int:
    from gst.runtime import snapshot

    ws, _game = workspace.current()
    paths = [Path(p) for p in args.path] if args.path else snapshot.default_paths(ws)
    result = snapshot.snapshot(ws, args.name, paths, force=args.force)
    workspace.journal(ws, f"gst trace snapshot {args.name}: {result['files']} files")
    emit(result, args.json, f"snapshot {args.name}: {result['files']} files ({human_size(result['copied_bytes'])} copied) from "
                            + ", ".join(result["roots"]) + "\nnext: act in the game, take another snapshot, gst trace diff")
    return 0


def cmd_diff(args) -> int:
    from gst.runtime import snapshot

    ws, _game = workspace.current()
    d = snapshot.diff(ws, args.a, args.b)
    lines = [f"{len(d['added'])} added, {len(d['removed'])} removed, {len(d['changed'])} changed"]
    lines += [f"  + {f}" for f in d["added"][:20]] + [f"  - {f}" for f in d["removed"][:20]]
    for c in d["changed"][:20]:
        lines.append(f"  ~ {c['file']}  {c['size_a']} -> {c['size_b']} bytes")
        for r in (c.get("ranges") or [])[:3]:
            lines.append(f"      {r['start']:#x}-{r['end']:#x}  {r['a']}  ->  {r['b']}")
    emit(d, args.json, "\n".join(lines))
    return 0


def cmd_sandbox(args) -> int:
    from gst.runtime import sandbox

    ws, game = workspace.current()
    if args.action == "create":
        result = sandbox.create(ws, game)
        workspace.journal(ws, f"gst sandbox create: {result['files']} files")
        text = (f"sandbox: {result['sandbox']} ({result['files']} files, {human_size(result['bytes'])}, {result['seconds']} s); "
                "tools that inject run only there, offline")
    elif args.action == "remove":
        result = sandbox.remove(ws)
        text = "sandbox removed" if result["removed"] else "no sandbox"
    else:
        result = sandbox.status(ws)
        text = (f"sandbox: {result['sandbox']} ({result['files']} files, {human_size(result['bytes'])})" if result["exists"]
                else "no sandbox (gst sandbox create)")
    emit(result, args.json, text)
    return 0
