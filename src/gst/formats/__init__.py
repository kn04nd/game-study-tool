"""Binary format work for `gst fmt`: identify files by signature, measure entropy, show a bounded hexdump,
list strings, survey a folder of unknown files and diff two versions of a file. Stdlib only; readers and
round-trips (dissect.cstruct) live in `gst.formats.readers`.

Every output is bounded: hexdumps stop at 512 bytes, string lists and diffs at a limit, so a large file
never floods the conversation.
"""

from __future__ import annotations

import collections
import math
import os
import re
from pathlib import Path

MAX_HEX = 512

# (offset, bytes, kind, detail). Longer, more specific signatures first; offsets other than 0 are explicit.
SIGNATURES: list[tuple[int, bytes, str, str]] = [
    (0, b"\x89PNG\r\n\x1a\n", "png", "PNG image"),
    (0, b"SQLite format 3\x00", "sqlite", "SQLite database"),
    (0, b"-==--==--==--==-", "ue-utoc", "Unreal IoStore table of contents (.utoc)"),
    (0, b"Kaydara FBX Binary", "fbx", "FBX model (binary)"),
    (0, b"\xfd7zXZ\x00", "xz", "xz stream"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z", "7-Zip archive"),
    (0, b"Rar!\x1a\x07", "rar", "RAR archive"),
    (0, b"\xabKTX 11\xbb", "ktx", "KTX texture"),
    (0, b"\xabKTX 20\xbb", "ktx2", "KTX2 texture"),
    (0, b"UnityFS\x00", "unity-bundle", "Unity AssetBundle (UnityFS)"),
    (0, b"UnityWeb", "unity-bundle", "Unity AssetBundle (UnityWeb)"),
    (0, b"UnityRaw", "unity-bundle", "Unity AssetBundle (UnityRaw)"),
    (0, b"\x1aE\xdf\xa3", "matroska", "Matroska/WebM video"),
    (0, b"PK\x03\x04", "zip", "ZIP container (also .jar/.pk3/.love and some .pak)"),
    (0, b"PK\x05\x06", "zip", "ZIP container (empty)"),
    (0, b"\x1f\x8b", "gzip", "gzip stream"),
    (0, b"\x28\xb5\x2f\xfd", "zstd", "Zstandard frame"),
    (0, b"\x04\x22\x4d\x18", "lz4", "LZ4 frame"),
    (0, b"BZh", "bzip2", "bzip2 stream"),
    (0, b"\xff\xd8\xff", "jpeg", "JPEG image"),
    (0, b"GIF8", "gif", "GIF image"),
    (0, b"DDS ", "dds", "DirectDraw Surface texture"),
    (0, b"OggS", "ogg", "Ogg container (Vorbis/Opus)"),
    (0, b"fLaC", "flac", "FLAC audio"),
    (0, b"ID3", "mp3", "MP3 audio (ID3 tag)"),
    (0, b"FSB5", "fsb5", "FMOD sample bank (FSB5)"),
    (0, b"BKHD", "wwise-bnk", "Wwise SoundBank"),
    (0, b"AKPK", "wwise-pck", "Wwise file package"),
    (0, b"CRID", "cri-usm", "CRI USM video"),
    (0, b"AFS2", "cri-awb", "CRI AWB audio archive"),
    (0, b"@UTF", "cri-utf", "CRI @UTF table (ACB/CPK)"),
    (0, b"CPK ", "cri-cpk", "CRI CPK archive"),
    (0, b"GDPC", "godot-pck", "Godot pack"),
    (0, b"RSRC", "godot-res", "Godot binary resource (.res/.scn)"),
    (0, b"GST2", "godot-ctex", "Godot 4 compressed texture"),
    (0, b"GDSC", "godot-gdc", "compiled GDScript"),
    (0, b"GVAS", "ue-gvas", "Unreal SaveGame (GVAS)"),
    (0, b"\xc1\x83\x2a\x9e", "ue-package", "Unreal package (.uasset/.umap)"),
    (0, b"\x1bLua", "lua-bytecode", "Lua bytecode"),
    (0, b"\x1bLJ", "luajit-bytecode", "LuaJIT bytecode"),
    (0, b"\xca\xfe\xba\xbe", "java-class", "Java class file"),
    (0, b"HLB", "hashlink", "HashLink bytecode"),
    (0, b"FWS", "swf", "Flash SWF (uncompressed)"),
    (0, b"CWS", "swf", "Flash SWF (zlib)"),
    (0, b"ZWS", "swf", "Flash SWF (LZMA)"),
    (0, b"\x34\x12\xaa\x55", "vpk", "Valve VPK"),
    (0, b"VBSP", "source-bsp", "Source engine map (BSP)"),
    (0, b"IDST", "source-mdl", "Source engine model (MDL)"),
    (0, b"VTF\x00", "vtf", "Valve texture (VTF)"),
    (0, b"BSA\x00", "bsa", "Bethesda archive (BSA)"),
    (0, b"BTDX", "ba2", "Bethesda archive (BA2)"),
    (0, b"TES4", "tes4", "Bethesda plugin (ESM/ESP)"),
    (0, b"BND4", "bnd4", "FromSoftware binder (BND4)"),
    (0, b"DCX\x00", "dcx", "FromSoftware compressed container (DCX)"),
    (0, b"BIK", "bink", "Bink video"),
    (0, b"KB2", "bink2", "Bink 2 video"),
    (0, b"MThd", "midi", "MIDI"),
    (0, b"glTF", "glb", "binary glTF"),
    (0, b"wOFF", "woff", "WOFF font"),
    (0, b"OTTO", "otf", "OpenType font"),
    (0, b"\x00\x01\x00\x00\x00", "ttf", "TrueType font"),
    (0, b"%PDF", "pdf", "PDF"),
    (0, b"\x7fELF", "elf", "ELF executable"),
    (0, b"MZ", "pe", "Windows executable or DLL (PE)"),
    (4, b"ftyp", "mp4", "MP4/MOV video"),
]
RIFF_FORMS = {b"WAVE": ("wav", "RIFF WAVE audio (also Wwise .wem)"), b"WEBP": ("webp", "WebP image"),
              b"AVI ": ("avi", "AVI video"), b"FEV ": ("fmod-bank", "FMOD Studio bank"),
              b"XWMA": ("xwma", "xWMA audio")}
IFF_FORMS = {b"GEN8": ("gamemaker-data", "GameMaker data.win (FORM/GEN8)")}


def entropy(data: bytes) -> float:
    """Shannon entropy in bits per byte (0 = constant, 8 = random)."""
    if not data:
        return 0.0
    counts = collections.Counter(data)
    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def entropy_class(value: float) -> str:
    if value < 1.0:
        return "padding/sparse"
    if value < 5.0:
        return "text or simple structure"
    if value < 7.2:
        return "structured binary"
    return "compressed or encrypted"


def _text_kind(head: bytes) -> tuple[str, str] | None:
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "text-utf16", "UTF-16 text (BOM)"
    body = head[3:] if head.startswith(b"\xef\xbb\xbf") else head
    if not body:
        return None
    printable = sum(1 for b in body if b in b"\t\r\n" or 32 <= b < 127 or b >= 0xC0) / len(body)
    if printable < 0.95:
        return None
    text = body.lstrip()
    if re.match(rb"\[[A-Za-z][\w .-]*\]\s*\r?\n", text):
        return "ini", "INI text"
    if text.startswith((b"{", b"[")):
        return "json", "JSON (or JSON-like) text"
    if text.startswith(b"<?xml") or text.startswith(b"<"):
        return "xml", "XML/markup text"
    if text.startswith(b"%YAML") or re.match(rb"[A-Za-z_][\w ]*:\s", text):
        return "yaml", "YAML or key: value text"
    if re.match(rb"\[[^\]\n]+\]\s*\n", text) or re.match(rb"[A-Za-z_][\w.]*\s*=", text):
        return "ini", "INI / key=value text"
    return "text", "plain text"


def identify_bytes(head: bytes, size: int | None = None) -> tuple[str, str]:
    """(kind, detail) from the first bytes of a file."""
    if head[:4] == b"RIFF" and len(head) >= 12:
        return RIFF_FORMS.get(head[8:12], ("riff", f"RIFF container ({head[8:12].decode('latin-1')!r})"))
    if head[:4] == b"FORM" and len(head) >= 12:
        for form, found in IFF_FORMS.items():
            if form in head[:16]:
                return found
        return "iff", "IFF FORM container"
    for offset, magic, kind, detail in SIGNATURES:
        if head[offset:offset + len(magic)] == magic:
            return kind, detail
    if head[:2] in (b"\x78\x01", b"\x78\x5e", b"\x78\x9c", b"\x78\xda"):
        return "zlib", "zlib stream (2-byte header: verify by inflating)"
    text = _text_kind(head)
    if text:
        return text
    if size is not None and size and not head.strip(b"\x00"):
        return "zeros", "all zero bytes at the start"
    return "unknown", "no known signature"


def _identify_tail(path: Path, size: int) -> tuple[str, str] | None:
    """Formats whose signature sits at the end: Unreal paks (footer), zips with data in front."""
    from gst.recon.sniff import unreal_pak_footer

    footer = unreal_pak_footer(path) if size > 64 else None
    if footer:
        enc = ", encrypted index" if footer.get("encrypted_index") else ""
        return "ue-pak", f"Unreal pak v{footer.get('version')} (footer magic){enc}"
    with open(path, "rb") as fh:
        fh.seek(max(0, size - 65_557))
        tail = fh.read()
    if b"PK\x05\x06" in tail:
        return "zip", "ZIP end record at the tail: data before the archive (embedded or self-extracting)"
    return None


def identify(path: Path, *, sample: int = 1 << 20) -> dict:
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(sample)
    kind, detail = identify_bytes(head[:4096], size)
    if kind in {"unknown", "zeros"}:
        kind, detail = _identify_tail(path, size) or (kind, detail)
    e = entropy(head)
    return {"path": str(path), "size": size, "kind": kind, "detail": detail, "magic": head[:8].hex(" "),
            "entropy": round(e, 2), "entropy_class": entropy_class(e)}


def hexdump(data: bytes, offset: int = 0, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hexes = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset + i:08x}  {hexes:<{width * 3 - 1}}  {text}")
    return "\n".join(lines)


def read_range(path: Path, offset: int, length: int) -> bytes:
    length = max(0, min(length, MAX_HEX))
    with open(path, "rb") as fh:
        if offset < 0:
            fh.seek(max(0, os.path.getsize(path) + offset))
        else:
            fh.seek(offset)
        return fh.read(length)


def entropy_map(path: Path, block: int = 4096, max_blocks: int = 4096) -> dict:
    """Entropy per block, merged into regions of the same class."""
    size = path.stat().st_size
    block = max(256, block, -(-size // max_blocks))
    regions: list[dict] = []
    values = []
    with open(path, "rb") as fh:
        offset = 0
        while True:
            chunk = fh.read(block)
            if not chunk:
                break
            e = entropy(chunk)
            values.append(e)
            cls = entropy_class(e)
            if regions and regions[-1]["class"] == cls:
                regions[-1]["end"] = offset + len(chunk)
                regions[-1]["max"] = max(regions[-1]["max"], round(e, 2))
            else:
                regions.append({"start": offset, "end": offset + len(chunk), "class": cls, "max": round(e, 2)})
            offset += len(chunk)
    bars = " ▁▂▃▄▅▆▇█"
    step = max(1, len(values) // 64)
    spark = "".join(bars[min(8, int(max(values[i:i + step]) / 8 * 8.99))] for i in range(0, len(values), step))
    return {"path": str(path), "size": size, "block": block, "overall": round(sum(values) / len(values), 2) if values else 0,
            "regions": regions[:200], "spark": spark}


def iter_strings(path: Path, *, minimum: int = 5, utf16: bool = True, chunk: int = 16 << 20):
    """Stream the ASCII (and UTF-16LE) runs of the whole file with their offsets, any size. Each chunk
    is cut where no run can continue (the carried tail starts at the last unfinished run), so a run
    that crosses a chunk boundary is reported once, whole."""
    kinds = [("ascii", re.compile(rb"[\x20-\x7e]{%d,}" % minimum), "ascii")]
    if utf16:
        kinds.append(("utf-16le", re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % minimum), "utf-16-le"))
    printable = bytes(range(0x20, 0x7F)) + b"\x00"
    with open(path, "rb") as fh:
        base, carry = 0, b""
        while True:
            block = fh.read(chunk)
            data = carry + block
            last = not block
            if last:
                cut = len(data)
            else:  # keep the printable/NUL tail for the next round (a run may continue there)
                cut = len(data.rstrip(printable))  # linear, unlike a regex anchored at the end
                if cut == 0 and len(data) > 2 * chunk:
                    cut = len(data)  # one giant run longer than two chunks: emit it in pieces
            head = data[:cut]
            found = [{"offset": base + m.start(), "encoding": label, "text": m.group().decode(codec)[:300]}
                     for label, rx, codec in kinds for m in rx.finditer(head)]
            yield from sorted(found, key=lambda s: s["offset"])
            if last:
                return
            carry = data[cut:]
            base += cut


def strings(path: Path, *, minimum: int = 5, limit: int | None = None, utf16: bool = True) -> list[dict]:
    """ASCII (and UTF-16LE) runs of the whole file (streamed), in file order; ``limit`` only when asked."""
    out = []
    for item in iter_strings(path, minimum=minimum, utf16=utf16):
        out.append(item)
        if limit is not None and len(out) >= limit:
            break
    return out


def diff(a: Path, b: Path, *, limit: int | None = None, merge: int = 8) -> dict:
    """Byte ranges that differ between two versions of a file (same layout) and the common prefix/suffix."""
    x, y = a.read_bytes(), b.read_bytes()
    prefix = 0
    while prefix < min(len(x), len(y)) and x[prefix] == y[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(x), len(y)) - prefix and x[-1 - suffix] == y[-1 - suffix]:
        suffix += 1
    ranges: list[list[int]] = []
    if len(x) == len(y):
        for i in range(prefix, len(x) - suffix):
            if x[i] != y[i]:
                if ranges and i - ranges[-1][1] <= merge:
                    ranges[-1][1] = i + 1
                else:
                    ranges.append([i, i + 1])
    out = [{"start": s, "end": e, "a": x[s:e][:64].hex(" "), "b": y[s:e][:64].hex(" ")} for s, e in ranges[:limit]]
    return {"a": str(a), "b": str(b), "size_a": len(x), "size_b": len(y), "identical": x == y,
            "common_prefix": prefix, "common_suffix": suffix, "same_size": len(x) == len(y),
            "ranges": out, "more_ranges": 0 if limit is None else max(0, len(ranges) - limit)}


def run_pattern(pattern: Path, path: Path, out: Path) -> dict:
    """An ImHex pattern (.hexpat) applied to a file, the decoded tree written as JSON (ImHex CLI, no
    "dangerous" pattern functions)."""
    import subprocess

    from gst import tools
    from gst.common import GsError

    imhex = tools.bin_path("imhex")
    if imhex is None:
        raise GsError("ImHex is not installed (gst tools install imhex)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)  # ImHex refuses an existing output file
    proc = subprocess.run([str(imhex), "--pl", "format", "-i", str(path), "-p", str(pattern), "-o", str(out), "-f", "json"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise GsError(f"ImHex pattern failed on {path.name}: {(proc.stderr or proc.stdout).strip()}")
    return {"file": str(path), "pattern": str(pattern), "json": str(out), "bytes": out.stat().st_size}


def scan(root: Path, *, limit_files: int | None = None) -> dict:
    """Survey of the files under ``root``: per extension, count, size, kinds found and mean entropy."""
    groups: dict[str, dict] = {}
    seen = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if limit_files is not None and seen >= limit_files:
                break
            seen += 1
            path = Path(dirpath) / name
            try:
                info = identify(path, sample=64 << 10)
            except OSError:
                continue
            ext = path.suffix.lower() or "(none)"
            g = groups.setdefault(ext, {"ext": ext, "files": 0, "bytes": 0, "kinds": collections.Counter(),
                                        "entropy": 0.0, "example": str(path.relative_to(root))})
            g["files"] += 1
            g["bytes"] += info["size"]
            g["kinds"][info["kind"]] += 1
            g["entropy"] += info["entropy"]
    rows = []
    for g in sorted(groups.values(), key=lambda g: -g["bytes"]):
        rows.append({**g, "kinds": dict(g["kinds"].most_common(4)), "entropy": round(g["entropy"] / g["files"], 2)})
    return {"root": str(root), "files": seen, "truncated": limit_files is not None and seen >= limit_files, "groups": rows}
