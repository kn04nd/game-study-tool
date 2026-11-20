"""Asset analyses on synthetic data (no game material): gltf_summary, dcc, shaders, textures, audio_refs, audio_catalog."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from gst import analysis


# --- a tiny glb built here --------------------------------------------------------------------------

def make_glb(path: Path, *, generator: str = "Khronos glTF Blender I/O v4.2", skinned: bool = True) -> Path:
    """Two triangles (4 verts), optional skin with 2 joints (one vertex weighted 50/50), one 3-key animation."""
    pos = struct.pack("<12f", 0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0)
    idx = struct.pack("<6H", 0, 1, 2, 0, 2, 3) + b"\0\0"
    joints = struct.pack("<16B", 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0)
    weights = struct.pack("<16f", 1, 0, 0, 0, 1, 0, 0, 0, .5, .5, 0, 0, 1, 0, 0, 0)
    ibm = struct.pack("<32f", *([100, 0, 0, 0, 0, 100, 0, 0, 0, 0, 100, 0, 0, 0, 0, 1] * 2))
    times = struct.pack("<3f", 0, 1 / 24, 2 / 24)
    rots = struct.pack("<12f", *([0, 0, 0, 1] * 3))
    chunks = [pos, idx, joints, weights, ibm, times, rots]
    offs, buf = [], b""
    for c in chunks:
        offs.append(len(buf))
        buf += c
    views = [{"buffer": 0, "byteOffset": o, "byteLength": len(c)} for o, c in zip(offs, chunks)]
    acc = [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 0]},
           {"bufferView": 1, "componentType": 5123, "count": 6, "type": "SCALAR"},
           {"bufferView": 2, "componentType": 5121, "count": 4, "type": "VEC4"},
           {"bufferView": 3, "componentType": 5126, "count": 4, "type": "VEC4"},
           {"bufferView": 4, "componentType": 5126, "count": 2, "type": "MAT4"},
           {"bufferView": 5, "componentType": 5126, "count": 3, "type": "SCALAR", "min": [0], "max": [2 / 24]},
           {"bufferView": 6, "componentType": 5126, "count": 3, "type": "VEC4"}]
    attrs = {"POSITION": 0}
    if skinned:
        attrs.update(JOINTS_0=2, WEIGHTS_0=3)
    j = {"asset": {"version": "2.0", "generator": generator},
         "nodes": [{"name": "Armature", "rotation": [-0.7071068, 0, 0, 0.7071068], "children": [1, 2]},
                   {"name": "ORG_hand.L"}, {"name": "Body.001", "mesh": 0, **({"skin": 0} if skinned else {})}],
         "meshes": [{"name": "Icosphere", "primitives": [{"attributes": attrs, "indices": 1, "material": 0}]}],
         "materials": [{"name": "Material.001"}], "accessors": acc, "bufferViews": views,
         "buffers": [{"byteLength": len(buf)}],
         "animations": [{"name": "Armature|Wave", "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}}],
                         "samplers": [{"input": 5, "output": 6}]}]}
    if skinned:
        j["skins"] = [{"joints": [0, 1], "inverseBindMatrices": 4}]
    js = json.dumps(j).encode()
    js += b" " * (-len(js) % 4)
    buf += b"\0" * (-len(buf) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(buf), 0x004E4942) + buf
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
    return path


@pytest.fixture
def ws_any(tmp_path):
    ws = tmp_path / "ws"
    (ws / "notes").mkdir(parents=True)
    game = {"slug": "g", "name": "G", "buildid": "1", "install_dir": str(tmp_path / "nowhere"),
            "engine": {"key": "godot", "version": "4.6"}}
    (ws / "game.json").write_text(json.dumps(game), encoding="utf-8")
    return ws, game


def test_gltf_summary_one_file(tmp_path):
    from gst.analysis.gltf_summary import summarize

    r = summarize(make_glb(tmp_path / "a.glb"), rel="a.glb", bones=True)
    assert (r["tris"], r["verts"], r["n_meshes"], r["skins"], r["bones"]) == (2, 4, 1, [2], ["Armature", "ORG_hand.L"])
    assert r["infl_hist"] == {"1": 3, "2": 1} and r["verts_multi_bone"] == 1 and r["max_joint_index"] == 1
    assert r["ibm_scale_min"] == r["ibm_scale_max"] == 100.0
    a = r["anims"][0]
    assert (a["name"], a["channels"], a["rotation"], a["keys"], a["dur"]) == ("Armature|Wave", 1, 1, 3, 0.083)
    assert a["dt_min"] == pytest.approx(1 / 24, abs=1e-4)
    assert r["bbox"] == [0, 0, 0, 1, 1, 0] and r["node_signatures"] == {"rotX-90": 1} and r["materials"] == ["Material.001"]


def test_gltf_summary_analysis_families_and_gdre_note(ws_any):
    ws, game = ws_any
    make_glb(ws / "assets" / "godot" / "p" / "models" / "chars" / "hero.glb", generator="GDRE Tools v2.7.0")
    make_glb(ws / "assets" / "godot" / "p" / "models" / "props" / "box.glb", generator="GDRE Tools v2.7.0", skinned=False)
    (ws / "assets" / "godot" / "p" / "models" / "bad.glb").write_bytes(b"nope")
    res = analysis.run(ws, game, "gltf_summary", {"detail": "hero"})
    assert res["status"] == "done", res["reason"]
    doc = json.loads((ws / "analysis" / "gltf_summary.json").read_text(encoding="utf-8"))
    s = doc["summary"]
    assert (s["files"], s["errors"], s["tris"], s["skinned"]) == (3, 1, 4, 1)
    fams = doc["data"]["families"]
    assert doc["data"]["family_base"] == "assets/godot/p/models" and set(fams) == {"chars", "props"}
    assert fams["chars"]["joints"] == {"2": 1} and "props" in fams
    assert any("GDRE" in n for n in doc["notes"])
    assert (ws / "analysis" / "gltf_summary" / "assets__godot__p__models__chars__hero.glb.json").is_file()


# --- dcc --------------------------------------------------------------------------------------------

def test_dcc_rules_and_verdicts():
    from gst.analysis.dcc import NameTable, build_report, classify, transform_kind

    t = NameTable()
    for kind, names in {"mesh": ["Bead.001", "Bead.002", "Icosphere", "Torus", "Cube", "pCylinder1", "Box001", "Rock 1"],
                        "clip": ["Armature|Action", "Take 001", "A_POLY_IDL_Masc", "BasicMotions@Jump01", "mixamo.com"],
                        "material": ["Material.001", "PolygonAncientWorlds_01_A"],
                        "gameobject": ["Armature", "mixamorig:Hips", "DEF-spine", "Bip01 Pelvis", "RL_BoneRoot",
                                       "Hipo_ROOTSHJnt", "joint1", "hand.L", "toe_end", "Plane (1)", "SM_Prop_Box_01"],
                        "texture": ["Seat_Base_color"]}.items():
        for n in names:
            t.add(kind, n)
    t.add("gameobject", "Cube", 5)
    rules = classify(t)
    assert rules["dup_suffix_.NNN"]["kinds"]["mesh"]["objects"] == 2
    assert rules["dup_suffix_.NNN"]["kinds"]["material"]["unique"] == 1
    assert rules["blender_only_primitive"]["unique"] == 2 and rules["shared_primitive"]["kinds"]["gameobject"]["objects"] == 5
    assert rules["maya_primitive"]["unique"] == 1 and rules["joint_N"]["unique"] == 1 and rules["Jnt_suffix"]["unique"] == 1
    for rid in ("armature_action_clip", "mixamorig", "mixamo_clip", "ORG-/DEF-/MCH-", "biped_Bip01", "max_object_NNN",
                "synty_A_POLY", "synty_Polygon", "synty_SM_SK", "RL_CC_Base", "ki_pack_at_clip", "mirror_.L/.R"):
        assert rules[rid]["unique"] == 1, rid
    rot = transform_kind(struct.pack("<iq4f3f3f", 0, 77, -0.7071068, 0, 0, 0.7071068, 0, 0, 0, 100, 100, 100))
    assert rot == ("rotX-90", "scale100", 77)
    assert transform_kind(struct.pack("<iq4f3f3f", 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 1))[:2] == ("identity", "scale1")
    tr = {"a.assets": {"transforms": 2, "counts": {"rotX-90": 1, "scale100": 1}, "sample_names": {}}}
    rep = build_report(t, tr, {"files": 0}, {"import_sources": {".blend": 3}})
    fams = rep.data["families"]
    assert fams["blender"]["verdict"] == "present" and fams["blender"]["kind"] == "inference"
    assert fams["blender"]["rules"]["godot_import_source_.blend"] == 3
    assert fams["fbx_import"]["verdict"] == "weak" and fams["maya"]["verdict"] == "present"
    assert "rigify" in rep.summary["present"] and "Caveats" in rep.markdown


def test_dcc_analysis_on_gltf_and_godot(ws_any):
    ws, game = ws_any
    proj = ws / "assets" / "godot" / "project"
    make_glb(proj / "models" / "hero.glb", generator="GDRE Tools v2.7.0")
    (proj / "player.tscn").write_text('[gd_scene format=3]\n[node name="Player" type="Node3D"]\n'
                                      '[node name="Cube_001" type="MeshInstance3D" parent="."]\nbone_name = "ORG_arm.R"\n',
                                      encoding="utf-8")
    (proj / "models" / "hero.glb.import").write_text('[remap]\nimporter="scene"\n[deps]\nsource_file="res://models/hero.blend"\n',
                                                     encoding="utf-8")
    res = analysis.run(ws, game, "dcc")
    assert res["status"] == "done", res["reason"]
    d = json.loads((ws / "analysis" / "dcc.json").read_text(encoding="utf-8"))["data"]
    assert d["gltf"]["generators"] == {"GDRE Tools v2.7.0": 1} and d["gltf"]["node_signatures"] == {"rotX-90": 1}
    assert d["godot"]["import_sources"] == {".blend": 1}
    assert d["rules"]["mirror_.L/.R"]["kinds"]["bone"]["unique"] == 2  # ORG_hand.L (glb) + ORG_arm.R (tscn)
    assert d["rules"]["armature_action_clip"]["unique"] == 1 and d["families"]["blender"]["verdict"] == "present"


# --- shaders / textures -----------------------------------------------------------------------------

def fake_sf(name: str, externals=()):
    return SimpleNamespace(name=name, externals=[SimpleNamespace(path=p) for p in externals])


def test_shader_info_and_material_info():
    from gst.analysis.unity.shaders import family, material_info, shader_info, signatures

    prop = lambda n, t: SimpleNamespace(m_Name=n, m_Type=t)  # noqa: E731
    pas = SimpleNamespace(m_State=SimpleNamespace(m_Name="FORWARD", m_Tags=SimpleNamespace(tags=[("LightMode", "ForwardBase")])))
    pf = SimpleNamespace(m_Name="Hurtable/Hurtable", m_PropInfo=SimpleNamespace(m_Props=[prop("_AlbedoTexture", 4), prop("__dirty", 2)]),
                         m_SubShaders=[SimpleNamespace(m_Passes=[pas, pas])], m_KeywordNames=["B", "A"],
                         m_FallbackName="Diffuse", m_CustomEditorName="ASEMaterialInspector")
    s = shader_info(SimpleNamespace(m_ParsedForm=pf))
    assert (s["name"], s["props"], s["keywords"], s["subshaders"], s["passes"]) == ("Hurtable/Hurtable", ["_AlbedoTexture", "__dirty"], ["A", "B"], 1, 2)
    assert s["lightmodes"] == {"ForwardBase": 2} and s["prop_types"] == {"Float": 1, "Texture": 1}
    assert signatures(s["name"], s["props"]) == ["amplify"] and family("Hurtable/Hurtable Fade") == "Hurtable"
    sf = fake_sf("sharedassets0.assets", ["resources.assets", "Library/unity default resources"])
    tree = {"m_Name": "Skin", "m_Shader": {"m_FileID": 1, "m_PathID": 9},
            "m_ValidKeywords": ["_EMISSION"], "m_SavedProperties": {
                "m_TexEnvs": [["_BumpMap", {"m_Texture": {"m_FileID": 0, "m_PathID": 5}}], ["_MainTex", {"m_Texture": {"m_FileID": 0, "m_PathID": 0}}]],
                "m_Floats": [["_Glossiness", 0.5]], "m_Colors": [["_Color", {}]]}}
    m = material_info(tree, sf)
    assert m["shader"] == "resources.assets|9" and m["textures"] == {"_BumpMap": "sharedassets0.assets|5"}
    assert m["keywords"] == ["_EMISSION"] and m["floats"] == ["_Glossiness"] and m["colors"] == ["_Color"]


def test_shaders_and_textures_reports():
    from gst.analysis.unity.shaders import build_report as shaders_report
    from gst.analysis.unity.textures import build_report as tex_report

    rows = {"a|1": {"name": "Hurtable/Hurtable", "file": "D/a", "path_id": 1, "props": ["__dirty"], "parsed": True},
            "a|2": {"name": "Standard", "file": "D/a", "path_id": 2, "props": ["_MainTex"], "parsed": True}}
    rep = shaders_report(rows, {"a|1": {"D/a": 3}, "x|9": {"D/a": 1}}, {"a|1": {"_EMISSION": 2}}, {"a|1": ["m1"]}, 4)
    assert rep.summary["shaders"] == 2 and rep.summary["materials_unresolved_shader"] == 1
    assert rep.data["families"]["Hurtable"]["materials"] == 3 and rep.data["shader_index"]["a|1"] == "Hurtable/Hurtable"
    assert rep.summary["signatures"] == {"amplify": 1}
    texs = {"a|5": {"name": "Body_N", "w": 512, "h": 512, "format": "BC5", "mips": 10, "bytes": 1, "streamed": False,
                    "file": "D/a", "path_id": 5},
            "a|6": {"name": "Atlas_Main", "w": 2048, "h": 1024, "format": "DXT1", "mips": 12, "bytes": 1, "streamed": True,
                    "file": "D/a", "path_id": 6},
            "a|7": {"name": "broken", "error": "x", "file": "D/a", "path_id": 7}}
    mats = [{"name": f"m{i}", "shader_name": "Hurtable/Hurtable", "textures": {"_MainTex": "a|6", "_NormalTexture": "a|5"}}
            for i in range(3)] + [{"name": "u", "shader_name": "?", "textures": {"_MainTex": "zz|1"}}]
    t = tex_report(texs, mats, have_shaders=True, atlas_min=3)
    s = t.summary
    assert (s["textures"], s["errors"], s["streamed"], s["square"], s["pow2"]) == (3, 1, 1, 1, 2)
    assert (s["normal_maps_by_slot"], s["normal_maps_by_name"], s["atlas_hints_shared"], s["atlas_hints_named"]) == (1, 1, 2, 1)
    assert t.data["families"]["Hurtable"]["normal_maps"] == 1 and t.data["unresolved_texture_refs"] == 1


# --- audio refs / catalog ---------------------------------------------------------------------------

def test_audio_raw_hits_and_typetree_paths():
    from gst.analysis.unity.audio_refs import fid_map, raw_hits, walk_pptrs

    sf = fake_sf("level0", ["resources.assets", "other.assets"])
    fids = fid_map(sf, {"resources.assets"})
    assert fids == {1: "resources.assets"}
    big = (1 << 33) + 7
    raw = struct.pack("<iq", 0, 12) + b"\1\0\0\0" + struct.pack("<iq", 3, 99) + struct.pack("<iq", 1, big) + struct.pack("<iq", 1, 5)
    hits = raw_hits(raw, fids, {big, 5}, {f"resources.assets|{big}", "resources.assets|5"})
    assert hits == [(28, f"resources.assets|{big}"), (40, "resources.assets|5")]
    assert raw_hits(struct.pack("<iq", 1, 6) * 2, fids, {5}, {"resources.assets|5"}) == []
    tree = {"m_GameObject": {"m_FileID": 0, "m_PathID": 1}, "Sounds": [{"m_FileID": 1, "m_PathID": 5}],
            "impact": {"Sounds": [{"m_FileID": 1, "m_PathID": 6}]}}
    assert list(walk_pptrs(tree)) == [("Sounds[]", (1, 5)), ("impact.Sounds[]", (1, 6))]


def _refs_doc():
    clips = [{"key": "res.assets|5", "name": "Door Open 01", "file": "D/res.assets", "path_id": 5, "refs": 2},
             {"key": "res.assets|6", "name": "Door Open 02", "file": "D/res.assets", "path_id": 6, "refs": 1},
             {"key": "res.assets|7", "name": "Lonely", "file": "D/res.assets", "path_id": 7, "refs": 0}]
    refs = [{"file": "D/res.assets", "path_id": 40, "type": "MonoBehaviour", "class": "Door", "go": "Hinge", "root": "Module - A",
             "scriptable": False, "field": "openSound.Sounds[]", "target": t, "how": "typetree"} for t in ("res.assets|5", "res.assets|6")]
    refs.append({"file": "D/res.assets", "path_id": 41, "type": "AudioSource", "class": "AudioSource", "go": "Speaker",
                 "root": "Truck", "scriptable": False, "field": "m_Resource", "target": "res.assets|5", "how": "binary"})
    return {"data": {"clips": clips, "refs": refs, "holder_refs": []}}


def test_audio_catalog_unity_events_and_code(tmp_path):
    from gst.analysis.audio_catalog import Sources, build_report, unity_events

    ws = tmp_path
    cs = ws / "decomp" / "cs" / "Assembly-CSharp"
    cs.mkdir(parents=True)
    (cs / "Door.cs").write_text("class Door {\n  public Sound openSound;\n  void Open() { openSound.Play(pos); }\n"
                                "  void X() { Load(\"Lonely\"); }\n}\n", encoding="utf-8")
    clip_dir = ws / "assets" / "unity" / "AudioClip" / "res.assets"
    clip_dir.mkdir(parents=True)
    (clip_dir / "Door Open 01.wav").write_bytes(b"RIFF")
    src = Sources(ws, [ws / "decomp"])
    exports = ({"res.assets|5": "assets/unity/AudioClip/res.assets/Door Open 01.wav"}, {"Lonely": ["assets/x/Lonely.wav"]})
    events = unity_events(_refs_doc(), exports, src)
    by = {e["name"]: e for e in events}
    door = by["Door.openSound"]
    assert door["defined_at"] == "decomp/cs/Assembly-CSharp/Door.cs:2" and door["fields"]["clips"] == 2
    assert door["used_by"][0] == {"where": "decomp/cs/Assembly-CSharp/Door.cs:3", "block": "Door", "key": "openSound.Play*"}
    assert {"where": "D/res.assets", "block": "Module - A / Hinge [Door]", "key": "openSound"} in door["used_by"]
    assert door["category"] == "module" and door["files"][1] == {"name": "Door Open 02", "file": None}
    assert by["AudioSource.m_audioClip"]["files"] == [{"name": "Door Open 01", "file": "assets/unity/AudioClip/res.assets/Door Open 01.wav"}]
    lonely = by["unreferenced: Lonely"]
    assert lonely["used_by"] == [] and lonely["files"][0]["file"] == "assets/x/Lonely.wav"
    m = src.mentions({"Lonely"})
    assert m["Lonely"] == [{"where": "decomp/cs/Assembly-CSharp/Door.cs:4", "block": "Door", "key": "code mention"}]
    rep = build_report(events, ws, "unity", "x")
    assert rep.summary["events"] == 3 and rep.summary["missing_files"] == 1 and rep.summary["without_export"] == 1


def test_audio_catalog_clip_exports_key_bundle_clips_by_cab(tmp_path):
    from gst.analysis.audio_catalog import clip_exports, unity_events

    ws = tmp_path
    root = ws / "assets" / "unity" / "AudioClip"

    def export(folder: str, name: str, source: str, pid: int, container: str | None = None) -> str:
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.wav").write_bytes(b"RIFF")
        side = {"source": source, "path_id": pid, "type": "AudioClip", "name": name, "file": f"{name}.wav"}
        if container:
            side["container"] = container
        (d / f"{name}.wav.gs.json").write_text(json.dumps(side), encoding="utf-8")
        return f"assets/unity/AudioClip/{folder}/{name}.wav"

    # the same clip ships in two bundles, each inside its own serialized file (CAB); plus a loose .assets clip
    a = export("maps__levels0-31.assetbundle", "Bell", "D/StreamingAssets/maps/levels0-31.assetbundle", 317,
               "BuildPlayer-Level_1.sharedAssets")
    b = export("maps__levels32-63.assetbundle", "Bell", "D/StreamingAssets/maps/levels32-63.assetbundle", 5574,
               "BuildPlayer-Level_61.sharedAssets")
    c = export("resources.assets", "Click", "D/resources.assets", 9)
    by_key, by_name = clip_exports(ws)
    assert by_key["buildplayer-level_1.sharedassets|317"] == a
    assert by_key["buildplayer-level_61.sharedassets|5574"] == b
    assert by_key["resources.assets|9"] == c
    assert sorted(by_name["Bell"]) == sorted([a, b])  # ambiguous by name: only the CAB key can resolve it

    clips = [{"key": "buildplayer-level_1.sharedassets|317", "name": "Bell", "file": "D/levels0-31.assetbundle",
              "path_id": 317, "cab": "BuildPlayer-Level_1.sharedAssets", "refs": 1},
             {"key": "buildplayer-level_61.sharedassets|5574", "name": "Bell", "file": "D/levels32-63.assetbundle",
              "path_id": 5574, "cab": "BuildPlayer-Level_61.sharedAssets", "refs": 1}]
    refs = [{"file": c["file"], "cab": c["cab"], "path_id": 50 + i, "type": "AudioSource", "class": "AudioSource",
             "go": "Bell", "root": "Ghost", "scriptable": False, "field": "m_Resource", "target": c["key"], "how": "typetree"}
            for i, c in enumerate(clips)]
    events = unity_events({"data": {"clips": clips, "refs": refs, "holder_refs": []}}, (by_key, by_name), None)
    files = sorted(f["file"] for e in events for f in e["files"])
    assert files == sorted([a, b])


def test_audio_catalog_godot_and_publish(ws_any):
    ws, game = ws_any
    proj = ws / "assets" / "godot" / "project"
    (proj / "assets" / "audio").mkdir(parents=True)
    (proj / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (proj / "assets" / "audio" / "jump.ogg").write_bytes(b"OggS")
    (proj / "scripts").mkdir()
    (proj / "scripts" / "Hero.cs").write_text('class Hero { string s = "res://assets/audio/jump.ogg"; }\n', encoding="utf-8")
    (proj / "hero.tscn").write_text(
        '[gd_scene format=3]\n[ext_resource type="AudioStream" path="res://assets/audio/jump.ogg" id="1_a"]\n'
        '[ext_resource type="AudioStream" path="res://assets/audio/gone.wav" id="2_b"]\n'
        '[ext_resource type="Script" path="res://scripts/audio/AudioClip.cs" id="3_c"]\n'
        '[sub_resource type="Resource" id="Res_1"]\nscript = ExtResource("3_c")\nStreams = [ExtResource("1_a"), ExtResource("2_b")]\n'
        'Volume = 0.5\n[node name="Hero" type="Node3D"]\nJumpSound = SubResource("Res_1")\n'
        '[node name="Sfx" type="AudioStreamPlayer3D" parent="."]\nstream = ExtResource("1_a")\nvolume_db = -3.0\n', encoding="utf-8")
    res = analysis.run(ws, game, "audio_catalog")
    assert res["status"] == "done", res["reason"]
    doc = json.loads((ws / "analysis" / "audio_catalog.json").read_text(encoding="utf-8"))
    by = {e["name"]: e for e in doc["data"]["events"]}
    jump = by["hero/Hero.JumpSound"]
    assert jump["fields"]["class"] == "AudioClip" and jump["fields"]["Volume"] == "0.5"
    assert jump["files"] == [{"name": "jump.ogg", "file": "assets/godot/project/assets/audio/jump.ogg"}, {"name": "gone.wav", "file": None}]
    assert any(u["key"] == "code mention" and u["where"] == "assets/godot/project/scripts/Hero.cs:1" for u in jump["used_by"])
    assert by["hero/Sfx.stream"]["fields"] == {"class": "AudioStreamPlayer3D", "volume_db": "-3.0"}
    cat = ws / "assets" / "audio" / "catalog.json"
    assert json.loads(cat.read_text(encoding="utf-8")) == doc["data"]["events"] and doc["summary"]["without_export"] == 1
    from gst.assets import audiocat

    assert {e["name"] for e in audiocat.load_catalog(ws)} == set(by)
    # a catalog the study wrote itself is kept unless publish=1
    (ws / "assets" / "audio" / ".catalog-by-gst").unlink()
    cat.write_text("[]", encoding="utf-8")
    analysis.run(ws, game, "audio_catalog")
    assert cat.read_text(encoding="utf-8") == "[]"
    analysis.run(ws, game, "audio_catalog", {"publish": "1"})
    assert len(json.loads(cat.read_text(encoding="utf-8"))) == 2


def test_m3c_analyses_are_registered():
    specs = analysis.all_specs()
    for n in ("dcc", "gltf_summary", "shaders", "textures", "audio_refs", "audio_catalog"):
        assert n in specs, n
    assert specs["dcc"].applies_to("godot") and specs["shaders"].applies_to("unity-il2cpp") and not specs["shaders"].applies_to("godot")
    names = [s.name for s in analysis.for_engine("unity-mono")]
    assert names.index("shaders") < names.index("textures") and names.index("audio_refs") < names.index("audio_catalog")


def test_mixer_group_paths_and_nearest_field():
    from gst.analysis.unity.audio_refs import group_paths, nearest_group

    pp = lambda pid: {"m_FileID": 0, "m_PathID": pid}  # noqa: E731
    groups = {10: {"m_Name": "Master", "m_Children": [pp(11), pp(12)]}, 11: {"m_Name": "Footsteps", "m_Children": []},
              12: {"m_Name": "UI", "m_Children": [pp(13)]}, 13: {"m_Name": "Clicks", "m_Children": []},
              20: {"m_Name": "Orphan", "m_Children": []}}
    paths = group_paths(groups, {1: {"m_Name": "Sound", "m_MasterGroup": pp(10)}})
    assert {k: v["path"] for k, v in paths.items()} == {10: "Sound", 11: "Sound/Footsteps", 12: "Sound/UI",
                                                         13: "Sound/UI/Clicks", 20: "Orphan"}
    cands = [("source", "a|1", "field"), ("jump.source", "a|2", "field")]
    assert nearest_group("jump.Sounds[]", cands) == ("a|2", "field")
    assert nearest_group("land.Sounds[]", cands) == ("a|1", "field") and nearest_group("x", []) is None
    assert nearest_group("land.Sounds[]", cands[1:]) is None  # a sibling struct's AudioSource is not this clip's


def test_audio_kinds_from_mixer_component_and_clip_names():
    from gst.analysis.audio_catalog import classify, tokens, unity_events

    assert tokens("soundHurtVO") == ["sound", "hurt", "vo"] and tokens("Material Impacts 02") == ["material", "impacts"]
    assert classify("Sound/Sound Master/Footsteps", "Door", "openSound", []) == ("footstep", "mixer")
    assert classify("Sound/Sound Master/Sound Effects", "EnemySlowMouth", "soundHurtVO", []) == ("voice", "component")
    assert classify(None, "LevelAmbience", "loopClip", []) == ("ambient", "component")
    assert classify(None, "PhysAudio", "breakLight", ["glass break"]) == ("impact", "component")
    assert classify(None, "PlayerAvatar", "jumpSound", ["menu click 01"]) == ("ui", "clip name")
    assert classify(None, "PlayerAvatar", "jumpSound", ["player jump01"]) == ("sound", "default")
    assert classify(None, "ItemGun", "soundShoot", []) == ("weapon", "component")
    doc = _refs_doc()
    for r in doc["data"]["refs"]:
        r["mixer_group"], r["mixer_via"] = ("Sound/Effects", "field") if r["class"] == "Door" else ("Music/Level", "audiosource")
    by = {e["name"]: e for e in unity_events(doc, ({}, {}), None)}
    door = by["Door.openSound"]  # Sound/Effects carries most references: the default group, not a category
    assert door["category"] == "module" and door["fields"]["mixer_group"] == "Sound/Effects"
    assert door["kind"] == "sound" and door["fields"]["kind_by"] == "default"
    speaker = by["AudioSource.m_audioClip"]
    assert speaker["category"] == "Music/Level" and (speaker["kind"], speaker["fields"]["kind_by"]) == ("music", "mixer")
    assert by["unreferenced: Lonely"]["fields"] == {"kind_by": "default"}


def test_dcc_dup_suffix_ignores_decimal_sizes():
    """Regression (REPO): 'Floor Museum 0.125x0.125' is a size, not Blender's '.NNN' duplicate suffix."""
    from gst.analysis.dcc import NameTable, classify

    t = NameTable()
    for n in ("Floor Museum 0.125x0.125", "Floor_1x0.125", "Scale 0.500", "Plank (0.250)"):
        t.add("gameobject", n)
    for n in ("wall broken_1x1.001", "stem 2.001", "Cube.012", "Bone0.001"):
        t.add("mesh", n)
    rule = classify(t)["dup_suffix_.NNN"]
    assert "gameobject" not in rule["kinds"] and rule["kinds"]["mesh"]["unique"] == 4
