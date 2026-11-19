"""Read-only binary sniffers: PE metadata, engine version probes, container headers.

pe_info/grep_file/unity_version/godot_pck are adapted from universal-modder's um/scan.py (MIT);
PE sections/imports/CodeView, pak/utoc/GDPC flags and script-bytecode headers are new.
"""

from __future__ import annotations

import os
import re
import struct
import time
from pathlib import Path

PROTECTOR_SECTIONS = {
    ".bind": ("drm-wrapper", "Steam DRM (SteamStub)"),
    ".vmp0": ("anti-tamper", "VMProtect"), ".vmp1": ("anti-tamper", "VMProtect"), ".vmp2": ("anti-tamper", "VMProtect"),
    ".themida": ("anti-tamper", "Themida/WinLicense"), ".winlice": ("anti-tamper", "Themida/WinLicense"),
    ".vm_sec": ("anti-tamper", "Themida/WinLicense"),
    ".enigma1": ("anti-tamper", "Enigma Protector"), ".enigma2": ("anti-tamper", "Enigma Protector"),
    ".arch": ("anti-tamper", "Denuvo (heuristic: .arch section)"), ".xtls": ("anti-tamper", "Denuvo (heuristic: .xtls section)"),
}


def pe_quick(path: Path) -> dict | None:
    """Machine + managed flag from the first 4 KB (fast, no pefile)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    if head[:2] != b"MZ" or len(head) < 0x40:
        return None
    off = struct.unpack_from("<I", head, 0x3C)[0]
    if off + 0x100 > len(head) or head[off:off + 4] != b"PE\0\0":
        return None
    machine = struct.unpack_from("<H", head, off + 4)[0]
    opt = off + 24
    magic = struct.unpack_from("<H", head, opt)[0]
    dd = opt + (96 if magic == 0x10B else 112)
    clr_rva = struct.unpack_from("<I", head, dd + 14 * 8)[0] if dd + 15 * 8 <= len(head) else 0
    return {"arch": {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(machine, hex(machine)), "managed": clr_rva != 0}


def pe_detail(path: Path) -> dict | None:
    """Sections, imports (DLL names), version strings, CodeView PDB name and protector hints (pefile)."""
    try:
        import pefile
    except ImportError:  # pragma: no cover
        return pe_quick(path)
    try:
        pe = pefile.PE(str(path), fast_load=True)
    except (pefile.PEFormatError, OSError):
        return None
    try:
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DEBUG"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"],
        ])
    except Exception:  # pefile raises many types on odd binaries
        pass
    sections = []
    for s in pe.sections:
        name = s.Name.rstrip(b"\x00").decode("latin-1", errors="replace")
        sections.append({"name": name, "size": s.SizeOfRawData, "entropy": round(s.get_entropy(), 2)})
    imports = sorted({e.dll.decode("latin-1", errors="replace").lower() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])})
    pdb = None
    for dbg in getattr(pe, "DIRECTORY_ENTRY_DEBUG", []):
        entry = getattr(dbg, "entry", None)
        if entry is not None and hasattr(entry, "PdbFileName"):
            pdb = entry.PdbFileName.rstrip(b"\x00").decode("latin-1", errors="replace")
            break
    version = {}
    for fileinfo in getattr(pe, "FileInfo", []) or []:
        for info in fileinfo if isinstance(fileinfo, list) else [fileinfo]:
            for table in getattr(info, "StringTable", []) or []:
                for k, v in table.entries.items():
                    version[k.decode("utf-8", errors="replace")] = v.decode("utf-8", errors="replace")
    names = [s["name"].lower() for s in sections]
    hints = []
    for n in names:
        if n in PROTECTOR_SECTIONS:
            hints.append(PROTECTOR_SECTIONS[n])
    blank = sum(1 for n in names if not n.strip())
    if blank >= 3:
        hints.append(("anti-tamper", "packed/virtualised (several unnamed sections)"))
    if names.count(".text") >= 2:
        hints.append(("anti-tamper", "Arxan/GuardIT (heuristic: duplicate .text sections)"))
    quick = pe_quick(path) or {}
    machine = quick.get("arch")
    pe.close()
    return {
        "arch": machine, "managed": quick.get("managed", False), "sections": sections, "imports": imports,
        "pdb": pdb, "version": {k: v for k, v in version.items() if k in
                                {"CompanyName", "ProductName", "FileVersion", "ProductVersion", "FileDescription", "OriginalFilename"}},
        "protector_hints": sorted(set(hints)),
    }


def pe_fixed_version(path: Path) -> str | None:
    """File version (a.b.c.d) from VS_FIXEDFILEINFO; Unreal stamps the engine version there by default."""
    try:
        import pefile
    except ImportError:  # pragma: no cover
        return None
    try:
        pe = pefile.PE(str(path), fast_load=True)
    except (pefile.PEFormatError, OSError):
        return None
    try:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        fixed = (getattr(pe, "VS_FIXEDFILEINFO", None) or [None])[0]
        if fixed is None:
            return None
        ms, ls = fixed.FileVersionMS, fixed.FileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:  # pefile raises many types on odd binaries
        return None
    finally:
        pe.close()


def grep_file(path: Path, patterns: list[bytes], limit_mb: int = 400, budget_s: float = 6.0) -> dict[bytes, bytes]:
    """First match of each regex in a (big) binary, read in chunks, bounded by size and time."""
    found: dict[bytes, bytes] = {}
    t0 = time.time()
    tail = b""
    try:
        with open(path, "rb") as f:
            read = 0
            while len(found) < len(patterns) and read < limit_mb << 20 and time.time() - t0 < budget_s:
                chunk = f.read(16 << 20)
                if not chunk:
                    break
                read += len(chunk)
                buf = tail + chunk
                for p in patterns:
                    if p not in found:
                        m = re.search(p, buf)
                        if m:
                            found[p] = m.group(0)
                tail = buf[-256:]
    except OSError:
        pass
    return found


def unity_version_from(path: Path) -> str | None:
    try:
        with open(path, "rb") as f:
            head = f.read(1 << 16)
    except OSError:
        return None
    m = re.search(rb"(?:20\d\d|6000|[45])\.\d+\.\d+[abfpx]\d+", head)
    return m.group(0).decode() if m else None


def godot_pck(path: Path) -> dict | None:
    """GDPC header of a .pck or of a pck appended to the exe: version, format, encrypted dir flag."""
    def parse(head: bytes, where: str) -> dict:
        fmt, major, minor, patch = struct.unpack_from("<4I", head, 4)
        flags = struct.unpack_from("<I", head, 20)[0] if fmt >= 2 and len(head) >= 24 else 0
        return {"version": f"{major}.{minor}.{patch}", "pack_format": fmt, "flags": flags,
                "dir_encrypted": bool(flags & 1), "where": where}
    try:
        with open(path, "rb") as f:
            head = f.read(24)
            if head[:4] == b"GDPC":
                return parse(head, "pck")
            f.seek(-12, os.SEEK_END)
            tail = f.read(12)
            if tail[8:] == b"GDPC":
                size = struct.unpack_from("<Q", tail, 0)[0]
                f.seek(-12 - size, os.SEEK_END)
                head = f.read(24)
                if head[:4] == b"GDPC":
                    return parse(head, "embedded in exe")
    except (OSError, struct.error):
        pass
    return None


UE_PAK_MAGIC = 0x5A6F12E1


def unreal_pak_footer(path: Path) -> dict | None:
    """Pak footer: version, encrypted index, key GUID (searches the last 1 KB for the magic)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 1024))
            tail = f.read()
    except OSError:
        return None
    pos = tail.rfind(struct.pack("<I", UE_PAK_MAGIC))
    if pos < 0:
        return None
    version = struct.unpack_from("<I", tail, pos + 4)[0]
    encrypted = None
    guid = None
    # v7+: [GUID 16][bEncryptedIndex 1][magic 4][version 4]...
    if pos >= 17:
        guid = tail[pos - 17:pos - 1].hex()
        encrypted = tail[pos - 1] == 1
    return {"version": version, "encrypted_index": encrypted, "key_guid": guid,
            "default_key": guid == "0" * 32 if guid else None}


