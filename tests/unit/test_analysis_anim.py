"""Unity animation analyses (animation, controller, avatar, rig, joints) on synthetic typetrees: CRC32 path maps, ZXY Euler,
MuscleClip decoding, controller hash resolution and brute force, humanoid mapping, rig mapping checks, joint tables."""

from __future__ import annotations

import math
import random
import struct
import zlib

import pytest

from gst import analysis
from gst.analysis.unity import animcore as ac
from gst.analysis.unity.animation import Maps, analyze_clip
from gst.analysis.unity.avatar import decode_avatar
from gst.analysis.unity.controller import Names, bruteforce, decode_controller, decode_override, words_of
from gst.analysis.unity.joints import family, joint_detail, mono_summary, rigidbody
from gst.analysis.unity.rig import geometry_check, hash_check, mapping_verdict, skin_stats


def w(f: float) -> int:
    return struct.unpack("<I", struct.pack("<f", f))[0]


NODES = [
    {"path": "/Player", "components": []},
    {"path": "/Player/Visuals", "components": [{"type": "Animator", "controller": "PlayerCtl", "avatar": None}]},
    {"path": "/Player/Visuals/Rig", "components": []},
    {"path": "/Player/Visuals/Rig/Hip", "components": []},
    {"path": "/Player/Visuals/Rig/Hip/Leg_L", "components": []},
    {"path": "/Player/Door", "components": [{"type": "Animation", "clips": ["Open"], "clip": "Open"}]},
    {"path": "/Player/Door/Panel", "components": []},
]


# --- path maps / rotations --------------------------------------------------------------------------

def test_crc_path_map_relative_to_the_animator():
    assert ac.crc("Rig/Hip") == zlib.crc32(b"Rig/Hip")
    maps = ac.animator_maps(NODES)
    anim = [m for m in maps if m["kind"] == "Animator"][0]
    assert anim["controller"] == "PlayerCtl" and anim["node"] == "/Player/Visuals"
    assert anim["map"][0] == "" and anim["map"][ac.crc("Rig/Hip/Leg_L")] == "Rig/Hip/Leg_L"
    assert ac.crc("Visuals/Rig") not in anim["map"]  # paths are relative to the Animator, not the prefab root
    legacy = [m for m in maps if m["kind"] == "Animation"][0]
    assert legacy["clips"] == ["Open"] and legacy["map"][ac.crc("Panel")] == "Panel"


@pytest.mark.parametrize("e", [(0, 0, 0), (10, 20, 30), (-45, 170, -100), (89.5, -30, 12), (-60, -120, 175),
                               (90, 30, 0), (-90, -45, 0)])
def test_quat_euler_round_trip_unity_convention(e):
    q = ac.euler_to_quat(*e)
    back = ac.quat_to_euler(*q)
    q2 = ac.euler_to_quat(*back)
    # same rotation (q and -q are equal rotations)
    dot = abs(sum(a * b for a, b in zip(q, q2)))
    assert dot == pytest.approx(1.0, abs=1e-6)
    if abs(e[0]) < 89:
        assert back == pytest.approx(list(e), abs=1e-6)


def test_quaternion_order_is_y_then_x_then_z():
    # pure rotations map to their own axis; composition order matters
    assert ac.quat_to_euler(*ac.euler_to_quat(0, 90, 0)) == pytest.approx([0, 90, 0], abs=1e-6)
    qx, qy, qz = ac.euler_to_quat(30, 0, 0), ac.euler_to_quat(0, 40, 0), ac.euler_to_quat(0, 0, 50)
    combined = ac._qmul(ac._qmul(qy, qx), qz)
    assert ac.euler_to_quat(30, 40, 50) == pytest.approx(combined, abs=1e-9)


def test_matrix_scale_rotation_of_an_fbx_bindpose():
    s = 0.8259
    # rotation -90 about X, uniform scale s: columns x=(s,0,0) y=(0,0,-s) z=(0,s,0)
    m = {"e00": s, "e10": 0, "e20": 0, "e01": 0, "e11": 0, "e21": -s, "e02": 0, "e12": s, "e22": 0,
         "e03": 1.0, "e13": 2.0, "e23": 3.0, "e30": 0, "e31": 0, "e32": 0, "e33": 1}
    out = ac.matrix_scale_rotation(m)
    assert out["scale"] == [s, s, s] and out["euler"] == pytest.approx([-90, 0, 0], abs=0.01)
    inv = ac.invert_affine(m)
    # inverse applied to the translation gives the origin
    p = [sum(inv[r][c] * [1.0, 2.0, 3.0][c] for c in range(3)) + inv[r][3] for r in range(3)]
    assert p == pytest.approx([0, 0, 0], abs=1e-9)


