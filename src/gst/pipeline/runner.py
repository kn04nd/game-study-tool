"""Scheduler: pools per resource (cpu, io, heavy=1), RAM weight per job (measured peak of the extractor, else unit
size; see ``budget``), a watchdog that only warns.

Each job runs in its own worker process (``python -X utf8 -m gst.pipeline.worker``). On Windows the
workers join a Job Object that dies with this process, so killing ``gst study`` also stops its workers
and their tools; the jobs they held stay ``running`` in jobs.db and the next ``gst study`` puts them
back to ``pending``. There is no time limit unless ``--timeout`` is given; the watchdog only logs a
warning when a job shows no activity (log, staging folder) for a while.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from gst.common import human_size
from gst.pipeline import budget
from gst.pipeline.jobs import FAILED, PAUSED, PENDING, RUNNING, JobsDB
from gst.pipeline.progress import Progress, write_status
from gst.pipeline.worker import FINALIZE, PARTIAL

HEAVY_SLOTS = 1
WATCHDOG_S = 1800.0
POLL_S = 0.25


class _KillOnClose:
    """A Windows Job Object with KILL_ON_JOB_CLOSE: workers (and their children) die with the runner."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            return

        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("Basic", BasicLimit), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = ExtendedLimit()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if k32.SetInformationJobObject(wintypes.HANDLE(handle), 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle = handle

    def add(self, proc: subprocess.Popen) -> None:
        if self.handle is None:
            return
        import ctypes
        from ctypes import wintypes

        ctypes.windll.kernel32.AssignProcessToJobObject(wintypes.HANDLE(self.handle), wintypes.HANDLE(int(proc._handle)))


def kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, stdin=subprocess.DEVNULL)
    else:
        proc.kill()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _activity(paths: list[Path]) -> tuple:
    """A cheap signature of what a job is doing: sizes and mtimes of its log and staging folder."""
    sig = []
    for p in paths:
        try:
            if p.is_dir():
                n, size, newest = 0, 0, 0.0
                for dirpath, _d, files in os.walk(p):
                    for f in files:
                        try:
                            st = os.stat(os.path.join(dirpath, f))
                        except OSError:
                            continue
                        n, size, newest = n + 1, size + st.st_size, max(newest, st.st_mtime)
                sig.append((n, size, newest))
            elif p.exists():
                st = p.stat()
                sig.append((st.st_size, st.st_mtime))
        except OSError:
            sig.append(None)
    return tuple(sig)


