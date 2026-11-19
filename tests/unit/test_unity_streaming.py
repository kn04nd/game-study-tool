"""Unity backend: lazy bundles, file classification, PPtr fixes, inventory merge, writer, typetree chain."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("UnityPy")

from gst.engines.unity import bundle, inventory, loader, typetree  # noqa: E402
from gst.engines.unity.export import Writer, source_folder  # noqa: E402


def make_bundle(path: Path, nodes: list[tuple[str, bytes]], *, block: int = 64, lz4: bool = True,
                engine: str = "2022.3.1f1") -> bytes:
    """A minimal UnityFS (version 7) bundle: blocks info right after the header (uncompressed)."""
    import lz4.block

    data = b"".join(d for _n, d in nodes)
    blocks, payload = [], b""
    for i in range(0, len(data), block):
        chunk = data[i:i + block]
        if lz4 and i // block % 2 == 0:
            comp = lz4.block.compress(chunk, store_size=False)
            blocks.append((len(chunk), len(comp), 2))
        else:
            comp = chunk
            blocks.append((len(chunk), len(comp), 0))
        payload += comp
    info = b"\0" * 16 + struct.pack(">i", len(blocks))
    info += b"".join(struct.pack(">IIH", u, c, f) for u, c, f in blocks)
    info += struct.pack(">i", len(nodes))
    off = 0
    for name, d in nodes:
        info += struct.pack(">qqI", off, len(d), 4) + name.encode() + b"\0"
        off += len(d)
    head = b"UnityFS\0" + struct.pack(">I", 7) + b"5.x.x\0" + engine.encode() + b"\0"
    head += struct.pack(">qIII", 0, len(info), len(info), 0x40)
    head += b"\0" * ((16 - len(head) % 16) % 16)
    raw = head + info + payload
    path.write_bytes(raw)
    return data


def test_bundle_header_and_lazy_reads(tmp_path):
    a = bytes(range(256)) * 3
    b = b"resource-bytes-" * 20
    data = make_bundle(tmp_path / "x.bundle", [("CAB-abc", a), ("CAB-abc.resS", b)])
    h = bundle.read_header(tmp_path / "x.bundle")
    assert [n[3] for n in h.nodes] == ["CAB-abc", "CAB-abc.resS"]
    assert h.uncompressed_size == len(data) and h.largest_block == 64 and "lz4" in h.compression
    io_ = bundle.BlocksIO(tmp_path / "x.bundle", h, cache_bytes=128)
    try:
        for pos, n in [(0, 10), (60, 10), (63, 200), (len(data) - 5, 50), (100, 0)]:
            assert io_.read_at(pos, n) == data[pos:pos + n]
        assert io_.cached <= 128 + 64  # LRU keeps the working set bounded
        sl = bundle.SliceIO(io_, len(a), len(b))
        assert sl.read(15) == b"resource-bytes-" and sl.seek(0, 2) == len(b) and sl.read() == b""
        sl.seek(5)
        assert sl.read() == b[5:]
    finally:
        io_.close()


def test_bundle_too_large_and_encrypted(tmp_path, monkeypatch):
    make_bundle(tmp_path / "x.bundle", [("CAB-a", b"z" * 300)], block=256)
    h = bundle.read_header(tmp_path / "x.bundle")
    monkeypatch.setattr(bundle, "available_ram", lambda: 100)
    with pytest.raises(bundle.BundleTooLarge, match="does not fit"):
        bundle.BlocksIO(tmp_path / "x.bundle", h)
    raw = bytearray((tmp_path / "x.bundle").read_bytes())
    i = raw.index(b"2022.3.1f1\0") + len(b"2022.3.1f1\0") + 8 + 8
    raw[i:i + 4] = struct.pack(">I", 0x40 | 0x1000)
    (tmp_path / "enc.bundle").write_bytes(bytes(raw))
    with pytest.raises(ValueError, match="encrypted"):
        bundle.read_header(tmp_path / "enc.bundle")


def _serialized_header(size: int, version: int = 22) -> bytes:
    head = struct.pack(">IIII", 0, 0, version, 0) + b"\0\0\0\0" + struct.pack(">I", 100) + struct.pack(">qq", size, 4096)
    return head + b"\0" * (size - len(head))


def test_classify_and_iter_files(tmp_path):
    data = tmp_path / "Game_Data"
    (data / "StreamingAssets" / "aa").mkdir(parents=True)
    (tmp_path / "Other_Data").mkdir()
    (data / "level0").write_bytes(_serialized_header(256))
    (data / "level0.resS").write_bytes(b"x" * 10)
    (data / "notes.txt").write_bytes(b"hello world, not unity" * 4)
    (data / "broken.assets").write_bytes(_serialized_header(256)[:-1])  # size mismatch -> not serialized
    make_bundle(data / "StreamingAssets" / "aa" / "a.bundle", [("CAB-a", b"q" * 40)])
    (tmp_path / "Other_Data" / "globalgamemanagers").write_bytes(_serialized_header(128, version=17)[:4]
                                                                  + struct.pack(">I", 128)
                                                                  + _serialized_header(128, version=17)[8:])
    assert loader.classify(data / "level0") == "serialized"
    assert loader.classify(data / "level0.resS") == "resource"
    assert loader.classify(data / "notes.txt") is None
    assert loader.classify(data / "broken.assets") is None
    found = {rel: kind for rel, kind, _ in loader.iter_unity_files(tmp_path)}
    assert found == {"Game_Data/level0": "serialized", "Game_Data/StreamingAssets/aa/a.bundle": "bundle",
                     "Other_Data/globalgamemanagers": "serialized"}  # every *_Data folder
    assert loader.resource_size(tmp_path, "Game_Data/level0") == 10


def test_pptr_fixes():
    assert loader.fix_pptr(16777216, 2701 << 24) == (1, 2701)
    assert loader.fix_pptr(1, 2701) == (1, 2701) and loader.fix_pptr(0, 5) == (0, 5)

    raw = struct.pack("<iq", 0, 77) + b"\x01\0\0\0" + struct.pack("<iq", 1, 2701)

    class FakeReader:
        endian = "<"

        def __init__(self):
            self.pos = 0

        def read_bytes(self, n):
            out = raw[self.pos:self.pos + n]
            self.pos += n
            return out

    r = FakeReader()

    class Obj:
        reader = r
        Position = 0

        def reset(self):
            r.pos = 0

    assert loader.raw_script_pptr(Obj()) == (1, 2701)
    assert loader.raw_gameobject_pid(Obj()) == 77
    assert loader.unwrap_offsetptr({"data": {"m_Clip": 1}}) == {"m_Clip": 1}


def _write_table(inv: Path, rel: str, cabs: dict[str, list[dict]]) -> Path:
    import gzip

    path = inventory.table_path(inv, rel)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"_header": 1, "file": rel, "kind": "bundle" if any(cabs) else "serialized"}) + "\n")
        for cab, rows in cabs.items():
            if cab:
                fh.write(json.dumps({"_cab": cab}) + "\n")
            fh.writelines(json.dumps(r) + "\n" for r in rows)
    return path


def test_inventory_index_and_streaming_reader(tmp_path):
    inv = tmp_path / "unity" / "_inventory"
    inv.mkdir(parents=True)
    rows = [{"type": "Texture2D", "class_id": 28, "name": "a", "path_id": 5, "size": 10},
            {"type": "Mesh", "class_id": 43, "name": "m", "path_id": 6, "size": 20},
            {"type": "MonoScript", "class_id": 115, "name": "Player", "path_id": 7, "size": 30}]
    table = _write_table(inv, "G_Data/resources.assets", {"": rows})
    _write_table(inv, "G_Data/StreamingAssets/m.bundle", {"CAB-1": [{"type": "Mesh", "class_id": 43, "name": "rock",
                                                                     "path_id": 1, "size": 5}]})
    legacy = {"type": "Mesh", "class_id": 43, "name": "old", "file": "G_Data/level0", "path_id": 9, "size": 1}
    (inv / "G_Data__level0.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")  # pre-format-2 table
    (inv / "G_Data__resources.assets.summary.json").write_text(json.dumps(
        {"file": "G_Data/resources.assets", "objects": 3, "types": {"Texture2D": 1, "Mesh": 1, "MonoScript": 1},
         "bytes": {"Texture2D": 10, "Mesh": 20, "MonoScript": 30}, "input_bytes": 999, "unity_version": "6000.3.1f1",
         "table": table.name, "table_bytes": table.stat().st_size}), encoding="utf-8")
    (inv / inventory.RESOURCES_MAP).write_text(json.dumps({"container": "textures/a", "file": "resources.assets",
                                                           "path_id": 5}) + "\n", encoding="utf-8")
    info = inventory.merge(inv, inv / inventory.INDEX, tmp_path / "INVENTORY.md", "G")
    assert info == {"files": 1, "objects": 3, "types": 3, "unity_version": "6000.3.1f1"}
    index = json.loads((inv / inventory.INDEX).read_text(encoding="utf-8"))
    assert index["format"] == 2 and index["files"]["G_Data/resources.assets"]["table"].endswith(".jsonl.gz")
    assert not (tmp_path / "inventory.jsonl").exists()  # no merged all-objects file
    md = (tmp_path / "INVENTORY.md").read_text(encoding="utf-8")
    assert "| Texture2D | 1 |" in md and "`G_Data/resources.assets`" in md
    meshes = list(inventory.iter_rows(inv, types=("Mesh",)))
    assert {(r["file"], r["name"], r.get("cab")) for r in meshes} == {
        ("G_Data/resources.assets", "m", None), ("G_Data/StreamingAssets/m.bundle", "rock", "CAB-1"),
        ("G_Data/level0", "old", None)}
    tex = list(inventory.iter_rows(inv, files="resources", name="^a$"))
    assert tex == [{**rows[0], "file": "G_Data/resources.assets", "container": "textures/a"}]
    assert list(inventory.iter_rows(inv, files=["G_Data/level0"])) == [{**legacy, "container": ""}]
    assert inventory.monoscripts(inv) == {("resources.assets", 7): "Player"}
    assert not list(tmp_path.rglob("*.partial*"))


def test_search_uses_inventory_tables(tmp_path):
    from gst.assets import unity

    ws = tmp_path / "ws"
    inv = ws / "assets" / "unity" / "_inventory"
    inv.mkdir(parents=True)
    _write_table(inv, "G_Data/level0", {"": [{"type": "Mesh", "class_id": 43, "name": "rock", "path_id": 1, "size": 5}]})
    (inv / inventory.INDEX).write_text("{}", encoding="utf-8")
    rows = unity.search(tmp_path / "no-install", types=("Mesh",), name="ro", ws=ws)  # never opens the install
    assert [(r["file"], r["name"]) for r in rows] == [("G_Data/level0", "rock")]


def test_writer_atomic_sidecar_and_deterministic_names(tmp_path):
    ws = tmp_path / "ws"
    w = Writer(ws / "assets" / "unity", ws, source="G_Data/StreamingAssets/aa/x.bundle", build="123", tool="UnityPy",
               tool_version="1.25.4", extractor="unity.textures", unit="u")
    p1 = w.target("Texture2D", "Hero", ".png", 1)
    p2 = w.target("Texture2D", "hero", ".png", 2)
    assert p1.parent.name == "StreamingAssets__aa__x.bundle" and p2.name == "hero__2.png"
    w.commit(p1, b"\x89PNG", meta={"path_id": 1, "name": "Hero", "dimensions": [4, 4], "container": ""})
    side = json.loads(Path(str(p1) + ".gs.json").read_text(encoding="utf-8"))
    assert side["size"] == 4 and side["path_id"] == 1 and side["build"] == "123" and side["tool_version"] == "1.25.4"
    assert side["source"] == "G_Data/StreamingAssets/aa/x.bundle" and side["dimensions"] == [4, 4]
    assert "container" not in side and len(side["sha256"]) == 64
    assert w.outputs == ["assets/unity/Texture2D/StreamingAssets__aa__x.bundle/Hero.png"] and w.out_bytes == 4
    assert not any((ws / "assets" / "unity" / ".partial").iterdir())
    w2 = Writer(ws / "assets" / "unity", ws, source="G_Data/StreamingAssets/aa/x.bundle", build="123", tool="UnityPy",
                tool_version="", extractor="unity.textures", unit="u")
    assert w2.target("Texture2D", "Hero", ".png", 1) == p1  # a re-run overwrites, no __id pile-up
    assert source_folder("Game_Data/level0") == "level0"


def test_typetree_source_chain_and_backup_snapshot(tmp_path):
    install = tmp_path / "Game"
    (install / "Game_Data" / "Managed").mkdir(parents=True)
    backup = install / "Game_BackUpThisFolder_ButDontShipItWithYourGame" / "Managed"
    backup.mkdir(parents=True)
    (backup / "Assembly-CSharp.dll").write_bytes(b"MZ fake")
    (install / "GameAssembly.dll").write_bytes(b"MZ")
    meta = install / "Game_Data" / "il2cpp_data" / "Metadata"
    meta.mkdir(parents=True)
    (meta / "global-metadata.dat").write_bytes(b"\xaf\x1b\xb1\xfa")
    ws = tmp_path / "ws"
    kinds = [s["kind"] for s in typetree.sources(install, ws, "42")]
    assert kinds == ["backup", "il2cpp"]  # empty Managed (IL2CPP build) is skipped
    snap = typetree.snapshot_backup(backup, ws, "42")
    assert (snap / "Assembly-CSharp.dll").read_bytes() == b"MZ fake" and snap.name == "managed-backup@42"
    import shutil

    shutil.rmtree(install / "Game_BackUpThisFolder_ButDontShipItWithYourGame")  # an update deleted it
    (ws / "decomp" / "il2cpp" / "dummydll").mkdir(parents=True)
    (ws / "decomp" / "il2cpp" / "dummydll" / "Assembly-CSharp.dll").write_bytes(b"MZ")
    kinds = [s["kind"] for s in typetree.sources(install, ws, "42")]
    assert kinds == ["backup", "il2cpp", "dummydll"]  # the snapshot survives the update


def test_read_mono_prefilter_never_reads_unknown_types(monkeypatch):
    calls = []
    monkeypatch.setattr(typetree, "read_bounded", lambda obj, node: (calls.append(node) or {"x": 1}, True))

    class Gen:
        def get_nodes_up(self, assembly, fullname):
            if fullname != "Known":
                raise KeyError(fullname)
            return "node"

    class Obj:
        serialized_type = None

        def read_typetree(self, nodes=None, check_read=True):
            calls.append(nodes)
            return {"x": 1}

    from gst.engines.unity.scripts import ScriptInfo

    assert typetree.read_mono(Obj(), Gen(), ScriptInfo("Unknown", "", "A")) is None
    assert typetree.read_mono(Obj(), None, ScriptInfo("Known", "", "A")) is None
    assert calls == []
    assert typetree.read_mono(Obj(), Gen(), ScriptInfo("Known", "", "A")) == {"x": 1} and calls == ["node"]


def test_lazy_bundle_registers_resource_nodes(tmp_path):
    import UnityPy

    make_bundle(tmp_path / "x.bundle", [("CAB-abc", b"\0" * 64), ("CAB-abc.resS", b"RESDATA!")])
    env = UnityPy.Environment(path=str(tmp_path))
    b = bundle.load_bundle(env, tmp_path / "x.bundle")
    try:
        reader = env.get_cab("CAB-abc.resS")
        reader.Position = 3
        assert reader.read_bytes(4) == b"DATA"
        assert "cab-abc" in env.cabs.pending  # serialized CAB parsed only on demand
    finally:
        b._lazy_blocks.close()


def test_mem_numbers():
    from gst.engines.unity import mem

    assert mem.available_ram() > 0 and mem.peak_rss() >= 0


def test_devrun_ctx(tmp_path):
    from gst.engines.unity import devrun

    (tmp_path / "game.json").write_text(json.dumps({"name": "G", "buildid": "7"}), encoding="utf-8")
    ctx = devrun.make_ctx(tmp_path / "inst", tmp_path, {"limit": 3})
    assert ctx.out_root == tmp_path / "assets" / "unity" and ctx.game["buildid"] == "7" and ctx.options == {"limit": 3}
    assert SimpleNamespace  # keep import used


def test_read_bounded_stops_at_object_bytes():
    """A node that does not match the data (huge array length) fails at the end of the object's bytes."""
    from UnityPy.helpers.TypeTreeNode import TypeTreeNode

    def n(level, typ, name, size, children=(), flags=0):
        return TypeTreeNode(level, typ, name, size, 1, list(children), m_MetaFlag=flags)

    arr = n(1, "vector", "m_List", -1, [n(2, "Array", "Array", -1, [n(3, "int", "size", 4), n(3, "int", "data", 4)], 0x4000)])
    root = n(0, "MonoBehaviour", "Base", -1, [n(1, "int", "m_Value", 4), arr])
    good = struct.pack("<ii", 7, 2) + struct.pack("<ii", 10, 20)
    bad = struct.pack("<ii", 7, 900_000_000)  # garbage length

    class Obj:
        def __init__(self, raw):
            self.raw = raw
            self.reader = SimpleNamespace(endian="<")
            self.assets_file = None

        def get_raw_data(self):
            return self.raw

    tree, complete = typetree.read_bounded(Obj(good), root)
    assert tree == {"m_Value": 7, "m_List": [10, 20]} and complete
    with pytest.raises(Exception):
        typetree.read_bounded(Obj(bad), root)
