"""Unreal containers, read with the stdlib: enough to report and decide without any external tool.

- Legacy ``.pak`` (v1-v12): footer (``FPakInfo``: version, encrypted-index flag, key GUID, index offset/size)
  and, when the index is plain, its header (mount point, entry count) and, for v10+, the full directory
  index (every packed path). v12 (``Utf8PakDirectory``, UE 5.4+) is the same layout with UTF-8 names,
  which repak 0.2.3 cannot read: :func:`list_pak` lists it.
- IoStore ``.utoc``/``.ucas``: the 144-byte ``FIoStoreTocHeader`` (version, container id, flags
  Compressed/Encrypted/Signed/Indexed/OnDemand, entry counts). ``global.utoc`` (container id all ones)
  holds the script objects (native class list), never assets.

Nothing here decrypts: an encrypted index or container is reported as such (guardrails.md rule 4).
"""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path

from gst.common import GsError

PAK_MAGIC = 0x5A6F12E1
UTOC_MAGIC = b"-==--==--==--==-"
UTOC_HEADER_SIZE = 144
GLOBAL_CONTAINER_ID = 0xFFFFFFFFFFFFFFFF
UTOC_FLAGS = {1: "compressed", 2: "encrypted", 4: "signed", 8: "indexed", 16: "on-demand"}
UTOC_VERSIONS = {1: "Initial", 2: "DirectoryIndex", 3: "PartitionSize", 4: "PerfectHash", 5: "PerfectHashWithOverflow",
                 6: "OnDemandMetaData", 7: "RemovedOnDemandMetaData", 8: "ReplaceIoChunkHashWithIoHash"}
PAK_VERSIONS = {1: "Initial", 2: "NoTimestamps", 3: "CompressionEncryption", 4: "IndexEncryption",
                5: "RelativeChunkOffsets", 6: "DeleteRecords", 7: "EncryptionKeyGuid", 8: "FNameBasedCompressionMethod",
                9: "FrozenIndex", 10: "PathHashIndex", 11: "Fnv64BugFix", 12: "Utf8PakDirectory"}
REPAK_MAX_VERSION = 11  # repak 0.2.3 (latest release) stops at v11


# --- folders -------------------------------------------------------------------------------------------

def paks_dir(install: Path) -> Path:
    for candidate in sorted(install.glob("*/Content/Paks")) + [install / "Content" / "Paks"]:
        if candidate.is_dir() and (any(candidate.glob("*.pak")) or any(candidate.glob("*.utoc"))):
            return candidate
    raise GsError(f"no Unreal Content/Paks folder under {install}")


def manifest_paths(install: Path) -> tuple[Path | None, list[str]]:
    """``Manifest_UFSFiles_*.txt``: the plain list of packed paths the engine ships next to the game."""
    for manifest in sorted(install.glob("Manifest_UFSFiles_*.txt")):
        paths = [line.split("\t", 1)[0].strip() for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines()
                 if line.strip()]
        return manifest, paths
    return None, []


def ue_game(paks: Path, game: dict, override: str | None = None) -> str:
    """CUE4Parse EGame value: --ue override, else the engine version recon found, else the cook naming."""
    if override:
        return "GAME_UE" + override.replace(".", "_")
    version = str((game.get("engine") or {}).get("version") or "")
    m = re.search(r"([45])\.(\d+)", version)  # "4.27.2", "UE4+Release-4.27"
    if m:
        return f"GAME_UE{m.group(1)}_{m.group(2)}"
    if any("WindowsNoEditor" in p.name for p in paks.glob("*.pak")):
        return "GAME_UE4_27"  # UE4 cook naming; pass --ue 4.xx when the game is older
    return "GAME_UE5_3"


def retoc_version(game_enum: str) -> str | None:
    """retoc --version value (UE4_25..UE5_7) for a CUE4Parse EGame value, when retoc knows it."""
    m = re.fullmatch(r"GAME_UE([45])_(\d+)", game_enum)
    if not m:
        return None
    major, minor = int(m.group(1)), int(m.group(2))
    if (major == 4 and 25 <= minor <= 27) or (major == 5 and 0 <= minor <= 7):
        return f"UE{major}_{minor}"
    return None


