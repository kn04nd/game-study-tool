"""Disk estimate and the --min-free floor (pauses jobs as paused-disk, never cuts), RAM probe and job
weights for the scheduler.

RAM rule of the scheduler (``Runner._ram_ok``, mirrored by ``capacity.simulate``): a job reserves its weight =
the measured peak RAM of its extractor in this workspace times ``RAM_MARGIN`` (``RamModel``, from the ``peak_ram``
column of done and failed jobs: the larger of the worker's own peak and the runner's samples of the worker's whole
process tree, so child tools count), else a default by kind: ``RAM_HEAVY_SHARE`` of the total RAM for ``heavy``
jobs (external tools: AssetRipper, Ghidra, Cpp2IL), ``RAM_UNSIZED_SHARE`` of it for ``cpu`` jobs without an input
size (analyses and finalize steps read the whole workspace), else the size rule ``RAM_FACTOR`` x unit size. A job starts when, after what the running jobs may still
take (weight minus what their process tree already uses) and its own weight, the free RAM stays at or above
``keep_free`` (the larger of ``RAM_KEEP_FREE`` and ``RAM_KEEP_SHARE`` of the free RAM). The first job always
starts."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
from pathlib import Path

from gst.common import GsError

# Output bytes per input byte, by category (rough; the estimate is a warning, never a limit).
OUT_RATIO = {
    "code": 3.0, "texture": 2.5, "mesh": 2.0, "audio": 6.0, "animation": 2.0, "prefab_scene": 2.0,
    "material_shader": 1.5, "data": 1.5, "localization": 1.5, "font": 1.0, "video": 0.0, "map": 0.05,
    "config_save": 1.0, "other": 1.0,
}
# RAM a worker needs per input byte, by resource pool (heavy tools manage their own heap: Ghidra -Xmx...),
# used until a job of the same extractor has measured its peak.
RAM_FACTOR = {"cpu": 3.0, "io": 0.0, "heavy": 0.0}
RAM_FLOOR = 64 << 20
RAM_MARGIN = 1.5            # measured peak x this = the reservation
RAM_KEEP_FREE = 2 << 30     # never plan to leave less free RAM than this ...
RAM_KEEP_SHARE = 0.2        # ... or than this share of the free RAM
MEASURE_BIG = 64 << 20      # units at least this large give a peak-per-byte ratio
RAM_HEAVY_SHARE = 0.4       # unmeasured heavy job (external tool): this share of the total RAM
RAM_UNSIZED_SHARE = 0.25    # unmeasured cpu job without an input size (an analysis): this share of the total RAM


def parse_size(text: str | int | None) -> int | None:
    """'20GB', '500 MB', '1.5T', 1024 -> bytes; None/'' -> None."""
    if text in (None, ""):
        return None
    if isinstance(text, int):
        return text
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMGT]?)(?:I?B)?\s*", str(text), re.I)
    if not m:
        raise GsError(f"not a size: {text!r} (examples: 20GB, 500MB)")
    return int(float(m.group(1)) * (1024 ** " KMGT".index((m.group(2) or " ").upper())))


def disk_free(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


# Extractors whose output does not scale like their category (catalogs, listings, one index).
OUT_RATIO_BY_NAME = {"files.loose": 0.001, "godot.inventory": 0.001, "unreal.inventory": 0.001, "maps.list": 0.0,
                     "code.native": 4.0, "godot.recover": 1.5}


def estimate_out(category: str, in_bytes: int, name: str | None = None) -> int:
    ratio = OUT_RATIO_BY_NAME.get(name or "", OUT_RATIO.get(category, 1.0))
    return int(in_bytes * ratio)


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                ("avail", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_avail", ctypes.c_ulonglong),
                ("virtual_total", ctypes.c_ulonglong), ("virtual_avail", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]


def memory() -> tuple[int, int]:
    """(total, available) physical RAM in bytes (GlobalMemoryStatusEx; a guess off Windows)."""
    if os.name == "nt":
        status = _MemoryStatus()
        status.length = ctypes.sizeof(_MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total), int(status.avail)
    try:
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
        avail = os.sysconf("SC_AVPHYS_PAGES") * size
        return pages * size, avail
    except (AttributeError, ValueError, OSError):
        return 8 << 30, 4 << 30


def ram_weight(size: int, resource: str) -> int:
    """Bytes of RAM the scheduler reserves for a job (unit size times the pool factor, with a floor)."""
    return max(RAM_FLOOR, int(size * RAM_FACTOR.get(resource, 1.0)))


MEASURED_STATUSES = ("done", "failed", "pending", "running", "paused-disk")


class RamModel:
    """Measured peak RAM per extractor (jobs with ``peak_ram``: done, failed - a lower bound, e.g. a MemoryError -
    and jobs planned again, pending or running, which keep the peak of their last run): the largest peak/size ratio
    of its big units times the size, never below the largest peak of its small units."""

    def __init__(self, rows=()):
        self.ratio: dict[str, float] = {}
        self.small: dict[str, int] = {}
        self.peak: dict[str, int] = {}
        for r in rows:
            ext, size, peak = r.get("extractor"), r.get("size") or 0, r.get("peak_ram")
            if not ext or not peak or r.get("status") not in (None, *MEASURED_STATUSES):
                continue
            self.peak[ext] = max(self.peak.get(ext, 0), peak)
            if size >= MEASURE_BIG:
                self.ratio[ext] = max(self.ratio.get(ext, 0.0), peak / size)
            else:
                self.small[ext] = max(self.small.get(ext, 0), peak)

    @classmethod
    def from_con(cls, con) -> "RamModel":
        try:
            marks = ", ".join("?" * len(MEASURED_STATUSES))
            cur = con.execute(f"SELECT extractor, size, peak_ram FROM jobs WHERE status IN ({marks}) "
                              "AND peak_ram IS NOT NULL", MEASURED_STATUSES)
            rows = cur.fetchall()
        except Exception:  # an older jobs.db without the column (read-only connections do not add it)
            return cls()
        return cls({"extractor": e, "size": s, "peak_ram": p} for e, s, p in rows)

    def measured(self, extractor: str, size: int) -> int | None:
        if extractor not in self.peak:
            return None
        need = int(self.ratio[extractor] * (size or 0)) if extractor in self.ratio else self.peak[extractor]
        return max(need, self.small.get(extractor, 0), RAM_FLOOR)


def job_weight(size: int, resource: str, extractor: str = "", model: RamModel | None = None,
               total_ram: int | None = None) -> int:
    """What the scheduler reserves for a job: measured peak x RAM_MARGIN, else the default of its kind (see the
    module docstring)."""
    resource = resource or "cpu"
    if model is not None:
        measured = model.measured(extractor, size)
        if measured:
            return max(RAM_FLOOR, int(measured * RAM_MARGIN))
    if resource == "heavy" or (resource == "cpu" and not size):
        total = total_ram if total_ram is not None else memory()[0]
        share = RAM_HEAVY_SHARE if resource == "heavy" else RAM_UNSIZED_SHARE
        return max(RAM_FLOOR, int(total * share))
    return ram_weight(size, resource)


def keep_free(avail: int) -> int:
    return max(RAM_KEEP_FREE, int(avail * RAM_KEEP_SHARE))


def ram_fits(avail: int, to_come: int, weight: int) -> bool:
    """Does a job of ``weight`` fit when ``avail`` is free and running jobs may still take ``to_come``?"""
    return avail - to_come - weight >= keep_free(avail)


def process_tree_ram(pid: int | None) -> int:
    """Working set of ``pid`` and its descendants in bytes (Windows; 0 elsewhere or when unknown).
    A worker is a venv launcher whose child interpreter does the work, so the tree is what counts."""
    if not pid or os.name != "nt":
        return 0
    try:
        from ctypes import wintypes

        class Entry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == ctypes.c_void_p(-1).value:
            return 0
        children: dict[int, list[int]] = {}
        try:
            entry = Entry()
            entry.dwSize = ctypes.sizeof(Entry)
            ok = k32.Process32FirstW(wintypes.HANDLE(snap), ctypes.byref(entry))
            while ok:
                children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
                ok = k32.Process32NextW(wintypes.HANDLE(snap), ctypes.byref(entry))
        finally:
            k32.CloseHandle(wintypes.HANDLE(snap))
        total, todo, seen = 0, [int(pid)], set()
        while todo:
            p = todo.pop()
            if p in seen:
                continue
            seen.add(p)
            todo += children.get(p, [])
            handle = k32.OpenProcess(0x1000 | 0x0010, False, p)  # QUERY_LIMITED_INFORMATION | VM_READ
            if not handle:
                continue
            try:
                c = Counters()
                c.cb = ctypes.sizeof(Counters)
                if k32.K32GetProcessMemoryInfo(wintypes.HANDLE(handle), ctypes.byref(c), c.cb):
                    total += int(c.WorkingSetSize)
            finally:
                k32.CloseHandle(wintypes.HANDLE(handle))
        return total
    except Exception:  # bookkeeping: an unknown use counts as nothing used yet (the full weight stays reserved)
        return 0
