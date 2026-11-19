"""Godot PCK lister in stdlib (pack format 1 = Godot 3, 2 = Godot 4.0-4.3, 3 = Godot 4.4+), standalone ``.pck``
or embedded at the end of the executable.

Layout (little endian)::

    "GDPC" u32 pack_format  u32 major u32 minor u32 patch
    v1:  u32 reserved[16]                                   directory follows
    v2:  u32 pack_flags  u64 file_base  u32 reserved[16]    directory follows
    v3:  u32 pack_flags  u64 file_base  u64 dir_offset      directory at dir_offset (relative to the pck start)
    directory: u32 file_count, then per file: u32 path_len (padded to 4), path, u64 offset, u64 size, md5[16],
               and u32 file_flags in v2/v3 (bit 0 = encrypted, bit 1 = removal in a patch pack)

``pack_flags`` bit 0 = encrypted directory (the file list is unreadable without the key: this module reports it and
stops, it never decrypts), bit 1 = file_base relative to the pck start (always in v3), bit 2 = sparse bundle.
Offsets are made absolute in the container (``offset``) and relative to the pck (``pck_offset``).
Embedded pck: the exe ends with ``u64 pck_size`` + ``"GDPC"``; the pck starts at ``end - 12 - pck_size``.
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

MAGIC = b"GDPC"
DIR_ENCRYPTED, REL_FILEBASE, SPARSE_BUNDLE = 1, 2, 4
FILE_ENCRYPTED, FILE_REMOVAL = 1, 2
KNOWN_FORMATS = (1, 2, 3)


class PckError(ValueError):
    pass


def locate(path: Path) -> tuple[int, str] | None:
    """(offset of the pck inside the file, "pck" | "embedded in exe"), or None when there is no GDPC pack."""
    with open(path, "rb") as f:
        if f.read(4) == MAGIC:
            return 0, "pck"
        f.seek(0, os.SEEK_END)
        end = f.tell()
        if end < 16:
            return None
        f.seek(end - 12)
        tail = f.read(12)
        if tail[8:] != MAGIC:
            return None
        size = struct.unpack_from("<Q", tail, 0)[0]
        start = end - 12 - size
        if start < 0:
            return None
        f.seek(start)
        if f.read(4) == MAGIC:
            return start, "embedded in exe"
    return None


def _u32(f) -> int:
    b = f.read(4)
    if len(b) != 4:
        raise PckError("truncated pack")
    return struct.unpack("<I", b)[0]


def _u64(f) -> int:
    b = f.read(8)
    if len(b) != 8:
        raise PckError("truncated pack")
    return struct.unpack("<Q", b)[0]


def res_path(raw: bytes) -> str:
    """Directory path -> ``res://...`` (Godot 4.4+ stores paths without the prefix; padding NULs stripped)."""
    p = raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")
    if p.startswith("res://") or p.startswith("user://"):
        return p
    return "res://" + p.lstrip("/")