UTOC_MAGIC = b"-==--==--==--==-"


def unreal_utoc(path: Path) -> dict | None:
    """IoStore .utoc header: TOC version and container flags (Compressed/Encrypted/Signed/Indexed)."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x90)
    except OSError:
        return None
    if head[:16] != UTOC_MAGIC or len(head) < 0x51:
        return None
    flags = head[0x50]
    return {"toc_version": head[16], "flags": flags, "compressed": bool(flags & 1), "encrypted": bool(flags & 2),
            "signed": bool(flags & 4), "indexed": bool(flags & 8)}


def gamemaker_data(path: Path) -> dict | None:
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return None
    if head[:4] == b"FORM":
        info = {"form": True}
        if head[8:12] == b"GEN8" and len(head) > 17:
            info["bytecode_version"] = head[17]
        return info
    return {"form": False}


def script_bytecode(path: Path) -> dict | None:
    """Identify Lua/LuaJIT/Java/Python bytecode by header bytes."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if head[:4] == b"\x1bLua" and len(head) > 6:
        return {"kind": "lua", "version": f"{head[4] >> 4}.{head[4] & 0xF}", "big_endian": head[6] == 0}
    if head[:3] == b"\x1bLJ":
        return {"kind": "luajit", "version": head[3]}
    if head[:4] == b"\xca\xfe\xba\xbe":
        return {"kind": "java-class", "major": struct.unpack(">H", head[6:8])[0]}
    if head[:3] == b"HLB":
        return {"kind": "hashlink", "version": head[3]}
    if head[:3] in (b"FWS", b"CWS", b"ZWS"):
        return {"kind": "swf", "compression": head[:3].decode()}
    if head[:4] == b"RPA-":
        return {"kind": "renpy-rpa", "header": head[:7].decode(errors="replace")}
    if head[:7] == b"RGSSAD\x00":
        return {"kind": "rgssad", "version": head[7]}
    return None


def java_release(path: Path) -> dict | None:
    """Bundled JRE 'release' file -> vendor/version."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    out = {}
    for key in ("JAVA_VERSION", "IMPLEMENTOR", "IMPLEMENTOR_VERSION"):
        m = re.search(rf'^{key}="?([^"\n]+)"?', text, re.M)
        if m:
            out[key.lower()] = m.group(1)
    return out or None
