"""Godot on synthetic fixtures only: PCK lister, .tscn/.tres parser, script exports, AnimationTree /
skeleton / ragdoll helpers and the analyses godot_pck, godot_scenes, godot_animation."""

from __future__ import annotations

import json
import math
import struct

import pytest

from gst import analysis
from gst.engines.godot import animtree, pck, scripts, tres

# --- PCK -------------------------------------------------------------------------------------------

ENTRIES = [("res://project.binary", b"cfg"), ("res://scenes/main.tscn", b"[gd_scene format=3]\n"),
           ("res://a/b/é.png", b"\x89PNG" + b"x" * 13)]


@pytest.mark.parametrize("fmt,version", [(1, (3, 5, 3)), (2, (4, 2, 2)), (3, (4, 6, 1))])
def test_pck_formats_roundtrip(tmp_path, fmt, version):
    p = tmp_path / "game.pck"
    p.write_bytes(pck.build(ENTRIES, fmt=fmt, version=version))
    info = pck.read(p)
    assert info["pack_format"] == fmt and info["version"] == ".".join(map(str, version)) and info["where"] == "pck"
    assert [e["path"] for e in info["files"]] == [e[0] for e in ENTRIES]
    raw = p.read_bytes()
    for e, (_path, data) in zip(info["files"], ENTRIES):
        assert raw[e["offset"]:e["offset"] + e["size"]] == data
        assert e["md5"] == __import__("hashlib").md5(data).hexdigest()
    assert info["bytes"] == sum(len(d) for _p, d in ENTRIES) and info["errors"] == []


def test_pck_embedded_in_exe(tmp_path):
    body = pck.build(ENTRIES, fmt=3)
    exe = tmp_path / "game.exe"
    exe.write_bytes(b"MZ" + b"\0" * 1000 + body + struct.pack("<Q", len(body)) + b"GDPC")
    info = pck.read(exe)
    assert info["where"] == "embedded in exe" and info["pck_start"] == 1002
    data = exe.read_bytes()
    e = info["files"][1]
    assert data[e["offset"]:e["offset"] + e["size"]] == ENTRIES[1][1] and e["pck_offset"] == e["offset"] - 1002
    (tmp_path / "other.exe").write_bytes(b"MZ" + b"\0" * 64)
    assert pck.find(tmp_path) == [exe]


def test_pck_encryption_is_reported_never_opened(tmp_path):
    p = tmp_path / "enc.pck"
    p.write_bytes(pck.build(ENTRIES, fmt=2, flags=pck.DIR_ENCRYPTED | pck.REL_FILEBASE))
    info = pck.read(p)
    assert info["dir_encrypted"] and info["files"] == [] and info["file_count"] == 3
    assert "key" in info["errors"][0]
    p2 = tmp_path / "files.pck"
    p2.write_bytes(pck.build(ENTRIES, fmt=3, file_flags={"res://scenes/main.tscn": pck.FILE_ENCRYPTED}))
    info2 = pck.read(p2)
    assert info2["encrypted_files"] == 1 and info2["files"][1]["encrypted"] and not info2["dir_encrypted"]


def test_pck_errors_and_cross_check(tmp_path):
    bad = tmp_path / "x.pck"
    bad.write_bytes(b"NOPE" * 10)
    with pytest.raises(pck.PckError):
        pck.read(bad)
    trunc = tmp_path / "t.pck"
    trunc.write_bytes(pck.build(ENTRIES, fmt=2)[:120])
    with pytest.raises(pck.PckError):
        pck.read(trunc)
    weird = tmp_path / "w.pck"
    weird.write_bytes(b"GDPC" + struct.pack("<4I", 9, 4, 0, 0) + b"\0" * 100)
    with pytest.raises(pck.PckError, match="unknown pack format 9"):
        pck.read(weird)
    files = [{"path": "res://a", "removal": False}, {"path": "res://b", "removal": False}]
    c = pck.cross_check(files, ["res://a", "res://c"])
    assert c == {"pck": 2, "inventory": 2, "common": 1, "only_pck": ["res://b"], "only_inventory": ["res://c"], "match": False}