# --- clips -------------------------------------------------------------------------------------------

def clip_tree(name="Walk", bindings=None, events=None):
    # streamed: curves 0-2 = Hip pos (x animated), 3-6 = Leg rot quaternion (animated); dense: curve 7 = active flag;
    # constant: curves 8.. = Leg scale (3)
    q0 = ac.euler_to_quat(0, 0, 0)
    q1 = ac.euler_to_quat(30, 0, 0)
    frames = []
    for t, x, q in ((-3.4028234663852886e38, 0.0, q0), (0.0, 0.0, q0), (1.0, 2.0, q1), (3.4028234663852886e38, 2.0, q1)):
        keys = [(0, x), (1, 0.0), (2, 0.0)] + [(3 + k, q[k]) for k in range(4)]
        frames += [w(t), len(keys)]
        for idx, val in keys:
            frames += [idx, w(0.0), w(0.0), w(0.0), w(val)]
    return {
        "m_Name": name, "m_Legacy": False, "m_Compressed": False, "m_SampleRate": 60.0, "m_WrapMode": 0,
        "m_Events": events or [], "m_HasGenericRootTransform": False, "m_HasMotionFloatCurves": False,
        "m_MuscleClip": {"m_StartTime": 0.0, "m_StopTime": 1.0, "m_LoopTime": True, "m_LoopBlend": False,
                         "m_DeltaPose": {"m_RootX": {"t": {"x": 0, "y": 0, "z": 0}}}, "m_AverageSpeed": {"x": 0, "y": 0, "z": 0},
                         "m_Clip": {"data": {"m_StreamedClip": {"data": frames, "curveCount": 7},
                                             "m_DenseClip": {"m_FrameCount": 3, "m_CurveCount": 1, "m_SampleRate": 2.0,
                                                             "m_BeginTime": 0.0, "m_SampleArray": [0.0, 1.0, 1.0]},
                                             "m_ConstantClip": {"data": [1.0, 1.0, 1.0]}}}},
        "m_ClipBindingConstant": {"genericBindings": bindings if bindings is not None else [
            {"path": ac.crc("Rig/Hip"), "attribute": 1, "typeID": 4, "customType": 0, "isPPtrCurve": 0},
            {"path": ac.crc("Rig/Hip/Leg_L"), "attribute": 2, "typeID": 4, "customType": 0, "isPPtrCurve": 0},
            {"path": ac.crc("Rig/Hip/Leg_L"), "attribute": ac.crc("m_IsActive"), "typeID": 1, "customType": 0, "isPPtrCurve": 0},
            {"path": 0xDEADBEEF, "attribute": 3, "typeID": 4, "customType": 0, "isPPtrCurve": 0}]},
    }


def test_decode_clip_streamed_dense_constant():
    d = ac.decode_clip(clip_tree()["m_MuscleClip"])
    assert (d["nstream"], d["ndense"], d["nconst"], d["frames"]) == (7, 1, 3, 3)
    assert [v for _, v in d["curves"][0]] == [0.0, 0.0, 2.0, 2.0]
    assert d["curves"][0][0][0] is None and d["curves"][0][2][0] == 1.0  # -inf sentinel time
    assert d["curves"][7] == [(0.0, 0.0), (0.5, 1.0), (1.0, 1.0)]
    assert d["curves"][8] == [(None, 1.0)]


