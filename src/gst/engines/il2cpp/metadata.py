"""IL2CPP ``global-metadata.dat`` header reader (stdlib only) and ``GameAssembly`` basics (pefile).

What it reads, without loading the whole file:

- magic (``0xFAB11BAF``) and version (24 ... 31, 35, 38, 39, 104+). A file whose magic is wrong is reported as
  ``standard: false`` (encrypted or obfuscated metadata): gst never tries to decrypt it.
- the section table. Two layouts exist: ``(offset, size)`` pairs up to v31 and ``(offset, size, count)`` triples
  from v38 (Unity 6000.2+; the counts are exact). The layout is found from the header itself (the first section
  starts right after the header), so an unknown version still gets its sections, with generic names.
- counts (images, types, methods, fields, strings...): exact from the v38+ triples; for older versions from the
  record sizes of each version (``count_exact: false`` when estimated); types are summed from the images.
- image (assembly) names, through the string heap.
- sub-version hints for v24 (24.0/24.1 vs 24.2+, from the header and image record sizes).

``GameAssembly.dll`` (or any IL2CPP binary): size, machine, image base, timestamp, sections and the count of
``il2cpp_*`` exports, through pefile. CodeRegistration/MetadataRegistration are taken from the tools' logs
(Cpp2IL and Il2CppInspector print them), not searched here.

Output: ``decomp/il2cpp/metadata.json`` (:func:`write`).
"""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path

MAGIC = 0xFAB11BAF
OUT_JSON = Path("decomp") / "il2cpp" / "metadata.json"

# Section names per header layout. Pairs (offset, size): Il2CppGlobalMetadataHeader of v24..v31 (Il2CppDumper's
# MetadataHeader, libil2cpp). Triples (offset, size, count): v38/v39 (Unity 6000.2/6000.3).
_V24_HEAD = ["stringLiterals", "stringLiteralData", "strings", "events", "properties", "methods",
             "parameterDefaultValues", "fieldDefaultValues", "fieldAndParameterDefaultValueData", "fieldMarshaledSizes",
             "parameters", "fields", "genericParameters", "genericParameterConstraints", "genericContainers", "nestedTypes",
             "interfaces", "vtableMethods", "interfaceOffsets", "typeDefinitions"]
_TAIL_OLD = ["unresolvedVirtualCallParameterTypes", "unresolvedVirtualCallParameterRanges", "windowsRuntimeTypeNames"]
LAYOUTS: dict[str, list[str]] = {
    # v24.0/24.1: rgctxEntries still in the metadata
    "24.0": _V24_HEAD + ["rgctxEntries", "images", "assemblies", "metadataUsageLists", "metadataUsagePairs", "fieldRefs",
                         "referencedAssemblies", "attributesInfo", "attributeTypes"] + _TAIL_OLD + ["exportedTypeDefinitions"],
    "24.2": _V24_HEAD + ["images", "assemblies", "metadataUsageLists", "metadataUsagePairs", "fieldRefs",
                         "referencedAssemblies", "attributesInfo", "attributeTypes"] + _TAIL_OLD + ["exportedTypeDefinitions"],
    "27": _V24_HEAD + ["images", "assemblies", "fieldRefs", "referencedAssemblies", "attributesInfo", "attributeTypes"]
    + _TAIL_OLD + ["windowsRuntimeStrings", "exportedTypeDefinitions"],
    "29": _V24_HEAD + ["images", "assemblies", "fieldRefs", "referencedAssemblies", "attributeData", "attributeDataRanges"]
    + _TAIL_OLD + ["windowsRuntimeStrings", "exportedTypeDefinitions"],
    "38": _V24_HEAD + ["images", "assemblies", "fieldRefs", "referencedAssemblies", "attributeData", "attributeDataRanges",
                       "unresolvedIndirectCallParameterTypes", "unresolvedIndirectCallParameterRanges",
                       "windowsRuntimeTypeNames", "windowsRuntimeStrings", "exportedTypeDefinitions"],
}

# Record sizes for the pair layouts (bytes per entry); counts derived from them are estimates.
_RECORDS_OLD = {"stringLiterals": 8, "events": 24, "properties": 20, "parameterDefaultValues": 12, "fieldDefaultValues": 12,
                "fieldMarshaledSizes": 12, "fields": 12, "genericParameterConstraints": 4, "nestedTypes": 4,
                "interfaces": 4, "vtableMethods": 4, "interfaceOffsets": 8, "fieldRefs": 8, "referencedAssemblies": 4,
                "exportedTypeDefinitions": 4}