# --- tres ------------------------------------------------------------------------------------------

def test_parse_values():
    v = tres.parse_value
    assert v("1") == 1 and v("-2.5") == -2.5 and v("1e-05") == 1e-05 and v("inf") == math.inf and v("-inf") == -math.inf
    assert math.isnan(v("nan")) and v("true") is True and v("null") is None
    assert v('"a\\"b\\nc\\u00e9"') == 'a"b\ncé'
    assert isinstance(v('&"idle"'), tres.StringName) and v('&"idle"') == "idle"
    assert isinstance(v('^"A/B:x"'), tres.NodePath) and v('NodePath("../X")') == "../X"
    assert v("Vector3(1, 2.5, -3)") == tres.Ctor("Vector3", (1, 2.5, -3))
    assert v('ExtResource("1_ab")') == tres.Ref("ext", "1_ab") and v("SubResource( 3 )") == tres.Ref("sub", "3")
    assert v('[1, "x", [true]]') == [1, "x", [True]] and v("[]") == [] and v("[1, 2,]") == [1, 2]
    assert v('{"a": 1, &"b": Vector2(0, 1), 3: null}') == {"a": 1, "b": tres.Ctor("Vector2", (0, 1)), "3": None}
    assert v("Array[int]([1, 2])") == [1, 2] and v('Array[ExtResource("2")]([ExtResource("3")])') == [tres.Ref("ext", "3")]
    assert v("Dictionary[String, int]({\"k\": 1})") == {"k": 1}
    assert v("PackedFloat32Array(0, 0.5, 1)").args == (0, 0.5, 1) and v("PackedStringArray()").args == ()
    obj = v('Object(InputEventKey,"resource_local_to_scene":false,"keycode":65)')
    assert obj.name == "Object" and obj.args[1] == ("resource_local_to_scene", False)


SCENE = '''[gd_scene load_steps=4 format=3 uid="uid://abc"]

[ext_resource type="Script" uid="uid://s1" path="res://scripts/Mover.cs" id="1_m"]
[ext_resource type="PackedScene" path="res://enemy.tscn" id="2_e"]

[sub_resource type="BoxShape3D" id="BoxShape3D_1"]
size = Vector3(1, 2, 3)

[sub_resource type="Resource" id="Resource_cfg"]
script = ExtResource("1_m")
Speed = 9.0

[node name="Root" type="Node3D"]
script = ExtResource("1_m")
Speed = 7.5
Label = "multi
line \\"text\\""
"metadata/odd key" = 1

[node name="Body" type="RigidBody3D" parent="." groups=["players", "physics"]]
mass = 50.0

[node name="Shape" type="CollisionShape3D" parent="Body"]
shape = SubResource("BoxShape3D_1")

[node name="Enemy" parent="Body" instance=ExtResource("2_e")]
Health = 30

[node name="Mesh" parent="Body/Enemy"]
visible = false

[connection signal="body_entered" from="Body" to="." method="_on_hit" flags=3 binds=[1]]

[editable path="Body/Enemy"]
'''


def test_parse_scene_sections():
    d = tres.parse(SCENE)
    assert d["kind"] == "scene" and d["header"]["uid"] == "uid://abc"
    assert d["ext_resources"]["1_m"]["path"] == "res://scripts/Mover.cs"
    assert d["sub_order"] == ["BoxShape3D_1", "Resource_cfg"]
    paths = [n["path"] for n in d["nodes"]]
    assert paths == [".", "Body", "Body/Shape", "Body/Enemy", "Body/Enemy/Mesh"]
    root = d["nodes"][0]
    assert root["props"]["Label"] == 'multi\nline "text"' and root["props"]["metadata/odd key"] == 1
    assert d["nodes"][1]["groups"] == ["players", "physics"] and d["nodes"][3]["instance"] == tres.Ref("ext", "2_e")
    assert tres.ref_path(d, d["nodes"][3]["instance"]) == "res://enemy.tscn"
    assert d["connections"][0]["signal"] == "body_entered" and d["connections"][0]["binds"] == [1]
    assert d["editable"] == ["Body/Enemy"]
    j = tres.to_json(d["sub_resources"]["BoxShape3D_1"]["props"])
    assert j == {"size": {"type": "Vector3", "args": [1, 2, 3]}}
    assert tres.to_json(tres.parse_value("PackedInt32Array(1, 2, 3, 4)"), packed_max=2) == {
        "type": "PackedInt32Array", "count": 4, "head": [1, 2, 3, 4]}