def test_bindings_resolution_and_ranges():
    tree = clip_tree()
    maps = Maps(ac.animator_maps(NODES))
    attrs = {ac.crc("m_IsActive"): "m_IsActive"}
    row, rows = analyze_clip(tree, controllers=["PlayerCtl"], maps=maps, attrs=attrs)
    assert [r["path"] for r in rows] == ["Rig/Hip", "Rig/Hip/Leg_L", "Rig/Hip/Leg_L", "#deadbeef"]
    hip, leg, active, unknown = rows
    assert hip["kind"] == "pos" and hip["keys"] == 4 and not hip["const"] and hip["range"][0] == [0.0, 2.0]
    assert leg["kind"] == "rot" and leg["range"][0] == pytest.approx([0.0, 30.0], abs=1e-3)
    assert active["kind"] == "active" and active["attribute"] == "m_IsActive" and active["class"] == "GameObject"
    assert unknown["const"] and not unknown["resolved"]
    assert row["unresolved"] == 1 and row["mapped_by"] == "controller" and row["kind"] == "generic"
    assert row["transforms_moved"] == 2 and row["bones_moved"] == ["Hip", "Leg_L"]
    assert row["length"] == 1.0 and row["fps"] == 60.0 and row["loop"] is True
    # without a controller the global map still resolves (counted apart)
    row2, _ = analyze_clip(tree, controllers=[], maps=maps, attrs=attrs)
    assert row2["resolved_global"] == 3 and row2["unresolved"] == 1


def test_clip_kinds_and_events():
    muscle = [{"path": 0, "attribute": 7, "typeID": 95, "customType": ac.CUSTOM_MUSCLE, "isPPtrCurve": 0}]
    ev = [{"time": 0.25, "functionName": "Step", "data": "L", "intParameter": 0, "floatParameter": 0.0}]
    row, rows = analyze_clip(clip_tree(bindings=muscle, events=ev), controllers=[], maps=Maps([]), attrs={})
    assert row["kind"] == "humanoid" and rows[0]["attribute"] == "RootT.x" and rows[0]["index"] == 7
    assert row["events"] == [{"time": 0.25, "function": "Step", "string": "L"}]
    shape = [{"path": ac.crc("Face"), "attribute": ac.crc("blendShape.Smile"), "typeID": 137,
              "customType": ac.CUSTOM_BLENDSHAPE, "isPPtrCurve": 0}]
    row, rows = analyze_clip(clip_tree(bindings=shape), controllers=[], maps=Maps([]),
                             attrs={ac.crc("blendShape.Smile"): "blendShape.Smile"})
    assert row["kind"] == "blendshape" and rows[0]["attribute"] == "blendShape.Smile"


def test_legacy_clip_curves():
    tree = {"m_Name": "Open", "m_Legacy": True, "m_MuscleClip": {}, "m_ClipBindingConstant": {"genericBindings": []},
            "m_RotationCurves": [{"path": "Panel", "curve": {"m_Curve": [
                {"time": 0, "value": dict(zip("xyzw", ac.euler_to_quat(0, 0, 0)))},
                {"time": 1, "value": dict(zip("xyzw", ac.euler_to_quat(0, 90, 0)))}]}}]}
    row, rows = analyze_clip(tree, controllers=[], maps=Maps([]), attrs={})
    assert row["mapped_by"] == "legacy" and rows[0]["path"] == "Panel" and rows[0]["range"][1] == pytest.approx([0, 90], abs=1e-3)


# --- controller ----------------------------------------------------------------------------------------

