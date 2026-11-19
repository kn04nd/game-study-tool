"""Reader of Godot binary resources (``.scn``, ``.res`` and the imported ``.scn``/``.res``/``.mesh``/``.anim`` under
``.godot/imported`` or ``.import``), Godot 3.x and 4.x. Stdlib only. Never decrypts anything: encrypted files are
not RSRC and are only reported by the callers.

Layout (port of ``core/io/resource_format_binary.cpp``, Godot Engine, MIT; see THIRD_PARTY_NOTICES)::

    "RSRC" | "RSCC" (compressed: u32 mode, u32 block size, u32 total, u32 csize per block, blocks; see codecs)
    u32 big_endian, u32 use_real64, u32 ver_major, u32 ver_minor, u32 ver_format (3 = Godot 3.x, 4/5/6 = Godot 4.x)
    string type, u64 import metadata offset, u32 flags (4.x: 1 named scene ids, 2 UIDs, 4 real_t is double,
    8 script class), u64 uid, [string script class], reserved u32s (14 words in all from flags on)
    u32 n + n strings (string table), u32 n + n ext resources (type, path, [u64 uid]),
    u32 n + n internal resources (path ``local://<id>``, u64 offset); the last one is the main resource.
    At each offset: string type, u32 property count, (u32 string-table index, variant) per property.

Variants: nil, bool, int32/int64, float/double, string, StringName, Vector2/3/4(i), Rect2(i), Plane, Quaternion, AABB,
Basis, Transform2D/3D, Projection, Color, NodePath (names + subnames, absolute bit; format < 3 has a property
field), RID, Object (empty, internal index, external index, old inline external), Callable/Signal (no data),
Dictionary, Array, and every packed array (byte data padded to 4). Typed ``Array[T]``/``Dictionary[K, V]`` are
stored as plain arrays/dictionaries in this format (the type comes from the property), so they read as lists/dicts.

:func:`read` returns the same document shape as :func:`gst.engines.godot.tres.parse` (``kind``, ``header``,
``ext_resources``, ``sub_resources``, ``sub_order``, ``nodes``, ``connections``, ``editable``, ``resource``), with
values as :class:`Ctor`/:class:`Ref`/:class:`NodePath`/:class:`StringName`; a ``PackedScene`` main resource is
unpacked from its ``_bundled`` SceneState (``scene/resources/packed_scene.cpp``) into ``nodes`` and ``connections``
like a ``.tscn``. ``line`` is None (binary); ``offset`` gives the byte offset of each resource. Extra key ``binary``:
versions, endianness, compression, flags. ``bulk=N`` leaves packed float/vector/byte arrays of N bytes or more
undecoded (a :class:`Bulk` with the item count; mesh buffers, keyframes): compressed blocks that hold only such
data are never decompressed.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from gst.engines.godot import codecs
from gst.engines.godot.tres import Bulk, Ctor, NodePath, Ref, StringName, key_str


class RsrcError(ValueError):
    pass


# variant ids (resource_format_binary.cpp)
V_NIL, V_BOOL, V_INT, V_FLOAT, V_STRING = 1, 2, 3, 4, 5
V_NODE_PATH, V_RID, V_OBJECT, V_INPUT_EVENT, V_DICTIONARY, V_ARRAY = 22, 23, 24, 25, 26, 30
V_INT64, V_DOUBLE, V_CALLABLE, V_SIGNAL, V_STRING_NAME = 40, 41, 42, 43, 44
V_IMAGE = 21  # Godot 2 only
# id -> (Godot 4 name, Godot 3 name, element: "r" real / "i" int32 / "f" float32, count)
MATH = {10: ("Vector2", "Vector2", "r", 2), 11: ("Rect2", "Rect2", "r", 4), 12: ("Vector3", "Vector3", "r", 3),
        13: ("Plane", "Plane", "r", 4), 14: ("Quaternion", "Quat", "r", 4), 15: ("AABB", "AABB", "r", 6),
        16: ("Basis", "Basis", "r", 9), 17: ("Transform3D", "Transform", "r", 12),
        18: ("Transform2D", "Transform2D", "r", 6), 20: ("Color", "Color", "f", 4),
        45: ("Vector2i", None, "i", 2), 46: ("Rect2i", None, "i", 4), 47: ("Vector3i", None, "i", 3),
        50: ("Vector4", None, "r", 4), 51: ("Vector4i", None, "i", 4), 52: ("Projection", None, "r", 16)}
# packed arrays: id -> (Godot 4 name, Godot 3 name, element, values per item)
PACKED = {31: ("PackedByteArray", "PoolByteArray", "B", 1), 32: ("PackedInt32Array", "PoolIntArray", "i", 1),
          33: ("PackedFloat32Array", "PoolRealArray", "f", 1), 34: ("PackedStringArray", "PoolStringArray", "s", 1),
          35: ("PackedVector3Array", "PoolVector3Array", "r", 3), 36: ("PackedColorArray", "PoolColorArray", "f", 4),
          37: ("PackedVector2Array", "PoolVector2Array", "r", 2), 48: ("PackedInt64Array", None, "q", 1),
          49: ("PackedFloat64Array", None, "d", 1), 53: ("PackedVector4Array", None, "r", 4)}
BULK_KINDS = {31, 33, 35, 36, 37, 49, 53}  # what ``bulk`` may leave undecoded (never ints/strings: scene tables)
OBJ_EMPTY, OBJ_EXTERNAL, OBJ_INTERNAL, OBJ_EXTERNAL_INDEX = 0, 1, 2, 3
FLAG_NAMED_SCENE_IDS, FLAG_UIDS, FLAG_REAL_DOUBLE, FLAG_SCRIPT_CLASS = 1, 2, 4, 8
RESERVED_FIELDS = 11
# SceneState (packed_scene.h)
NO_PARENT_SAVED = 0x7FFFFFFF
NAME_INDEX_BITS = 18
FLAG_ID_IS_PATH = 1 << 30
TYPE_INSTANTIATED = 0x7FFFFFFF
FLAG_INSTANCE_IS_PLACEHOLDER = 1 << 30
FLAG_PROP_NAME_MASK = (1 << 30) - 1
FLAG_MASK = (1 << 24) - 1
CONNECT_PERSIST = 2
UID_CHARS = "abcdefghijklmnopqrstuvwxy0123456789"[:25] + "012345678"  # ResourceUID::id_to_text: base 34


def uid_text(uid: int | None) -> str | None:
    """``ResourceUID::id_to_text``: ``uid://`` + base-34 digits ('a'-'y' then '0'-'8'); invalid (< 0) -> None."""
    if uid is None or uid < 0:
        return None
    out = []
    while True:  # do-while, as Godot: 0 -> "uid://a"
        out.append(UID_CHARS[uid % 34])
        uid //= 34
        if not uid:
            break
    return "uid://" + "".join(reversed(out))


