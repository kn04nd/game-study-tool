"""Godot binary resources (RSRC/RSCC) read by the stdlib reader, the codecs behind RSCC, the scene -> model ->
skeleton links and the godot_shaders analysis. Synthetic files only: a tiny RSRC encoder lives below."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib

import pytest

from gst import analysis
from gst.engines.godot import animtree, codecs, rsrc, tres
from gst.engines.godot.tres import Ctor, NodePath, Ref, StringName

# --- a tiny RSRC encoder (the inverse of rsrc.py, enough for the tests) ---------------------------------

G4_IDS = {"Vector2": 10, "Rect2": 11, "Vector3": 12, "Plane": 13, "Quaternion": 14, "AABB": 15, "Basis": 16,
          "Transform3D": 17, "Transform2D": 18, "Color": 20, "Vector2i": 45, "Rect2i": 46, "Vector3i": 47,
          "Vector4": 50, "Vector4i": 51, "Projection": 52, "Quat": 14, "Transform": 17}
PACKED_IDS = {"PackedByteArray": 31, "PackedInt32Array": 32, "PackedFloat32Array": 33, "PackedStringArray": 34,
              "PackedVector3Array": 35, "PackedColorArray": 36, "PackedVector2Array": 37, "PackedInt64Array": 48,
              "PackedFloat64Array": 49, "PackedVector4Array": 53, "PoolRealArray": 33, "PoolIntArray": 32,
              "PoolStringArray": 34, "PoolVector3Array": 35}
INT_KINDS = {"Vector2i", "Rect2i", "Vector3i", "Vector4i"}


class Enc:
    def __init__(self, *, big=False, version=(4, 6), fmt=6, flags=rsrc.FLAG_NAMED_SCENE_IDS | rsrc.FLAG_UIDS,
                 real64=False):
        self.e = ">" if big else "<"
        self.big, self.version, self.fmt = big, version, fmt
        self.flags = flags | (rsrc.FLAG_REAL_DOUBLE if real64 else 0)
        self.real = "d" if real64 else "f"
        self.strings: list[str] = []
        self.ids: list[str] = []  # internal resource ids, in order

    def p(self, fmt: str, *v) -> bytes:
        return struct.pack(self.e + fmt, *v)

    def ustr(self, s: str) -> bytes:
        b = s.encode("utf-8") + b"\0"
        return self.p("I", len(b)) + b

    def sidx(self, s: str) -> bytes:
        if s not in self.strings:
            self.strings.append(s)
        return self.p("I", self.strings.index(s))

    def var(self, v) -> bytes:
        if v is None:
            return self.p("I", 1)
        if isinstance(v, bool):
            return self.p("II", 2, int(v))
        if isinstance(v, int):
            return self.p("Ii", 3, v) if -2**31 <= v < 2**31 else self.p("Iq", 40, v)
        if isinstance(v, float):
            return self.p("I" + self.real, 4, v)
        if isinstance(v, StringName):
            return self.p("I", 44) + self.ustr(str(v))
        if isinstance(v, NodePath):
            text = str(v)
            absolute = text.startswith("/")
            names, _, subs = text.lstrip("/").partition(":")
            names = [n for n in names.split("/") if n]
            subs = [s for s in subs.split(":") if s]
            out = self.p("IHH", 22, len(names), len(subs) | (0x8000 if absolute else 0))
            return out + b"".join(self.sidx(x) for x in names + subs)
        if isinstance(v, str):
            return self.p("I", 5) + self.ustr(v)
        if isinstance(v, Ref):
            if v.kind == "ext":
                return self.p("III", 24, 3, int(v.id))
            return self.p("III", 24, 2, self.ids.index(v.id))
        if isinstance(v, list):
            return self.p("II", 30, len(v)) + b"".join(self.var(x) for x in v)
        if isinstance(v, dict):
            return self.p("II", 26, len(v)) + b"".join(self.var(k) + self.var(x) for k, x in v.items())
        if isinstance(v, Ctor):
            if v.name in G4_IDS:
                kind = "i" if v.name in INT_KINDS else ("f" if v.name == "Color" else self.real)
                return self.p("I", G4_IDS[v.name]) + self.p(f"{len(v.args)}{kind}", *v.args)
            t = PACKED_IDS[v.name]
            if t == 31:
                data = bytes(v.args)
                return self.p("II", t, len(data)) + data + b"\0" * ((4 - len(data) % 4) % 4)
            if t == 34:
                return self.p("II", t, len(v.args)) + b"".join(self.ustr(s) for s in v.args)
            kind = {32: "i", 33: "f", 48: "q", 49: "d"}.get(t, self.real)
            per = {35: 3, 36: 4, 37: 2, 53: 4}.get(t, 1)
            return self.p("II", t, len(v.args) // per) + self.p(f"{len(v.args)}{kind}", *v.args)
        raise TypeError(v)

    def build(self, main_type: str, resources: list[tuple[str, str, dict]], ext=(), *, compress: int | None = None,
              uid: int = -1) -> bytes:
        """``resources`` = [(id, type, props)], the last one is the main resource; ``ext`` = [(type, path, uid)]."""
        self.ids = [r[0] for r in resources]
        bodies = []
        for _rid, rtype, props in resources:
            body = self.ustr(rtype) + self.p("I", len(props))
            for k, v in props.items():
                body += self.sidx(k) + self.var(v)
            bodies.append(body)
        # big-endian flag + use_real64, written before Godot switches the file to big endian
        head = struct.pack("<II", 1 if self.big else 0, 0)
        head += self.p("III", self.version[0], self.version[1], self.fmt) + self.ustr(main_type)
        head += self.p("Q", 0)
        if self.version[0] >= 4:
            head += self.p("I", self.flags) + self.p("q", uid) + b"\0" * 4 * rsrc.RESERVED_FIELDS
        else:
            head += b"\0" * 4 * 14
        tables = self.p("I", len(self.strings)) + b"".join(self.ustr(s) for s in self.strings)
        tables += self.p("I", len(ext))
        for t, path, euid in ext:
            tables += self.ustr(t) + self.ustr(path) + (self.p("q", euid) if self.flags & rsrc.FLAG_UIDS else b"")
        names = [("local://" + rid) if i < len(resources) - 1 else "" for i, (rid, _t, _p) in enumerate(resources)]
        index_size = 4 + sum(len(self.ustr(n)) + 8 for n in names)
        base = (0 if compress is not None else 4) + len(head) + len(tables) + index_size
        index = self.p("I", len(names))
        off = base
        for n, body in zip(names, bodies):
            index += self.ustr(n) + self.p("Q", off)
            off += len(body)
        payload = head + tables + index + b"".join(bodies)
        if compress is None:
            return b"RSRC" + payload
        return b"RSCC" + rscc(payload, compress)


def rscc(payload: bytes, mode: int, block: int = 64) -> bytes:
    blocks = [payload[i:i + block] for i in range(0, len(payload) // block * block + 1, block)]
    comp = [_compress(b, mode) for b in blocks]
    return struct.pack("<III", mode, block, len(payload)) + b"".join(struct.pack("<I", len(c)) for c in comp) + b"".join(comp)


def _compress(b: bytes, mode: int) -> bytes:
    if mode == 1:
        return zlib.compress(b)
    if mode == 3:
        co = zlib.compressobj(wbits=31)
        return co.compress(b) + co.flush()
    if mode == 2:  # one zstd frame with a raw block (window 8 KiB, no content size)
        return b"\x28\xb5\x2f\xfd" + bytes([0x00, 0x18]) + (1 | (len(b) << 3)).to_bytes(3, "little") + b
    if mode == 0:  # FastLZ level 1, literal runs only (Godot pads inputs under 16 bytes to 16)
        b = b if len(b) >= 16 else b + b"\0" * (16 - len(b))
        return b"".join(bytes([len(b[i:i + 32]) - 1]) + b[i:i + 32] for i in range(0, len(b), 32))
    raise ValueError(mode)


def bundled(names: list[str], variants: list, nodes: list[dict], conns=(), node_paths=(), editable=(), version=3,
            base_scene=None) -> dict:
    """``SceneState::get_bundled_scene`` for the given nodes ({parent, owner, type, name, index, instance, props:
    [(name, value index)], groups})."""
    raw = []
    for n in nodes:
        raw += [n.get("parent", -1), n.get("owner", -1), n.get("type", rsrc.TYPE_INSTANTIATED),
                n["name"] | ((n.get("index", -1) + 1) << rsrc.NAME_INDEX_BITS), n.get("instance", -1), len(n.get("props", []))]
        for k, v in n.get("props", []):
            raw += [k, v]
        raw += [len(n.get("groups", []))] + list(n.get("groups", []))
    craw = []
    for c in conns:
        craw += [c["from"], c["to"], c["signal"], c["method"], c.get("flags", 2), len(c.get("binds", []))]
        craw += list(c.get("binds", []))
        if version >= 3:
            craw.append(c.get("unbinds", 0))
    d = {"names": Ctor("PackedStringArray", tuple(names)), "variants": variants, "node_count": len(nodes),
         "nodes": Ctor("PackedInt32Array", tuple(raw)), "conn_count": len(conns), "conns": Ctor("PackedInt32Array", tuple(craw)),
         "node_paths": [NodePath(p) for p in node_paths], "editable_instances": [NodePath(p) for p in editable],
         "version": version}
    if base_scene is not None:
        d["base_scene"] = base_scene
    return d


# --- codecs ----------------------------------------------------------------------------------------------

def sample(n, seed=12345):
    words = [b"Skeleton3D", b"bones/", b"name", b"Transform3D", b"0.5", b"res://", b"AnimationPlayer", b"\x00\x01"]
    x = seed
    out = bytearray()
    while len(out) < n:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out += words[x % len(words)] if x & 0x100 else bytes([(x >> 16) & 0xFF])
    return bytes(out[:n])


ZSTD_FIXTURES = {  # sample(n) compressed by CPython 3.14 compression.zstd (libzstd 1.5.7) at a level
    (3000, 3): (
        "28b52ffd60b80add1d009421dc000165aa1fad536b656c65746f6e3344626f6e65732f416e696d6174696f6e506c61796572"
        "0001ac7265733a2f2f1e5f6cfdff19af601d04acb41d022b3a6e616d65b7080001302e35ee39104895b5cc8911ff62302e35"
        "dff935000128ca7c02ea2d69afe6874cd25d2cf479f686302e35266212d5cb302e3577156a68ba8edb74080af8d0dd68bb00"
        "015f245472616e73666f726d3344d21887302e355cba38a59b3df700017ff811290001939557ce5af118d7bc045b7ef19400"
        "01c42aacad1f3b2c991afceb15decfe0bcd61e971b0b9d63d3af97000145dd07896a3f68e5de93302e3538bdd75be7b8dc61"
        "302e35d18ebeaa506a55dc44000143a9d6c0f661d92d12725bc2e2da0001b2048000016f900c2ef50948e9c38c6e44000132"
        "af3f1386cb57a0b4302e35d5fe000001870f5f654652dfa213302e35bf4211dec979baa9e84d6be29a21d511379889bbad51"
        "b084f5302e35f52f81453dc36788302e357590c707e01e039ad18b00013f24f6c35ae676421610aa05d98330c70acf85f0ef"
        "4cb71fc334df604ab2803239a194302e3514f3a6000139436e616d65c09defd01feebdf305c49b45c494d864b91f538b9530"
        "2e359e5f40977a1a4d7f70801b42caf5db0001f9b87ef6f9302e3501a86deca2ba54000141894d000168e6ab0ef3109da5f1"
        "525d2e0d960001419ef50aa49d027b66bd1d9783a856a5c30450c2fa7328140dbc5c1e00011a0feb77415472616e73666f80"
        "d6a80141cb54cd3419f63350c281d4ea1160104ba8a424111b519085c2ce18398826e5a354329cab38b6906a45915c08bbb5"
        "ac264d60d8806a0d4ae21b99b2e9f4ae9021826922eb927e0466a2726b701e3fd57841f4599882a0b3516dceb7dd72fe56d7"
        "baf48f6018619dea2d314ceaf176c2c18b79ae4a849c33ba40b63c397b943c6d7587e3b815a3638a85cef4033c73485b6462"
        "4925963c48c3145a7af560eb2f3b7d3b5a56554042b87abbb64e96da691e5c084e6205a6c6338ff0a151a911f468f4d4e7a1"
        "47f32330ad155b5d201d1601dee5d8d87fd06449b4688700f97da0cdaa611791919076e7076d2875228a04d7efb22a7b6e1f"
        "1c29da138d8d11876c51291d0b013d2211a46681193208507de4a4182e26393ef9fb73c7ea28b95bf747282505c33f3c77fd"
        "493994429e732b0e2d845a11d20ab05bc76eb2040a1b80b0f6691c7024c01a62714586d9e058855b87989abfc7a6efc07f3e"
        "944102293d802247ce9fff3be4aca5da75c11f07a3555bb0a58e494ddb723c6131cfb6992d98bd6d7159798b02b78e2c4fa3"
        "ab111d20ace3bb55f3cc218ca6be2a"
    ),
    (3000, 19): (
        "28b52ffd60b80a451b00141edc000165aa1fad536b656c65746f6e3344626f6e65732f416e696d6174696f6e506c61796572"
        "0001ac7265733a2f2f1e5f6cfdff19af601d04acb41d022b3a6e616d65b7080001302e35ee39104895b5cc8911ff62dff935"
        "28ca7c02ea2d69afe6874cd25d2cf479f686266212d5cb77156a68ba8edb74080af8d0dd68bb00015f245472616e73666f72"
        "6d3344d218875cba38a59b3df77ff811290001939557ce5af118d7bc045b7ef1940001c42aac6aad1f3b2c991afceb15decf"
        "e0bcd61e971b0b9d63d3af97000145dd07896a3f68e5de9338bdd75be7b8dc61d18ebeaa506a55dc44000143a9d6c0f661d9"
        "2d12725b24c2e2da0001b204806f900c2ef50948e9c38c6e32af3f1386cb57a0b4d5fe00870f5f654652dfa213bf4211dec9"
        "79baa9e84d6be29a21d511379889bbad51b084f5f52f8681453dc367887590c707e01e039ad18b00013f24f6c35ae6764216"
        "10aa05d98330c70acf85f0ef4cb71fc334df604ab2803239a19414f3a63943c09defd01feebdf305c49b45c494d864b91f53"
        "8b959e5f407a1a4d7f70801b42caf5db0001f9b87ef6f901a86deca2ba5441894d000168e6ab0ef3109da5f1525d2e0d9600"
        "01419ef50aa49d027b66bd1d9783a856a5c30450c2fa7328140dbc5c1e00011a0feb77415472616e73666f80bba86168ef8a"
        "a42019f63340444450163792581096a0035a0222f01412122e20995134292c63b7a49651e8023def7df52c5597fd195a65b2"
        "f4ecf601d42556fba2c5c5e25a785eba2bc4f023607f4fc53bc00f850203b431ef42411b0205a745f6d19b219df7d7713769"
        "a72bb76a2661387eac1afceb4ce1c72ec679b9034c3b1ea3ae086fe0011836be602caa75b6dd96cc4d5711c977bbd9177c6e"
        "8ff37d427d65b9f28e1a3ed5d6a683589ec45d08fc24222f45c211483478d110a34a5e52875f4b68acc2f1cb2e5936dbf3a1"
        "fb58b8755403d840042d36744d29229d0e60f48f159af4e8421395f123c5f9187499cf1915e31101fdc5deb8712081c0121a"
        "c53f39295f157c2539cfe998bc1eeed84f8422948c734b6e9910eaf0eff6558f017d6d9caf15264fcfb70ba8805a64eb687f"
        "276e73862fc8b29e34f4f6684612d0203d2aa5ca61bac9ab2a28b9210396d62c1fbaf067c1ad0a66a9f5525b8d1386e38f55"
        "c05f2efe61d31f50dcfda20e060d3dce5899742f6100d942e5cf2c4b34c7aa02"
    ),
    (200, 1): (
        "28b52ffd20c8950300c405dc000165aa1fad536b656c65746f6e3344626f6e65732f416e696d6174696f6e506c6179657200"
        "01ac7265733a2f2f1e5f6cfdff19af601d04acb41d022b3a6e616d65b7080001302e35ee39104895b5cc896e616d6511ff62"
        "302e3507004ff8fe66230c1b82e156a17aa6301cad5150"
    ),
}


@pytest.mark.parametrize("key", list(ZSTD_FIXTURES))
def test_pure_python_zstd_matches_libzstd(key):
    n, _level = key
    data = bytes.fromhex("".join(ZSTD_FIXTURES[key]))
    assert codecs.ZstdDecoder().decompress(data) == sample(n)
    assert codecs.decompress(2, data, n) == sample(n)  # whichever backend this Python has


def test_zstd_raw_rle_and_skippable_frames():
    raw = _compress(b"hello raw block", 2)
    rle = b"\x28\xb5\x2f\xfd\x00\x18" + (1 | (1 << 1) | (5 << 3)).to_bytes(3, "little") + b"z"
    skip = struct.pack("<II", 0x184D2A50, 3) + b"abc"
    assert codecs.ZstdDecoder().decompress(skip + raw + rle) == b"hello raw blockzzzzz"
    with pytest.raises(codecs.CodecError):
        codecs.ZstdDecoder().decompress(b"\x00\x01\x02\x03rest")


def test_fastlz_levels():
    # literal "abc", long match (9 bytes at distance 3), literal "X"
    assert codecs.fastlz_decompress(bytes.fromhex("02616263e000020058"), 13) == b"abcabcabcabcX"
    # literal "abcd", short match (4 bytes at distance 4)
    assert codecs.fastlz_decompress(bytes.fromhex("03616263644003"), 8) == b"abcdabcd"
    assert codecs.decompress(0, _compress(b"tiny", 0), 4) == b"tiny"


# --- the reader ------------------------------------------------------------------------------------------

def _anim_props():
    return {"resource_name": "run", "length": 0.5, "loop_mode": 1,
            "tracks/0/type": "position_3d", "tracks/0/path": NodePath("Skeleton3D:hips"),
            "tracks/0/keys": Ctor("PackedFloat32Array", (0.0, 1.0, 0.0, 0.0, 0.0, 0.5, 1.0, 0.0, 1.0, 0.0)),
            "tracks/1/type": "method", "tracks/1/path": NodePath("."),
            "tracks/1/keys": {"times": Ctor("PackedFloat32Array", (0.25,)), "transitions": Ctor("PackedFloat32Array", (1.0,)),
                              "values": [{"method": StringName("footstep"), "args": []}]}}


def _hero_scene(enc: Enc, *, g3: bool = False):
    """A model-like scene: Root(Node3D) > Skeleton3D (hips, spine) > Body(MeshInstance3D with a skin), an
    AnimationPlayer with a library holding 'run', an instanced child scene (ext 0) with editable children, a
    connection with a bind."""
    names = ["Root", "Node3D", "Skeleton3D", "Body", "MeshInstance3D", "AnimationPlayer", "Child",
             "bones/0/name", "bones/0/parent", "bones/1/name", "bones/1/parent", "skin", "skeleton",
             "libraries/" if not g3 else "anims/run", "root_node", "players", "ready", "_on_ready", "script",
             "position"]
    variants = ["hips", -1, "spine", 0, Ref("sub", "Skin_1"), NodePath(".."),
                Ref("sub", "AnimationLibrary_a") if not g3 else Ref("sub", "Animation_run"), Ref("ext", "0"), 7,
                Ref("ext", "1"), Ctor("Vector3", (1.0, 2.0, 3.0))]
    N = {n: i for i, n in enumerate(names)}
    nodes = [
        {"name": N["Root"], "type": N["Node3D"], "props": [(N["script"], 9)], "groups": [N["players"]]},
        {"parent": 0, "owner": 0, "name": N["Skeleton3D"], "type": N["Skeleton3D"],
         "props": [(N["bones/0/name"], 0), (N["bones/0/parent"], 1), (N["bones/1/name"], 2), (N["bones/1/parent"], 3)]},
        {"parent": 1, "owner": 0, "name": N["Body"], "type": N["MeshInstance3D"],
         "props": [(N["skin"], 4), (N["skeleton"], 5), (N["position"], 10)]},
        {"parent": 0, "owner": 0, "name": N["AnimationPlayer"], "type": N["AnimationPlayer"],
         "props": [(N["libraries/" if not g3 else "anims/run"], 6), (N["root_node"], 5)], "index": 2},
        {"parent": 0, "owner": 0, "name": N["Child"], "instance": 7},
    ]
    conns = [{"from": 0, "to": 0, "signal": N["ready"], "method": N["_on_ready"], "binds": [8], "flags": 3}]
    scene = bundled(names, variants, nodes, conns, editable=["Child"], version=2 if g3 else 3)
    subs = [("Skin_1", "Skin", {"bind_count": 2})]
    if g3:
        subs.append(("Animation_run", "Animation", _anim_props()))
    else:
        subs += [("Animation_run", "Animation", _anim_props()),
                 ("AnimationLibrary_a", "AnimationLibrary", {"_data": {StringName("run"): Ref("sub", "Animation_run")}})]
    ext = [("PackedScene", "res://child.tscn", 1234567), ("Script", "res://hero.gd", -1)]
    return subs + [("", "PackedScene", {"_bundled": scene})], ext


def test_uid_text():
    assert rsrc.uid_text(0) == "uid://a" and rsrc.uid_text(25) == "uid://0" and rsrc.uid_text(34) == "uid://ba"
    assert rsrc.uid_text(-1) is None and rsrc.uid_text(None) is None


@pytest.mark.parametrize("big,real64,compress", [(False, False, None), (True, False, None), (False, True, None),
                                                 (False, False, 1), (False, False, 2), (True, False, 3), (False, False, 0)])
def test_binary_scene_reads_like_tscn(big, real64, compress):
    enc = Enc(big=big, real64=real64)
    res, ext = _hero_scene(enc)
    data = enc.build("PackedScene", res, ext, compress=compress, uid=34)
    doc = rsrc.read_bytes(data)
    assert doc["kind"] == "scene" and doc["header"] == {"type": "PackedScene", "format": 6, "uid": "uid://ba"}
    assert doc["binary"]["big_endian"] is big and doc["binary"]["compression"] == (
        None if compress is None else codecs.MODES[compress])
    assert [(n["path"], n["type"], n["parent"]) for n in doc["nodes"]] == [
        (".", "Node3D", None), ("Skeleton3D", "Skeleton3D", "."), ("Skeleton3D/Body", "MeshInstance3D", "Skeleton3D"),
        ("AnimationPlayer", "AnimationPlayer", "."), ("Child", None, ".")]
    root, skel, body, player, child = doc["nodes"]
    assert root["groups"] == ["players"] and tres.ref_path(doc, root["props"]["script"]) == "res://hero.gd"
    assert tres.ref_path(doc, child["instance"]) == "res://child.tscn" and player["index"] == 2
    assert doc["ext_resources"]["0"]["uid"] == rsrc.uid_text(1234567) and doc["ext_resources"]["1"]["uid"] is None
    assert body["props"]["skin"] == Ref("sub", "Skin_1") and body["props"]["skeleton"] == NodePath("..")
    assert body["props"]["position"] == Ctor("Vector3", (1.0, 2.0, 3.0))
    assert animtree.skeleton(skel["props"])["names"] == ["hips", "spine"]
    assert doc["connections"] == [{"signal": "ready", "from": ".", "to": ".", "method": "_on_ready", "line": None,
                                   "flags": 3, "binds": [7]}]
    assert doc["editable"] == ["Child"] and doc["sub_order"] == ["Skin_1", "Animation_run", "AnimationLibrary_a"]
    lib = animtree.libraries(doc, player["props"])
    assert lib == {"": {"@library": Ref("sub", "AnimationLibrary_a")}}
    a = animtree.animation(doc, doc["sub_resources"]["Animation_run"]["props"])
    assert a["name"] == "run" and [t["keys"] for t in a["tracks"]] == [2, 1] and a["methods"] == {"footstep": 1}
    # the JSON form is the one tres gives
    j = tres.to_json(doc["sub_resources"]["AnimationLibrary_a"]["props"])
    assert j == {"_data": {"run": {"ref": "sub", "id": "Animation_run"}}}


def test_godot3_resource_names_and_old_ids():
    enc = Enc(version=(3, 5), fmt=3, flags=0)
    res, ext = _hero_scene(enc, g3=True)
    res[-2] = ("1", "Animation", {**_anim_props(), "rot": Ctor("Quat", (0.0, 0.0, 0.0, 1.0)),
                                  "xf": Ctor("Transform", tuple(float(i) for i in range(12))),
                                  "arr": Ctor("PoolRealArray", (1.0, 2.0))})
    res[0] = ("0", "Skin", {"bind_count": 2})
    # Godot 3 internal references are the number in local://N
    scene = res[-1][2]["_bundled"]
    scene["variants"] = [Ref("sub", "0") if v == Ref("sub", "Skin_1") else Ref("sub", "1") if v == Ref("sub", "Animation_run")
                         else v for v in scene["variants"]]
    enc.ids = []

    class E3(Enc):
        def var(self, v):
            if isinstance(v, Ref) and v.kind == "sub":
                return self.p("III", 24, 2, int(v.id))
            return super().var(v)

    e3 = E3(version=(3, 5), fmt=3, flags=0)
    doc = rsrc.read_bytes(e3.build("PackedScene", res, [(t, p, -1) for t, p, _u in ext]))
    assert doc["binary"]["ver_major"] == 3 and "uid" not in doc["header"]
    anim = doc["sub_resources"]["1"]["props"]
    assert anim["rot"] == Ctor("Quat", (0.0, 0.0, 0.0, 1.0)) and anim["xf"].name == "Transform"
    assert anim["arr"] == Ctor("PoolRealArray", (1.0, 2.0))
    player = doc["nodes"][3]
    assert animtree.libraries(doc, player["props"]) == {"": {"run": Ref("sub", "1")}}
    assert doc["nodes"][2]["props"]["skin"] == Ref("sub", "0")


def test_resource_bulk_and_skip_types():
    enc = Enc()
    big = Ctor("PackedFloat32Array", tuple(float(i) for i in range(3000)))
    res = [("ArrayMesh_1", "ArrayMesh", {"_surfaces": [{"vertex_data": Ctor("PackedByteArray", tuple(range(256)) * 40)}]}),
           ("Animation_x", "Animation", {"tracks/0/type": "rotation_3d", "tracks/0/path": NodePath("S:b"),
                                         "tracks/0/keys": big}),
           ("", "Resource", {"mesh": Ref("sub", "ArrayMesh_1"), "anim": Ref("sub", "Animation_x"),
                             "colors": Ctor("PackedColorArray", (1.0, 0.5, 0.25, 1.0)), "d": {1: "one", "k": [1, 2.5]},
                             "big": 2**40, "nested": [StringName("a"), None, True]})]
    data = enc.build("Resource", res, compress=2)
    full = rsrc.read_bytes(data)
    assert full["kind"] == "resource" and full["resource"]["props"]["d"] == {"1": "one", "k": [1, 2.5]}
    assert full["resource"]["props"]["big"] == 2**40 and full["resource"]["props"]["nested"] == ["a", None, True]
    assert len(full["sub_resources"]["ArrayMesh_1"]["props"]["_surfaces"][0]["vertex_data"].args) == 10240
    lazy = rsrc.read_bytes(data, bulk=4096, skip_types=("ArrayMesh",))
    keys = lazy["sub_resources"]["Animation_x"]["props"]["tracks/0/keys"]
    assert keys == tres.Bulk("PackedFloat32Array", 3000) and animtree.track_keys("rotation_3d", keys) == 500
    assert tres.to_json(keys) == {"type": "PackedFloat32Array", "count": 3000, "decoded": False}
    assert lazy["sub_resources"]["ArrayMesh_1"] == {"type": "ArrayMesh", "props": {}, "line": None,
                                                    "offset": lazy["sub_resources"]["ArrayMesh_1"]["offset"], "skipped": True}
    assert lazy["binary"]["blocks_decoded"] < full["binary"]["blocks_decoded"]
    assert lazy["resource"]["props"]["colors"] == Ctor("PackedColorArray", (1.0, 0.5, 0.25, 1.0))


def test_reader_errors_and_headers():
    enc = Enc()
    data = enc.build("Resource", [("", "Resource", {"a": 1})])
    assert rsrc.header_bytes(data)["type"] == "Resource"
    for bad in (data[:40], b"RSRC" + struct.pack("<5I", 0, 0, 5, 0, 6), b"NOPE" + data[4:]):
        with pytest.raises(rsrc.RsrcError):
            rsrc.read_bytes(bad)
    ctex = b"GST2" + struct.pack("<4Ii3I", 1, 64, 32, (1 << 23) | (1 << 26), -1, 0, 0, 0)
    ctex += struct.pack("<IHHII", 2, 64, 32, 7, 19)
    h = rsrc.ctex_header(ctex)
    assert h["width"] == 64 and h["data_format"] == "webp" and h["image_format"] == "DXT5"
    assert h["flags"] == ["has_mipmaps", "detect_normal"] and h["mipmaps"] == 7


# --- scene -> model -> skeleton links + analyses ------------------------------------------------------------

def _model_scn() -> bytes:
    """An imported glb scene: hero(Node3D) > Armature > Skeleton3D (3 bones) > Body (skinned); AnimationPlayer whose
    'run' animation targets Armature/Skeleton3D."""
    enc = Enc()
    names = ["hero", "Node3D", "Armature", "Skeleton3D", "Body", "MeshInstance3D", "AnimationPlayer",
             "bones/0/name", "bones/0/parent", "bones/1/name", "bones/1/parent", "bones/2/name", "bones/2/parent",
             "skin", "libraries/"]
    N = {n: i for i, n in enumerate(names)}
    variants = ["hips", -1, "spine", 0, "head", 1, Ref("sub", "Skin_1"), Ref("sub", "AnimationLibrary_a")]
    nodes = [{"name": N["hero"], "type": N["Node3D"]},
             {"parent": 0, "owner": 0, "name": N["Armature"], "type": N["Node3D"]},
             {"parent": 1, "owner": 0, "name": N["Skeleton3D"], "type": N["Skeleton3D"],
              "props": [(N["bones/0/name"], 0), (N["bones/0/parent"], 1), (N["bones/1/name"], 2), (N["bones/1/parent"], 3),
                        (N["bones/2/name"], 4), (N["bones/2/parent"], 5)]},
             {"parent": 2, "owner": 0, "name": N["Body"], "type": N["MeshInstance3D"], "props": [(N["skin"], 6)]},
             {"parent": 0, "owner": 0, "name": N["AnimationPlayer"], "type": N["AnimationPlayer"],
              "props": [(N["libraries/"], 7)]}]
    anim = _anim_props()
    anim["tracks/0/path"] = NodePath("Armature/Skeleton3D:hips")
    res = [("Skin_1", "Skin", {}), ("Animation_run", "Animation", anim),
           ("AnimationLibrary_a", "AnimationLibrary", {"_data": {StringName("run"): Ref("sub", "Animation_run")}}),
           ("", "PackedScene", {"_bundled": bundled(names, variants, nodes)})]
    return enc.build("PackedScene", res, compress=2)


PLAYER = '''[gd_scene format=3]

[ext_resource type="PackedScene" path="res://models/hero.glb" id="1"]
[ext_resource type="PackedScene" path="res://rig/ragdoll.tscn" id="2"]
[ext_resource type="Shader" path="res://fx/toon.gdshader" id="3"]

[sub_resource type="ShaderMaterial" id="Mat_1"]
shader = ExtResource("3")
shader_parameter/tint = Color(1, 0, 0, 1)

[node name="Player" type="CharacterBody3D"]

[node name="hero" parent="." instance=ExtResource("1")]

[node name="Body" parent="hero/Armature/Skeleton3D" index="0"]
material_override = SubResource("Mat_1")

[node name="Sim" parent="hero/Armature/Skeleton3D" instance=ExtResource("2")]

[node name="AnimationTree" type="AnimationTree" parent="."]
root_node = NodePath("../hero")
anim_player = NodePath("../hero/AnimationPlayer")
'''
RAGDOLL = '''[gd_scene format=3]

[node name="Sim" type="PhysicalBoneSimulator3D"]

[node name="PB hips" type="PhysicalBone3D" parent="."]
bone_name = "hips"
joint_type = 2
mass = 2.0

[node name="PB spine" type="PhysicalBone3D" parent="."]
bone_name = "spine"
mass = 1.5
'''
TOON = '''// toon shading with an outline pass
shader_type spatial;
render_mode cull_back, diffuse_toon, specular_toon;
#include "res://fx/common.gdshaderinc"

group_uniforms colors;
uniform vec4 tint : source_color = vec4(1.0, 1.0, 1.0, 1.0);
uniform sampler2D ramp : hint_default_white, filter_nearest;
uniform float outline_width : hint_range(0.0, 0.1, 0.001) = 0.02;
global uniform float wind_strength;
instance uniform float dissolve_amount = 0.0;
group_uniforms;
const float PI2 = 6.28318;

void vertex() {
    VERTEX += NORMAL * outline_width * sin(TIME);
}

void fragment() {
    float n = texture(ramp, UV).r;
    if (n < dissolve_amount) { discard; }
    ALBEDO = tint.rgb;
}

void light() {
    DIFFUSE_LIGHT += smoothstep(0.0, 0.05, dot(NORMAL, LIGHT)) * ATTENUATION * LIGHT_COLOR;
}
'''


@pytest.fixture
def rig_ws(tmp_path):
    ws = tmp_path / "ws"
    proj = ws / "assets" / "godot" / "project"
    for d in ("models", "rig", "fx", ".godot/imported", "prefabs"):
        (proj / d).mkdir(parents=True, exist_ok=True)
    (proj / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (proj / "prefabs" / "player.tscn").write_text(PLAYER, encoding="utf-8")
    (proj / "rig" / "ragdoll.tscn").write_text(RAGDOLL, encoding="utf-8")
    (proj / "fx" / "toon.gdshader").write_text(TOON, encoding="utf-8")
    (proj / "fx" / "common.gdshaderinc").write_text("float luma(vec3 c) { return dot(c, vec3(0.3, 0.6, 0.1)); }\n",
                                                   encoding="utf-8")
    (proj / "models" / "hero.glb").write_bytes(b"glTF")
    imported = f"hero.glb-{hashlib.md5(b'res://models/hero.glb').hexdigest()}.scn"
    (proj / ".godot" / "imported" / imported).write_bytes(_model_scn())
    (proj / "models" / "hero.glb.import").write_text(
        '[remap]\n\nimporter="scene"\nimporter_version=1\ntype="PackedScene"\nuid="uid://abc"\n'
        f'path="res://.godot/imported/{imported}"\n\n[deps]\n\nsource_file="res://models/hero.glb"\n', encoding="utf-8")
    game = {"slug": "g", "name": "G", "buildid": "1", "install_dir": str(tmp_path / "none"),
            "engine": {"key": "godot", "version": "4.6"}}
    (ws / "game.json").write_text(json.dumps(game), encoding="utf-8")
    return ws, game, proj, imported


def test_scene_model_skeleton_chain(rig_ws):
    from gst.analysis.godot import Docs
    from gst.analysis.godot.links import Project

    ws, game, proj, imported = rig_ws
    pr = Project(proj, Docs(proj))
    info = pr.import_info("res://models/hero.glb")
    assert info["importer"] == "scene" and info["imported"] == f"res://.godot/imported/{imported}"
    (c,) = pr.chains("res://prefabs/player.tscn")
    assert c["model"] == "res://models/hero.glb" and c["model_node"] == "hero" and c["skeleton"] == "hero/Armature/Skeleton3D"
    assert c["bones"] == 3 and c["roots"] == ["hips"] and c["bone_names"] == ["hips", "spine", "head"]
    assert c["skinned_meshes"] == ["hero/Armature/Skeleton3D/Body"]
    (p,) = c["animation_players"]
    assert p["node"] == "hero/AnimationPlayer" and p["names"] == ["run"] and p["root_node"] == "hero"
    (t,) = c["animation_trees"]
    assert t["node"] == "AnimationTree" and t["anim_player"] == "hero/AnimationPlayer"
    assert c["ragdoll"]["bodies"] == 2 and c["ragdoll"]["bones"] == ["hips", "spine"]
    assert c["ragdoll"]["simulators"] == ["hero/Armature/Skeleton3D/Sim"] and c["ragdoll"]["mass_total"] == 3.5
    assert c["ragdoll"]["defined_in"] == ["res://rig/ragdoll.tscn"]
    # without the .import file the md5 naming rule finds the same imported scene
    (proj / "models" / "hero.glb.import").unlink()
    pr2 = Project(proj, Docs(proj))
    assert pr2.import_info("res://models/hero.glb")["via"] == "md5 naming rule"
    assert pr2.chains("res://prefabs/player.tscn")[0]["bones"] == 3


def test_godot_animation_characters_and_rigs_facet(rig_ws):
    from gst.library import facets

    ws, game, _proj, _imp = rig_ws
    r = analysis.run(ws, game, "godot_animation")
    assert r["status"] == "done", r["reason"]
    doc = json.loads((ws / "analysis" / "godot_animation.json").read_text(encoding="utf-8"))
    s, d = doc["summary"], doc["data"]
    assert s["characters"] == 1 and s["characters_animated"] == 1 and s["characters_ragdoll"] == 1
    assert s["characters_max_bones"] == 3 and s["parse_errors"] == 0
    assert d["characters"][0]["top_level"] is True
    rows = []
    facets.extract(ws, lambda table, row: rows.append((table, row)))
    rigs = [row for t, row in rows if t == "rigs"]
    (ch,) = [x for x in rigs if x["kind"] == "godot-character"]
    assert ch["item"] == "res://prefabs/player.tscn" and ch["bones"] == 3 and ch["mesh"] == "res://models/hero.glb"
    assert ch["root_bone"] == "hips" and ch["ref"] == "data.characters[0]"
    r2 = analysis.run(ws, game, "godot_scenes")
    assert r2["status"] == "done", r2["reason"]
    sc = json.loads((ws / "analysis" / "godot_scenes.json").read_text(encoding="utf-8"))
    player = next(f for f in sc["data"]["files"] if f["path"] == "res://prefabs/player.tscn")
    assert player["models"][0]["model"] == "res://models/hero.glb"
    assert sc["summary"]["imported_binary"] == 1 and sc["summary"]["imported_errors"] == 0


def test_godot_shaders_analysis_and_facet(rig_ws):
    from gst.library import facets

    ws, game, proj, _imp = rig_ws
    (proj / "fx" / "dissolve.tres").write_text(
        '[gd_resource type="ShaderMaterial" format=3]\n\n[sub_resource type="Shader" id="Shader_1"]\n'
        'code = "shader_type canvas_item;\\nuniform float progress : hint_range(0, 1) = 0.5;\\n'
        'void fragment() { COLOR.a *= step(progress, texture(TEXTURE, UV).r); }\\n"\n\n'
        '[resource]\nshader = SubResource("Shader_1")\nshader_parameter/progress = 0.25\n', encoding="utf-8")
    (proj / "fx" / "graph.tres").write_text(
        '[gd_resource type="VisualShader" format=3]\n\n[sub_resource type="VisualShaderNodeFloatParameter" id="P_1"]\n'
        'parameter_name = "glow"\n\n[sub_resource type="VisualShaderNodeTexture" id="T_1"]\n\n'
        '[resource]\nmode = 0\nnodes/fragment/2/node = SubResource("P_1")\nnodes/fragment/3/node = SubResource("T_1")\n',
        encoding="utf-8")
    r = analysis.run(ws, game, "godot_shaders")
    assert r["status"] == "done", r["reason"]
    doc = json.loads((ws / "analysis" / "godot_shaders.json").read_text(encoding="utf-8"))
    s, d = doc["summary"], doc["data"]
    by = {x["name"]: x for x in d["shaders"]}
    toon = by["res://fx/toon.gdshader"]
    assert toon["shader_type"] == "spatial" and toon["render_modes"] == ["cull_back", "diffuse_toon", "specular_toon"]
    u = {x["name"]: x for x in toon["uniforms"]}
    assert u["tint"]["type"] == "vec4" and u["tint"]["hints"] == ["source_color"] and u["tint"]["default"] == "vec4(1.0, 1.0, 1.0, 1.0)"
    assert u["outline_width"]["hints"] == ["hint_range(0.0, 0.1, 0.001)"] and u["outline_width"]["group"] == "colors"
    assert u["wind_strength"]["scope"] == "global" and u["dissolve_amount"]["scope"] == "instance"
    assert toon["includes"] == ["res://fx/common.gdshaderinc"] and toon["functions"] == ["vertex", "fragment", "light"]
    assert {"toon", "outline", "dissolve", "vertex_displacement", "alpha_clip"} <= set(toon["features"])
    assert toon["materials"] == 1 and toon["used_by"] == ["res://prefabs/player.tscn"]
    (mat,) = [m for m in d["materials"] if m["shader"] == "res://fx/toon.gdshader"]
    assert mat["params"] == {"tint": "Color(1, 0, 0, 1)"} and mat["where"] == "res://prefabs/player.tscn::SubResource(Mat_1)"
    emb = by["res://fx/dissolve.tres::SubResource(Shader_1)"]
    assert emb["shader_type"] == "canvas_item" and emb["materials"] == 1 and emb["uniforms"][0]["default"] == "0.5"
    vs = by["res://fx/graph.tres"]
    assert vs["kind"] == "visual_shader" and vs["uniforms"][0]["name"] == "glow" and vs["node_types"]["VisualShaderNodeTexture"] == 1
    assert by["res://fx/common.gdshaderinc"]["kind"] == "include"
    assert s["shaders"] == 4 and s["uniforms"] >= 7
    rows = []
    facets.extract(ws, lambda table, row: rows.append((table, row)))
    sh = {row["name"]: row for t, row in rows if t == "shaders"}
    assert sh["res://fx/toon.gdshader"]["family"] == "spatial" and sh["res://fx/toon.gdshader"]["uniforms"] == 5
    assert "toon" in json.loads(sh["res://fx/toon.gdshader"]["signatures"])
    assert not [g for g in facets.gaps("godot", {"godot_shaders"}, "g") if g["facet"] == "shaders"]


def test_encrypted_resources_are_reported_not_opened(tmp_path):
    from gst.analysis.godot import Docs

    (tmp_path / "secret.scn").write_bytes(b"GDEC" + b"\x01" * 60)
    docs = Docs(tmp_path)
    assert docs.get("res://secret.scn") is None
    assert docs.errors["res://secret.scn"] == "encrypted (GDEC): not decrypted"