def read(path: Path | str) -> dict:
    """Header and directory of a pack. Returns ``{container, where, pck_start, pack_format, version, flags,
    dir_encrypted, rel_filebase, sparse_bundle, file_base, file_count, files: [{path, offset, pck_offset, size,
    md5, encrypted, removal}], encrypted_files, removal_files, bytes, errors}``. An encrypted directory gives
    ``files == []`` and ``file_count`` from the clear header (diagnose, never decrypt)."""
    path = Path(path)
    found = locate(path)
    if found is None:
        raise PckError(f"{path.name}: no GDPC pack (neither a .pck nor an exe with an embedded pack)")
    start, where = found
    container_size = path.stat().st_size
    out: dict = {"container": path.name, "where": where, "pck_start": start, "container_bytes": container_size,
                 "files": [], "errors": []}
    with open(path, "rb") as f:
        f.seek(start + 4)
        fmt, major, minor, patch = (_u32(f) for _ in range(4))
        out.update(pack_format=fmt, version=f"{major}.{minor}.{patch}")
        if fmt not in KNOWN_FORMATS:
            raise PckError(f"{path.name}: unknown pack format {fmt} (known: {', '.join(map(str, KNOWN_FORMATS))})")
        flags, file_base = 0, 0
        if fmt >= 2:
            flags = _u32(f)
            file_base = _u64(f)
        if fmt == 3:
            dir_offset = _u64(f)
            f.seek(start + dir_offset)
            out["dir_offset"] = dir_offset
        else:
            f.read(16 * 4)  # reserved
        rel = fmt == 3 or bool(flags & REL_FILEBASE)
        out.update(flags=flags, dir_encrypted=bool(flags & DIR_ENCRYPTED), rel_filebase=rel,
                   sparse_bundle=bool(flags & SPARSE_BUNDLE), file_base=file_base)
        count = _u32(f)
        out["file_count"] = count
        if out["dir_encrypted"]:
            out["errors"].append("encrypted directory: the file list needs the game's key (guardrails rule 4); "
                                 "only the clear header was read")
            out.update(encrypted_files=None, removal_files=None, bytes=None)
            return out
        # v1 offsets are relative to the pck start; v2 without REL_FILEBASE are absolute in the container
        base = file_base + start if (rel or fmt == 1) else file_base
        files = []
        for i in range(count):
            n = _u32(f)
            raw = f.read(n)
            if len(raw) != n:
                raise PckError(f"{path.name}: directory truncated at entry {i} of {count}")
            ofs, size = _u64(f), _u64(f)
            md5 = f.read(16)
            fflags = _u32(f) if fmt >= 2 else 0
            absolute = base + ofs
            files.append({"path": res_path(raw), "offset": absolute, "pck_offset": absolute - start, "size": size,
                          "md5": md5.hex(), "encrypted": bool(fflags & FILE_ENCRYPTED),
                          "removal": bool(fflags & FILE_REMOVAL)})
    for e in files:
        if e["offset"] + e["size"] > container_size and not e["removal"]:
            out["errors"].append(f"{e['path']}: data past the end of {path.name}")
    out["files"] = files
    out["encrypted_files"] = sum(1 for e in files if e["encrypted"])
    out["removal_files"] = sum(1 for e in files if e["removal"])
    out["bytes"] = sum(e["size"] for e in files)
    return out


def find(install: Path) -> list[Path]:
    """Every pack of an install: ``*.pck`` (largest first), then executables with an embedded pack."""
    install = Path(install)
    packs = sorted((p for p in install.glob("*.pck") if p.is_file()), key=lambda p: (-p.stat().st_size, p.name))
    for exe in sorted(install.glob("*.exe")):
        try:
            hit = locate(exe)
        except OSError:
            hit = None
        if hit and hit[1] == "embedded in exe":
            packs.append(exe)
    return packs


def cross_check(files: list[dict], inventory_paths: list[str]) -> dict:
    """Compare the pck directory with another listing (GDRE ``--list-files``): counts and path differences."""
    ours = {e["path"] for e in files if not e["removal"]}
    theirs = set(inventory_paths)
    only_pck = sorted(ours - theirs)
    only_inv = sorted(theirs - ours)
    return {"pck": len(ours), "inventory": len(theirs), "common": len(ours & theirs), "only_pck": only_pck,
            "only_inventory": only_inv, "match": not only_pck and not only_inv}


def build(entries: list[tuple[str, bytes]], *, fmt: int = 3, version=(4, 4, 1), flags: int = REL_FILEBASE,
          file_flags: dict[str, int] | None = None, strip_res: bool = True) -> bytes:
    """A pack in memory (tests and fixtures; never used on a game). ``entries`` = (res path, data)."""
    file_flags = file_flags or {}
    paths = []
    for p, _data in entries:
        q = p[len("res://"):] if strip_res and p.startswith("res://") and fmt == 3 else p
        b = q.encode("utf-8")
        b += b"\0" * ((4 - len(b) % 4) % 4)
        paths.append(b)
    head_len = 4 + 16 + (4 + 8 + 8 if fmt == 3 else (4 + 8 + 64 if fmt == 2 else 64))
    dir_len = 4 + sum(4 + len(b) + 8 + 8 + 16 + (4 if fmt >= 2 else 0) for b in paths)
    file_base = head_len + dir_len
    dirb = struct.pack("<I", len(entries))
    ofs = 0 if fmt >= 2 else file_base  # v1 offsets are relative to the pck start, v2/v3 to file_base
    for (p, data), b in zip(entries, paths):
        dirb += struct.pack("<I", len(b)) + b + struct.pack("<QQ", ofs, len(data)) + hashlib.md5(data).digest()
        if fmt >= 2:
            dirb += struct.pack("<I", file_flags.get(p, 0))
        ofs += len(data)
    head = MAGIC + struct.pack("<4I", fmt, *version)
    if fmt == 3:
        head += struct.pack("<IQQ", flags, file_base, head_len)
    elif fmt == 2:
        head += struct.pack("<IQ", flags, file_base) + b"\0" * 64
    else:
        head += b"\0" * 64
    return head + dirb + b"".join(data for _p, data in entries)