def ctl_tree(tos_names):
    def cond(param, mode, thr=0.0):
        return {"data": {"m_ConditionMode": mode, "m_EventID": ac.crc(param), "m_EventThreshold": thr}}

    def trans(dest, conds, dur=0.25):
        return {"data": {"m_DestinationState": dest, "m_ConditionConstantArray": conds, "m_TransitionDuration": dur,
                         "m_HasExitTime": False, "m_HasFixedDuration": True, "m_InterruptionSource": 0}}

    leaf = lambda i: {"data": {"m_ClipID": i, "m_ChildIndices": [], "m_Duration": 1.0}}  # noqa: E731
    blend = {"data": {"m_NodeArray": [
        {"data": {"m_BlendType": 0, "m_BlendEventID": ac.crc("Speed"), "m_BlendEventYID": 0xFFFFFFFF, "m_ChildIndices": [1, 2],
                  "m_Blend1dData": {"data": {"m_ChildThresholdArray": [0.0, 1.0]}}, "m_ClipID": 0xFFFFFFFF}},
        leaf(0), leaf(1)]}}
    states = [
        {"data": {"m_NameID": ac.crc("Idle"), "m_FullPathID": ac.crc("Base Layer.Idle"), "m_Speed": 1.0, "m_Loop": True,
                  "m_WriteDefaultValues": True, "m_BlendTreeConstantArray": [{"data": {"m_NodeArray": [leaf(0)]}}],
                  "m_TransitionConstantArray": [trans(1, [cond("Speed", 3, 0.1)]), trans(30001, [cond("Hidden", 1)])]}},
        {"data": {"m_NameID": ac.crc("Move"), "m_FullPathID": ac.crc("Base Layer.Move"), "m_Speed": 1.0, "m_Loop": True,
                  "m_WriteDefaultValues": True, "m_BlendTreeConstantArray": [blend], "m_TransitionConstantArray": []}},
    ]
    sm = {"data": {"m_StateConstantArray": states, "m_AnyStateTransitionConstantArray": [trans(0, [cond("Hidden", 2)])],
                   "m_SelectorStateConstantArray": [{"data": {"m_FullPathID": ac.crc("Base Layer"), "m_IsEntry": True}},
                                                    {"data": {"m_FullPathID": ac.crc("Base Layer"), "m_IsEntry": False}}],
                   "m_DefaultState": 0}}
    return {"m_Name": "Ctl", "m_TOS": [(ac.crc(n), n) for n in tos_names],
            "m_Controller": {"m_LayerArray": [{"data": {"m_StateMachineIndex": 0, "m_Binding": ac.crc("Base Layer"),
                                                        "(int&)m_LayerBlendingMode": 0, "m_DefaultWeight": 0.0, "m_IKPass": False,
                                                        "m_BodyMask": {"word0": 4294967295, "word1": 4294967295, "word2": 524287},
                                                        "m_SkeletonMask": {"data": {"m_Data": []}}}}],
                             "m_StateMachineArray": [sm],
                             "m_Values": {"data": {"m_ValueArray": [{"m_ID": ac.crc("Speed"), "m_Type": 1, "m_Index": 0},
                                                                    {"m_ID": ac.crc("Hidden"), "m_Type": 4, "m_Index": 0},
                                                                    {"m_ID": ac.crc("Consumed Item"), "m_Type": 9, "m_Index": 1}]}},
                             "m_DefaultValues": {"data": {"m_FloatValues": [0.5], "m_BoolValues": [True, False]}}}}


CLIPS = [{"key": "a:1", "name": "Idle"}, {"key": "a:2", "name": "Run"}]


def test_controller_decode_with_tos():
    names = Names()
    d = decode_controller(ctl_tree(["Base Layer", "Idle", "Move", "Speed", "Hidden", "Base Layer.Idle", "Base Layer.Move"]),
                          CLIPS, names)
    assert [(p["name"], p["type"], p["default"]) for p in d["params"]] == [
        ("Speed", "Float", 0.5), ("Hidden", "Bool", True), (f"#{ac.crc('Consumed Item')}", "Trigger", False)]
    L = d["layers"][0]
    assert L["name"] == "Base Layer" and L["default_state"] == "Idle" and L["blending"] == "Override" and L["body_mask_full"]
    idle, move = L["states"]
    assert idle["clips"] == ["Idle"] and idle["transitions"][0] == {
        "to": "Move", "conditions": [{"param": "Speed", "mode": "Greater", "threshold": 0.1}], "duration": 0.25,
        "fixed": True, "offset": None}
    assert idle["transitions"][1]["to"] == "exit:Base Layer"
    assert move["motion"]["blend"] == "1D" and move["motion"]["param"] == "Speed"
    assert [(c["clip"], c["threshold"]) for c in move["motion"]["children"]] == [("Idle", 0.0), ("Run", 1.0)]
    assert L["any_state"][0]["conditions"][0]["mode"] == "IfNot"
    assert d["counts"]["unresolved_params"] == 1 and d["counts"]["blend_trees"] == 1


def test_controller_hash_names_from_code_and_bruteforce():
    names = Names()
    names.add("Hidden", "code", "Scripts/Player.cs:12")
    tree = ctl_tree(["Base Layer", "Idle", "Move", "Speed"])
    d = decode_controller(tree, CLIPS, names)
    hidden = d["params"][1]
    assert hidden["name"] == "Hidden" and hidden["name_source"] == "code" and hidden["evidence"] == "Scripts/Player.cs:12"
    target = ac.crc("Consumed Item")
    hits = bruteforce({target}, words_of(["ConsumedItemEvent", "Items"]))
    assert hits[target][0] == "Consumed Item"
    names.add(hits[target][0], "bruteforce")
    d = decode_controller(tree, CLIPS, names)
    assert d["params"][2]["name"] == "Consumed Item" and d["params"][2]["confidence"] == "low"