def test_parse_godot3_and_resource():
    d3 = tres.parse('[gd_scene load_steps=2 format=2]\n\n[ext_resource path="res://a.gd" type="Script" id=1]\n\n'
                    '[node name="N" type="Spatial"]\nscript = ExtResource( 1 )\ntransform = Transform( 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0 )\n')
    assert tres.ref_path(d3, d3["nodes"][0]["props"]["script"]) == "res://a.gd"
    r = tres.parse('[gd_resource type="Resource" script_class="Cfg" load_steps=2 format=3]\n\n[resource]\nhp = 3\n')
    assert r["kind"] == "resource" and r["resource"]["type"] == "Resource" and r["resource"]["props"] == {"hp": 3}
    with pytest.raises(tres.TresError, match=r"line \d+: expected"):
        tres.parse('[gd_scene format=3]\nx = Vector3(1, 2\n')


# --- script exports --------------------------------------------------------------------------------

CS = '''public partial class Mover : BaseMover
{
	[Export(PropertyHint.None, "")]
	public float Speed = 3f;

	[Export(PropertyHint.None, "")]
	public Godot.Collections.Array<NodePath> Targets { get; set; } = new();

	[Export]
	private int _count;

	public float NotExported = 1f;
}
'''
CS_BASE = 'public class BaseMover : Node3D\n{\n\t[Export]\n\tpublic int Health = 100;\n}\n'
GD = '''extends Node
@export var speed := 5.0
@export_range(0, 10) var jump: float = 2.5  # comment
@export
var label: String = "x"
export(int) var old_style = 4
var internal = 1
'''


def test_script_exports(tmp_path):
    assert set(scripts.cs_exports(CS)) == {"Speed", "Targets", "_count"}
    assert scripts.cs_exports(CS)["Speed"] == {"type": "float", "default": "3f"}
    assert scripts.cs_class(CS, "Mover") == ("Mover", "BaseMover")
    g = scripts.gd_exports(GD)
    assert set(g) == {"speed", "jump", "label", "old_style"} and g["jump"]["default"] == "2.5" and g["speed"]["default"] == "5.0"
    proj = tmp_path / "proj"
    (proj / "scripts").mkdir(parents=True)
    (proj / "scripts" / "p.gd").write_text(GD, encoding="utf-8")
    cs = tmp_path / "decomp" / "cs" / "Game"
    cs.mkdir(parents=True)
    (cs / "Mover.cs").write_text(CS, encoding="utf-8")
    (cs / "BaseMover.cs").write_text(CS_BASE, encoding="utf-8")
    idx = scripts.ScriptIndex(proj, [proj, tmp_path / "decomp" / "cs"])
    m = idx.exports("res://scripts/Mover.cs")
    assert m["language"] == "csharp" and m["exports"]["Health"]["declared_in"] == "BaseMover" and "Speed" in m["exports"]
    assert set(idx.exports("res://scripts/p.gd")["exports"]) == {"speed", "jump", "label", "old_style"}
    assert idx.exports("res://missing.cs")["source"] is None


# --- animation / tree / skeleton / ragdoll ------------------------------------------------------------

