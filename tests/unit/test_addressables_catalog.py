"""Addressables content catalogs: the binary ``catalog.bin`` (1.21+/2.x) and the JSON one decode to the same summary.

Both catalogs are built here from scratch (synthetic): two bundles, three assets, labels, a GUID key, one shared
dependency bundle.
"""

from __future__ import annotations

import base64
import json
import struct

from gst.engines.unity.export import addressables as A

RM = "Unity.ResourceManager, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
MSCORLIB = "mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
P_BUNDLE = "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider"
P_ASSET = "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider"
T_BUNDLE = "UnityEngine.ResourceManagement.ResourceProviders.IAssetBundleResource"
ROOT = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}\\StandaloneWindows64\\"

# (primary key, internal id, provider, type, dependencies (indexes), extra keys, bundle size)
LOCATIONS = [
    ("chars_abc.bundle", ROOT + "chars.bundle", P_BUNDLE, T_BUNDLE, [], [], 1234),
    ("shared_def.bundle", ROOT + "shared.bundle", P_BUNDLE, T_BUNDLE, [], [], 99),
    ("Hero", "Assets/Chars/Hero.prefab", P_ASSET, "UnityEngine.GameObject", [0, 1], ["0123456789abcdef", "Chars"], 0),
    ("Villain", "Assets/Chars/Villain.prefab", P_ASSET, "UnityEngine.GameObject", [0, 1], ["Chars"], 0),
    ("Assets/UI/Icon.png", "Assets/UI/Icon.png", P_ASSET, "UnityEngine.Texture2D", [1], ["Ícone"], 0),
]


def key_table():
    """``{key: [location indexes]}`` in insertion order (primary keys first, then the extra keys)."""
    keys: dict[str, list[int]] = {}
    for i, (pk, *_rest) in enumerate(LOCATIONS):
        keys.setdefault(pk, []).append(i)
    for i, loc in enumerate(LOCATIONS):
        for k in loc[5]:
            keys.setdefault(k, []).append(i)
    return keys


class BinWriter:
    """A tiny BinaryStorageBuffer writer for the test (layout documented in ``addressables.py``)."""

    def __init__(self, header: int = 32):
        self.buf = bytearray(header)
        self.strings: dict[tuple, int] = {}

    def raw(self, data: bytes) -> int:
        pos = len(self.buf)
        self.buf += data
        return pos

    def blob(self, data: bytes) -> int:
        self.buf += struct.pack("<I", len(data))
        return self.raw(data)

    def plain(self, s: str) -> int:
        if ("p", s) not in self.strings:
            try:
                self.strings[("p", s)] = self.blob(s.encode("ascii"))
            except UnicodeEncodeError:
                self.strings[("p", s)] = self.blob(s.encode("utf-16le")) | A.UNICODE_FLAG
        return self.strings[("p", s)]

    def string(self, s: str, sep: str = "") -> int:
        parts = s.split(sep) if sep else [s]
        if len(parts) == 1:
            return self.plain(s)
        node = A.NULL
        for p in parts:
            node = self.raw(struct.pack("<II", self.plain(p), node))
        return node | A.DYNAMIC_FLAG

    def stype(self, asm: str, cls: str) -> int:
        return self.raw(struct.pack("<II", self.string(asm, "."), self.string(cls, ".")))

    def ids(self, values) -> int:
        return self.blob(struct.pack(f"<{len(values)}I", *values))


