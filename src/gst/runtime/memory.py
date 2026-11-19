"""Read-only memory scans of a game process: find where a value lives (first scan), then narrow the
candidates as the value changes in the game (next scans). Nothing is ever written to the process.

Candidates are kept in `traces/scan.json` (+ `scan.bin`, packed addresses) so the next scan reads only them.
"""

from __future__ import annotations

import array
import ctypes
import re
import struct
import time
from pathlib import Path

from gst.common import GsError, read_json, write_json

TYPES = {"int8": ("<b", 1), "uint8": ("<B", 1), "int16": ("<h", 2), "int32": ("<i", 4), "uint32": ("<I", 4),
         "int64": ("<q", 8), "float": ("<f", 4), "double": ("<d", 8)}
MAX_CANDIDATES = 4_000_000
CHUNK = 16 << 20


def open_process(pid: int):
    from gst.runtime import win

    handle = win.OpenProcess(win.PROCESS_QUERY_INFORMATION | win.PROCESS_VM_READ, False, pid)
    if not handle:
        raise GsError(f"cannot open pid {pid} for reading: {win.last_error()}")
    return handle


def regions(handle, *, writable_only: bool = True, include_image: bool = True) -> list[tuple[int, int, str, str]]:
    """(base, size, protection, type) of committed readable regions."""
    from gst.runtime import win

    out = []
    mbi = win.MEMORY_BASIC_INFORMATION()
    address = 0
    while win.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base, size = mbi.BaseAddress or 0, mbi.RegionSize
        protect = mbi.Protect & 0xFF
        if (mbi.State == win.MEM_COMMIT and not mbi.Protect & win.PAGE_GUARD and protect in win.READABLE
                and (not writable_only or protect in win.WRITABLE)
                and (mbi.Type != win.MEM_IMAGE or include_image)):
            kind = {win.MEM_PRIVATE: "private", win.MEM_MAPPED: "mapped", win.MEM_IMAGE: "image"}.get(mbi.Type, "?")
            out.append((base, size, win.READABLE[protect], kind))
        address = base + size
        if address >= 0x7FFFFFFFFFFF:
            break
    return out


def read(handle, address: int, size: int) -> bytes | None:
    from gst.runtime import win

    buf = ctypes.create_string_buffer(size)
    got = win.SIZE_T()
    if not win.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, size, ctypes.byref(got)) or got.value == 0:
        return None
    return buf.raw[:got.value]


def encode(value: str, kind: str) -> bytes:
    fmt, _size = TYPES[kind]
    number = float(value) if kind in {"float", "double"} else int(value, 0)
    try:
        return struct.pack(fmt, number)
    except struct.error as exc:
        raise GsError(f"{value} does not fit {kind}: {exc}") from exc


def decode(raw: bytes, kind: str):
    return struct.unpack(TYPES[kind][0], raw)[0]


def first_scan(handle, value: str, kind: str) -> tuple[array.array, int]:
    """Aligned addresses holding ``value``; floats match within 1e-4 of the value (rounding in game code)."""
    _fmt, size = TYPES[kind]
    hits = array.array("Q")
    scanned = 0
    target = needle = None
    if kind in {"float", "double"}:
        target = float(value)  # every aligned slot, compared numerically
    else:
        needle = re.compile(re.escape(encode(value, kind)))
    for base, length, _prot, _kind in regions(handle):
        for offset in range(0, length, CHUNK):
            data = read(handle, base + offset, min(CHUNK, length - offset))
            if not data:
                continue
            scanned += len(data)
            if kind in {"float", "double"}:
                for i in range(0, len(data) - size + 1, size):
                    v = struct.unpack_from(TYPES[kind][0], data, i)[0]
                    if abs(v - target) <= 1e-4 * max(1.0, abs(target)):
                        hits.append(base + offset + i)
            else:
                for m in needle.finditer(data):
                    if m.start() % size == 0:
                        hits.append(base + offset + m.start())
            if len(hits) > MAX_CANDIDATES:
                raise GsError(f"more than {MAX_CANDIDATES:,} addresses hold {value}: pick a rarer value or a wider type")
    return hits, scanned


def next_scan(handle, candidates: array.array, kind: str, *, value: str | None, mode: str,
              previous: dict[int, bytes]) -> tuple[array.array, dict[int, bytes]]:
    """Keep candidates whose current value matches: `value` (equal), or `mode` changed/unchanged/increased/decreased
    relative to the previous scan."""
    _fmt, size = TYPES[kind]
    kept, now = array.array("Q"), {}
    wanted = None
    if value is not None:
        wanted = float(value) if kind in {"float", "double"} else int(value, 0)
    for address in candidates:
        raw = read(handle, address, size)
        if raw is None or len(raw) < size:
            continue
        current = decode(raw, kind)
        before = decode(previous[address], kind) if address in previous else None
        if wanted is not None:
            ok = abs(current - wanted) <= 1e-4 * max(1.0, abs(wanted)) if kind in {"float", "double"} else current == wanted
        elif mode == "changed":
            ok = before is not None and current != before
        elif mode == "unchanged":
            ok = before is not None and current == before
        elif mode == "increased":
            ok = before is not None and current > before
        elif mode == "decreased":
            ok = before is not None and current < before
        else:
            raise GsError("next scan: give a value or --changed/--unchanged/--increased/--decreased")
        if ok:
            kept.append(address)
            now[address] = raw
    return kept, now


def save_state(traces: Path, pid: int, kind: str, candidates: array.array, values: dict[int, bytes], history: list) -> None:
    traces.mkdir(parents=True, exist_ok=True)
    (traces / "scan.bin").write_bytes(candidates.tobytes())
    small = {hex(a): values[a].hex() for a in list(candidates)[:200_000] if a in values}
    write_json(traces / "scan.json", {"pid": pid, "type": kind, "count": len(candidates), "history": history,
                                      "values": small, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})


def load_state(traces: Path) -> tuple[dict, array.array, dict[int, bytes]]:
    meta = read_json(traces / "scan.json", None)
    if meta is None or not (traces / "scan.bin").is_file():
        raise GsError("no previous scan here: gst trace scan <value> first")
    candidates = array.array("Q")
    candidates.frombytes((traces / "scan.bin").read_bytes())
    values = {int(a, 16): bytes.fromhex(v) for a, v in meta.get("values", {}).items()}
    return meta, candidates, values