# Il2CppMethodDefinition: 56 bytes in 24.0/24.1 (with the invoker/rgctx indices), 32 up to v29, 36 in v31
# (returnParameterToken). Il2CppParameterDefinition 12 (16 before v24.?: nameIndex, token, customAttribute, type).
_METHOD_SIZE = {"24.0": 56, "24.2": 32, "27": 32, "29": 32, "31": 36}
_IMAGE_SIZE = {"24.0": 32, "24.1": 40}  # 24.1+ adds customAttributeStart/Count: 40 bytes up to v31


class MetadataError(ValueError):
    pass


def _i32(buf: bytes, at: int) -> int:
    return struct.unpack_from("<i", buf, at)[0]


def _u32(buf: bytes, at: int) -> int:
    return struct.unpack_from("<I", buf, at)[0]


def _family(version: int) -> str:
    if version >= 38:
        return "38"
    if version >= 29:
        return "29"
    if version >= 27:
        return "27"
    return "24.2"


def read_header(path: str | os.PathLike) -> dict:
    """Magic, version, layout and section table of a global-metadata.dat (reads the header only)."""
    path = Path(path)
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(8)
        if len(head) < 8:
            raise MetadataError(f"{path.name}: {size} bytes, too short for an IL2CPP metadata header")
        magic, version = _u32(head, 0), _i32(head, 4)
        info: dict = {"file": str(path), "size": size, "magic": f"{magic:08x}", "magic_bytes": head[:4].hex(),
                      "version": version, "standard": magic == MAGIC}
        if magic != MAGIC:
            info["reason"] = ("magic is not FAB11BAF: encrypted, obfuscated or not an IL2CPP metadata file "
                              "(gst does not decrypt it; guardrails.md rule 4)")
            return info
        first = struct.unpack("<i", fh.read(4))[0]
        if first <= 8 or first > min(size, 1 << 16):
            raise MetadataError(f"{path.name}: first section offset {first} is not a header size")
        fh.seek(0)
        header = fh.read(first)
    stride = None
    for candidate in (12, 8) if version >= 35 else (8, 12):
        if (first - 8) % candidate:
            continue
        n = (first - 8) // candidate
        ok = all(0 <= _i32(header, 8 + i * candidate) <= size
                 and 0 <= _i32(header, 12 + i * candidate) <= size - _i32(header, 8 + i * candidate)
                 for i in range(n))
        if ok:
            stride = candidate
            break
    if stride is None:
        raise MetadataError(f"{path.name}: header of {first} bytes matches neither (offset, size) pairs nor "
                            "(offset, size, count) triples")
    n = (first - 8) // stride
    names = _names_for(version, stride, n)
    sections = []
    for i in range(n):
        at = 8 + i * stride
        sec = {"name": names[i] if names else f"section_{i}", "offset": _i32(header, at), "size": _i32(header, at + 4)}
        if stride == 12:
            sec["count"] = _i32(header, at + 8)
            sec["count_exact"] = True
        sections.append(sec)
    info.update(header_size=first, layout="offset-size-count" if stride == 12 else "offset-size", sections=sections,
                section_names_known=bool(names))
    info["subversion"] = _subversion(version, n, sections)
    return info


def _names_for(version: int, stride: int, n: int) -> list[str] | None:
    if stride == 12:
        names = LAYOUTS["38"]
        return names if len(names) == n else None
    if version == 24:
        for key in ("24.0", "24.2"):
            if len(LAYOUTS[key]) == n:
                return LAYOUTS[key]
        return None
    names = LAYOUTS[_family(version)]
    return names if len(names) == n else None