def build_bin(with_hash: bool = True) -> bytes:
    w = BinWriter(32 if with_hash else 28)
    keys = w.blob(bytes(8 * len(key_table())))  # the keys array comes first, right after the header
    str_type = w.stype(MSCORLIB, "System.String")
    opt_type = w.stype(RM, "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleRequestOptions")
    loc_ids: list[int] = []
    for pk, iid, prov, typ, deps, _keys, size in LOCATIONS:
        extra = A.NULL
        if prov == P_BUNDLE:
            hid = w.raw(bytes(range(16)))
            obj = w.raw(struct.pack("<IIIII", hid, w.plain("name_" + pk), 777, size, A.NULL))
            extra = w.raw(struct.pack("<II", opt_type, obj))
        dep_set = w.ids([loc_ids[d] for d in deps]) if deps else A.NULL
        loc_ids.append(w.raw(struct.pack("<IIIIiII", w.string(pk, "/"), w.string(iid, "/"), w.string(prov, "."),
                                         dep_set, 0, extra, w.stype("Unity", typ))))
    pairs = []
    for key, locs in key_table().items():
        sep = "/" if "/" in key else ""
        obj = w.raw(struct.pack("<II", w.string(key, sep), ord(sep) if sep else 0))
        pairs += [w.raw(struct.pack("<II", str_type, obj)), w.ids([loc_ids[i] for i in locs])]
    struct.pack_into(f"<{len(pairs)}I", w.buf, keys, *pairs)
    inst = w.raw(struct.pack("<III", w.plain("Inst"), w.stype(RM, "A.B.InstanceProvider"), A.NULL))
    init = w.ids([inst])
    head = [keys, w.plain("AddressablesMainContentCatalog"), inst, inst, init]
    if with_hash:
        head.append(w.plain("feedface"))
    struct.pack_into(f"<iI{len(head)}I", w.buf, 0, A.BIN_MAGIC, 2, *head)
    return bytes(w.buf)


def _obj(value) -> bytes:
    if isinstance(value, int):
        return b"\x04" + struct.pack("<i", value)
    try:
        data = value.encode("ascii")
        return b"\x00" + struct.pack("<i", len(data)) + data
    except UnicodeEncodeError:
        data = value.encode("utf-16le")
        return b"\x01" + struct.pack("<i", len(data)) + data


def build_json() -> dict:
    ids, providers, types = [], [], []
    for _pk, iid, prov, typ, *_ in LOCATIONS:
        for v, acc in ((iid, ids), (prov, providers), (typ, types)):
            if v not in acc:
                acc.append(v)
    keys = list(key_table().items())
    # one extra key per distinct dependency set (the JSON format points at a key whose bucket lists the deps)
    dep_keys = {}
    for _pk, _iid, _prov, _typ, deps, *_ in LOCATIONS:
        if deps and tuple(deps) not in dep_keys:
            dep_keys[tuple(deps)] = len(keys)
            keys.append((len(dep_keys) * 1000, list(deps)))
    key_data = struct.pack("<i", len(keys))
    buckets = struct.pack("<i", len(keys))
    for k, locs in keys:
        buckets += struct.pack(f"<ii{len(locs)}i", len(key_data), len(locs), *locs)
        key_data += _obj(k)
    extra = b""
    entries = struct.pack("<i", len(LOCATIONS))
    key_index = {k: i for i, (k, _l) in enumerate(keys)}
    for pk, iid, prov, typ, deps, _k, size in LOCATIONS:
        data_idx = -1
        if prov == P_BUNDLE:
            data_idx = len(extra)
            asm, cls = RM.encode(), b"UnityEngine.ResourceManagement.ResourceProviders.AssetBundleRequestOptions"
            js = json.dumps({"m_Hash": bytes(range(16)).hex(), "m_Crc": 777, "m_BundleName": "name_" + pk,
                             "m_BundleSize": size}).encode("utf-16le")
            extra += b"\x07" + bytes([len(asm)]) + asm + bytes([len(cls)]) + cls + struct.pack("<i", len(js)) + js
        entries += struct.pack("<7i", ids.index(iid), providers.index(prov), dep_keys.get(tuple(deps), -1) if deps else -1,
                               0, data_idx, key_index[pk], types.index(typ))
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return {"m_LocatorId": "AddressablesMainContentCatalog", "m_BuildResultHash": "feedface", "m_InternalIds": ids,
            "m_ProviderIds": providers, "m_resourceTypes": [{"m_ClassName": t} for t in types],
            "m_KeyDataString": b64(key_data), "m_BucketDataString": b64(buckets), "m_EntryDataString": b64(entries),
            "m_ExtraDataString": b64(extra)}


