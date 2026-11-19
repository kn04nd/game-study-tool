"""Code index over a workspace's decomp/ tree: ``<workspace>/index/code.db`` (SQLite + FTS5 trigram).

Built by `gst index` (and after `gst decompile`), read by `gst q`. Incremental: a file is re-parsed only
when its size or mtime changed. Paths are stored relative to the workspace, with forward slashes.
"""

from __future__ import annotations

import os
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from gst.common import GsError

SCHEMA_VERSION = "1"
EXTRACTORS = {".cs": "csharp", ".gd": "gdscript", ".java": "java", ".lua": "lua", ".c": "ghidra"}  # other languages (js...) are not indexed yet
GENERIC_DIRS = {"media", "lua", "scripts", "script", "src", "data", "game", "assets", "content", "classes"}
BATCH = 400

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, lang TEXT, module TEXT,
    size INTEGER, mtime REAL, lines INTEGER, parse_error INTEGER);
CREATE TABLE IF NOT EXISTS symbols (id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, parent_id INTEGER,
    kind TEXT, name TEXT, qname TEXT, signature TEXT, line INTEGER, end_line INTEGER, module TEXT, body_hash TEXT);
CREATE TABLE IF NOT EXISTS bases (symbol_id INTEGER, file_id INTEGER, base TEXT, base_name TEXT);
CREATE TABLE IF NOT EXISTS refs (file_id INTEGER, symbol_id INTEGER, kind TEXT, target TEXT, name TEXT, line INTEGER);
CREATE TABLE IF NOT EXISTS strings (id INTEGER PRIMARY KEY, file_id INTEGER, symbol_id INTEGER, value TEXT, line INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(qname, content='symbols', content_rowid='id', tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS strings_fts USING fts5(value, content='strings', content_rowid='id', tokenize='trigram');
CREATE INDEX IF NOT EXISTS sym_name ON symbols(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS sym_qname ON symbols(qname COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS sym_file ON symbols(file_id, line);
CREATE INDEX IF NOT EXISTS sym_parent ON symbols(parent_id);
CREATE INDEX IF NOT EXISTS ref_name ON refs(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ref_symbol ON refs(symbol_id);
CREATE INDEX IF NOT EXISTS ref_file ON refs(file_id);
CREATE INDEX IF NOT EXISTS base_name ON bases(base_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS base_symbol ON bases(symbol_id);
CREATE INDEX IF NOT EXISTS str_file ON strings(file_id);
"""


def db_path(ws: Path) -> Path:
    return ws / "index" / "code.db"


def connect(ws: Path, *, write: bool = False) -> sqlite3.Connection:
    path = db_path(ws)
    if not write and not path.exists():
        raise GsError(f"no code index yet in {ws} (gst index, or gst decompile which indexes at the end)")
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path) if write else sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    if write:
        con.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;" + SCHEMA)
    return con


def module_of(rel: str) -> str:
    """First meaningful folder under the backend dir: `decomp/cs/Assembly-CSharp/...` -> `Assembly-CSharp`,
    `decomp/lua/media/lua/client/...` -> `client` (generic folder names are skipped)."""
    parts = rel.split("/")[2:-1]
    return next((p for p in parts if p.lower() not in GENERIC_DIRS), parts[0] if parts else "")


def _work(item: tuple[str, str, str]) -> tuple[str, dict | None, str | None]:
    rel, full, lang = item
    try:
        source = Path(full).read_bytes()
        if lang == "csharp":
            from gst.index import csharp

            return rel, csharp.extract(rel, source), None
        if lang == "ghidra":
            from gst.index import ghidra

            return rel, ghidra.extract(rel, source, full), None
        if lang in {"gdscript", "java", "lua"}:
            import importlib

            module = importlib.import_module(f"gst.index.{lang}")
            return rel, module.extract(rel, source), None
        return rel, None, f"no extractor for {lang}"
    except Exception as exc:  # one bad file must not stop the index
        return rel, None, f"{exc.__class__.__name__}: {exc}"


def _scan(ws: Path) -> dict[str, tuple[int, float, str]]:
    found = {}
    root = ws / "decomp"
    if not root.is_dir():
        return found
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            lang = EXTRACTORS.get(os.path.splitext(name)[1].lower())
            if lang:
                full = os.path.join(dirpath, name)
                st = os.stat(full)
                found[Path(full).relative_to(ws).as_posix()] = (st.st_size, st.st_mtime, lang)
    return found


def _delete_files(con: sqlite3.Connection, ids: list[int]) -> None:
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for table in ("symbols", "refs", "bases", "strings"):
            con.execute(f"DELETE FROM {table} WHERE file_id IN ({marks})", chunk)
        con.execute(f"DELETE FROM files WHERE id IN ({marks})", chunk)


def _store(con: sqlite3.Connection, rel: str, size: int, mtime: float, lang: str, data: dict) -> None:
    module = module_of(rel)
    cur = con.execute("INSERT INTO files(path, lang, module, size, mtime, lines, parse_error) VALUES (?,?,?,?,?,?,?)",
                      (rel, lang, module, size, mtime, data["lines"], int(data["has_error"])))
    file_id = cur.lastrowid
    ids: dict[int, int] = {}
    for local, parent, kind, name, qname, signature, line, end_line, body_hash in data["symbols"]:
        cur = con.execute("INSERT INTO symbols(file_id, parent_id, kind, name, qname, signature, line, end_line, module, body_hash)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (file_id, ids.get(parent), kind, name, qname, signature, line, end_line, module, body_hash))
        ids[local] = cur.lastrowid
    con.executemany("INSERT INTO bases(symbol_id, file_id, base, base_name) VALUES (?,?,?,?)",
                    [(ids[local], file_id, base, _base_name(base)) for local, base in data["bases"]])
    con.executemany("INSERT INTO refs(file_id, symbol_id, kind, target, name, line) VALUES (?,?,?,?,?,?)",
                    [(file_id, ids.get(who), kind, target, name, line) for kind, target, name, line, who in data["refs"]])
    con.executemany("INSERT INTO strings(file_id, symbol_id, value, line) VALUES (?,?,?,?)",
                    [(file_id, ids.get(who), value, line) for value, line, who in data["strings"]])


def _base_name(base: str) -> str:
    from gst.index.csharp import _last_identifier

    return _last_identifier(base)


def build(ws: Path, *, rebuild: bool = False, jobs: int | None = None, progress=None) -> dict:
    t0 = time.time()
    if rebuild and db_path(ws).exists():
        for suffix in ("", "-wal", "-shm"):
            Path(str(db_path(ws)) + suffix).unlink(missing_ok=True)
    con = connect(ws, write=True)
    on_disk = _scan(ws)
    known = {row["path"]: (row["id"], row["size"], row["mtime"]) for row in con.execute("SELECT id, path, size, mtime FROM files")}
    stale = [fid for path, (fid, size, mtime) in known.items()
             if path not in on_disk or on_disk[path][:2] != (size, mtime)]
    todo = [(rel, str(ws / rel), lang) for rel, (size, mtime, lang) in on_disk.items()
            if rel not in known or known[rel][1:] != (size, mtime)]
    _delete_files(con, stale)
    failures: list[str] = []
    if todo:
        workers = jobs or max(1, min(8, (os.cpu_count() or 2) - 1))
        done = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for rel, data, error in pool.map(_work, todo, chunksize=24):
                if data is None:
                    failures.append(f"{rel}: {error}")
                else:
                    size, mtime, lang = on_disk[rel]
                    _store(con, rel, size, mtime, lang, data)
                done += 1
                if done % BATCH == 0:
                    con.commit()
                    if progress:
                        progress(done, len(todo))
    con.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    con.execute("INSERT INTO strings_fts(strings_fts) VALUES('rebuild')")
    con.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                    [("schema", SCHEMA_VERSION), ("built_at", time.strftime("%Y-%m-%dT%H:%M:%S"))])
    con.commit()
    summary = stats(con)
    con.close()
    return {**summary, "parsed": len(todo), "removed": sum(1 for path in known if path not in on_disk),
            "failures": failures, "failed": len(failures), "seconds": round(time.time() - t0, 1)}


def stats(con: sqlite3.Connection) -> dict:
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    modules = [dict(r) for r in con.execute(
        "SELECT module, COUNT(*) AS files, SUM(lines) AS lines FROM files GROUP BY module ORDER BY lines DESC")]
    kinds = {r[0]: r[1] for r in con.execute("SELECT kind, COUNT(*) FROM symbols GROUP BY kind ORDER BY 2 DESC")}
    return {"files": one("SELECT COUNT(*) FROM files"), "lines": one("SELECT COALESCE(SUM(lines), 0) FROM files"),
            "symbols": one("SELECT COUNT(*) FROM symbols"), "refs": one("SELECT COUNT(*) FROM refs"),
            "strings": one("SELECT COUNT(*) FROM strings"), "parse_errors": one("SELECT COUNT(*) FROM files WHERE parse_error"),
            "modules": modules, "kinds": kinds}