def _subversion(version: int, n: int, sections: list[dict]) -> dict:
    """Best-effort sub-version (Il2CppDumper/Cpp2IL name them 24.1, 24.2...): only what the metadata alone shows."""
    if version != 24:
        return {"version": str(version), "candidates": [str(version)], "how": "header version"}
    if n == len(LAYOUTS["24.0"]):
        images = next((s for s in sections if s["name"] == "images"), None)
        count_32 = images and images["size"] % _IMAGE_SIZE["24.0"] == 0
        count_40 = images and images["size"] % _IMAGE_SIZE["24.1"] == 0
        if count_40 and not count_32:
            return {"version": "24.1", "candidates": ["24.1"], "how": "rgctxEntries in the header, 40-byte images"}
        if count_32 and not count_40:
            return {"version": "24.0", "candidates": ["24.0"], "how": "rgctxEntries in the header, 32-byte images"}
        return {"version": "24.1", "candidates": ["24.0", "24.1"], "how": "rgctxEntries in the header; image size ambiguous"}
    return {"version": "24.2", "candidates": ["24.2", "24.3", "24.4", "24.5"],
            "how": "no rgctxEntries in the header; 24.3+ differ only in the binary (CodeRegistration)"}


def section(info: dict, name: str) -> dict | None:
    return next((s for s in info.get("sections", []) if s["name"] == name), None)


def _record_size(info: dict, name: str) -> int | None:
    sec = section(info, name)
    if sec is None:
        return None
    if sec.get("count"):
        return sec["size"] // sec["count"] if sec["count"] else None
    sub = info.get("subversion", {}).get("version", "")
    if name == "images":
        return _IMAGE_SIZE["24.0"] if sub == "24.0" else _IMAGE_SIZE["24.1"]
    if name == "methods":
        key = "31" if info["version"] >= 31 else ("24.0" if sub in ("24.0", "24.1") else _family(info["version"]))
        return _METHOD_SIZE.get(key)
    if name == "parameters":
        return 12
    return _RECORDS_OLD.get(name)