def check_summary(s):
    assert s["locator_id"] == "AddressablesMainContentCatalog" and s["build_version"] == "feedface"
    assert [A.bundle_file(b) for b in s["bundles"]] == ["chars.bundle", "shared.bundle"]
    assert {p for p in s["provider_ids"] if p} == {P_BUNDLE, P_ASSET}
    assets = {a["address"]: a for a in s["assets"]}
    assert set(assets) == {"Hero", "Villain", "Assets/UI/Icon.png"}
    hero = assets["Hero"]
    assert hero["bundle"] == "chars.bundle" and hero["dependencies"] == ["shared.bundle"]
    assert hero["type"] == "GameObject" and hero["internal_id"] == "Assets/Chars/Hero.prefab"
    assert sorted(hero["keys"]) == ["0123456789abcdef", "Chars"]
    assert assets["Assets/UI/Icon.png"]["bundle"] == "shared.bundle" and assets["Assets/UI/Icon.png"]["keys"] == ["Ícone"]
    bundle_locs = [loc for loc in s["locations"] if "bundle" in loc]
    assert sorted(loc["bundle"]["bundle_size"] for loc in bundle_locs) == [99, 1234]
    assert all(loc["bundle"]["crc"] == 777 and loc["bundle"]["hash"] == bytes(range(16)).hex() for loc in bundle_locs)
    keys = {str(k["key"]): k for k in s["keys"]}
    assert len(keys["Chars"]["locations"]) == 2


def test_binary_catalog_decodes():
    s = A.decode_binary_catalog(build_bin())
    check_summary(s)
    assert s["format"] == "binary" and s["version"] == 2
    assert s["instance_provider"]["type"] == "A.B.InstanceProvider" and len(s["init_objects"]) == 1
    # a header without the build hash field (keys array right after 7 fields) still decodes
    short = A.decode_binary_catalog(build_bin(with_hash=False))
    assert short["build_version"] is None and len(short["assets"]) == 3


def test_json_catalog_decodes_and_matches_binary():
    j = A.decode_json_catalog(build_json())
    check_summary(j)
    assert j["format"] == "json" and j["key_data_bytes"] > 0
    cmp = A.compare_summaries(j, A.decode_binary_catalog(build_bin()))
    for name, v in cmp.items():
        assert not v["only_first"] and not v["only_second"], (name, v)


def test_decode_catalog_by_content_and_errors(tmp_path):
    import pytest

    p = tmp_path / "catalog.bin"
    p.write_bytes(build_bin())
    assert A.decode_catalog(p)["format"] == "binary"
    bad = bytearray(build_bin())
    struct.pack_into("<I", bad, 8, len(bad) + 100)  # keys array outside the file
    with pytest.raises(A.BinaryCatalogError):
        A.decode_binary_catalog(bytes(bad))
    with pytest.raises(A.BinaryCatalogError):
        A.decode_binary_catalog(b"\0" * 40)


def test_export_writes_decoded_binary_and_active_flag(tmp_path):
    from gst.engines.unity.export import Writer

    install = tmp_path / "game"
    aa = install / "G_Data" / "StreamingAssets" / "aa"
    aa.mkdir(parents=True)
    (aa / "catalog.bin").write_bytes(build_bin())
    (aa / "catalog.json").write_text(json.dumps(build_json()), encoding="utf-8")
    (aa / "settings.json").write_text(json.dumps({"m_CatalogLocations": [
        {"m_InternalId": "{UnityEngine.AddressableAssets.Addressables.RuntimePath}/catalog.bin"}]}), encoding="utf-8")
    assert A.active_catalog(aa) == "catalog.bin"
    ws = tmp_path / "ws"
    out = []
    for name in ("catalog.bin", "catalog.json"):
        w = Writer(ws / "assets" / "unity", ws, source=f"G_Data/StreamingAssets/aa/{name}", build="1", tool="gst",
                   tool_version="", extractor="unity.addressables", unit=name)
        out += A.export_catalog(w, install, aa / name)
    decoded = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in out if p.name.endswith(".decoded.json")}
    assert set(decoded) == {"catalog.bin.decoded.json", "catalog.decoded.json"}
    assert decoded["catalog.bin.decoded.json"]["active"] is True and decoded["catalog.decoded.json"]["active"] is False
    assert len(decoded["catalog.bin.decoded.json"]["assets"]) == 3
