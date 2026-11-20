"""One subprocess per unit (``python -X utf8 -m gst.pipeline.worker <jobs.db> <job-id>``): runs
Extractor.run, so a crash in a parser (a segfault in a native wheel, an exception) kills one job,
never the study. Extractors write into ``<out_root>/.partial/<job>/`` and ``commit`` renames the
result into place atomically; sidecars are written after the rename.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

from gst.common import long_path, read_json

PARTIAL = ".partial"
FINALIZE = "<finalize>"  # unit id of the job that runs Extractor.finalize(ctx) after the extractor's units
OPTIONS_FILE = "options.json"  # jobs/options.json: the CLI options of the running study


def context_for(ws: Path, game: dict, options: dict | None = None, *, log: logging.Logger | None = None,
                out_subdir: str = ""):
    """The Context of one extractor: ``out_root`` = ``<ws>/<Extractor.out_subdir>`` (the workspace by default)."""
    from gst.extractors.base import Context

    out_root = Path(ws) / out_subdir if out_subdir else Path(ws)
    return Context(workspace=ws, game=game, install_dir=Path(game["install_dir"]), out_root=out_root,
                   policy=str(game.get("verdict") or "no-runtime"), log=log or logging.getLogger("gst.extractor"),
                   options=dict(options or {}))


def partial_path(out_root: Path, extractor: str, unit_id: str) -> Path:
    key = hashlib.sha1(f"{extractor}|{unit_id}".encode("utf-8")).hexdigest()[:12]
    return Path(out_root) / PARTIAL / f"{re.sub(r'[^A-Za-z0-9._-]', '_', extractor)}-{key}"


def partial_dir(ctx, unit) -> Path:
    """A fresh staging folder for one unit, inside the workspace (same volume: the rename is atomic).
    Whatever is left in it when the job ends (an interrupted or failed run) is removed by the worker."""
    path = partial_path(ctx.out_root, unit.extractor, unit.id)
    rmtree_force(long_path(path))
    os.makedirs(long_path(path))
    return path


def rmtree_force(path: str | Path) -> None:
    """Remove a tree even when it holds read-only files (the user-data copies are read-only)."""
    import stat

    def onexc(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    if os.path.exists(path):
        shutil.rmtree(path, onexc=onexc)


def commit(partial: Path, final: Path) -> Path:
    """Move ``partial`` (file or folder) to ``final`` atomically; an older ``final`` is replaced."""
    src, dst = long_path(partial), long_path(final)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    old = None
    if os.path.exists(dst):
        if os.path.isdir(dst):
            old = f"{dst}.old-{os.getpid()}-{int(time.time())}"
            os.replace(dst, old)
        else:
            os.unlink(dst)
    replace_retry(src, dst)
    if old:
        rmtree_force(old)
    return final


def replace_retry(src: str, dst: str, *, wait_s: float = 30.0) -> None:
    """``os.replace`` that waits out a transient Windows lock (an antivirus or the indexer scanning the files
    just written makes renaming a fresh folder fail with WinError 5/32 for a moment). Waiting on a lock held by
    another process, not a work limit: the last error is raised after ``wait_s``."""
    deadline = time.time() + wait_s
    delay = 0.1
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.time() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 2.0)


def rel_outputs(ws: Path, paths) -> list[str]:
    out = []
    for p in paths:
        try:
            out.append(Path(p).relative_to(ws).as_posix())
        except ValueError:
            out.append(Path(p).as_posix())
    return out


def tree_bytes(path: Path) -> int:
    root = long_path(path)
    if os.path.isfile(root):
        return os.path.getsize(root)
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def peak_ram() -> int | None:
    """Peak RAM of this process in bytes (Windows: PeakWorkingSetSize; elsewhere ru_maxrss), None if unknown.
    Child tools (AssetRipper, Ghidra...) are not counted: they are separate processes."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            c = Counters()
            c.cb = ctypes.sizeof(Counters)
            psapi = ctypes.WinDLL("psapi")
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return int(c.PeakWorkingSetSize)
            return None
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(rss) if sys.platform == "darwin" else int(rss) * 1024
    except Exception:  # bookkeeping never fails a job
        return None


def run_job(db_file: Path, job_id: int) -> int:
    from gst.extractors import registry
    from gst.extractors.base import Result, Unit
    from gst.pipeline.jobs import FAILED, JobsDB
    from gst.workspace import load

    db_file = Path(db_file)
    ws = db_file.parent.parent
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger(f"gst.job.{job_id}")
    with JobsDB(db_file) as db:
        job = db.get(job_id)
        if job is None:
            sys.stderr.write(f"gst: no job {job_id} in {db_file}\n")
            return 2
        t0 = time.time()
        unit = Unit(id=job["unit_id"], extractor=job["extractor"], relpaths=job["relpaths"], size=job["size"] or 0,
                    fingerprint=job["fingerprint"], meta=job["meta"] or {})
        options = read_json(ws / "jobs" / OPTIONS_FILE, {}) or {}
        out_root = ws
        try:
            game = load(ws)
            cls = registry.get(job["extractor"])
            ctx = context_for(ws, game, options, log=log, out_subdir=getattr(cls, "out_subdir", ""))
            out_root = ctx.out_root
            extractor = cls()
            log.info("job %s: %s %s", job_id, unit.extractor, unit.id)
            if unit.id == FINALIZE:
                result = extractor.finalize(ctx)
                if result is None:
                    result = Result(status="skipped", notes=["finalize: nothing to do"])
            else:
                result = extractor.run(unit, ctx)
            if not isinstance(result, Result):
                raise TypeError(f"{unit.extractor}.{'finalize' if unit.id == FINALIZE else 'run'} returned "
                                f"{type(result).__name__}, not Result")
        except Exception:  # a bug in one extractor fails one job, with the whole traceback
            result = Result(status=FAILED, error=traceback.format_exc())
        for root in {Path(ws), Path(out_root)}:  # staging leftovers, never results
            rmtree_force(long_path(partial_path(root, unit.extractor, unit.id)))
        db.finish(job_id, status=result.status, outputs=result.outputs, out_bytes=result.out_bytes, error=result.error,
                  tool=result.tool, tool_version=result.tool_version, notes=result.notes,
                  duration_s=round(time.time() - t0, 2), peak_ram=peak_ram())
        log.info("job %s: %s in %.1f s", job_id, result.status, time.time() - t0)
    return 0


def main(argv: list[str] | None = None) -> int:
    from gst.common import setup_console

    setup_console()
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        sys.stderr.write("usage: python -X utf8 -m gst.pipeline.worker <jobs.db> <job-id>\n")
        return 2
    return run_job(Path(args[0]), int(args[1]))


def write_options(ws: Path, options: dict) -> None:
    from gst.common import write_text

    write_text(ws / "jobs" / OPTIONS_FILE, json.dumps(options, indent=2, default=str) + "\n")


if __name__ == "__main__":
    sys.exit(main())