ANIM_TREE = '''[gd_resource type="AnimationNodeBlendTree" load_steps=6 format=3]

[sub_resource type="AnimationNodeAnimation" id="A_idle"]
animation = &"Idle"

[sub_resource type="AnimationNodeAnimation" id="A_walk"]
animation = &"Walk"

[sub_resource type="AnimationNodeStateMachineTransition" id="T1"]
advance_mode = 2

[sub_resource type="AnimationNodeStateMachineTransition" id="T2"]
xfade_time = 0.2
advance_mode = 2
advance_condition = &"is_walking"

[sub_resource type="AnimationNodeStateMachineTransition" id="T3"]
advance_mode = 2
advance_expression = "velocity.length() < 0.1"

[sub_resource type="AnimationNodeBlendSpace1D" id="BS"]
blend_point_0/node = SubResource("A_idle")
blend_point_0/pos = 0.0
blend_point_1/node = SubResource("A_walk")
blend_point_1/pos = 1.0

[sub_resource type="AnimationNodeStateMachine" id="SM"]
states/End/node = null
states/Idle/node = SubResource("A_idle")
states/Idle/position = Vector2(300, 100)
"states/Walk Fast/node" = SubResource("BS")
states/Start/node = null
transitions = ["Start", "Idle", SubResource("T1"), "Idle", "Walk Fast", SubResource("T2"), "Walk Fast", "Idle", SubResource("T3")]

[sub_resource type="AnimationNodeOneShot" id="OS"]
filter_enabled = true
filters = ["Skeleton3D:arm", "Skeleton3D:hand"]
fadein_time = 0.2

[resource]
nodes/SM/node = SubResource("SM")
nodes/Shot/node = SubResource("OS")
node_connections = [&"output", 0, &"Shot", &"Shot", 0, &"SM"]
'''

ANIM_SCENE = '''[gd_scene format=3]

[ext_resource type="AnimationNodeBlendTree" path="res://anim/tree.tres" id="1"]
[ext_resource type="AudioStream" path="res://snd/step.wav" id="2"]

[sub_resource type="Animation" id="Animation_walk"]
resource_name = "walk"
length = 0.8
loop_mode = 1
tracks/0/type = "position_3d"
tracks/0/path = NodePath("Skeleton3D:hips")
tracks/0/keys = PackedFloat32Array(0, 1, 0, 0, 0, 0.4, 1, 0, 0.1, 0, 0.8, 1, 0, 0, 0)
tracks/1/type = "rotation_3d"
tracks/1/path = NodePath("Skeleton3D:hips")
tracks/1/keys = PackedFloat32Array(0, 1, 0, 0, 0, 1)
tracks/2/type = "method"
tracks/2/path = NodePath(".")
tracks/2/keys = {
"times": PackedFloat32Array(0.2, 0.6),
"transitions": PackedFloat32Array(1, 1),
"values": [{"args": [], "method": &"footstep"}, {"args": [1], "method": &"footstep"}]
}
tracks/3/type = "audio"
tracks/3/path = NodePath("Audio")
tracks/3/keys = {
"clips": [{"end_offset": 0.0, "start_offset": 0.0, "stream": ExtResource("2")}],
"times": PackedFloat32Array(0)
}
tracks/4/type = "value"
tracks/4/path = NodePath("Mesh:visible")
tracks/4/keys = {
"times": PackedFloat32Array(0, 0.5, 0.7),
"transitions": PackedFloat32Array(1, 1, 1),
"update": 1,
"values": [true, false, true]
}

[sub_resource type="AnimationLibrary" id="AnimationLibrary_x"]
_data = {
&"walk": SubResource("Animation_walk")
}

[sub_resource type="BoxShape3D" id="B"]

[node name="Char" type="Node3D"]

[node name="Skeleton3D" type="Skeleton3D" parent="."]
bones/0/name = "hips"
bones/0/parent = -1
bones/1/name = "spine"
bones/1/parent = 0
bones/2/name = "head"
bones/2/parent = 1

[node name="Hand" type="BoneAttachment3D" parent="Skeleton3D"]
bone_name = "head"

[node name="Sim" type="PhysicalBoneSimulator3D" parent="Skeleton3D"]

[node name="PB hips" type="PhysicalBone3D" parent="Skeleton3D/Sim"]
bone_name = "hips"
mass = 2.0

[node name="PB spine" type="PhysicalBone3D" parent="Skeleton3D/Sim"]
bone_name = "spine"
joint_type = 3
joint_constraints/angular_limit_enabled = true
mass = 1.5

[node name="Joint" type="Generic6DOFJoint3D" parent="Skeleton3D/Sim/PB spine"]
node_a = NodePath("..")
node_b = NodePath("../../PB hips")
angular_limit_x/upper_angle = 0.5

[node name="AnimationPlayer" type="AnimationPlayer" parent="."]
libraries = {
&"": SubResource("AnimationLibrary_x")
}
autoplay = "walk"

[node name="AnimationTree" type="AnimationTree" parent="."]
tree_root = ExtResource("1")
anim_player = NodePath("../AnimationPlayer")
parameters/SM/conditions/is_walking = false
'''


