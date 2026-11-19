"""`gst lib search`: FTS5 over the notes, FACTS.md, ARCHITECTURE.md and analysis Markdown of every workspace.

The query is FTS5 syntax (`ragdoll`, `"voice chat"`, `ragdoll AND joint`, `rag*`); accents are folded
(`fisica` finds `física`). Text that is not valid FTS5 (`R.E.P.O.`, `a-b`) is searched as quoted words.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from gst.library import connect, resolve_games

KINDS = ("notes", "analysis", "facts")


def _quoted(query: str) -> str:
    words = re.findall(r"[\w*]+", query, re.UNICODE)
    return " ".join('"' + w.strip("*").replace('"', "") + '"' + ("*" if w.endswith("*") else "") for w in words if w.strip("*"))


def search(query: str, *, home: Path | None = None, games: list[str] | None = None, kind: str | None = None,
           limit: int | None = None) -> list[dict]:
    """Hits ``{game, slug, kind, path (workspace-relative), file (absolute), line, heading, text, snippet}``, best first."""
    conn = connect(home)
    try:
        where, params = ["docs MATCH ?"], []
        if games:
            keys = [r["key"] for r in resolve_games(conn, games)]
            where.append(f"game IN ({', '.join('?' * len(keys))})")
            params += keys
        if kind:
            where.append("kind = ?")
            params.append(kind)
        workspaces = {r["key"]: r["workspace"] for r in conn.execute("SELECT key, workspace FROM games")}
        sql = (f"SELECT game, slug, kind, path, line, heading, text, snippet(docs, 0, '[', ']', '...', 24) AS snip "
               f"FROM docs WHERE {' AND '.join(where)} ORDER BY bm25(docs), game, path, line"
               + (" LIMIT ?" if limit else ""))
        tail = [limit] if limit else []
        try:
            rows = conn.execute(sql, [query] + params + tail).fetchall()
        except sqlite3.OperationalError:
            quoted = _quoted(query)
            rows = conn.execute(sql, [quoted] + params + tail).fetchall() if quoted else []
    finally:
        conn.close()
    return [{"game": r["game"], "slug": r["slug"], "kind": r["kind"], "path": r["path"], "line": int(r["line"]),
             "file": f"{workspaces.get(r['game'], '')}/{r['path']}", "heading": r["heading"], "text": r["text"],
             "snippet": r["snip"]} for r in rows]