def _count(info: dict, name: str) -> tuple[int | None, bool]:
    sec = section(info, name)
    if sec is None:
        return None, False
    if "count" in sec:
        return sec["count"], True
    rec = _record_size(info, name)
    return (sec["size"] // rec, False) if rec else (None, False)


def _cstr(blob: bytes, at: int) -> str:
    end = blob.find(b"\0", at)
    return blob[at:end if end >= 0 else len(blob)].decode("utf-8", errors="replace")


def images(path: str | os.PathLike, info: dict) -> list[dict]:
    """Image (assembly) names with their type ranges: ``[{index, name, type_start, type_count}]``."""
    img, strings = section(info, "images"), section(info, "strings")
    if not img or not strings or not img["size"]:
        return []
    rec = _record_size(info, "images")
    if not rec:
        return []
    with open(path, "rb") as fh:
        fh.seek(img["offset"])
        table = fh.read(img["size"])
        fh.seek(strings["offset"])
        heap = fh.read(strings["size"])
    # nameIndex (4), assemblyIndex (4), typeStart, typeCount (4), exportedTypeStart, exportedTypeCount (4), entryPoint,
    # token, customAttributeStart, customAttributeCount. typeStart/exportedTypeStart are TypeDefinitionIndex: 4 bytes up
    # to v31; from v38 their width depends on the number of types (1, 2 or 4), so the 32 fixed bytes + 2 x width.
    width = 4
    if info.get("layout") == "offset-size-count" and (rec - 32) // 2 in (1, 2, 4) and (rec - 32) % 2 == 0:
        width = (rec - 32) // 2
    fmt = {1: "<B", 2: "<H", 4: "<i"}[width]
    out = []
    for i in range(len(table) // rec):
        at = i * rec
        name_index = _i32(table, at)
        name = _cstr(heap, name_index) if 0 <= name_index < len(heap) else f"<bad string index {name_index}>"
        row = {"index": i, "name": name}
        if rec >= 12 + 2 * width:
            row["type_start"] = struct.unpack_from(fmt, table, at + 8)[0]
            row["type_count"] = _i32(table, at + 8 + width)
        out.append(row)
    return out


def _count_strings(path: str | os.PathLike, sec: dict | None) -> int | None:
    if not sec:
        return None
    total = 0
    with open(path, "rb") as fh:
        fh.seek(sec["offset"])
        left = sec["size"]
        while left > 0:
            chunk = fh.read(min(left, 1 << 22))
            if not chunk:
                break
            total += chunk.count(b"\0")
            left -= len(chunk)
    return total


def summarize(path: str | os.PathLike) -> dict:
    """Header + counts + image names (the content of ``metadata.json``'s ``metadata`` key)."""
    info = read_header(path)
    if not info["standard"]:
        return info
    imgs = images(path, info)
    counts: dict[str, dict] = {}
    for key, name in (("images", "images"), ("types", "typeDefinitions"), ("methods", "methods"), ("fields", "fields"),
                      ("properties", "properties"), ("events", "events"), ("parameters", "parameters"),
                      ("string_literals", "stringLiterals"), ("assemblies", "assemblies"),
                      ("generic_containers", "genericContainers"), ("nested_types", "nestedTypes")):
        value, exact = _count(info, name)
        if value is not None:
            counts[key] = {"count": value, "exact": exact}
    if imgs:
        counts["images"] = {"count": len(imgs), "exact": counts.get("images", {}).get("exact", False)}
        if "type_count" in imgs[0] and not counts.get("types", {}).get("exact"):
            counts["types"] = {"count": sum(max(0, i["type_count"]) for i in imgs), "exact": True, "from": "images"}
    if "string_literals" in counts and not counts["string_literals"]["exact"]:
        counts["string_literals"]["note"] = "8-byte records assumed (length, dataIndex)"
    strings = _count_strings(path, section(info, "strings"))
    if strings is not None:
        counts["strings"] = {"count": strings, "exact": True, "from": "NUL-terminated string heap"}
    info["counts"] = counts
    info["images"] = imgs
    return info


def find_inputs(install: str | os.PathLike) -> list[dict]:
    """IL2CPP inputs of an install: ``[{binary, metadata, data_dir}]`` (paths relative to the install, ``/``)."""
    install = Path(install)
    found = []
    binaries = [p for p in (install / "GameAssembly.dll", install / "GameAssembly.so") if p.is_file()]
    for meta in sorted(install.glob("*_Data/il2cpp_data/Metadata/global-metadata.dat")):
        data_dir = meta.parents[2]
        binary = binaries[0] if binaries else None
        found.append({"metadata": meta.relative_to(install).as_posix(),
                      "binary": binary.relative_to(install).as_posix() if binary else None,
                      "data_dir": data_dir.relative_to(install).as_posix()})
    return found


def binary_info(path: str | os.PathLike) -> dict:
    """Size, machine, image base, timestamp, sections and IL2CPP API exports of a PE (pefile)."""
    path = Path(path)
    out: dict = {"file": str(path), "size": path.stat().st_size}
    try:
        import pefile
    except ImportError as exc:  # core dependency; keep the metadata part working anyway
        out["error"] = f"pefile not available: {exc}"
        return out
    try:
        pe = pefile.PE(str(path), fast_load=True)
    except pefile.PEFormatError as exc:
        out["error"] = f"not a PE file: {exc}"
        return out
    try:
        fh = pe.FILE_HEADER
        oh = pe.OPTIONAL_HEADER
        out.update(format="PE32+" if oh.Magic == 0x20B else "PE32",
                   machine=pefile.MACHINE_TYPE.get(fh.Machine, hex(fh.Machine)),
                   image_base=hex(oh.ImageBase), timestamp=fh.TimeDateStamp,
                   timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(fh.TimeDateStamp)),
                   entry_point=hex(oh.AddressOfEntryPoint),
                   sections=[{"name": s.Name.rstrip(b"\0").decode("ascii", "replace"), "va": hex(s.VirtualAddress),
                              "vsize": s.Misc_VirtualSize, "raw_size": s.SizeOfRawData,
                              "characteristics": hex(s.Characteristics)} for s in pe.sections])
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
        exports = [e.name.decode("ascii", "replace") for e in getattr(getattr(pe, "DIRECTORY_ENTRY_EXPORT", None), "symbols", [])
                   if e.name]
        out["exports"] = len(exports)
        out["il2cpp_exports"] = sum(1 for e in exports if e.startswith("il2cpp_"))
    finally:
        pe.close()
    return out


def write(ws: Path, data: dict) -> Path:
    from gst.common import write_json

    path = Path(ws) / OUT_JSON
    tmp = path.with_name(f"{path.name}.partial-{os.getpid()}")
    write_json(tmp, data)
    os.replace(tmp, path)
    return path


def load(ws: Path) -> dict | None:
    from gst.common import read_json

    data = read_json(Path(ws) / OUT_JSON, None)
    return data if isinstance(data, dict) else None


def version_key(info: dict) -> str:
    """The version used to pick tools: the sub-version for v24 (``24.2``), else the major (``39``)."""
    sub = (info.get("subversion") or {}).get("version")
    return str(sub or info.get("version"))