def test_bruteforce_matches_naive_pairs():
    rnd = random.Random(7)
    vocab = sorted({"".join(rnd.choice("abcdeXYZ") for _ in range(rnd.randint(1, 6))) for _ in range(60)})
    targets = {ac.crc(rnd.choice(vocab) + sep + rnd.choice(vocab)) for sep in ("", " ", "_") for _ in range(5)}
    fast = bruteforce(targets, vocab)
    naive: dict[int, set] = {}
    for a in vocab:
        for b in vocab:
            for sep in ("", " ", "_"):
                h = ac.crc(a + sep + b)
                if h in targets:
                    naive.setdefault(h, set()).add(a + sep + b)
    for a in vocab:
        if ac.crc(a) in targets:
            naive.setdefault(ac.crc(a), set()).add(a)
    assert {k: set(v) for k, v in fast.items()} == naive


def test_override_controller():
    refs = {11: {"key": "a:11", "name": "Walk"}, 12: {"key": "a:12", "name": "Walk_NOHEADBOB"}, 5: {"key": "a:5", "name": "Base"}}
    tree = {"m_Name": "NoBob", "m_Controller": {"m_FileID": 0, "m_PathID": 5},
            "m_Clips": [{"m_OriginalClip": {"m_FileID": 0, "m_PathID": 11}, "m_OverrideClip": {"m_FileID": 0, "m_PathID": 12}}]}
    o = decode_override(tree, lambda p: refs.get(p["m_PathID"]))
    assert o["base"]["name"] == "Base" and o["overrides"][0]["override"]["name"] == "Walk_NOHEADBOB"


# --- avatar ----------------------------------------------------------------------------------------------

def test_avatar_humanoid_mapping_and_description_check():
    bones = ["", "Armature", "Armature/Hips", "Armature/Hips/Spine", "Armature/Hips/Spine/Head"]
    hbi = [-1] * 25
    hbi[0], hbi[7], hbi[11] = 2, 3, 4  # Hips, Spine, Head (internal order: UpperChest at 9, Head at 11)
    tree = {"m_Name": "HumanAvatar", "m_TOS": [(ac.crc(b), b) for b in bones],
            "m_Avatar": {"m_AvatarSkeleton": {"data": {"m_Node": [{}] * 5}},
                         "m_SkeletonNameIDArray": [ac.crc(b) for b in bones],
                         "m_Human": {"data": {"m_HumanBoneIndex": hbi, "m_LeftHand": {"data": {"m_HandBoneIndex": [-1] * 15}},
                                              "m_ArmTwist": 0.5, "m_HasTDoF": False}},
                         "m_RootMotionBoneIndex": 2},
            "m_HumanDescription": {"m_Human": [{"m_HumanName": "Hips", "m_BoneName": "Hips"},
                                               {"m_HumanName": "Spine", "m_BoneName": "Spine"},
                                               {"m_HumanName": "Head", "m_BoneName": "Head"}], "m_Skeleton": [{}] * 5}}
    d = decode_avatar(tree)
    assert d["humanoid"] and d["mapping"] == {"Hips": "Armature/Hips", "Spine": "Armature/Hips/Spine",
                                              "Head": "Armature/Hips/Spine/Head"}
    assert d["root_motion_bone"] == "Armature/Hips" and d["mapping_check"] == {"agree": 3, "disagree": 0}
    assert d["settings"]["ArmTwist"] == 0.5
    generic = decode_avatar({"m_Name": "G", "m_TOS": [], "m_Avatar": {"m_SkeletonNameIDArray": [1], "m_Human": {"data": {}}}})
    assert not generic["humanoid"] and generic["skeleton"] == ["#1"]