class Runner:
    def __init__(self, ws: Path, *, jobs: int | None = None, timeout: float | None = None, min_free: int | None = None,
                 watchdog_s: float = WATCHDOG_S, progress: Progress | None = None, python: str | None = None):
        self.ws = ws
        self.db_file = ws / "jobs" / "jobs.db"
        self.slots = max(1, jobs or max(1, min(4, (os.cpu_count() or 2) // 2)))
        self.timeout = timeout
        self.min_free = min_free
        self.watchdog_s = watchdog_s
        self.progress = progress or Progress(ws)
        self.python = python or sys.executable
        self.running: dict[int, dict] = {}
        self.ram_model = budget.RamModel()
        self.killer = _KillOnClose()
        (ws / "logs" / "jobs").mkdir(parents=True, exist_ok=True)

    # --- scheduling decisions --------------------------------------------------------------------

    def _weight(self, job: dict) -> int:
        return budget.job_weight(job["size"] or 0, job["resource"] or "cpu", job["extractor"], self.ram_model)

    def _ram_ok(self, weight: int) -> bool:
        if not self.running:
            return True  # always make progress: one job at a time even when RAM is short
        _total, avail = budget.memory()
        # what running jobs may still take: their reservation minus what they already hold (counted in ``avail``)
        to_come = sum(max(0, r["weight"] - budget.process_tree_ram(r["proc"].pid)) for r in self.running.values())
        return budget.ram_fits(avail, to_come, weight)

    def _disk_ok(self) -> bool:
        return not self.min_free or budget.disk_free(self.ws) >= self.min_free

    def _blocked_by_deps(self, extractor: str, active: set[str], active_stages: set[int] | None = None) -> bool:
        from gst.extractors import registry

        try:
            cls = registry.get(extractor)
        except KeyError:
            return False
        if getattr(cls, "after_earlier_stages", False) and any(s < cls.stage for s in active_stages or ()):
            return True
        return any(dep in active for dep in cls.after)

    # --- processes -------------------------------------------------------------------------------

    def _start(self, db: JobsDB, job: dict) -> None:
        db.claim(job["id"])
        log_path = self.ws / "logs" / "jobs" / f"{job['id']}.log"
        fh = open(log_path, "a", encoding="utf-8")
        fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} job {job['id']} {job['extractor']} {job['unit_id']}\n")
        fh.flush()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen([self.python, "-P", "-X", "utf8", "-m", "gst.pipeline.worker", str(self.db_file), str(job["id"])],
                                stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(self.ws),
                                creationflags=flags)
        self.killer.add(proc)
        db.set_pid(job["id"], proc.pid)
        now = time.time()
        self.running[job["id"]] = {**job, "proc": proc, "fh": fh, "log": log_path, "t0": now, "last_change": now,
                                   "last_warn": 0.0, "sig": None, "weight": self._weight(job)}
        self.progress.event("start", job=job["id"], extractor=job["extractor"], unit=job["unit_id"], size=job["size"])
        self.progress.say(f"[{job['id']}] {job['extractor']} {job['unit_id']} ({human_size(job['size'] or 0)})")

    def _reap(self, db: JobsDB) -> None:
        now = time.time()
        finished_any = False
        for job_id, r in list(self.running.items()):
            proc: subprocess.Popen = r["proc"]
            code = proc.poll()
            if code is None:
                if self.timeout and now - r["t0"] > self.timeout:
                    kill_tree(proc)
                    db.finish(job_id, status=FAILED, error=f"stopped by --timeout after {self.timeout:.0f} s",
                              duration_s=round(now - r["t0"], 2))
                    self.progress.warn(f"job {job_id} stopped by --timeout ({self.timeout:.0f} s)", job=job_id)
                    code = -1
                else:
                    self._watchdog(job_id, r, now)
                    continue
            r["fh"].close()
            row = db.get(job_id) or {}
            if row.get("status") == RUNNING:  # the worker died without reporting (crash, kill)
                text = r["log"].read_text(encoding="utf-8", errors="replace") if r["log"].exists() else ""
                db.finish(job_id, status=FAILED, error=f"worker exited with code {code} without a result\n{text}",
                          duration_s=round(now - r["t0"], 2))
                row = db.get(job_id) or {}
            del self.running[job_id]
            finished_any = True
            self.progress.event("finish", job=job_id, extractor=r["extractor"], unit=r["unit_id"], status=row.get("status"),
                                seconds=row.get("duration_s"), out_bytes=row.get("out_bytes"))
            self.progress.say(f"[{job_id}] {row.get('status')} in {row.get('duration_s')} s: {r['extractor']} {r['unit_id']}")
        if finished_any:  # a finished job may have measured its extractor's peak RAM
            self.ram_model = budget.RamModel.from_con(db.con)

    def _watchdog(self, job_id: int, r: dict, now: float) -> None:
        if now - r.get("checked", 0) < min(60.0, self.watchdog_s / 4):
            return
        r["checked"] = now
        sig = _activity([r["log"], self.ws / PARTIAL, self.ws / "logs"])
        if sig != r["sig"]:
            r["sig"], r["last_change"] = sig, now
            return
        idle = now - r["last_change"]
        if idle >= self.watchdog_s and now - r["last_warn"] >= self.watchdog_s:
            r["last_warn"] = now
            self.progress.warn(f"job {job_id} ({r['extractor']} {r['unit_id']}) shows no activity for {int(idle)} s; "
                               "still running (no time limit: --timeout N stops jobs; this is only a warning)", job=job_id)

    # --- main loop -------------------------------------------------------------------------------

    def run(self) -> dict:
        t0 = time.time()
        started = finished = 0
        paused = False
        last_status = 0.0
        with JobsDB(self.db_file) as db:
            self.ram_model = budget.RamModel.from_con(db.con)
            try:
                while True:
                    self._reap(db)
                    pending = db.pending()
                    if not pending and not self.running:
                        break
                    if pending and not self._disk_ok():
                        if not self.running:
                            for job in pending:
                                db.set_status(job["id"], PAUSED, f"free disk below --min-free {human_size(self.min_free or 0)}")
                            self.progress.warn(f"paused: free space {human_size(budget.disk_free(self.ws))} is below --min-free "
                                               f"{human_size(self.min_free or 0)}; {len(pending)} jobs paused-disk (rerun gst study "
                                               "after freeing space)")
                            paused = True
                            break
                        time.sleep(1)
                        continue
                    active = db.active_extractors() if pending else set()
                    heavy = sum(1 for r in self.running.values() if r["resource"] == "heavy")
                    # an extractor's <finalize> job waits for every other unit of that extractor
                    unit_work = {j["extractor"] for j in pending if j["unit_id"] != FINALIZE}
                    unit_work |= {r["extractor"] for r in self.running.values()}
                    active_stages = {j["stage"] for j in pending} | {r.get("stage") or 0 for r in self.running.values()}
                    for job in pending:
                        if len(self.running) >= self.slots:
                            break
                        if job["resource"] == "heavy" and heavy >= HEAVY_SLOTS:
                            continue
                        if job["unit_id"] == FINALIZE and job["extractor"] in unit_work:
                            continue
                        if self._blocked_by_deps(job["extractor"], active - {job["extractor"]}, active_stages):
                            continue
                        if not self._ram_ok(self._weight(job)):
                            break
                        self._start(db, job)
                        unit_work.add(job["extractor"])
                        started += 1
                        heavy += job["resource"] == "heavy"
                    if time.time() - last_status > 5:
                        write_status(self.ws, db, running=list(self.running.values()), started=t0)
                        last_status = time.time()
                    time.sleep(POLL_S)
            except KeyboardInterrupt:
                for job_id, r in list(self.running.items()):
                    kill_tree(r["proc"])
                    db.set_status(job_id, PENDING)
                self.progress.warn("interrupted: running jobs went back to pending")
                raise
            finally:
                write_status(self.ws, db, running=list(self.running.values()), started=t0)
            counts = db.counts()
        finished = started
        totals: dict[str, int] = {}
        for per in counts.values():
            for status, n in per.items():
                totals[status] = totals.get(status, 0) + n
        return {"started": started, "finished": finished, "paused": paused, "seconds": round(time.time() - t0, 1),
                "totals": totals}
