"""Disk estimate and the --min-free floor (pauses jobs as paused-disk, never cuts), RAM probe and job
weights for the scheduler."""

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
# RAM a worker needs per input byte, by resource pool (heavy tools manage their own heap: Ghidra -Xmx...).
RAM_FACTOR = {"cpu": 3.0, "io": 0.0, "heavy": 0.0}
RAM_FLOOR = 64 << 20


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