def test_avatar_skeleton_ids_are_name_hashes_and_muscles_are_named():
    """Real builds: m_SkeletonNameIDArray = CRC32 of the bone name, m_TOS = paths (Safari-TV's avatars)."""
    paths = ["", "Rig", "Rig/Pelvis", "Rig/Pelvis/Spine1", "Rig/Pelvis/Spine1/Spine2", "Rig/Pelvis/Spine1/Spine2/HeadBone"]
    hbi = [-1] * 25
    hbi[0], hbi[7], hbi[8], hbi[11] = 2, 3, 4, 5  # Hips, Spine, Chest, Head (UpperChest at 9 unmapped)
    tree = {"m_Name": "H", "m_TOS": [(ac.crc(p), p) for p in paths],
            "m_Avatar": {"m_SkeletonNameIDArray": [ac.crc(p.rsplit("/", 1)[-1]) for p in paths],
                         "m_Human": {"data": {"m_HumanBoneIndex": hbi}}},
            "m_HumanDescription": {"m_Human": [
                {"m_HumanName": "Hips", "m_BoneName": "Pelvis"}, {"m_HumanName": "Spine", "m_BoneName": "Spine1"},
                {"m_HumanName": "Chest", "m_BoneName": "Spine2"},
                {"m_HumanName": "Head", "m_BoneName": "HeadBone",
                 "m_Limit": {"m_Min": {"x": -40, "y": -40, "z": -40}, "m_Max": {"x": 40, "y": 40, "z": 40},
                             "m_Value": {"x": 0, "y": 0, "z": 0}, "m_Modified": True}}],
                "m_Skeleton": [{"m_Name": p.rsplit("/", 1)[-1]} for p in paths]}}
    d = decode_avatar(tree)
    assert d["unnamed_nodes"] == 0 and d["skeleton"][2] == "Rig/Pelvis"
    assert d["mapping"]["Chest"] == "Rig/Pelvis/Spine1/Spine2" and d["mapping_check"] == {"agree": 4, "disagree": 0}
    assert d["hd_limits"] == {"Head": {"min": {"x": -40, "y": -40, "z": -40}, "max": {"x": 40, "y": 40, "z": 40},
                                       "value": {"x": 0, "y": 0, "z": 0}}}
    muscles = {m["name"]: m for m in d["muscles"]}
    assert len(d["muscles"]) == 95 and muscles["Head Nod Down-Up"]["node"] == "Rig/Pelvis/Spine1/Spine2/HeadBone"
    assert muscles["Spine Front-Back"]["index"] == 0 and muscles["Spine Front-Back"]["curve"] == 42
    assert muscles["Left Thumb 1 Stretched"]["bone"] == "Left Thumb Proximal" and muscles["Jaw Close"]["node"] is None
    # without the TOS, the HumanDescription skeleton names still name the nodes
    tree["m_TOS"] = []
    assert decode_avatar(tree)["skeleton"][2] == "Pelvis"


def test_humanoid_tables_and_clip_curve_names():
    from gst.analysis.unity import humanoid as H

    assert len(H.BONE_NAMES) == 55 and H.BONE_NAMES[0] == "Hips" and H.BONE_NAMES[-1] == "UpperChest"
    assert H.BONE_NAMES[24] == "Left Thumb Proximal" and H.BONE_NAMES[53] == "Right Little Distal"
    assert len(H.MUSCLE_NAMES) == 95 and len(set(H.MUSCLE_NAMES)) == 95 and H.BODY_MUSCLES == 55
    assert H.MUSCLE_NAMES[21] == "Left Upper Leg Front-Back" and H.MUSCLE_NAMES[54] == "Right Hand In-Out"
    assert H.MUSCLE_NAMES[55] == "Left Thumb 1 Stretched" and H.MUSCLE_NAMES[94] == "Right Little 3 Stretched"
    assert set(H.MUSCLE_BONE.values()) <= set(H.BONE_NAMES) and "Hips" not in H.MUSCLE_BONE.values()
    names = [H.clip_attribute(i) for i in (1, 8, 15, 41, 42, 96, 97, 136, 137, 199, 200)]
    assert names == [("MotionT.y", "motion"), ("RootT.y", "root"), ("LeftFootT.y", "goal"), ("RightHandQ.w", "goal"),
                     ("Spine Front-Back", "muscle"), ("Right Hand In-Out", "muscle"), ("Left Thumb 1 Stretched", "finger"),
                     ("Right Little 3 Stretched", "finger"), ("SpineTDOF.x", "tdof"), ("RightHandTDOF.z", "tdof"),
                     ("#200", "unknown")]
    muscle = [{"path": 0, "attribute": a, "typeID": 95, "customType": ac.CUSTOM_MUSCLE, "isPPtrCurve": 0} for a in (8, 42)]
    row, rows = analyze_clip(clip_tree(bindings=muscle), controllers=[], maps=Maps([]), attrs={})
    assert [(r["attribute"], r["group"], r["index"]) for r in rows] == [("RootT.y", "root", 8),
                                                                       ("Spine Front-Back", "muscle", 42)]
    assert row["humanoid_groups"] == {"muscle": 1, "root": 1}


# --- rig -----------------------------------------------------------------------------------------------------

