"""SGSV, the sample game's save (samples/sample-game/native/save.cpp): header, one record per entity,
CRC-32 of everything before it. Fully mapped: nothing is kept as raw bytes."""

from __future__ import annotations

import io
import zlib

from dissect.cstruct import cstruct

DEFINITION = """
struct Header {
    char    magic[4];       // "SGSV"
    uint16  version;        // 2
    uint16  flags;
    uint32  entity_count;
    uint64  timestamp;      // seconds since 1970
    uint32  tick;           // simulation steps (60 per second)
};

struct Entity {
    uint8   type;           // 1 player, 2 enemy
    uint8   name_len;
    char    name[name_len];
    float   x;
    float   y;
    float   z;
    int32   hp;
    int32   max_hp;
    uint32  extra;          // player: score; enemy: damage per hit
    float   timer;          // player: regeneration; enemy: attack
};
"""

cs = cstruct()
cs.load(DEFINITION)


def parse(data: bytes) -> dict:
    stream = io.BytesIO(data)
    header = cs.Header(stream)
    entities = [cs.Entity(stream) for _ in range(header.entity_count)]
    crc = cs.uint32(stream)
    return {"header": header, "entities": entities, "crc": crc, "rest": stream.read()}


def dump(parsed: dict) -> bytes:
    body = parsed["header"].dumps() + b"".join(e.dumps() for e in parsed["entities"])
    return body + cs.uint32.dumps(parsed["crc"]) + parsed["rest"]


def describe(parsed: dict) -> dict:
    body = dump(parsed)[:-4 - len(parsed["rest"])]
    player = next((e for e in parsed["entities"] if e.type == 1), None)
    return {"entities": len(parsed["entities"]), "tick": parsed["header"].tick, "player_hp": player.hp if player else None,
            "crc_ok": zlib.crc32(body) == parsed["crc"], "unmapped_bytes": len(parsed["rest"])}
