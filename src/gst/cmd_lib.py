"""gst lib: the cross-game library GS_HOME/_library/library.db.

    gst lib build [--games a,b] [--workspace DIR] [--legacy [HOME]]   rebuild from every workspace of the registry
    gst lib compare rigs peak repo pratfall safari-tv                  _library/compare/rigs-<games>.md (pt-BR table)
    gst lib search "ragdoll" [--game peak] [--kind notes]             FTS over notes, FACTS, ARCHITECTURE, analysis .md
    gst lib status                                                     games, rows per facet, gaps

The library only reads the workspaces; it writes only GS_HOME/_library/. `--workspace` (a workspace or another
GS_HOME) and `--legacy` (a gs v1 home, read for search only) are remembered in _library/library.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gst.common import GsError, emit


def register(sub, parent) -> None:
    p = sub.add_parser("lib", parents=[parent], help="cross-game library: build, compare facets, search every study's notes",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")

    b = s.add_parser("build", parents=[parent], help="rebuild GS_HOME/_library/library.db from every workspace (idempotent)")
    b.add_argument("--games", help="comma-separated slugs (default: every workspace)")
    b.add_argument("--workspace", action="append", default=[], metavar="DIR",
                   help="extra workspace folder or another GS_HOME (remembered; repeatable)")
    b.add_argument("--legacy", nargs="?", const="", metavar="HOME",
                   help="also index the notes of a legacy gs home for search, read-only (default: gs_home of the gs config; remembered)")
    b.add_argument("--forget", action="append", default=[], metavar="DIR", help="drop a remembered --workspace/--legacy folder")
    b.set_defaults(func=cmd_build)

    c = s.add_parser("compare", parents=[parent], help="compare one facet across games -> _library/compare/<facet>-<games>.md")
    c.add_argument("facet", help="rigs, joints (ragdoll), animation, audio, middleware, systems, shaders, dcc")
    c.add_argument("games", nargs="*", help="game slugs or names (default: every game in the library)")
    c.add_argument("--print", action="store_true", help="also print the Markdown")
    c.set_defaults(func=cmd_compare)

    q = s.add_parser("search", parents=[parent], help="full-text search over every study's notes and analysis Markdown")
    q.add_argument("query", help='FTS5 query: ragdoll, "voice chat", rag*, ragdoll AND joint (accents folded)')
    q.add_argument("--game", action="append", default=[], help="only this game (repeatable or comma-separated)")
    q.add_argument("--kind", choices=("notes", "analysis", "facts"), help="notes (notes/ + ARCHITECTURE.md), analysis, facts")
    q.add_argument("--limit", type=int, default=None, help="at most N hits (default: all)")
    q.set_defaults(func=cmd_search)

    st = s.add_parser("status", parents=[parent], help="what the library holds: games, rows per facet, gaps")
    st.set_defaults(func=cmd_status)


def _legacy_home() -> str:
    from gst import config

    try:
        text = config.legacy_config_path().read_text(encoding="utf-8")
    except OSError:
        text = ""
    value = (config._parse_flat(text) or {}).get("gs_home")
    if not value:
        raise GsError("no gs_home in the legacy gs config: pass the folder, --legacy D:/game-study")
    return value


def cmd_build(args) -> int:
    from gst import config, library
    from gst.common import norm_path
    from gst.library import build

    home = config.gs_home()
    settings = library.load_settings(home)
    for item in args.workspace:
        settings["workspaces"].append(Path(item).resolve().as_posix())
    if args.legacy is not None:
        settings["legacy"].append(Path(args.legacy or _legacy_home()).resolve().as_posix())
    forget = {norm_path(Path(f).resolve()) for f in args.forget}
    for key in ("workspaces", "legacy"):
        settings[key] = [x for x in dict.fromkeys(settings[key]) if norm_path(x) not in forget]
    games = [g.strip() for g in (args.games or "").split(",") if g.strip()]
    result = build.build(home, games=games or None, extra=settings["workspaces"], legacy=settings["legacy"])
    if args.workspace or args.legacy is not None or args.forget:
        library.save_settings(home, settings)
    result["settings"] = settings
    lines = [f"{result['db']}: {len(result['games'])} games in {result['seconds']} s"]
    for g in result["games"]:
        rows = ", ".join(f"{k} {v}" for k, v in sorted(g["rows"].items())) or "no analysis rows"
        lines.append(f"  {g['key']:<18} {g['origin']:<8} {g['engine']:<7} {rows}; {g['docs']} doc lines")
    missing = [x for x in result["gaps"] if x["kind"] == "missing"]
    lines.append(f"gaps: {len(missing)} missing analyses, {len(result['gaps']) - len(missing)} partial/none "
                 "(gst lib status lists them)")
    lines += [f"warning: {w}" for w in result["warnings"]]
    emit(result, args.json, "\n".join(lines))
    return 0


def cmd_compare(args) -> int:
    from gst.common import read_text
    from gst.library import compare

    names = [n.strip() for item in args.games for n in item.split(",") if n.strip()]
    result = compare.compare(args.facet, names)
    text = result["path"]
    if args.print:
        text = read_text(result["path"]) + "\n" + text
    emit(result, args.json, text)
    return 0


def cmd_search(args) -> int:
    from gst.library import search

    games = [n.strip() for item in args.game for n in item.split(",") if n.strip()]
    hits = search.search(args.query, games=games or None, kind=args.kind, limit=args.limit)
    lines = [f"{h['file']}:{h['line']}  {h['snippet']}" for h in hits]
    emit(hits, args.json, "\n".join(lines) or "no hits")
    return 0


def cmd_status(args) -> int:
    from gst.library import connect, facets

    conn = connect()
    try:
        meta = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM meta")}
        games = []
        for g in conn.execute("SELECT * FROM games ORDER BY key"):
            rows = {t: conn.execute(f"SELECT count(*) FROM {t} WHERE game = ?", (g["key"],)).fetchone()[0] for t in facets.TABLES}
            gaps = [dict(r) for r in conn.execute("SELECT facet, analysis, kind, reason FROM gaps WHERE game = ? ORDER BY facet",
                                                   (g["key"],))]
            games.append({"key": g["key"], "name": g["name"], "origin": g["origin"], "engine": g["engine"],
                          "workspace": g["workspace"], "analyses": json.loads(g["analyses"] or "[]"),
                          "rows": {k: v for k, v in rows.items() if v}, "docs": g["docs"], "gaps": gaps})
    finally:
        conn.close()
    lines = [f"library built {meta.get('built_at')} ({meta.get('seconds')} s), {len(games)} games"]
    for g in games:
        lines.append(f"{g['key']} ({g['name']}, {g['engine']}, {g['origin']}): "
                     + (", ".join(f"{k} {v}" for k, v in g["rows"].items()) or "no facet rows") + f"; {g['docs']} doc lines")
        lines += [f"  gap {x['facet']}: {x['reason']}" for x in g["gaps"]]
    emit({"meta": meta, "games": games}, args.json, "\n".join(lines))
    return 0
