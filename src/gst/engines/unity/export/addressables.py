"""Addressables catalogs (``StreamingAssets/aa/``): copy + decoded summary.

Two content catalog formats, decoded to the same summary (:func:`summary_shape`):

- ``catalog.json`` (Addressables 1.x, and 1.21+ with the JSON option): internal ids and provider ids as lists, the
  key -> location table packed in base64 strings (``m_KeyDataString``, ``m_BucketDataString``,
  ``m_EntryDataString``, ``m_ExtraDataString``);
- ``catalog.bin`` (Addressables 1.21+/2.x default): the binary ``ContentCatalogData`` written by
  ``BinaryStorageBuffer`` (layout below).

Summary: locator id, build hash, providers, internal ids, bundles, resource types, every key with its locations,
every location (primary key, internal id, provider, type, dependencies, bundle options), and per asset: address ->
bundle -> dependency bundles. ``settings.json`` and ``link.xml`` are copied as they are.

Binary layout (reimplemented from the format, little endian; format reference: Unity Addressables package source,
``ContentCatalogData`` / ``BinaryStorageBuffer``, see THIRD_PARTY_NOTICES.md). Every "id" is a byte offset into the
file; ``0xFFFFFFFF`` = null. Arrays and strings have their byte size as a uint32 just before the id; fixed structs
have none.

- header at 0: int32 magic ``0x0DE38942``, int32 version, then uint32 ids: keys array, locator id string,
  instance provider, scene provider, init objects array, build result hash string (the last one only when the
  header is long enough: the keys array starts 4 bytes after the header);
- string id: bit 31 = UTF-16LE (else ASCII), bit 30 = dynamic string: a linked list of ``{uint part, uint next}``
  nodes holding the parts last-first, joined with the separator the reader knows (``/`` for internal ids and
  primary keys, ``.`` for type names and providers) or, inside a key object, the separator stored with it;
- serialized type: ``{uint assembly, uint class}`` (dynamic strings, ``.``);
- object with type data: ``{uint type, uint object}``; a ``System.String`` object is ``{uint string, uint separator}``,
  ``Int32``/``UInt32``/``UInt16``/``Hash128`` objects are raw values;
- keys array: ``{uint key object, uint location ids array}`` pairs;
- location: ``{primary key, internal id, provider, dependency ids array, int32 dependency hash, extra data object,
  serialized type}`` (7 x uint32);
- ``AssetBundleRequestOptions`` extra data: ``{Hash128 id, bundle name, crc, bundle size, common data}``;
- object initialization data: ``{uint id string, uint serialized type, uint data string}``.
"""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

CATALOG_NAMES = ("catalog.json", "catalog.bin", "settings.json", "link.xml", "catalog.hash")
BIN_MAGIC = 0x0DE38942
NULL = 0xFFFFFFFF
UNICODE_FLAG = 0x80000000
DYNAMIC_FLAG = 0x40000000
BUNDLE_PROVIDER = "AssetBundleProvider"
BUNDLE_TYPE = "IAssetBundleResource"


def find_catalogs(install: Path):
    for data in sorted(Path(install).glob("*_Data")):
        aa = data / "StreamingAssets" / "aa"
        if not aa.is_dir():
            continue
        for p in sorted(aa.rglob("*")):
            if p.is_file() and (p.name in CATALOG_NAMES or (p.name.startswith("catalog") and p.suffix in (".json", ".bin", ".hash"))):
                yield p