# --- legacy .pak ---------------------------------------------------------------------------------------

def _fstring(buf: bytes, pos: int) -> tuple[str, int]:
    """FString at ``pos``: int32 length (negative = UTF-16 chars), bytes with a trailing NUL."""
    (n,) = struct.unpack_from("<i", buf, pos)
    pos += 4
    if n == 0:
        return "", pos
    if n < 0:
        raw = buf[pos:pos - 2 * n]
        return raw.decode("utf-16-le", errors="replace").rstrip("\0"), pos - 2 * n
    raw = buf[pos:pos + n]
    return raw.decode("utf-8", errors="replace").rstrip("\0"), pos + n  # ASCII before v12, UTF-8 from v12


def pak_footer(path: Path) -> dict | None:
    """``FPakInfo`` from the end of the file: version, encrypted index, key GUID, index offset and size."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 1024))
            base = f.tell()
            tail = f.read()
    except OSError:
        return None
    pos = tail.rfind(struct.pack("<I", PAK_MAGIC))
    while pos >= 0:
        if pos + 28 <= len(tail):
            version, index_offset, index_size = struct.unpack_from("<IQQ", tail, pos + 4)
            if 1 <= version <= 64 and index_offset + index_size <= size:
                break
        pos = tail.rfind(struct.pack("<I", PAK_MAGIC), 0, pos)
    if pos < 0:
        return None
    info = {"version": version, "version_name": PAK_VERSIONS.get(version, f"v{version}"), "index_offset": index_offset,
            "index_size": index_size, "size": size, "encrypted_index": None, "key_guid": None}
    if version >= 7 and pos >= 17:  # [GUID 16][bEncryptedIndex 1][magic][version][offset][size][hash 20]...
        guid = tail[pos - 17:pos - 1].hex()
        info.update(key_guid=guid, encrypted_index=tail[pos - 1] == 1, default_key=guid == "0" * 32)
    elif pos >= 1:
        info["encrypted_index"] = tail[pos - 1] == 1
    info["footer_offset"] = base + pos
    return info


def pak_info(path: Path) -> dict | None:
    """Footer plus, when the index is plain, its header: mount point and entry count (and for v10+ the
    path-hash and full-directory index locations)."""
    info = pak_footer(path)
    if info is None:
        return None
    if info["encrypted_index"] or not info["index_size"]:
        return info
    try:
        with open(path, "rb") as f:
            f.seek(info["index_offset"])
            head = f.read(min(info["index_size"], 4096))
        mount, pos = _fstring(head, 0)
        (entries,) = struct.unpack_from("<i", head, pos)
        pos += 4
        info.update(mount_point=mount, entries=entries)
        if info["version"] >= 10:
            (seed,) = struct.unpack_from("<Q", head, pos)
            pos += 8
            (has_phi,) = struct.unpack_from("<i", head, pos)
            pos += 4
            if has_phi:
                pos += 8 + 8 + 20
            (has_fdi,) = struct.unpack_from("<i", head, pos)
            pos += 4
            if has_fdi:
                fdi_offset, fdi_size = struct.unpack_from("<QQ", head, pos)
                info.update(full_directory_index=True, fdi_offset=fdi_offset, fdi_size=fdi_size)
            else:
                info["full_directory_index"] = False
            info["path_hash_seed"] = seed
    except (OSError, struct.error):
        info["index_error"] = "index header unreadable"
    return info


def list_pak(path: Path) -> list[str]:
    """Every packed path of a plain v10-v12 pak from its full directory index (mount point + dir + file),
    the way repak prints them (the ``../../../`` prefix of the mount point stripped)."""
    info = pak_info(path)
    if info is None:
        raise GsError(f"{path.name}: not a pak (no footer)")
    if info["encrypted_index"]:
        raise GsError(f"{path.name}: the pak index is encrypted (rule 4: published key only)", 3)
    if info["version"] < 10 or not info.get("full_directory_index"):
        raise GsError(f"{path.name}: pak v{info['version']} has no full directory index; list it with repak")
    with open(path, "rb") as f:
        f.seek(info["fdi_offset"])
        buf = f.read(info["fdi_size"])
    mount = info.get("mount_point") or ""
    mount = re.sub(r"^(\.\./)+", "", mount.replace("\\", "/"))
    (dirs,) = struct.unpack_from("<i", buf, 0)
    pos = 4
    out: list[str] = []
    for _ in range(dirs):
        dname, pos = _fstring(buf, pos)
        (files,) = struct.unpack_from("<i", buf, pos)
        pos += 4
        for _ in range(files):
            fname, pos = _fstring(buf, pos)
            pos += 4  # entry location
            out.append((mount + dname.lstrip("/") + fname).lstrip("/"))
    return sorted(out)


# --- IoStore -------------------------------------------------------------------------------------------

def utoc_header(path: Path) -> dict | None:
    """``FIoStoreTocHeader``: version, container id, flags, entry counts, key GUID."""
    try:
        with open(path, "rb") as f:
            head = f.read(UTOC_HEADER_SIZE)
    except OSError:
        return None
    if len(head) < UTOC_HEADER_SIZE or head[:16] != UTOC_MAGIC:
        return None
    version = head[16]
    (header_size, entries, blocks, block_entry_size, method_count, method_len, block_size, dir_index_size,
     partitions) = struct.unpack_from("<9I", head, 20)
    (container_id,) = struct.unpack_from("<Q", head, 56)
    guid = head[64:80].hex()
    flags = head[80]
    (partition_size,) = struct.unpack_from("<Q", head, 88)
    return {"toc_version": version, "version_name": UTOC_VERSIONS.get(version, f"v{version}"), "header_size": header_size,
            "entries": entries, "compressed_blocks": blocks, "compression_methods": method_count,
            "compression_block_size": block_size, "directory_index_size": dir_index_size, "partitions": partitions,
            "partition_size": partition_size, "container_id": f"{container_id:016x}",
            "global": container_id == GLOBAL_CONTAINER_ID, "key_guid": guid, "flags": flags,
            "flag_names": [n for bit, n in UTOC_FLAGS.items() if flags & bit],
            **{n.replace("-", "_"): bool(flags & bit) for bit, n in UTOC_FLAGS.items()}}


# --- the folder ----------------------------------------------------------------------------------------

def containers(paks: Path) -> list[dict]:
    """Every container of the folder with what the headers say: kind ``pak``, ``iostore`` or ``global``."""
    out = []
    for pak in sorted(paks.glob("*.pak")):
        info = pak_info(pak) or {}
        out.append({"name": pak.name, "kind": "pak", "size": pak.stat().st_size, "version": info.get("version"),
                    "encrypted": bool(info.get("encrypted_index")), "signed": pak.with_suffix(".sig").exists(),
                    "key_guid": info.get("key_guid"), "mount_point": info.get("mount_point"), "entries": info.get("entries"),
                    "readable": bool(info) and not info.get("encrypted_index")})
    for toc in sorted(paks.glob("*.utoc")):
        info = utoc_header(toc) or {}
        ucas = toc.with_suffix(".ucas")
        size = toc.stat().st_size + (ucas.stat().st_size if ucas.exists() else 0)
        out.append({"name": toc.name, "kind": "global" if info.get("global") else "iostore", "size": size,
                    "version": info.get("toc_version"), "encrypted": bool(info.get("encrypted")),
                    "signed": bool(info.get("signed")), "key_guid": info.get("key_guid"), "entries": info.get("entries"),
                    "container_id": info.get("container_id"), "flags": info.get("flag_names", []),
                    "readable": bool(info) and not info.get("encrypted")})
    return out


def pak_of(container: dict, paks: Path) -> Path | None:
    """The .pak that sits next to an IoStore container (UE ships a small pak per utoc), if any."""
    if container["kind"] == "pak":
        return paks / container["name"]
    p = paks / (container["name"][:-5] + ".pak")
    return p if p.exists() else None
