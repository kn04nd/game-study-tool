"""gst index / gst q: build and query the code index of the current workspace (decomp/ -> index/code.db)."""

from __future__ import annotations

from gst import workspace
from gst.common import emit

ACTIONS = {
    "def": "definitions of a name, Type.Member or Ns.Type.Member",
    "sym": "fuzzy search over qualified names",
    "refs": "where a name is called, constructed or referenced",
    "callers": "who calls a method (or constructs a type)",
    "callees": "what a method calls, with candidate definitions",
    "show": "source of a symbol or of the symbol around path:line",
    "outline": "members of a type, or symbols of a file",
    "hier": "bases and derived types of a type",
    "strings": "string literals containing a text",
    "grep": "regex over decomp/ with ripgrep (enclosing symbol per hit)",
    "stats": "what the index holds",
}


def register(sub, parent) -> None:
    p = sub.add_parser("index", parents=[parent], help="build/update the code index of decomp/ (gst q reads it)",
                       description=__doc__)
    p.add_argument("--rebuild", action="store_true", help="drop the index and parse everything again")
    p.add_argument("--jobs", type=int, help="parser processes (default: CPUs - 1, max 8)")
    p.set_defaults(func=cmd_index)

    q = sub.add_parser("q", parents=[parent], help="query the code index (def, refs, callers, show, ...)",
                       description="Query the code index. Actions: " + "; ".join(f"{k} = {v}" for k, v in ACTIONS.items()))
    q.add_argument("action", choices=list(ACTIONS), metavar="action", help=" | ".join(ACTIONS))
    q.add_argument("query", nargs="?", default="", help="name, Type.Member, path:line, text or regex")
    q.add_argument("--kind", help="filter by symbol kind (class, method, field, property, ...)")
    q.add_argument("--limit", type=int, default=None, help="rows (default: all)")
    q.add_argument("--lines", type=int, default=None, help="show: maximum lines printed (default: the whole symbol)")
    q.add_argument("--context", type=int, default=0, help="show: extra lines around the symbol")
    q.add_argument("--glob", help="grep: file glob, e.g. '*.cs'")
    q.add_argument("-i", "--ignore-case", action="store_true", help="grep: case-insensitive")
    q.set_defaults(func=cmd_q)


def cmd_index(args) -> int:
    import sys

    from gst import index

    ws, _game = workspace.current()
    progress = (lambda done, total: sys.stderr.write(f"gst: indexed {done}/{total} files\n")) if not args.json else None
    summary = index.build(ws, rebuild=args.rebuild, jobs=args.jobs, progress=progress)
    workspace.journal(ws, f"gst index: {summary['files']} files, {summary['symbols']} symbols")
    mods = ", ".join(f"{m['module']} {m['files']}" for m in summary["modules"][:6])
    text = (f"index: {summary['files']} files ({summary['lines']} lines), {summary['symbols']} symbols, "
            f"{summary['refs']} refs, {summary['strings']} strings; parsed {summary['parsed']}, removed {summary['removed']} "
            f"in {summary['seconds']} s\nmodules: {mods or '-'}")
    if summary["failed"]:
        text += f"\nfailed: {summary['failed']} files, e.g. {summary['failures'][0]}"
    if summary["parse_errors"]:
        text += f"\nfiles with syntax errors (still indexed): {summary['parse_errors']}"
    emit(summary, args.json, text)
    return 0


def _loc(d: dict) -> str:
    return f"{d['path']}:{d['line']}"