def test_animation_and_tree_helpers():
    d = tres.parse(ANIM_SCENE)
    a = animtree.animation(d, d["sub_resources"]["Animation_walk"]["props"])
    assert a["name"] == "walk" and a["length"] == 0.8 and a["loop_mode"] == 1 and a["track_count"] == 5
    assert [t["keys"] for t in a["tracks"]] == [3, 1, 2, 1, 3] and a["keys"] == 10
    assert a["methods"] == {"footstep": 2} and a["audio"] == ["res://snd/step.wav"]
    assert a["by_type"] == {"audio": 1, "method": 1, "position_3d": 1, "rotation_3d": 1, "value": 1}
    lib = animtree.libraries(d, d["sub_resources"]["AnimationLibrary_x"]["props"])
    assert list(lib[""]) == ["walk"]
    sk = animtree.skeleton(d["nodes"][1]["props"])
    assert sk["bones"] == 3 and sk["roots"] == ["hips"] and sk["names"] == ["hips", "spine", "head"]
    pb = animtree.physical_bone(d["nodes"][5]["props"])
    assert pb["joint_type"] == "hinge" and pb["joint_constraints"] == {"angular_limit_enabled": True}
    j = animtree.joint(d["nodes"][6])
    assert j["node_a"] == "Skeleton3D/Sim/PB spine" and j["node_b"] == "Skeleton3D/Sim/PB hips"
    assert animtree.resolve_node_path(".", "../X") == "../../X"
    t = tres.parse(ANIM_TREE)
    g = animtree.graph(t, "AnimationNodeBlendTree", t["resource"]["props"])
    st = animtree.graph_stats(g)
    assert st["state_machines"] == 1 and st["states"] == 2 and st["transitions"] == 3
    assert st["conditions"] == {"is_walking": 1} and st["expressions"] == ["velocity.length() < 0.1"]
    assert st["animations"] == {"Idle": 2, "Walk": 1}
    sm = g["nodes"]["SM"]
    assert set(sm["states"]) == {"Start", "End", "Idle", "Walk Fast"} and sm["transitions"][1]["xfade_time"] == 0.2
    assert g["nodes"]["Shot"]["filters"] == 2 and g["connections"][0] == {"to": "output", "port": 0, "from": "Shot"}
    assert len(g["nodes"]["SM"]["states"]["Walk Fast"]["points"]) == 2


# --- the analyses ------------------------------------------------------------------------------------

