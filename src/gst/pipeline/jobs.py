"""jobs/jobs.db (SQLite WAL): one row per unit with inputs (relpath,size,mtime_ns), buildid, status, attempts,
duration, peak RAM of the worker (``peak_ram``, bytes; added to older databases on open), outputs, bytes,
full error, tool and version.

Resume rules (``upsert``): a unit whose fingerprint matches a ``done``/``skipped`` job is skipped; a
changed fingerprint resets the job to ``pending``; ``failed`` stays failed unless ``--retry-failed``;
``paused-disk`` and stale ``running`` (the study process died) go back to ``pending``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from gst.common import long_path

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"
UNSUPPORTED, BLOCKED, PAUSED, SKIPPED = "unsupported", "blocked-policy", "paused-disk", "skipped"
ALL_STATUSES = (PENDING, RUNNING, DONE, FAILED, UNSUPPORTED, BLOCKED, PAUSED, SKIPPED)
FINAL = (DONE, FAILED, UNSUPPORTED, BLOCKED, SKIPPED)
KEEP_WHEN_SAME = (DONE, SKIPPED, UNSUPPORTED, BLOCKED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    extractor TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    relpaths TEXT NOT NULL DEFAULT '[]',
    inputs TEXT NOT NULL DEFAULT '[]',
    fingerprint TEXT NOT NULL DEFAULT '',
    buildid TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    duration_s REAL,
    outputs TEXT NOT NULL DEFAULT '[]',
    out_bytes INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    tool TEXT,
    tool_version TEXT,
    notes TEXT NOT NULL DEFAULT '[]',
    category TEXT,
    resource TEXT,
    stage INTEGER NOT NULL DEFAULT 50,
    seq INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}',
    pid INTEGER,
    peak_ram INTEGER,
    UNIQUE (extractor, unit_id)
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS jobs_extractor ON jobs (extractor);
"""


BUSY_WAIT_S: float | None = None  # set below: waiting on a lock held by another worker, not a work limit
BUSY_WAIT_S = 60.0


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def stat_inputs(install: Path, relpaths: Iterable[str]) -> list[list]:
    """[(relpath, size, mtime_ns)] of each input; a missing file is (relpath, -1, 0)."""
    out = []
    for rel in relpaths:
        try:
            st = os.stat(long_path(install / rel))
            out.append([rel, st.st_size, st.st_mtime_ns])
        except OSError:
            out.append([rel, -1, 0])
    return out


def fingerprint_of(inputs: list[list], buildid: str | None, extra: str = "") -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(inputs, separators=(",", ":")).encode("utf-8"))
    digest.update(f"|{buildid or '?'}|{extra}".encode("utf-8"))
    return digest.hexdigest()


def db_path(ws: Path) -> Path:
    return ws / "jobs" / "jobs.db"


class JobsDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path), timeout=BUSY_WAIT_S, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("PRAGMA busy_timeout=60000")
        self.con.executescript(SCHEMA)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Columns added after a database was created (``CREATE TABLE IF NOT EXISTS`` keeps the old table)."""
        have = {r[1] for r in self.con.execute("PRAGMA table_info(jobs)")}
        if "peak_ram" not in have:
            self.con.execute("ALTER TABLE jobs ADD COLUMN peak_ram INTEGER")

    @classmethod
    def open_readonly(cls, path: Path) -> "JobsDB":
        """Open an existing jobs.db without writing anything (no -wal/-shm, no schema, not even a folder mtime):
        for dry runs. ``immutable=1`` skips locking, so a study running at the same time may be seen stale."""
        self = cls.__new__(cls)
        self.path = Path(path)
        self.con = sqlite3.connect(self.path.resolve().as_uri() + "?immutable=1", uri=True, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        return self

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "JobsDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- planning --------------------------------------------------------------------------------

    def upsert(self, *, extractor: str, unit_id: str, relpaths: list[str], inputs: list[list], fingerprint: str,
               buildid: str | None, category: str, resource: str, stage: int, seq: int, size: int, meta: dict,
               retry_failed: bool = False, force: bool = False, status: str = PENDING, error: str | None = None) -> str:
        """Insert or refresh one unit. Returns what happened: new | changed | forced | retry | kept-<status> | resumed."""
        row = self.con.execute("SELECT id, status, fingerprint FROM jobs WHERE extractor=? AND unit_id=?",
                               (extractor, unit_id)).fetchone()
        common = (json.dumps(relpaths), json.dumps(inputs), fingerprint, buildid, category, resource, stage, seq, size,
                  json.dumps(meta, default=str))
        if row is None:
            self.con.execute(
                "INSERT INTO jobs (relpaths, inputs, fingerprint, buildid, category, resource, stage, seq, size, meta, "
                "extractor, unit_id, status, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*common, extractor, unit_id, status, error))
            return "new"
        same = row["fingerprint"] == fingerprint
        old = row["status"]
        if status != PENDING and (force or not same or old not in (DONE,)):
            new_status, action = status, f"set-{status}"  # policy decided at plan time (blocked-policy...)
        elif force:
            new_status, action = PENDING, "forced"
        elif not same:
            new_status, action = PENDING, "changed"
        elif old in KEEP_WHEN_SAME:
            new_status, action = old, f"kept-{old}"
        elif old == FAILED:
            new_status, action = (PENDING, "retry") if retry_failed else (FAILED, "kept-failed")
        else:  # pending, running (stale), paused-disk
            new_status, action = PENDING, "resumed"
        reset = new_status != old or not same or force
        if reset:
            self.con.execute(
                "UPDATE jobs SET relpaths=?, inputs=?, fingerprint=?, buildid=?, category=?, resource=?, stage=?, seq=?, "
                "size=?, meta=?, status=?, error=?, outputs=CASE WHEN ? THEN '[]' ELSE outputs END, "
                "attempts=CASE WHEN ? THEN 0 ELSE attempts END WHERE id=?",
                (*common, new_status, error if new_status != PENDING else None, not same or force, not same or force, row["id"]))
        else:
            self.con.execute("UPDATE jobs SET relpaths=?, inputs=?, fingerprint=?, buildid=?, category=?, resource=?, "
                             "stage=?, seq=?, size=?, meta=? WHERE id=?", (*common, row["id"]))
        return action

    def recover_stale(self) -> int:
        """Jobs left ``running`` by a study process that died go back to ``pending``."""
        return self.con.execute("UPDATE jobs SET status='pending', pid=NULL WHERE status='running'").rowcount

    def resume_paused(self) -> int:
        return self.con.execute("UPDATE jobs SET status='pending' WHERE status='paused-disk'").rowcount

    # --- running ---------------------------------------------------------------------------------

    def claim(self, job_id: int) -> None:
        self.con.execute("UPDATE jobs SET status='running', attempts=attempts+1, started_at=?, error=NULL WHERE id=?",
                         (now(), job_id))

    def set_pid(self, job_id: int, pid: int) -> None:
        self.con.execute("UPDATE jobs SET pid=? WHERE id=?", (pid, job_id))

    def finish(self, job_id: int, *, status: str, outputs: list[str] | None = None, out_bytes: int = 0,
               error: str | None = None, tool: str = "", tool_version: str = "", notes: list[str] | None = None,
               duration_s: float | None = None, peak_ram: int | None = None) -> None:
        self.con.execute(
            "UPDATE jobs SET status=?, outputs=?, out_bytes=?, error=?, tool=?, tool_version=?, notes=?, duration_s=?, "
            "finished_at=?, pid=NULL, peak_ram=? WHERE id=?",
            (status, json.dumps(outputs or []), int(out_bytes or 0), error, tool, tool_version, json.dumps(notes or []),
             duration_s, now(), peak_ram, job_id))

    def set_status(self, job_id: int, status: str, error: str | None = None) -> None:
        self.con.execute("UPDATE jobs SET status=?, error=COALESCE(?, error), pid=NULL WHERE id=?", (status, error, job_id))

    # --- queries ---------------------------------------------------------------------------------

    def get(self, job_id: int) -> dict | None:
        row = self.con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return as_dict(row) if row else None

    def find(self, extractor: str, unit_id: str) -> dict | None:
        row = self.con.execute("SELECT * FROM jobs WHERE extractor=? AND unit_id=?", (extractor, unit_id)).fetchone()
        return as_dict(row) if row else None

    def rows(self, *, status: str | None = None, extractor: str | None = None, where: str = "",
             params: tuple = ()) -> list[dict]:
        sql, args = "SELECT * FROM jobs WHERE 1=1", []
        if status:
            sql += " AND status=?"
            args.append(status)
        if extractor:
            sql += " AND (extractor=? OR extractor LIKE ?)"
            args += [extractor, f"{extractor}.%"]
        if where:
            sql += f" AND ({where})"
            args += list(params)
        sql += " ORDER BY stage, seq, id"
        return [as_dict(r) for r in self.con.execute(sql, args)]

    def pending(self) -> list[dict]:
        cols = "id, extractor, unit_id, resource, size, stage, seq"
        return [dict(r) for r in self.con.execute(f"SELECT {cols} FROM jobs WHERE status='pending' ORDER BY stage, seq, id")]

    def counts(self) -> dict[str, dict[str, int]]:
        """extractor -> status -> count."""
        out: dict[str, dict[str, int]] = {}
        for r in self.con.execute("SELECT extractor, status, COUNT(*) n FROM jobs GROUP BY extractor, status"):
            out.setdefault(r["extractor"], {})[r["status"]] = r["n"]
        return out

    def active_extractors(self) -> set[str]:
        """Extractors with pending or running jobs (dependencies wait on these)."""
        return {r[0] for r in self.con.execute("SELECT DISTINCT extractor FROM jobs WHERE status IN ('pending','running')")}

    def reset(self, *, ids: list[int] | None = None, extractor: str | None = None, status: str | None = None) -> int:
        sql, args = "UPDATE jobs SET status='pending', error=NULL, attempts=0, pid=NULL WHERE 1=1", []
        if ids:
            sql += f" AND id IN ({','.join('?' * len(ids))})"
            args += ids
        if extractor:
            sql += " AND (extractor=? OR extractor LIKE ?)"
            args += [extractor, f"{extractor}.%"]
        if status:
            sql += " AND status=?"
            args.append(status)
        return self.con.execute(sql, args).rowcount


def as_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("relpaths", "inputs", "outputs", "notes", "meta"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                pass
    return d


# --- one study at a time per workspace --------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class StudyLock:
    """jobs/study.lock with the PID of the running ``gst study``; a lock of a dead process is taken over."""

    def __init__(self, ws: Path):
        self.path = ws / "jobs" / "study.lock"

    def holder(self) -> int | None:
        try:
            pid = int(self.path.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            return None
        return pid if pid != os.getpid() and pid_alive(pid) else None

    def acquire(self) -> None:
        from gst.common import GsError

        other = self.holder()
        if other:
            raise GsError(f"another gst study (PID {other}) is running on this workspace; wait for it or stop that PID")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()), encoding="utf-8")

    def release(self) -> None:
        try:
            if self.path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                self.path.unlink()
        except OSError:
            pass