def chain(n=8, shuffle=False):
    positions = [[float(i), 0.0, 0.0] for i in range(n)]
    bindposes = [{"e00": 1, "e11": 1, "e22": 1, "e33": 1, "e03": -p[0], "e13": -p[1], "e23": -p[2]} for p in positions]
    verts, weights, indices = [], [], []
    for i in range(n):
        for k in range(5):
            verts.append((i + 0.1 * k, 0.05 * k, 0.0))
            weights.append((0.7, 0.3, 0.0, 0.0))
            indices.append((i, min(n - 1, i + 1), 0, 0))
    if shuffle:
        perm = list(range(n))
        random.Random(3).shuffle(perm)
        indices = [(perm[a], perm[b], c, d) for a, b, c, d in indices]
    return verts, weights, indices, bindposes


def test_skin_stats_influences_and_geometry_check():
    v, ws, ix, bps = chain()
    st = skin_stats(ws, ix, v, bps)
    assert st["max_influences"] == 2 and st["influences_hist"] == {"2": 40}
    assert st["geometry"]["near"] == 1.0 and st["geometry"]["judged"]
    assert mapping_verdict({"hashes": 0}, st["geometry"], []) is True
    v, ws, ix, bps = chain(shuffle=True)
    bad = skin_stats(ws, ix, v, bps)
    assert bad["geometry"]["near"] < 0.5 and mapping_verdict({"hashes": 0}, bad["geometry"], []) is False


def test_hash_check_names_paths_and_root_index():
    nodes = [{"name": "Hip", "path": "/P/Armature/Hip", "transform": 1}, {"name": "Leg", "path": "/P/Armature/Hip/Leg", "transform": 2}]
    hc = hash_check([ac.crc("Armature/Hip"), ac.crc("Leg")], nodes, ac.crc("Armature/Hip"), 0)
    assert hc["match"] == 2 and hc["compared"] == 2 and hc["root_index_ok"] and hc["count_ok"]
    swapped = hash_check([ac.crc("Leg"), ac.crc("Armature/Hip")], nodes, ac.crc("Armature/Hip"), 0)
    assert swapped["root_index_ok"] is False and swapped["match_other_index"] == 2
    assert mapping_verdict(swapped, None, []) is False
    renamed = hash_check([ac.crc("Armature/Hip"), ac.crc("Leg_renamed_in_model")], nodes, 0, None)
    assert renamed["match"] == 1 and mapping_verdict(renamed, None, []) is None  # partial names: no vote
    assert mapping_verdict({"hashes": 0}, {"judged": False, "near": 1.0, "random_near": 1.0}, []) is None
    assert mapping_verdict({"hashes": 0}, None, [130]) is False


def test_geometry_needs_enough_bones():
    g = geometry_check({0: [0, 0, 0], 1: [1, 0, 0]}, [[0, 0, 0], [1, 0, 0]], 1.0)
    assert g["judged"] is False and g["near"] == 1.0


# --- joints ------------------------------------------------------------------------------------------------

def test_configurable_and_hinge_joint_details():
    cj = {"m_XMotion": 0, "m_YMotion": 0, "m_ZMotion": 0, "m_AngularXMotion": 1, "m_AngularYMotion": 1, "m_AngularZMotion": 1,
          "m_LowAngularXLimit": {"limit": -177.0}, "m_HighAngularXLimit": {"limit": 177.0}, "m_AngularYLimit": {"limit": 177.0},
          "m_AngularZLimit": {"limit": 177.0},
          "m_AngularXDrive": {"positionSpring": 3500.0, "positionDamper": 350.0, "maximumForce": 3.4028234663852886e38},
          "m_AngularYZDrive": {"positionSpring": 3500.0, "positionDamper": 350.0, "maximumForce": 1.0},
          "m_RotationDriveMode": 0, "m_ProjectionMode": 1, "m_ProjectionDistance": 0.1, "m_ProjectionAngle": 180.0,
          "m_BreakForce": float("inf"), "m_Anchor": {"x": 0, "y": 0.5, "z": 0}}
    d = joint_detail("ConfigurableJoint", cj)
    assert d["motion"]["X"] == "Locked" and d["motion"]["AngularX"] == "Limited"
    assert d["angular_x"] == [-177.0, 177.0] and d["drives"]["AngularX"] == {"spring": 3500.0, "damper": 350.0, "max_force": "inf"}
    assert d["rotation_drive_mode"] == "XYAndZ" and d["break_force"] == "inf" and d["anchor"] == [0, 0.5, 0]
    h = joint_detail("HingeJoint", {"m_UseLimits": 1, "m_Limits": {"min": -90.0, "max": 0.0}, "m_UseSpring": 0, "m_UseMotor": 0,
                                    "m_Axis": {"x": 0, "y": 1, "z": 0}})
    assert h["use_limits"] and h["limits"] == [-90.0, 0.0] and h["axis"] == [0, 1, 0]
    assert rigidbody({"m_Mass": 25.0, "m_AngularDamping": 0.05, "m_IsKinematic": 0})["mass"] == 25.0
    assert family("Module - Wall (3)") == "Module - Wall" and family("Door(Clone)") == "Door"