def is_binary(head: bytes) -> bool:
    return head[:4] in (b"RSRC", b"RSCC")


# --- byte sources -------------------------------------------------------------------------------

class _Plain:
    """An uncompressed file: positions (and resource offsets) count from the start of the file, magic included."""

    def __init__(self, data: bytes):
        self.data = data
        self.length = len(data)

    def read(self, pos: int, n: int) -> bytes:
        if n < 0 or pos + n > self.length:
            raise RsrcError(f"read past the end ({pos}+{n} > {self.length})")
        return self.data[pos:pos + n]


class _Blocks:
    """``FileAccessCompressed`` content (after the RSCC magic), blocks decompressed on demand. Positions count in
    the decompressed stream, which has no magic (resource offsets are written that way)."""

    def __init__(self, data: bytes, start: int):
        if len(data) < start + 12:
            raise RsrcError("truncated RSCC header")
        self.mode, self.block, self.length = struct.unpack_from("<3I", data, start)
        if self.block == 0:
            raise RsrcError("RSCC block size 0")
        count = self.length // self.block + 1
        sizes = struct.unpack_from(f"<{count}I", data, start + 12)
        off = start + 12 + 4 * count
        self.blocks = []
        for s in sizes:
            self.blocks.append((off, s))
            off += s
        if off > len(data):
            raise RsrcError(f"RSCC blocks end at {off}, file has {len(data)} bytes")
        self.data = data
        self.cache: dict[int, bytes] = {}
        self.decoded = 0

    def _block(self, i: int) -> bytes:
        got = self.cache.get(i)
        if got is None:
            off, csize = self.blocks[i]
            size = min(self.block, self.length - i * self.block)
            try:
                got = codecs.decompress(self.mode, self.data[off:off + csize], size)
            except Exception as exc:  # CodecError, zlib.error, truncated data
                raise RsrcError(f"RSCC block {i}: {exc}") from exc
            if len(self.cache) > 64:  # keep memory bounded on big files (blocks are re-decoded if needed)
                self.cache.clear()
            self.cache[i] = got
            self.decoded += 1
        return got

    def read(self, pos: int, n: int) -> bytes:
        if n < 0 or pos + n > self.length:
            raise RsrcError(f"read past the end ({pos}+{n} > {self.length})")
        bs = self.block
        out = []
        while n > 0:
            i, o = divmod(pos, bs)
            chunk = self._block(i)[o:o + n]
            if not chunk:
                raise RsrcError("empty RSCC block")
            out.append(chunk)
            pos += len(chunk)
            n -= len(chunk)
        return b"".join(out)


