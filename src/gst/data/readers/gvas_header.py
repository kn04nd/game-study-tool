"""Unreal Engine SaveGame (GVAS) container, from the engine's public source (SaveGameSystem): header,
engine version, custom versions and save class; the property stream that follows stays raw bytes.

String lengths count the terminating NUL; a negative length (UTF-16 text) is not handled here.
"""

from __future__ import annotations

import io

from dissect.cstruct import cstruct

DEFINITION = """
struct Start {
    char    magic[4];           // "GVAS"
    int32   save_version;       // 2 (UE4), 3 adds a UE5 package version
    int32   ue4_version;
};

struct EngineVersion {
    uint16  major;
    uint16  minor;
    uint16  patch;
    uint32  changelist;
    int32   branch_len;
    char    branch[branch_len];
};

struct CustomVersion {
    char    guid[16];
    int32   version;
};

struct CustomVersions {
    int32   format;
    int32   count;
    CustomVersion versions[count];
};

struct ClassName {
    int32   length;
    char    name[length];
};
"""

cs = cstruct()
cs.load(DEFINITION)


def parse(data: bytes) -> dict:
    stream = io.BytesIO(data)
    start = cs.Start(stream)
    ue5 = cs.int32(stream) if start.save_version >= 3 else None
    engine = cs.EngineVersion(stream)
    custom = cs.CustomVersions(stream)
    save_class = cs.ClassName(stream)
    return {"start": start, "ue5": ue5, "engine": engine, "custom": custom, "class": save_class, "properties": stream.read()}


def dump(parsed: dict) -> bytes:
    ue5 = cs.int32.dumps(parsed["ue5"]) if parsed["ue5"] is not None else b""
    return (parsed["start"].dumps() + ue5 + parsed["engine"].dumps() + parsed["custom"].dumps() + parsed["class"].dumps()
            + parsed["properties"])


def describe(parsed: dict) -> dict:
    e = parsed["engine"]
    return {"engine": f"{e.major}.{e.minor}.{e.patch}", "branch": e.branch.rstrip(b"\x00").decode("latin-1"),
            "save_class": parsed["class"].name.rstrip(b"\x00").decode("latin-1"),
            "custom_versions": parsed["custom"].count, "property_bytes": len(parsed["properties"])}
