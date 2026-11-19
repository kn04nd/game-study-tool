"""`gst lib build`: GS_HOME/_library/library.db from scratch, every time (idempotent; a few seconds).

The database is written to `library.db.partial-<pid>` and renamed over the old one at the end, so a failed build
leaves the previous library intact. Workspaces are only read.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

from gst.library import SCHEMA_VERSION, Workspace, db_path, discover, facets

DOC_KINDS = ("notes", "analysis", "facts")
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}")


def schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE games (key TEXT PRIMARY KEY, slug TEXT, name TEXT, origin TEXT, engine TEXT, engine_key TEXT,
                            engine_label TEXT, engine_version TEXT, buildid TEXT, workspace TEXT, home TEXT,
                            analyses TEXT, middleware TEXT, docs INTEGER);
        CREATE TABLE sources (game TEXT, name TEXT, path TEXT, version INTEGER, generated_at TEXT, bytes INTEGER);
        CREATE TABLE gaps (game TEXT, facet TEXT, analysis TEXT, kind TEXT, reason TEXT);
        CREATE VIRTUAL TABLE docs USING fts5(text, heading UNINDEXED, game UNINDEXED, slug UNINDEXED, kind UNINDEXED,
                                             path UNINDEXED, line UNINDEXED, tokenize='unicode61 remove_diacritics 2');
    """)
    for table, columns in facets.TABLES.items():
        cols = ", ".join(f"{name} {kind}" for name, kind in columns)
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, game TEXT, {cols}, source TEXT, ref TEXT)")
        conn.execute(f"CREATE INDEX {table}_game ON {table}(game)")


def doc_files(ws: Path) -> list[tuple[str, Path]]:
    """``(kind, file)`` indexed for search: notes/**/*.md and ARCHITECTURE.md (notes), FACTS.md (facts),
    analysis/*.md (analysis)."""
    out: list[tuple[str, Path]] = []
    notes = ws / "notes"
    if notes.is_dir():
        out += [("notes", p) for p in sorted(notes.rglob("*.md")) if p.name != "INDEX.md"]
    if (ws / "ARCHITECTURE.md").is_file():
        out.append(("notes", ws / "ARCHITECTURE.md"))
    if (ws / "FACTS.md").is_file():
        out.append(("facts", ws / "FACTS.md"))
    if (ws / "analysis").is_dir():
        out += [("analysis", p) for p in sorted((ws / "analysis").glob("*.md"))]
    return out


def index_docs(conn: sqlite3.Connection, w: Workspace) -> int:
    """One FTS row per non-empty line (exact path:line hits), with the nearest heading for context."""
    rows = 0
    for kind, path in doc_files(w.path):
        rel = path.relative_to(w.path).as_posix()
        heading = ""
        batch = []
        text = path.read_bytes().decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or _SEPARATOR.match(stripped):
                continue
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
            batch.append((stripped, heading, w.key, w.slug, kind, rel, number))
        conn.executemany("INSERT INTO docs (text, heading, game, slug, kind, path, line) VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
        rows += len(batch)
    return rows


def _insert(conn: sqlite3.Connection, game: str, table: str, row: dict) -> None:
    cols = ["game"] + [c for c, _ in facets.TABLES[table]] + ["source", "ref"]
    conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [game] + [row.get(c) for c in cols[1:]])


def add_workspace(conn: sqlite3.Connection, w: Workspace) -> dict:
    counts: dict[str, int] = {}
    present: list[str] = []
    if w.origin != "legacy":  # legacy gs workspaces have no analysis/: search only

        def emit(table: str, row: dict) -> None:
            _insert(conn, w.key, table, row)
            counts[table] = counts.get(table, 0) + 1

        rd, present = facets.extract(w.path, emit)
        for name, s in sorted(rd.sources.items()):
            conn.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)",
                         (w.key, name, s["path"], s.get("version"), s.get("generated_at"), s.get("bytes")))
        for g in facets.gaps(w.engine, set(present), w.slug) + rd.notes:
            conn.execute("INSERT INTO gaps VALUES (?, ?, ?, ?, ?)", (w.key, g["facet"], g.get("analysis"), g["kind"], g["reason"]))
    docs = index_docs(conn, w)
    engine = w.game.get("engine") or {}
    conn.execute("INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (w.key, w.slug, w.name, w.origin, w.engine, engine.get("key"), engine.get("label"), engine.get("version"),
                  str(w.game.get("buildid") or ""), w.path.as_posix(), w.home.as_posix(), json.dumps(sorted(present)),
                  json.dumps(w.game.get("middleware") or [], ensure_ascii=False), docs))
    return {"key": w.key, "origin": w.origin, "engine": w.engine, "analyses": sorted(present), "rows": counts, "docs": docs}


def build(home: Path, *, games: list[str] | None = None, extra: list[str] | None = None,
          legacy: list[str] | None = None) -> dict:
    """Rebuild ``home/_library/library.db``; returns a summary (games, rows per table, gaps, warnings)."""
    started = time.time()
    found, warnings = discover(home, games=games, extra=extra, legacy=legacy)
    final = db_path(home)
    final.parent.mkdir(parents=True, exist_ok=True)
    partial = final.with_name(f"{final.name}.partial-{os.getpid()}")
    partial.unlink(missing_ok=True)
    conn = sqlite3.connect(partial)
    try:
        schema(conn)
        per_game = [add_workspace(conn, w) for w in found]
        seconds = round(time.time() - started, 2)
        meta = {"schema_version": SCHEMA_VERSION, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "home": home.as_posix(),
                "games": [g["key"] for g in per_game], "extra": extra or [], "legacy": legacy or [], "filter": games or [],
                "seconds": seconds}
        conn.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
        tables = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in facets.TABLES}
        tables["docs"] = conn.execute("SELECT count(*) FROM docs").fetchone()[0]
        gap_rows = conn.execute("SELECT game, facet, analysis, kind, reason FROM gaps ORDER BY game, facet").fetchall()
        conn.commit()
    finally:
        conn.close()
    os.replace(partial, final)
    return {"db": final.as_posix(), "games": per_game, "tables": tables, "gaps": [
        {"game": g[0], "facet": g[1], "analysis": g[2], "kind": g[3], "reason": g[4]} for g in gap_rows],
        "warnings": warnings, "seconds": round(time.time() - started, 2)}