def test_ragdoll_summary_names_pptrs():
    tree = {"m_Script": {"m_FileID": 0, "m_PathID": 9}, "handler": {"MusclesPower": 0.8, "chains": [
        {"ChainName": "Spine", "BoneSetups": [{"SourceBone": {"m_FileID": 0, "m_PathID": 42}, "MassMultiplier": 1.25}]}]}}
    out = mono_summary(tree, {42: "/Char/Armature/Hip"})
    assert "m_Script" not in out and out["handler"]["chains"][0]["BoneSetups"][0]["SourceBone"] == "/Char/Armature/Hip"


# --- registration ------------------------------------------------------------------------------------------

def test_m3b_analyses_registered_and_ordered():
    specs = analysis.all_specs()
    for n in ("animation", "controller", "avatar", "rig", "joints"):
        assert n in specs and specs[n].engines == analysis.UNITY
    order = [s.name for s in analysis.for_engine("unity-mono")]
    assert order.index("controller") < order.index("animation") and order.index("prefabs") < order.index("rig")
    assert order.index("rig") < order.index("animation") and order.index("prefabs") < order.index("joints")
    assert math.isclose(ac.f32(w(1.5)), 1.5)


def test_code_index_literals_are_unquoted():
    from gst.analysis.unity.controller import unquote

    assert unquote('"Input X"') == "Input X" and unquote('@"a\b"') == "a\b" and unquote("'x'") == "x"
    assert unquote('"say \\"hi\\""') == 'say "hi"' and unquote("plain") == "plain"


def test_humanoid_clip_with_transform_curves_too():
    bindings = [{"path": ac.crc("Hip"), "attribute": 1, "typeID": 4, "customType": 0, "isPPtrCurve": 0},
                {"path": 0, "attribute": 50, "typeID": 95, "customType": ac.CUSTOM_MUSCLE, "isPPtrCurve": 0}]
    row, rows = analyze_clip(clip_tree(bindings=bindings), controllers=[], maps=Maps([]), attrs={})
    assert row["kind"] == "humanoid" and "muscles_moved" in row and row["humanoid_groups"] == {"muscle": 1}


def test_avatar_human_bone_index_goes_through_the_human_skeleton():
    """m_HumanBoneIndex indexes m_Human.m_Skeleton (ids = path hashes), not the avatar skeleton."""
    paths = ["", "Rig", "Rig/Extra", "Rig/Pelvis", "Rig/Pelvis/Spine1"]
    human_paths = ["Rig/Pelvis", "Rig/Pelvis/Spine1"]  # the human skeleton skips "Rig/Extra"
    hbi = [-1] * 25
    hbi[0], hbi[7] = 0, 1
    av = {"m_SkeletonNameIDArray": [ac.crc(p.rsplit("/", 1)[-1]) for p in paths],
          "m_Human": {"data": {"m_HumanBoneIndex": hbi, "m_Skeleton": {"data": {"m_ID": [ac.crc(p) for p in human_paths]}}}},
          "m_HumanSkeletonIndexArray": [3, 4]}
    tree = {"m_Name": "H", "m_TOS": [(ac.crc(p), p) for p in paths], "m_Avatar": av}
    assert decode_avatar(tree)["mapping"] == {"Hips": "Rig/Pelvis", "Spine": "Rig/Pelvis/Spine1"}
    av["m_Human"]["data"]["m_Skeleton"] = {"data": {"m_ID": []}}  # no path ids: the index array still maps
    assert decode_avatar(tree)["mapping"] == {"Hips": "Rig/Pelvis", "Spine": "Rig/Pelvis/Spine1"}