# --- reader -------------------------------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes, bulk: int | None = None):
        magic = data[:4]
        if magic == b"RSRC":
            self.src: Any = _Plain(data)
            self.compression = None
            self.pos = 4
        elif magic == b"RSCC":
            self.src = _Blocks(data, 4)
            self.compression = codecs.MODES.get(self.src.mode, str(self.src.mode))
            self.pos = 0
        else:
            raise RsrcError(f"not a binary resource (magic {magic!r})")
        self.bulk = bulk
        self.e = "<"
        self.inline_ext = 0

    # primitives
    def raw(self, n: int) -> bytes:
        b = self.src.read(self.pos, n)
        self.pos += n
        return b

    def u32(self) -> int:
        return struct.unpack(self.e + "I", self.raw(4))[0]

    def i32(self) -> int:
        return struct.unpack(self.e + "i", self.raw(4))[0]

    def u16(self) -> int:
        return struct.unpack(self.e + "H", self.raw(2))[0]

    def u64(self) -> int:
        return struct.unpack(self.e + "Q", self.raw(8))[0]

    def i64(self) -> int:
        return struct.unpack(self.e + "q", self.raw(8))[0]

    def nums(self, kind: str, n: int) -> tuple:
        if kind == "r":
            kind = "d" if self.real64 else "f"
        size = struct.calcsize(kind)
        return struct.unpack(f"{self.e}{n}{kind}", self.raw(size * n))

    def ustr(self) -> str:
        n = self.u32()
        if n == 0:
            return ""
        if n > self.src.length - self.pos:
            raise RsrcError(f"string of {n} bytes past the end of the file")
        return self.raw(n).split(b"\0", 1)[0].decode("utf-8", errors="replace")

    def sname(self) -> str:
        i = self.u32()
        if i & 0x80000000:
            n = i & 0x7FFFFFFF
            return self.raw(n).split(b"\0", 1)[0].decode("utf-8", errors="replace") if n else ""
        if i >= len(self.strings):
            raise RsrcError(f"string index {i} out of the table ({len(self.strings)})")
        return self.strings[i]

    def pad(self, n: int) -> None:
        extra = 4 - (n % 4)
        if extra < 4:
            self.pos += extra

    # header and tables
    def header(self) -> dict:
        big = struct.unpack("<I", self.raw(4))[0]  # read before the endianness switch, as Godot does
        self.e = ">" if big else "<"
        use_real64 = self.u32()
        self.ver_major, self.ver_minor, self.ver_format = self.u32(), self.u32(), self.u32()
        if self.ver_format > 6 or self.ver_major > 4:
            raise RsrcError(f"format {self.ver_format} / Godot {self.ver_major}.{self.ver_minor} is newer than this reader")
        self.g3 = self.ver_major < 4
        self.type = self.ustr()
        self.importmd = self.u64()
        self.flags = self.u32()
        uid = self.i64()
        self.uid = uid if self.flags & FLAG_UIDS else None
        self.script_class = self.ustr() if self.flags & FLAG_SCRIPT_CLASS else None
        self.pos += 4 * RESERVED_FIELDS
        self.real64 = bool(self.flags & FLAG_REAL_DOUBLE)
        return {"big_endian": bool(big), "use_real64": bool(use_real64), "ver_major": self.ver_major,
                "ver_minor": self.ver_minor, "ver_format": self.ver_format, "type": self.type, "flags": self.flags,
                "uid": uid_text(self.uid), "script_class": self.script_class, "compression": self.compression,
                "bytes": self.src.length}

    def tables(self) -> None:
        self.strings = [self.ustr() for _ in range(self.u32())]
        self.ext: list[dict] = []
        for _ in range(self.u32()):
            t, p = self.ustr(), self.ustr()
            uid = self.i64() if self.flags & FLAG_UIDS else None
            self.ext.append({"type": t, "path": p, "uid": uid_text(uid)})
        self.internal = []
        for _ in range(self.u32()):
            p = self.ustr()
            self.internal.append((p, self.u64()))
        self.named = bool(self.flags & FLAG_NAMED_SCENE_IDS)

    @staticmethod
    def local_id(path: str) -> str:
        return path[len("local://"):] if path.startswith("local://") else path

    # variants
    def variant(self) -> Any:
        t = self.u32()
        if t == V_NIL:
            return None
        if t == V_BOOL:
            return bool(self.u32())
        if t == V_INT:
            return self.i32()
        if t == V_INT64:
            return self.i64()
        if t == V_FLOAT:
            return self.nums("r", 1)[0]
        if t == V_DOUBLE:
            return self.nums("d", 1)[0]
        if t == V_STRING:
            return self.ustr()
        if t == V_STRING_NAME:
            return StringName(self.ustr())
        m = MATH.get(t)
        if m is not None:
            name = m[1] if self.g3 and m[1] else m[0]
            return Ctor(name, self.nums(m[2], m[3]))
        if t == V_NODE_PATH:
            names = self.u16()
            subs = self.u16()
            absolute = bool(subs & 0x8000)
            subs &= 0x7FFF
            if self.ver_format < 3:
                subs += 1
            parts = [self.sname() for _ in range(names)]
            subnames = [self.sname() for _ in range(subs)]
            subnames = [s for s in subnames if s] if self.ver_format < 3 else subnames
            text = ("/" if absolute else "") + "/".join(parts) + "".join(":" + s for s in subnames)
            return NodePath(text)
        if t == V_RID:
            return self.u32()
        if t == V_OBJECT:
            return self.obj()
        if t in (V_CALLABLE, V_SIGNAL):
            return Ctor("Callable" if t == V_CALLABLE else "Signal", ())
        if t == V_INPUT_EVENT:
            return Ctor("InputEvent", ())
        if t == V_DICTIONARY:
            n = self.u32() & 0x7FFFFFFF
            out = {}
            for _ in range(n):
                k = self.variant()
                out[key_str(k)] = self.variant()
            return out
        if t == V_ARRAY:
            n = self.u32() & 0x7FFFFFFF
            return [self.variant() for _ in range(n)]
        p = PACKED.get(t)
        if p is not None:
            return self.packed(t, p)
        if t == V_IMAGE:
            raise RsrcError("Image variant (Godot 2 format) is not supported")
        raise RsrcError(f"unknown variant type {t} at byte {self.pos - 4}")

    def packed(self, t: int, p: tuple) -> Any:
        name = p[1] if self.g3 and p[1] else p[0]
        n = self.u32()
        kind, per = p[2], p[3]
        if kind == "s":
            return Ctor(name, tuple(self.ustr() for _ in range(n)))
        if kind == "r":
            kind = "d" if self.real64 else "f"
        size = 1 if kind == "B" else struct.calcsize(kind)
        nbytes = n * per * size
        if self.bulk is not None and t in BULK_KINDS and nbytes >= self.bulk:
            if self.pos + nbytes > self.src.length:
                raise RsrcError(f"{name} of {n} items past the end of the file")
            self.pos += nbytes
            if kind == "B":
                self.pad(n)
            return Bulk(name, n * per)
        if kind == "B":
            data = self.raw(n)
            self.pad(n)
            return Ctor(name, tuple(data))
        return Ctor(name, self.nums(kind, n * per))

    def obj(self) -> Any:
        kind = self.u32()
        if kind == OBJ_EMPTY:
            return None
        if kind == OBJ_INTERNAL:
            index = self.u32()
            if self.named:
                if index >= len(self.internal):
                    raise RsrcError(f"internal resource index {index} out of range ({len(self.internal)})")
                return Ref("sub", self.local_id(self.internal[index][0]))
            return Ref("sub", str(index))
        if kind == OBJ_EXTERNAL_INDEX:
            index = self.u32()
            return Ref("ext", str(index))
        if kind == OBJ_EXTERNAL:  # old inline form: type + path
            t, path = self.ustr(), self.ustr()
            rid = f"inline{self.inline_ext}"
            self.inline_ext += 1
            self.inline.append((rid, {"type": t, "path": path, "uid": None}))
            return Ref("ext", rid)
        raise RsrcError(f"unknown object encoding {kind}")

    def resources(self, skip_types=()) -> list[dict]:
        out = []
        self.inline: list = []
        last = len(self.internal) - 1
        for i, (path, offset) in enumerate(self.internal):
            self.pos = offset
            t = self.ustr()
            props: dict = {}
            if t in skip_types and i != last:
                out.append({"path": path, "id": self.local_id(path), "type": t, "props": props, "offset": offset,
                            "skipped": True})
                continue
            for _ in range(self.u32()):
                name = self.sname()
                if not name:
                    raise RsrcError(f"property without a name in {t} at {offset}")
                props[name] = self.variant()
            out.append({"path": path, "id": self.local_id(path), "type": t, "props": props, "offset": offset})
        return out


