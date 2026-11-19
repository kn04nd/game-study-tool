"""Unity exporters with fake mesh handlers, the extractor registry, and the no-ceilings rule."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("UnityPy")

SRC = Path(__file__).resolve().parents[2] / "src" / "gst"


class FakeHandler:
    """Two triangles (one quad), 4 vertices, skinned to 2 bones."""

    m_VertexCount = 4
    m_Vertices = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    m_Normals = [(0.0, 0.0, -1.0)] * 4
    m_UV0 = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    m_BoneIndices = [(0, 0, 0, 0), (0, 1, 0, 0), (1, 0, 0, 0), (1, 0, 0, 0)]
    m_BoneWeights = [(1.0, 0, 0, 0), (0.5, 0.5, 0, 0), (1.0, 0, 0, 0), (1.0, 0, 0, 0)]

    def get_triangles(self):
        return [[(0, 1, 2), (0, 2, 3)]]


def _identity():
    return SimpleNamespace(**{f"e{r}{c}": 1.0 if r == c else 0.0 for r in range(4) for c in range(4)})


def test_gltf_skinned_quad(tmp_path):
    pytest.importorskip("pygltflib")
    from gst.engines.unity.export import gltf

    bp = _identity()
    bp.e03 = 2.0  # translation x in the bindpose -> mirrored in glTF
    bones = [{"name": "root", "t": [0, 0, 0], "r": [0, 0, 0, 1], "s": [1, 1, 1], "parent": None, "joint": True},
             {"name": "tip", "t": [0, 1, 0], "r": [0, 0, 0, 1], "s": [1, 1, 1], "parent": 0, "joint": True}]
    out = tmp_path / "quad.glb"
    stats = gltf.build_glb("quad", FakeHandler(), bones, [bp, _identity()], out)
    assert stats == {"vertices": 4, "triangles": 2, "joints": 2, "nodes": 3}
    summary = gltf.read_glb_summary(out)
    assert summary["magic"] == 0x46546C67 and summary["skins"] == 1 and summary["joints"] == 2
    import pygltflib as g

    doc = g.GLTF2().load_binary(str(out))
    pos = doc.accessors[doc.meshes[0].primitives[0].attributes.POSITION]
    assert pos.max[0] == 0.0 and pos.min[0] == -1.0  # X mirrored
    assert doc.nodes[1].children == [2] and doc.scenes[0].nodes == [0, 1]
    blob = doc.binary_blob()
    ibm = doc.accessors[doc.skins[0].inverseBindMatrices]
    view = doc.bufferViews[ibm.bufferView]
    import struct

    m0 = struct.unpack_from("<16f", blob, view.byteOffset)
    assert m0[12] == -2.0  # column-major translation x, mirrored


def test_gltf_static_when_no_bones(tmp_path):
    pytest.importorskip("pygltflib")
    from gst.engines.unity.export import gltf

    stats = gltf.build_glb("quad", FakeHandler(), [], [], tmp_path / "s.glb")
    assert stats["joints"] == 0 and gltf.read_glb_summary(tmp_path / "s.glb")["skins"] == 0


def test_obj_text_and_stats():
    from gst.engines.unity.export import mesh

    m = SimpleNamespace(m_Name="quad", m_SubMeshes=[1], m_BindPose=[1, 2], m_Shapes=None)
    text = mesh.obj_text(m, FakeHandler())
    assert text.count("\nv ") == 4 and "v -1 0 0" in text and "f 3/3/3 2/2/2 1/1/1" in text
    assert mesh.mesh_stats(m, FakeHandler()) == {"vertices": 4, "triangles": 2, "submeshes": 1, "bindposes": 2,
                                                  "skinned": True, "blendshapes": 0}


def test_material_pairs_and_addressables(tmp_path):
    from gst.engines.unity.export import addressables, material

    assert dict(material._pairs([["_MainTex", 1], {"first": "_Color", "second": 2}])) == {"_MainTex": 1, "_Color": 2}
    summary = addressables.decode_json_catalog({"m_InternalIds": ["{UnityEngine.AddressableAssets}/a.bundle", "Assets/x.prefab"],
                                                "m_ProviderIds": ["P"], "m_KeyDataString": "abcd"})
    assert summary["bundles"] == ["{UnityEngine.AddressableAssets}/a.bundle"] and summary["key_data_bytes"] == 4
    aa = tmp_path / "G_Data" / "StreamingAssets" / "aa"
    aa.mkdir(parents=True)
    (aa / "catalog.json").write_text('{"m_InternalIds": ["x.bundle"]}', encoding="utf-8")
    (aa / "settings.json").write_text("{}", encoding="utf-8")
    found = [p.name for p in addressables.find_catalogs(tmp_path)]
    assert found == ["catalog.json", "settings.json"]


def test_to_jsonable():
    from gst.engines.unity.export import to_jsonable

    assert to_jsonable({"a": b"\x01\x02", "b": float("nan"), "c": (1, 2)}) == {"a": {"_bytes_b64": "AQI="}, "b": "nan",
                                                                              "c": [1, 2]}


def test_unity_extractors_registered():
    from gst.extractors import registry
    from gst.extractors.base import CATEGORIES, RESOURCES

    names = {cls.name: cls for cls in registry.for_engine("unity-il2cpp")}
    expected = {"unity.inventory", "unity.textures", "unity.sprites", "unity.audio", "unity.meshes", "unity.gltf",
                "unity.animations", "unity.materials", "unity.shaders", "unity.monobehaviours", "unity.prefabs_scenes",
                "unity.text", "unity.fonts", "unity.video", "unity.terrain", "unity.navmesh", "unity.addressables",
                "unity.localization", "unity.assetripper_project"}
    assert expected <= set(names)
    assert {c.name for c in registry.for_engine("unity-mono")} >= expected
    for cls in names.values():
        assert cls.category in CATEGORIES and cls.resource in RESOURCES
    assert names["unity.assetripper_project"].resource == "heavy"
    # ordering attributes: inventory first, exports after it, AssetRipper last
    assert names["unity.inventory"].stage == 40 and names["unity.assetripper_project"].stage == 70
    for n, cls in names.items():
        if n.startswith("unity.") and n not in ("unity.inventory", "unity.addressables"):
            assert "unity.inventory" in cls.after and cls.stage >= 50, n


def test_discover_units_and_inventory_filter(tmp_path):
    import json

    from gst.engines.unity import extractors
    from gst.extractors.base import Context
    from test_unity_streaming import _serialized_header, make_bundle

    install = tmp_path / "Game"
    data = install / "Game_Data"
    (data / "StreamingAssets").mkdir(parents=True)
    (data / "level0").write_bytes(_serialized_header(256))
    (data / "level0.resS").write_bytes(b"r" * 100)
    make_bundle(data / "StreamingAssets" / "m.bundle", [("CAB-m", b"q" * 300)])
    ws = tmp_path / "ws"
    ctx = Context(workspace=ws, game={"buildid": "9"}, install_dir=install, out_root=ws / "assets" / "unity",
                  policy="offline-ok")
    units = list(extractors.UnityTextures().discover(ctx))
    assert [u.id for u in units] == ["Game_Data/level0", "Game_Data/StreamingAssets/m.bundle"]
    assert units[0].size == 356 and units[1].meta["uncompressed"] == 300 and units[1].meta["kind"] == "bundle"
    inv = ws / "assets" / "unity" / "_inventory"
    inv.mkdir(parents=True)
    (inv / "Game_Data__level0.summary.json").write_text(json.dumps({"file": "Game_Data/level0", "types": {"Mesh": 3}}), encoding="utf-8")
    assert [u.id for u in extractors.UnityTextures().discover(ctx)] == ["Game_Data/StreamingAssets/m.bundle"]
    inv_units = list(extractors.UnityInventory().discover(ctx))
    assert [u.id for u in inv_units] == ["Game_Data/level0", "Game_Data/StreamingAssets/m.bundle"]
    assert extractors.UnityInventory.has_finalize() and extractors.UnityInventory.out_subdir == "assets/unity"
    res = extractors.UnityInventory().finalize(ctx)  # INDEX.json + INVENTORY.md, as the <finalize> job
    assert res.status == "done" and res.outputs == ["assets/unity/_inventory/INDEX.json", "assets/INVENTORY.md"]


def test_too_large_bundle_is_unsupported(tmp_path, monkeypatch):
    from gst.engines.unity import bundle, extractors
    from gst.extractors.base import Context, Unit
    from test_unity_streaming import make_bundle

    install = tmp_path / "Game"
    (install / "Game_Data").mkdir(parents=True)
    make_bundle(install / "Game_Data" / "big.bundle", [("CAB-b", b"q" * 500)], block=500)
    monkeypatch.setattr(bundle, "available_ram", lambda: 10)
    ctx = Context(workspace=tmp_path / "ws", game={}, install_dir=install, out_root=tmp_path / "ws" / "assets" / "unity",
                  policy="offline-ok")
    unit = Unit(id="Game_Data/big.bundle", extractor="unity.meshes", relpaths=["Game_Data/big.bundle"], size=500)
    res = extractors.UnityMeshes().run(unit, ctx)
    assert res.status == "unsupported" and "fallback assetripper" in " ".join(res.notes)


UNITY_MODULES = sorted([*(SRC / "engines" / "unity").rglob("*.py"), SRC / "assets" / "unity.py",
                        SRC / "assets" / "assetripper.py"])


@pytest.mark.parametrize("path", UNITY_MODULES, ids=lambda p: p.name)
def test_no_ceilings_in_unity_modules(path):
    """No DEFAULT_LIMIT, no constant timeouts, no truncated error text (docs/INTERFACES.md)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id != "DEFAULT_LIMIT", f"{path.name}:{node.lineno}"
        if isinstance(node, ast.keyword) and node.arg == "timeout":
            assert not isinstance(node.value, ast.Constant) or node.value.value is None, f"{path.name}:{node.lineno}"
        if isinstance(node, ast.arg) and node.arg == "timeout":
            pass
        if isinstance(node, ast.FunctionDef):
            for arg, default in zip(reversed(node.args.args + node.args.kwonlyargs),
                                    reversed(node.args.defaults + [d for d in node.args.kw_defaults if d is not None])):
                if arg.arg in ("timeout", "limit"):
                    assert isinstance(default, ast.Constant) and default.value is None, f"{path.name}:{node.lineno}"