def cmd_q(args) -> int:
    from gst import index
    from gst.common import GsError
    from gst.index import query

    ws, _game = workspace.current()
    action, q = args.action, args.query
    if action != "stats" and not q:
        raise GsError(f"gst q {action} needs a query")
    con = index.connect(ws) if action != "grep" or index.db_path(ws).exists() else None
    if action == "def":
        rows = query.defs(con, q, kind=args.kind, limit=args.limit)
        text = "\n".join(f"{r['kind']:<9} {r['qname']}" + ("" if r["kind"] in query.TYPE_KINDS else f"  {r['signature']}")
                         + f"  {_loc(r)}" for r in rows)
    elif action == "sym":
        rows = query.symbols(con, q, kind=args.kind, limit=args.limit)
        text = "\n".join(f"{r['kind']:<9} {r['qname']}  {_loc(r)}" for r in rows)
    elif action == "refs":
        rows = query.references(con, q, limit=args.limit)
        text = "\n".join(f"{_loc(r)}  {r['owner'] or '-'}  {r['kind']} {r['target']}" for r in rows)
        if rows and rows[0].get("by_name") and "." in q:
            text += "\n[by name only: no use written as the qualified name]"
    elif action == "callers":
        rows = query.callers(con, q, limit=args.limit)
        text = "\n".join(f"{r['caller']}  ({r['calls']}x)  {_loc(r)}" + ("  [REA call graph]" if r.get("source") == "rea" else "")
                         for r in rows)
        if rows and rows[0].get("by_name") and "." in q:
            text += "\n[by name only: no call written as the qualified name; check each call site]"
    elif action == "callees":
        rows = query.callees(con, q, limit=args.limit)

        def target(r: dict) -> str:
            if not r["defined_as"]:
                return "(not in index: engine/library code)"
            more = f" (+{r['definitions'] - 3})" if r["definitions"] > 3 else ""
            guess = "  [by name only]" if r["resolved_by"] == "name" and r["definitions"] > 1 else ""
            return ", ".join(r["defined_as"]) + more + guess

        text = "\n".join(f"{r['kind']:<4} {r['target']}  :{r['line']}  -> {target(r)}" for r in rows)
    elif action == "show":
        rows = query.show(con, ws, q, max_lines=args.lines, context=args.context)
        text = (f"{rows['kind']} {rows['qname'] or ''}  {rows['path']}:{rows['start']}-{rows['end']}\n{rows['text']}"
                + (f"\n... truncated at {args.lines} lines (symbol ends at line {rows['full_end']}; --lines N)" if rows["truncated"] else "")
                + (("\nother matches:\n  " + "\n  ".join(rows["others"])) if rows["others"] else ""))
    elif action == "outline":
        rows = query.outline(con, q, limit=args.limit)
        text = "\n".join(f"{'  ' if i else ''}{r['kind']:<9} {r['signature'] or r['name']}  :{r['line']}"
                         + (f"  {r['path']}" if not i else "") for i, r in enumerate(rows))
    elif action == "hier":
        rows = query.hierarchy(con, q, limit=args.limit)
        t = rows["type"]
        text = (f"{t['kind']} {t['qname']}  {_loc(t)}\n  bases: {', '.join(rows['bases']) or '-'}\n  derived:\n"
                + ("\n".join(f"    {'  ' * (d['level'] - 1)}{d['qname']}  {_loc(d)}" for d in rows["derived"]) or "    -"))
    elif action == "strings":
        rows = query.strings(con, q, limit=args.limit)
        text = "\n".join(f"{_loc(r)}  {r['owner'] or '-'}  {r['value']}" for r in rows)
    elif action == "grep":
        rows = query.grep(con, ws, q, glob=args.glob, limit=args.limit, ignore_case=args.ignore_case)
        text = "\n".join(f"{_loc(r)}  [{r['owner'] or '-'}]  {r['text']}" for r in rows)
    else:
        rows = index.stats(con)
        text = (f"{rows['files']} files, {rows['lines']} lines, {rows['symbols']} symbols, {rows['refs']} refs, "
                f"{rows['strings']} strings\nkinds: " + ", ".join(f"{k} {v}" for k, v in rows["kinds"].items())
                + "\nmodules:\n" + "\n".join(f"  {m['module']:<40} {m['files']:>6} files {m['lines']:>9} lines" for m in rows["modules"]))
    emit(rows, args.json, text or "no results")
    return 0