def _simplify(path: str) -> str:
    parts: list[str] = []
    for seg in str(path).split("/"):
        if seg in ("", "."):
            continue
        if seg == ".." and parts and parts[-1] != "..":
            parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts) or "."


def _ints(v: Any) -> list[int]:
    if isinstance(v, Ctor):
        return list(v.args)
    if isinstance(v, (list, tuple)):
        return list(v)
    return []


def unpack_scene(bundled: dict, g3: bool = False) -> dict:
    """A PackedScene ``_bundled`` dictionary -> ``{nodes, connections, editable}`` in the ``tres.parse`` shape."""
    names = [str(x) for x in _ints(bundled.get("names"))]
    variants = bundled.get("variants") or []
    node_count = int(bundled.get("node_count") or 0)
    raw = _ints(bundled.get("nodes"))
    conn_count = int(bundled.get("conn_count") or 0)
    craw = _ints(bundled.get("conns"))
    node_paths = [str(x) for x in (bundled.get("node_paths") or [])]
    editable = [_simplify(str(x)) for x in (bundled.get("editable_instances") or [])]
    base_scene = bundled.get("base_scene", -1)
    base_scene = base_scene if isinstance(base_scene, int) else -1
    ids = _ints(bundled.get("node_ids"))
    version = int(bundled.get("version") or 1)

    def name(i: int) -> str:
        if not 0 <= i < len(names):
            raise RsrcError(f"scene name index {i} out of range ({len(names)})")
        return names[i]

    def var(i: int) -> Any:
        if not 0 <= i < len(variants):
            raise RsrcError(f"scene variant index {i} out of range ({len(variants)})")
        return variants[i]

    nd = []
    idx = 0
    try:
        for _ in range(node_count):
            parent, owner, ntype, name_index, inst = raw[idx:idx + 5]
            idx += 5
            props = []
            for _p in range(raw[idx]):
                props.append((raw[idx + 1], raw[idx + 2]))
                idx += 2
            idx += 1
            groups = raw[idx + 1:idx + 1 + raw[idx]]
            idx += 1 + raw[idx]
            nd.append({"parent": parent, "owner": owner, "type": ntype, "name": name_index & ((1 << NAME_INDEX_BITS) - 1),
                       "index": (name_index >> NAME_INDEX_BITS) - 1, "instance": inst, "props": props, "groups": groups})
    except ValueError as exc:
        raise RsrcError(f"truncated scene node table: {exc}") from exc

    def is_root(p: int) -> bool:
        return p < 0 or p == NO_PARENT_SAVED

    def node_path(i: int, for_parent: bool) -> str | None:
        if is_root(nd[i]["parent"]):
            return None if for_parent else "."
        sub: list[str] = []
        base = ""
        nidx = i
        seen = set()
        while True:
            p = nd[nidx]["parent"]
            if is_root(p):
                break
            if not for_parent or nidx != i:
                sub.insert(0, name(nd[nidx]["name"]))
            if p & FLAG_ID_IS_PATH:
                base = node_paths[p & FLAG_MASK]
                break
            nidx = p & FLAG_MASK
            if nidx in seen or nidx >= len(nd):
                raise RsrcError(f"bad parent chain at scene node {i}")
            seen.add(nidx)
        return _simplify("/".join([base] + sub) if base else "/".join(sub))

    def ref_path(v: int) -> str:
        if v & FLAG_ID_IS_PATH:
            return _simplify(node_paths[v & FLAG_MASK])
        return node_path(v & FLAG_MASK, False) or "."

    nodes = []
    for i, n in enumerate(nd):
        parent = node_path(i, True)
        nm = name(n["name"])
        path = "." if parent is None else (nm if parent == "." else f"{parent}/{nm}")
        node: dict = {"name": nm, "type": None if n["type"] == TYPE_INSTANTIATED else name(n["type"]), "parent": parent,
                      "path": path, "line": None}
        inst = n["instance"]
        if inst >= 0:
            if inst & FLAG_INSTANCE_IS_PLACEHOLDER:
                node["instance_placeholder"] = var(inst & FLAG_MASK)
            else:
                node["instance"] = var(inst & FLAG_MASK)
        elif is_root(n["parent"]) and base_scene >= 0:
            node["instance"] = var(base_scene)
        if n["groups"]:
            node["groups"] = [name(g) for g in n["groups"]]
        if not is_root(n["owner"]):
            owner = ref_path(n["owner"])
            if owner != ".":
                node["owner"] = owner
        if n["index"] >= 0:
            node["index"] = n["index"]
        if i < len(ids):
            node["unique_id"] = ids[i]
        node["props"] = {name(k if g3 else k & FLAG_PROP_NAME_MASK): var(v) for k, v in n["props"]}
        nodes.append(node)

    conns = []
    idx = 0
    for _ in range(conn_count):
        frm, to, sig, meth, flags, nb = craw[idx:idx + 6]
        idx += 6
        binds = [var(b) for b in craw[idx:idx + nb]]
        idx += nb
        unbinds = 0
        if version >= 3:
            unbinds = craw[idx]
            idx += 1
        c: dict = {"signal": name(sig), "from": ref_path(frm), "to": ref_path(to), "method": name(meth), "line": None}
        if flags != CONNECT_PERSIST:
            c["flags"] = flags
        if binds:
            c["binds"] = binds
        if unbinds:
            c["unbinds"] = unbinds
        conns.append(c)
    return {"nodes": nodes, "connections": conns, "editable": editable}