def active_catalog(aa_dir: Path) -> str | None:
    """File name of the catalog ``settings.json`` loads (``AddressablesMainContentCatalog``), or None."""
    p = Path(aa_dir) / "settings.json"
    try:
        settings = json.loads(p.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    for loc in settings.get("m_CatalogLocations") or []:
        iid = str(loc.get("m_InternalId") or "").replace("\\", "/")
        if iid:
            return iid.rsplit("/", 1)[-1]
    return None


def bundle_file(internal_id: str) -> str:
    return str(internal_id).replace("\\", "/").rsplit("/", 1)[-1]


def short_type(name: str | None) -> str:
    return str(name or "").rsplit(".", 1)[-1]


def summary_shape(fmt: str, version, locator_id, build_version, provider_ids: list, internal_ids: list,
                  resource_types: list, keys: list[dict], locations: list[dict], extra: dict | None = None) -> dict:
    """The summary both decoders return. ``keys`` = ``[{key, type, locations: [index]}]``; ``locations`` =
    ``[{primary_key, internal_id, provider, type, dependencies: [index], bundle?}]``. Adds ``bundles`` and
    ``assets`` (address -> bundle -> dependency bundles, with every key that resolves to the asset)."""
    keys_of: dict[int, list] = {}
    for k in keys:
        for i in k["locations"]:
            keys_of.setdefault(i, []).append(k["key"])
    assets = []
    for i, loc in enumerate(locations):
        if is_bundle(loc):
            continue
        deps = [locations[d] for d in loc["dependencies"] if 0 <= d < len(locations)]
        dep_bundles = [bundle_file(d["internal_id"]) for d in deps if is_bundle(d)]
        assets.append({"address": loc["primary_key"], "internal_id": loc["internal_id"], "type": short_type(loc["type"]),
                       "provider": short_type(loc["provider"]), "bundle": dep_bundles[0] if dep_bundles else None,
                       "dependencies": dep_bundles[1:],
                       "keys": [k for k in keys_of.get(i, []) if k != loc["primary_key"]], "location": i})
    out = {"format": fmt, "version": version, "locator_id": locator_id, "build_version": build_version,
           "provider_ids": provider_ids, "internal_ids": internal_ids,
           "bundles": [i for i in internal_ids if isinstance(i, str) and i.endswith(".bundle")],
           "resource_types": resource_types, "keys": keys, "locations": locations, "assets": assets}
    out.update(extra or {})
    return out


def is_bundle(loc: dict) -> bool:
    return short_type(loc.get("provider")) == BUNDLE_PROVIDER or short_type(loc.get("type")) == BUNDLE_TYPE


# --- JSON catalog (1.x) ---------------------------------------------------------------------------------------------

def _b64(text) -> bytes:
    try:
        return base64.b64decode(text or "")
    except (ValueError, TypeError):
        return b""


def read_serialized_object(buf: bytes, pos: int):
    """``SerializationUtilities.ReadObjectFromByteArray``: ``(value, type name)`` of the object at ``pos``."""
    kind = buf[pos]
    pos += 1
    if kind == 0:  # ASCII string
        n = struct.unpack_from("<i", buf, pos)[0]
        return buf[pos + 4:pos + 4 + n].decode("ascii", "replace"), "string"
    if kind == 1:  # UTF-16 string
        n = struct.unpack_from("<i", buf, pos)[0]
        return buf[pos + 4:pos + 4 + n].decode("utf-16le", "replace"), "string"
    if kind == 2:
        return struct.unpack_from("<H", buf, pos)[0], "uint16"
    if kind == 3:
        return struct.unpack_from("<I", buf, pos)[0], "uint32"
    if kind == 4:
        return struct.unpack_from("<i", buf, pos)[0], "int32"
    if kind in (5, 6):  # Hash128 / Type: byte length + ASCII
        n = buf[pos]
        return buf[pos + 1:pos + 1 + n].decode("ascii", "replace"), "hash128" if kind == 5 else "type"
    if kind == 7:  # JSON object: assembly, class, UTF-16 json
        n = buf[pos]
        asm = buf[pos + 1:pos + 1 + n].decode("ascii", "replace")
        pos += 1 + n
        n = buf[pos]
        cls = buf[pos + 1:pos + 1 + n].decode("ascii", "replace")
        pos += 1 + n
        jn = struct.unpack_from("<i", buf, pos)[0]
        text = buf[pos + 4:pos + 4 + jn].decode("utf-16le", "replace")
        try:
            value = json.loads(text)
        except ValueError:
            value = text
        return {"assembly": asm, "class": cls, "json": value}, "json"
    return None, f"unknown:{kind}"


def _json_bundle_options(obj) -> dict | None:
    if not isinstance(obj, dict) or not str(obj.get("class", "")).endswith("AssetBundleRequestOptions"):
        return None
    j = obj.get("json") if isinstance(obj.get("json"), dict) else {}
    return {"hash": j.get("m_Hash"), "bundle_name": j.get("m_BundleName"), "crc": j.get("m_Crc"),
            "bundle_size": j.get("m_BundleSize")}


def decode_json_catalog(data: dict) -> dict:
    ids = list(data.get("m_InternalIds") or [])
    prefixes = data.get("m_InternalIdPrefixes") or []
    if prefixes:
        for i, iid in enumerate(ids):
            if isinstance(iid, str) and "#" in iid:
                head, _, tail = iid.partition("#")
                if head.isdigit() and int(head) < len(prefixes):
                    ids[i] = prefixes[int(head)] + tail
    providers = data.get("m_ProviderIds") or []
    rtypes = [t.get("m_ClassName") for t in data.get("m_resourceTypes", []) or [] if isinstance(t, dict)]
    key_data = _b64(data.get("m_KeyDataString"))
    bucket_data = _b64(data.get("m_BucketDataString"))
    entry_data = _b64(data.get("m_EntryDataString"))
    extra_data = _b64(data.get("m_ExtraDataString"))
    buckets = []
    if len(bucket_data) >= 4:
        n = struct.unpack_from("<i", bucket_data, 0)[0]
        pos = 4
        for _ in range(n):
            off, cnt = struct.unpack_from("<ii", bucket_data, pos)
            pos += 8
            entries = list(struct.unpack_from(f"<{cnt}i", bucket_data, pos))
            pos += 4 * cnt
            buckets.append((off, entries))
    keys = []
    for off, entries in buckets:
        value, kind = read_serialized_object(key_data, off) if off < len(key_data) else (None, "missing")
        keys.append({"key": value, "type": kind, "locations": entries})
    locations = []
    if len(entry_data) >= 4:
        n = struct.unpack_from("<i", entry_data, 0)[0]
        for e in range(n):
            iid, prov, dep_key, _dep_hash, data_idx, pkey, rtype = struct.unpack_from("<7i", entry_data, 4 + 28 * e)
            loc = {"primary_key": keys[pkey]["key"] if 0 <= pkey < len(keys) else None,
                   "internal_id": ids[iid] if 0 <= iid < len(ids) else None,
                   "provider": providers[prov] if 0 <= prov < len(providers) else None,
                   "type": rtypes[rtype] if 0 <= rtype < len(rtypes) else None,
                   "dependencies": list(keys[dep_key]["locations"]) if 0 <= dep_key < len(keys) else []}
            if data_idx >= 0 and data_idx < len(extra_data):
                opts = _json_bundle_options(read_serialized_object(extra_data, data_idx)[0])
                if opts:
                    loc["bundle"] = opts
            locations.append(loc)
    return summary_shape("json", None, data.get("m_LocatorId"),
                         data.get("m_BuildResultHash") or data.get("m_buildResultHash"), providers, ids, rtypes,
                         keys, locations, {"key_data_bytes": len(data.get("m_KeyDataString", "") or ""),
                                           "entry_data_bytes": len(data.get("m_EntryDataString", "") or "")})


# --- binary catalog (1.21+/2.x) -------------------------------------------------------------------------------------

class BinaryCatalogError(ValueError):
    pass


class BinaryReader:
    """Reads ids of a ``BinaryStorageBuffer`` (see the module docstring for the layout)."""

    def __init__(self, buf: bytes):
        self.buf = buf
        self._strings: dict[tuple[int, str], str | None] = {}

    def u32(self, pos: int) -> int:
        if pos < 0 or pos + 4 > len(self.buf):
            raise BinaryCatalogError(f"offset 0x{pos:x} outside the file ({len(self.buf)} bytes)")
        return struct.unpack_from("<I", self.buf, pos)[0]

    def struct(self, fmt: str, pos: int) -> tuple:
        size = struct.calcsize(fmt)
        if pos < 0 or pos + size > len(self.buf):
            raise BinaryCatalogError(f"struct at 0x{pos:x} outside the file")
        return struct.unpack_from(fmt, self.buf, pos)

    def block(self, pos: int) -> bytes:
        n = self.u32(pos - 4)
        if pos + n > len(self.buf):
            raise BinaryCatalogError(f"block at 0x{pos:x} ({n} bytes) outside the file")
        return self.buf[pos:pos + n]

    def ids(self, pos: int) -> list[int]:
        if pos == NULL:
            return []
        raw = self.block(pos)
        return list(struct.unpack(f"<{len(raw) // 4}I", raw[:len(raw) // 4 * 4]))

    def _plain(self, sid: int) -> str:
        if sid & UNICODE_FLAG:
            return self.block(sid & ~UNICODE_FLAG & 0xFFFFFFFF).decode("utf-16le", "replace")
        return self.block(sid).decode("ascii", "replace")

    def string(self, sid: int, sep: str = "") -> str | None:
        if sid == NULL:
            return None
        key = (sid, sep)
        if key in self._strings:
            return self._strings[key]
        if sid & DYNAMIC_FLAG and not sid & UNICODE_FLAG:
            parts, node, seen = [], sid & ~DYNAMIC_FLAG, set()
            while node != NULL:
                node &= ~DYNAMIC_FLAG
                if node in seen:
                    raise BinaryCatalogError(f"dynamic string loop at 0x{node:x}")
                seen.add(node)
                part, node = self.struct("<II", node)
                parts.append(self._plain(part))
            value = sep.join(reversed(parts))
        else:
            value = self._plain(sid)
        self._strings[key] = value
        return value

    def type_name(self, tid: int) -> tuple[str | None, str | None]:
        if tid == NULL:
            return None, None
        asm, cls = self.struct("<II", tid)
        return self.string(asm, "."), self.string(cls, ".")

    def typed_object(self, oid: int) -> tuple[object, str | None, int]:
        """``(value, class name, object id)`` of an object written with its type data."""
        if oid == NULL:
            return None, None, NULL
        tid, obj = self.struct("<II", oid)
        _asm, cls = self.type_name(tid)
        if obj == NULL:
            return None, cls, obj
        if cls == "System.String":
            sid, sep = self.struct("<II", obj)
            return self.string(sid, chr(sep) if 0 < sep < 0x110000 else ""), cls, obj
        if cls in ("System.Int32",):
            return self.struct("<i", obj)[0], cls, obj
        if cls in ("System.UInt32",):
            return self.struct("<I", obj)[0], cls, obj
        if cls in ("System.UInt16",):
            return self.struct("<H", obj)[0], cls, obj
        if cls in ("System.Int64",):
            return self.struct("<q", obj)[0], cls, obj
        if cls == "UnityEngine.Hash128":
            return self.buf[obj:obj + 16].hex(), cls, obj
        return None, cls, obj

    def bundle_options(self, obj: int) -> dict:
        hid, name, crc, size, _common = self.struct("<IIIII", obj)
        return {"hash": self.buf[hid:hid + 16].hex() if hid != NULL else None, "bundle_name": self.string(name),
                "crc": crc, "bundle_size": size}

    def init_data(self, oid: int) -> dict | None:
        if oid == NULL:
            return None
        sid, tid, did = self.struct("<III", oid)
        asm, cls = self.type_name(tid)
        return {"id": self.string(sid, "."), "type": cls, "assembly": asm, "data": self.string(did)}


def _key_kind(cls: str | None) -> str:
    return {"System.String": "string", "System.Int32": "int32", "System.UInt32": "uint32", "System.UInt16": "uint16",
            "UnityEngine.Hash128": "hash128"}.get(cls or "", short_type(cls) or "?")


def decode_binary_catalog(buf: bytes) -> dict:
    """Decode a binary ``catalog.bin`` into the :func:`summary_shape` summary. Raises :class:`BinaryCatalogError`."""
    if len(buf) < 32:
        raise BinaryCatalogError("too small for a binary catalog")
    magic, version = struct.unpack_from("<iI", buf, 0)
    if magic != BIN_MAGIC:
        raise BinaryCatalogError(f"bad magic 0x{magic & 0xFFFFFFFF:08x} (expected 0x{BIN_MAGIC:08x})")
    r = BinaryReader(buf)
    keys_id = r.u32(8)
    fields = max(0, min(6, (keys_id - 4 - 8) // 4)) if keys_id != NULL else 6
    hdr = list(struct.unpack_from(f"<{fields}I", buf, 8)) + [NULL] * (6 - fields)
    keys_id, locator_id, inst_id, scene_id, init_id, hash_id = hdr
    raw_keys = r.ids(keys_id)
    index: dict[int, int] = {}
    order: list[int] = []

    def loc_index(lid: int) -> int:
        if lid not in index:
            index[lid] = len(order)
            order.append(lid)
        return index[lid]

    keys = []
    for k in range(0, len(raw_keys) - 1, 2):
        value, cls, _obj = r.typed_object(raw_keys[k])
        keys.append({"key": value, "type": _key_kind(cls), "locations": [loc_index(x) for x in r.ids(raw_keys[k + 1])]})
    locations: list[dict] = []
    pos = 0
    while pos < len(order):  # dependencies can add locations no key lists
        lid = order[pos]
        pos += 1
        pk, iid, prov, deps, _dep_hash, extra, tid = r.struct("<IIIIiII", lid)
        _asm, cls = r.type_name(tid)
        loc = {"primary_key": r.string(pk, "/"), "internal_id": r.string(iid, "/"), "provider": r.string(prov, "."),
               "type": cls, "dependencies": [loc_index(d) for d in r.ids(deps)]}
        if extra != NULL:
            _v, ecls, obj = r.typed_object(extra)
            if ecls and ecls.endswith("AssetBundleRequestOptions") and obj != NULL:
                loc["bundle"] = r.bundle_options(obj)
            elif ecls:
                loc["extra_type"] = ecls
        locations.append(loc)
    internal_ids, providers, rtypes = [], [], []
    for loc in locations:
        for value, acc in ((loc["internal_id"], internal_ids), (loc["provider"], providers), (loc["type"], rtypes)):
            if value is not None and value not in acc:
                acc.append(value)
    inst, scene = r.init_data(inst_id), r.init_data(scene_id)
    init_objects = [r.init_data(x) for x in r.ids(init_id)]
    return summary_shape("binary", version, r.string(locator_id), r.string(hash_id), providers, internal_ids, rtypes,
                         keys, locations, {"instance_provider": inst, "scene_provider": scene,
                                           "init_objects": init_objects})


def decode_catalog(path: Path, raw: bytes | None = None) -> dict:
    """Decode ``catalog*.json`` or ``catalog*.bin`` by its content (``ValueError`` on anything else)."""
    raw = Path(path).read_bytes() if raw is None else raw
    if raw[:4] == struct.pack("<I", BIN_MAGIC):
        return decode_binary_catalog(raw)
    return decode_json_catalog(json.loads(raw.decode("utf-8-sig")))


def compare_summaries(a: dict, b: dict) -> dict:
    """What two decoded catalogs share and where they differ (internal ids, providers, addresses, bundles)."""
    def addresses(s):
        return {str(x["address"]) for x in s["assets"]}

    out = {}
    for name, fn in (("internal_ids", lambda s: {str(i).replace("\\", "/") for i in s["internal_ids"]}),
                     ("providers", lambda s: {p for p in s["provider_ids"] if p}), ("addresses", addresses),
                     ("bundles", lambda s: {bundle_file(i) for i in s["bundles"]}),
                     ("resource_types", lambda s: {t for t in s["resource_types"] if t})):
        x, y = fn(a), fn(b)
        out[name] = {"both": len(x & y), "only_first": sorted(x - y), "only_second": sorted(y - x)}
    return out


def export_catalog(w, install: Path, path: Path) -> list:
    rel = path.relative_to(install).as_posix()
    raw = path.read_bytes()
    copy = w.target("Addressables", path.name, "", rel)
    w.commit(copy, raw, meta={"res_path": rel, "type": "AddressablesFile", "name": path.name})
    out = [copy]
    if path.name.startswith("catalog") and path.suffix in (".json", ".bin"):
        try:
            summary = decode_catalog(path, raw)
        except (ValueError, UnicodeDecodeError, struct.error):
            return out
        summary["active"] = active_catalog(path.parent) == path.name
        sp = w.target("Addressables", path.stem + (".decoded" if path.suffix == ".json" else ".bin.decoded"), ".json", rel)
        w.json(sp, summary, meta={"res_path": rel, "type": "AddressablesCatalog", "name": path.name,
                                  "count": len(summary["internal_ids"])})
        out.append(sp)
    return out