@pytest.fixture
def godot_ws(tmp_path):
    ws = tmp_path / "ws"
    proj = ws / "assets" / "godot" / "project"
    (proj / "anim").mkdir(parents=True)
    (proj / "data").mkdir()
    (proj / ".godot" / "exported").mkdir(parents=True)
    (proj / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (proj / "main.tscn").write_text(SCENE, encoding="utf-8")
    (proj / "enemy.tscn").write_text('[gd_scene format=3]\n\n[ext_resource type="Script" path="res://scripts/Mover.cs" id="1"]\n\n'
                                     '[node name="Enemy" type="CharacterBody3D"]\nscript = ExtResource("1")\nHealth = 10\n'
                                     '\n[node name="Mesh" type="MeshInstance3D" parent="."]\n', encoding="utf-8")
    (proj / "char.tscn").write_text(ANIM_SCENE, encoding="utf-8")
    (proj / "anim" / "tree.tres").write_text(ANIM_TREE, encoding="utf-8")
    (proj / "data" / "cfg.tres").write_text('[gd_resource type="Resource" format=3]\n\n[ext_resource type="Script" '
                                            'path="res://scripts/Mover.cs" id="1"]\n\n[resource]\nscript = ExtResource("1")\nSpeed = 4.0\n',
                                            encoding="utf-8")
    (proj / "data" / "old.res").write_bytes(b"RSRC" + struct.pack("<5I", 0, 0, 4, 6, 6) + struct.pack("<I", 10) + b"Animation\0")
    (proj / ".godot" / "exported" / "x.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")  # import cache: skipped
    cs = ws / "decomp" / "cs" / "Game"
    cs.mkdir(parents=True)
    (cs / "Mover.cs").write_text(CS, encoding="utf-8")
    (cs / "BaseMover.cs").write_text(CS_BASE, encoding="utf-8")
    install = tmp_path / "install"
    install.mkdir()
    (install / "Game.pck").write_bytes(pck.build([("res://main.tscn", b"x"), ("res://enemy.tscn", b"yy")], fmt=3))
    with open(ws / "assets" / "inventory.jsonl", "w", encoding="utf-8") as fh:
        for p in ("res://main.tscn", "res://enemy.tscn"):
            fh.write(json.dumps({"path": p}) + "\n")
    game = {"slug": "g", "name": "G", "buildid": "1", "install_dir": str(install), "engine": {"key": "godot", "version": "4.6"}}
    (ws / "game.json").write_text(json.dumps(game), encoding="utf-8")
    return ws, game


def _load(ws, name):
    return json.loads((ws / "analysis" / f"{name}.json").read_text(encoding="utf-8"))


def test_godot_scenes_analysis(godot_ws):
    ws, game = godot_ws
    r = analysis.run(ws, game, "godot_scenes")
    assert r["status"] == "done", r["reason"]
    doc = _load(ws, "godot_scenes")
    s, d = doc["summary"], doc["data"]
    assert s["scenes"] == 3 and s["resources"] == 2 and s["binary_files"] == 0
    assert s["parse_errors"] == 1 and s["binary_errors"] == 1  # data/old.res: a truncated binary header
    assert d["parse_errors"]["res://data/old.res"].startswith("binary:")
    assert d["node_types"]["(instance)"] == 1 and d["node_types"]["(override)"] == 1
    assert d["instance_graph"]["edges"] == [{"from": "res://main.tscn", "to": "res://enemy.tscn", "count": 1}]
    assert "res://enemy.tscn" not in d["instance_graph"]["top_level"]
    assert d["connections"][0]["method"] == "_on_hit" and d["signals"] == {"body_entered": 1}
    mover = d["tunables"]["res://scripts/Mover.cs"]
    assert mover["exports"]["Speed"]["default"] == "3f" and list(mover["exports"]["Speed"]["values"]) == ["7.5"]
    health = mover["exports"]["Health"]  # inherited export, set on enemy.tscn and overridden on its instance
    assert health["declared_in"] == "BaseMover" and set(health["values"]) == {"10", "30"}
    assert any("instance of res://enemy.tscn" in w for w in health["values"]["30"]["where"])
    assert "Label" in mover["engine"]  # not an export of Mover: an engine/other property of the node
    assert d["data_resources"]["res://scripts/Mover.cs"]["items"] == 2  # cfg.tres [resource] + main.tscn sub-resource
    assert s["exported_props_off_default"] >= 2
    side = ws / "analysis" / "godot_scenes" / "main.tscn.json"
    sj = json.loads(side.read_text(encoding="utf-8"))
    assert sj["nodes"][0]["script"] == "res://scripts/Mover.cs" and sj["nodes"][3]["instance"] == "res://enemy.tscn"
    assert not (ws / "analysis" / "godot_scenes" / ".godot").exists()


def test_godot_animation_analysis(godot_ws):
    ws, game = godot_ws
    r = analysis.run(ws, game, "godot_animation")
    assert r["status"] == "done", r["reason"]
    doc = _load(ws, "godot_animation")
    s, d = doc["summary"], doc["data"]
    assert s["animations"] == 1 and s["method_tracks"] == 1 and s["audio_tracks"] == 1 and s["parse_errors"] == 1
    assert s["state_machines"] == 1 and s["states"] == 2 and s["transitions"] == 3 and s["skeletons"] == 1
    assert s["physical_bones"] == 2 and s["joints"] == 1 and s["ragdoll_scenes"] == 1
    tree_node = next(t for t in d["trees"] if t["node"] == "AnimationTree")
    assert tree_node["tree_resource"] == "res://anim/tree.tres" and tree_node["parameters"] == {"SM/conditions/is_walking": False}
    player = next(p for p in d["players"] if p["node"] == "AnimationPlayer")
    assert player["libraries"] == {"[default]": ["walk"]} and player["autoplay"] == "walk"
    rag = d["ragdolls"][0]
    assert rag["builtin_joints"] == 1 and rag["joint_node_types"] == {"Generic6DOFJoint3D": 1} and rag["mass_total"] == 3.5
    assert "AnimationTrees" in (ws / "analysis" / "godot_animation.md").read_text(encoding="utf-8")


def test_godot_pck_analysis(godot_ws):
    ws, game = godot_ws
    r = analysis.run(ws, game, "godot_pck", {"md5": "1"})
    assert r["status"] == "done", r["reason"]
    doc = _load(ws, "godot_pck")
    assert doc["summary"] == {"packs": 1, "files": 2, "bytes": 3, "encrypted_dirs": 0, "encrypted_files": 0,
                              "md5_mismatch": 0, "inventory_match": True}
    assert (ws / "analysis" / "godot_pck" / "Game.pck.files.json").is_file()


def test_godot_analyses_skip_without_inputs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    game = {"slug": "g", "install_dir": str(tmp_path / "none"), "engine": {"key": "godot"}}
    for name in ("godot_scenes", "godot_animation", "godot_pck"):
        r = analysis.run(ws, game, name)
        assert r["status"] == "skipped" and "needs godot." in r["reason"]
    unity = {"slug": "u", "engine": {"key": "unity-mono"}}
    assert analysis.run(ws, unity, "godot_scenes")["status"] == "skipped"


def test_godot_study_plans_m5_and_m3c_analyses():
    names = [s.name for s in analysis.for_engine("godot")]
    for n in ("godot_pck", "godot_scenes", "godot_animation", "audio_catalog", "gltf_summary", "dcc"):
        assert n in names, n
    assert "godot_scenes" not in [s.name for s in analysis.for_engine("unity-mono")]
    cats = {n: analysis.get(n).category for n in ("godot_pck", "godot_scenes", "godot_animation", "dcc")}
    assert cats == {"godot_pck": "data", "godot_scenes": "prefab_scene", "godot_animation": "animation", "dcc": "other"}


def test_study_job_gets_the_analysis_category(monkeypatch):
    from gst.analysis import stage
    from gst.extractors import registry

    monkeypatch.setattr(registry, "_REGISTRY", {})
    cls = stage._make(analysis.get("godot_animation"))
    assert cls.name == "analysis.godot_animation" and cls.category == "animation"
    assert registry._REGISTRY["godot"]["analysis.godot_animation"] is cls


def test_dotnet_covers_godot_csharp(tmp_path):
    """Task 4: Godot .NET assemblies (data_<game>_*/ with GodotSharp) go to code.dotnet (ilspycmd)."""
    from gst.decompile import dotnet

    data = tmp_path / "data_Game_windows_x86_64"
    data.mkdir()
    (tmp_path / "data_NotGodot").mkdir()
    for name in ("GodotSharp.dll", "Game.dll"):
        (data / name).write_bytes(b"MZ" + b"\0" * 62)
    assert dotnet.assembly_dirs(tmp_path) == [data]