def read_bytes(data: bytes, *, bulk: int | None = None, skip_types=()) -> dict:
    """A whole binary resource -> the ``tres.parse`` document shape (+ ``binary``). ``skip_types``: classes of
    sub-resources whose properties are not read (``props`` empty, ``skipped`` true; e.g. ArrayMesh when only the
    scene tree matters): their bytes, and the compressed blocks holding only them, are never touched."""
    r = _Reader(data, bulk)
    try:
        info = r.header()
        r.tables()
        res = r.resources(skip_types)
    except struct.error as exc:
        raise RsrcError(f"truncated data: {exc}") from exc
    if not res:
        raise RsrcError("no internal resources")
    doc: dict = {"kind": None, "header": {"type": info["type"], "format": info["ver_format"]},
                 "ext_resources": {}, "sub_resources": {}, "sub_order": [], "nodes": [], "connections": [],
                 "editable": [], "resource": None, "binary": info}
    if info["uid"]:
        doc["header"]["uid"] = info["uid"]
    if info["script_class"]:
        doc["header"]["script_class"] = info["script_class"]
    for i, e in enumerate(r.ext):
        doc["ext_resources"][str(i)] = {**e, "line": None}
    for rid, e in r.inline:
        doc["ext_resources"][rid] = {**e, "line": None}
    main = res[-1]
    for item in res[:-1]:
        doc["sub_resources"][item["id"]] = {"type": item["type"], "props": item["props"], "line": None,
                                            "offset": item["offset"]}
        if item.get("skipped"):
            doc["sub_resources"][item["id"]]["skipped"] = True
        doc["sub_order"].append(item["id"])
    if main["type"] == "PackedScene" and isinstance(main["props"].get("_bundled"), dict):
        doc["kind"] = "scene"
        doc.update(unpack_scene(main["props"]["_bundled"], r.g3))
    else:
        doc["kind"] = "resource"
        doc["resource"] = {"type": main["type"], "props": main["props"], "line": None, "offset": main["offset"]}
    if isinstance(r.src, _Blocks):
        info["blocks"] = len(r.src.blocks)
        info["blocks_decoded"] = r.src.decoded
    return doc


def read(path: str | Path, *, bulk: int | None = None, skip_types=()) -> dict:
    with open(path, "rb") as fh:
        return read_bytes(fh.read(), bulk=bulk, skip_types=skip_types)


def header_bytes(data: bytes) -> dict:
    """Only the header (type, versions, flags, uid, compression): cheap, for listings."""
    r = _Reader(data)
    try:
        return r.header()
    except struct.error as exc:
        raise RsrcError(f"truncated header: {exc}") from exc


def header(path: str | Path) -> dict:
    with open(path, "rb") as fh:
        return header_bytes(fh.read())


# --- compressed textures (.ctex / .stex): header only ------------------------------------------------

IMAGE_FORMATS = ["L8", "LA8", "R8", "RG8", "RGB8", "RGBA8", "RGBA4444", "RGB565", "RF", "RGF", "RGBF", "RGBAF", "RH",
                 "RGH", "RGBH", "RGBAH", "RGBE9995", "DXT1", "DXT3", "DXT5", "RGTC_R", "RGTC_RG", "BPTC_RGBA", "BPTC_RF",
                 "BPTC_RFU", "ETC", "ETC2_R11", "ETC2_R11S", "ETC2_RG11", "ETC2_RG11S", "ETC2_RGB8", "ETC2_RGBA8",
                 "ETC2_RGB8A1", "ETC2_RA_AS_RG", "DXT5_RA_AS_RG", "ASTC_4x4", "ASTC_4x4_HDR", "ASTC_8x8",
                 "ASTC_8x8_HDR", "R16", "RG16", "RGB16", "RGBA16", "R16I", "RG16I", "RGB16I", "RGBA16I"]
DATA_FORMATS = {0: "image", 1: "png", 2: "webp", 3: "basis_universal"}
CTEX_FLAGS = {22: "stream", 23: "has_mipmaps", 24: "detect_3d", 25: "detect_roughness", 26: "detect_normal",
              27: "detect_bptc"}


def ctex_header(head: bytes) -> dict:
    """Header of a Godot 4 ``CompressedTexture2D`` (``GST2``, first 52 bytes): size, flags, stored data format
    (image/png/webp/basis) and Image format. Pixel data is not read."""
    if head[:4] != b"GST2" or len(head) < 52:
        raise RsrcError("not a GST2 compressed texture (or truncated header)")
    version, w, h, df, mip_limit = struct.unpack_from("<4Ii", head, 4)
    data_format, iw, ih, mipmaps, fmt = struct.unpack_from("<IHHII", head, 36)
    return {"version": version, "width": w, "height": h, "flags": [n for b, n in CTEX_FLAGS.items() if df & (1 << b)],
            "mipmap_limit": mip_limit, "data_format": DATA_FORMATS.get(data_format, data_format),
            "image_width": iw, "image_height": ih, "mipmaps": mipmaps,
            "image_format": IMAGE_FORMATS[fmt] if 0 <= fmt < len(IMAGE_FORMATS) else fmt}
