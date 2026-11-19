"""Unreal: uas-shaped JSON readers (Blueprint defaults, DataTables, curves, skeletons, meshes, animation, physics),
the unreal_values / unreal_animation analyses, their library facets and note blocks, uas --no-skip-types, and the
IoStore plan (plain / mixed / encrypted). Synthetic JSON only, no game content."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gst import analysis
from gst.common import GsError
from gst.engines import unreal as ue
from gst.engines.unreal import paks, uas
from gst.engines.unreal import uasjson as uj

PKG = "Game/Content/Hero/BP_Hero"
KEY = "0x" + "CD" * 32
URL = "https://example.org/published-keys"


def ref(cls: str, name: str, path: str) -> dict:
    return {"ObjectName": f"{cls}'{name}'", "ObjectPath": path}


def hero_bp() -> list[dict]:
    """A Blueprint package: generated class (0), CDO (1), a native movement component (2), an SCS root + child (3-6),
    an inherited-component override (7-8), a timeline curve (9), a function (10)."""
    gen = ref("BlueprintGeneratedClass", "BP_Hero_C", f"{PKG}.0")
    cdo = ref("BP_Hero_C", "Default__BP_Hero_C", f"{PKG}.1")
    return [
        {"Type": "BlueprintGeneratedClass", "Name": "BP_Hero_C", "Package": PKG,
         "SuperStruct": ref("Class", "HeroCharacter", "/Script/Game"), "ClassDefaultObject": cdo,
         "ChildProperties": [{"Name": "MaxAmmo"}, {"Name": "K2Node_Event_X"}], "Properties": {}},
        {"Type": "BP_Hero_C", "Name": "Default__BP_Hero_C", "Package": PKG, "Properties": {
            "UberGraphFrame": {}, "MaxAmmo": 30, "bCanDash": True, "Team": "ETeam::Blue",
            "Movement": ref("HeroMovement", "Default__BP_Hero_C:CharMove", f"{PKG}.2"),
            "HitSound": ref("SoundCue", "Hit_Cue", "Game/Content/Audio/Hit_Cue.0"),
            "Title": {"Namespace": "", "Key": "K", "SourceString": "Hero", "LocalizedString": "Hero"},
            "Spawnable": {"AssetPathName": "/Game/Hero/BP_Gun.BP_Gun_C", "SubPathString": ""},
            "Missing": None, "Dash": {"Speed": 900.0, "Cooldown[1]": 2}}},
        {"Type": "HeroMovement", "Name": "CharMove", "Flags": "RF_DefaultSubObject", "Outer": cdo,
         "Properties": {"MaxWalkSpeed": 600.0, "JumpZVelocity": 420.0, "AirControl": 0.2}},
        {"Type": "SimpleConstructionScript", "Name": "SCS", "Outer": gen,
         "Properties": {"RootNodes": [ref("SCS_Node", "BP_Hero_C:SCS.SCS_Node_0", f"{PKG}.4")]}},
        {"Type": "SCS_Node", "Name": "SCS_Node_0", "Outer": ref("SimpleConstructionScript", "BP_Hero_C:SCS", f"{PKG}.3"),
         "Properties": {"ComponentClass": ref("Class", "SceneComponent", "/Script/Engine"),
                        "ComponentTemplate": ref("SceneComponent", "BP_Hero_C:Root_GEN_VARIABLE", f"{PKG}.11"),
                        "ChildNodes": [ref("SCS_Node", "BP_Hero_C:SCS.SCS_Node_1", f"{PKG}.5")],
                        "InternalVariableName": "Root"}},
        {"Type": "SCS_Node", "Name": "SCS_Node_1", "Outer": ref("SimpleConstructionScript", "BP_Hero_C:SCS", f"{PKG}.3"),
         "Properties": {"ComponentClass": ref("Class", "SphereComponent", "/Script/Engine"),
                        "ComponentTemplate": ref("SphereComponent", "BP_Hero_C:Sense_GEN_VARIABLE", f"{PKG}.6"),
                        "InternalVariableName": "Sense"}},
        {"Type": "SphereComponent", "Name": "Sense_GEN_VARIABLE", "Outer": gen, "Properties": {"SphereRadius": 250.0}},
        {"Type": "InheritableComponentHandler", "Name": "ICH", "Outer": gen, "Properties": {"Records": [
            {"ComponentClass": ref("Class", "SkeletalMeshComponent", "/Script/Engine"),
             "ComponentTemplate": ref("SkeletalMeshComponent", "BP_Hero_C:Mesh_GEN_VARIABLE", f"{PKG}.8"),
             "ComponentKey": {"OwnerClass": ref("BlueprintGeneratedClass", "BP_Base_C", "Game/Content/BP_Base.0"),
                              "SCSVariableName": "Mesh"}}]}},
        {"Type": "SkeletalMeshComponent", "Name": "Mesh_GEN_VARIABLE", "Outer": gen,
         "Properties": {"SkeletalMesh": ref("SkeletalMesh", "SK_Hero", "Game/Content/Hero/SK_Hero.0"), "bCastShadow": False}},
        {"Type": "CurveFloat", "Name": "CurveFloat_0", "Outer": gen, "Properties": {"FloatCurve": {"Keys": [
            {"Time": 0.0, "Value": 0.0}, {"Time": 1.0, "Value": 2.5}]}}},
        {"Type": "Function", "Name": "Dash", "Outer": gen},
        {"Type": "SceneComponent", "Name": "Root_GEN_VARIABLE", "Outer": gen},
    ]


def skeleton_pkg() -> list[dict]:
    p = "Game/Content/Hero/SK_Hero_Skeleton"
    me = ref("Skeleton", "SK_Hero_Skeleton", f"{p}.0")
    return [
        {"Type": "Skeleton", "Name": "SK_Hero_Skeleton", "Package": p,
         "Properties": {"Sockets": [ref("SkeletalMeshSocket", "SK_Hero_Skeleton:S0", f"{p}.1")],
                        "SlotGroups": [{"GroupName": "DefaultGroup", "SlotNames": ["DefaultSlot", "Upper"]}],
                        "VirtualBones": [{"SourceBoneName": "hand_l", "TargetBoneName": "hand_r", "VirtualBoneName": "VB_hands"}]},
         "ReferenceSkeleton": {"FinalRefBoneInfo": [{"Name": "root", "ParentIndex": -1}, {"Name": "pelvis", "ParentIndex": 0},
                                                    {"Name": "spine", "ParentIndex": 1}, {"Name": "hand_l", "ParentIndex": 2},
                                                    {"Name": "hand_r", "ParentIndex": 2}]},
         "NameMappings": {"AnimationCurves": {"CurveMetaDataMap": {"Blink": {}, "FootIK": {}}}}, "Guid": "G"},
        {"Type": "SkeletalMeshSocket", "Name": "S0", "Outer": me,
         "Properties": {"SocketName": "weapon", "BoneName": "hand_r", "RelativeLocation": {"X": 1.0, "Y": 0.0, "Z": 0.0}}},
    ]


def mesh_pkg() -> list[dict]:
    return [{"Type": "SkeletalMesh", "Name": "SK_Hero", "Package": "Game/Content/Hero/SK_Hero",
             "Properties": {"Skeleton": ref("Skeleton", "SK_Hero_Skeleton", "Game/Content/Hero/SK_Hero_Skeleton.0"),
                            "PhysicsAsset": ref("PhysicsAsset", "PA_Hero", "Game/Content/Hero/PA_Hero.0"),
                            "LODInfo": [{}, {}], "MorphTargets": [ref("MorphTarget", "SK_Hero:Smile", "Game/Content/Hero/SK_Hero.1")]},
             "SkeletalMaterials": [{"MaterialSlotName": "Body", "Material": ref("MaterialInstanceConstant", "MI_Hero", "Game/Content/Hero/MI_Hero.0")}],
             "LODModels": [{"NumVertices": 5000, "Sections": [{"NumTriangles": 3000, "MaxBoneInfluences": 4},
                                                              {"NumTriangles": 1000, "MaxBoneInfluences": 8}]},
                           {"NumVertices": 1200, "Sections": [{"NumTriangles": 900, "MaxBoneInfluences": 4}]}],
             "ImportedBounds": {"SphereRadius": 95.5}}]


def anim_pkg() -> list[dict]:
    p = "Game/Content/Hero/A_Hero_Jump"
    me = ref("AnimSequence", "A_Hero_Jump", f"{p}.0")
    return [
        {"Type": "AnimSequence", "Name": "A_Hero_Jump", "Package": p,
         "Properties": {"NumFrames": 31, "SequenceLength": 1.0, "bEnableRootMotion": True,
                        "Skeleton": ref("Skeleton", "SK_Hero_Skeleton", "Game/Content/Hero/SK_Hero_Skeleton.0"),
                        "Notifies": [{"NotifyName": "FootStep", "LinkValue": 0.25,
                                      "Notify": ref("AnimNotify_PlaySound", "A_Hero_Jump:N0", f"{p}.1"), "Duration": 0.0}]},
         "CompressedTrackToSkeletonMapTable": [0, 1, 2], "CompressedDataStructure": {"KeyEncodingFormat": "AKF_PerTrackCompression"}},
        {"Type": "AnimNotify_PlaySound", "Name": "N0", "Outer": me, "Properties": {}},
    ]


def montage_pkg() -> list[dict]:
    return [{"Type": "AnimMontage", "Name": "AM_Hero_Attack", "Package": "Game/Content/Hero/AM_Hero_Attack",
             "Properties": {"SequenceLength": 1.5, "BlendOut": {"BlendTime": 0.1},
                            "CompositeSections": [{"SectionName": "Start", "NextSectionName": "Loop", "SegmentBeginTime": 0.0},
                                                  {"SectionName": "Loop", "NextSectionName": "None", "SegmentBeginTime": 0.5}],
                            "SlotAnimTracks": [{"SlotName": "Upper", "AnimTrack": {"AnimSegments": [
                                {"AnimReference": ref("AnimSequence", "A_Hero_Swing", "Game/Content/Hero/A_Hero_Swing.0"),
                                 "AnimStartTime": 0.0, "AnimEndTime": 1.5, "AnimPlayRate": 1.25}]}}]}}]


def blendspace_pkg() -> list[dict]:
    return [{"Type": "BlendSpace", "Name": "BS_Hero_Move", "Package": "Game/Content/Hero/BS_Hero_Move",
             "Properties": {"BlendParameters": {"DisplayName": "Speed", "Min": 0.0, "Max": 600.0, "GridNum": 4},
                            "BlendParameters[1]": {"DisplayName": "Direction", "Min": -180.0, "Max": 180.0},
                            "SampleData": [{"Animation": ref("AnimSequence", "A_Idle", "Game/Content/Hero/A_Idle.0"),
                                            "SampleValue": {"X": 0.0, "Y": 0.0, "Z": 0.0}},
                                           {"Animation": ref("AnimSequence", "A_Run", "Game/Content/Hero/A_Run.0"),
                                            "SampleValue": {"X": 600.0, "Y": 0.0, "Z": 0.0}, "RateScale": 1.1}]}}]


def abp_pkg() -> list[dict]:
    p = "Game/Content/Hero/ABP_Hero"
    return [
        {"Type": "AnimBlueprintGeneratedClass", "Name": "ABP_Hero_C", "Package": p,
         "SuperStruct": ref("Class", "AnimInstance", "/Script/Engine"),
         "Properties": {"TargetSkeleton": ref("Skeleton", "SK_Hero_Skeleton", "Game/Content/Hero/SK_Hero_Skeleton.0"),
                        "SyncGroupNames": ["Feet"],
                        "BakedStateMachines": [{"MachineName": "Locomotion", "InitialState": 0, "States": [
                            {"StateName": "Idle"}, {"StateName": "Run"}, {"StateName": "Jump", "bIsAConduit": False}],
                            "Transitions": [{"PreviousState": 0, "NextState": 1, "CrossfadeDuration": 0.2,
                                             "BlendMode": "EAlphaBlendOption::Linear"},
                                            {"PreviousState": 1, "NextState": 2, "CrossfadeDuration": 0.1},
                                            {"PreviousState": 2, "NextState": 0, "CrossfadeDuration": 0.3}]}]}},
        {"Type": "ABP_Hero_C", "Name": "Default__ABP_Hero_C", "Package": p, "Properties": {
            "AnimGraphNode_SequencePlayer": {"Sequence": ref("AnimSequence", "A_Idle", "Game/Content/Hero/A_Idle.0")},
            "AnimGraphNode_SequencePlayer_2": {"Sequence": None},
            "AnimGraphNode_BlendSpacePlayer": {"BlendSpace": ref("BlendSpace", "BS_Hero_Move", "Game/Content/Hero/BS_Hero_Move.0")},
            "AnimGraphNode_StateMachine": {"StateMachineIndexInClass": 0}, "LeanAmount": 0.5}},
    ]


def physics_pkg() -> list[dict]:
    p = "Game/Content/Hero/PA_Hero"
    me = ref("PhysicsAsset", "PA_Hero", f"{p}.0")
    return [
        {"Type": "PhysicsAsset", "Name": "PA_Hero", "Package": p,
         "Properties": {"SkeletalBodySetups": [ref("SkeletalBodySetup", "PA_Hero:B0", f"{p}.1"),
                                               ref("SkeletalBodySetup", "PA_Hero:B1", f"{p}.2")],
                        "ConstraintSetup": [ref("PhysicsConstraintTemplate", "PA_Hero:C0", f"{p}.3"),
                                            ref("PhysicsConstraintTemplate", "PA_Hero:C1", f"{p}.4")]}},
        {"Type": "SkeletalBodySetup", "Name": "B0", "Outer": me, "Properties": {
            "BoneName": "pelvis", "AggGeom": {"SphylElems": [{"Radius": 10.0, "Length": 20.0}],
                                              "BoxElems": [{"X": 1.0}]},
            "DefaultInstance": {"MassInKgOverride": 30.0, "bOverrideMass": True}}},
        {"Type": "SkeletalBodySetup", "Name": "B1", "Outer": me, "Properties": {
            "BoneName": "spine", "AggGeom": {"SphereElems": [{"Radius": 8.0}]}, "DefaultInstance": {"MassInKgOverride": 99.0}}},
        {"Type": "PhysicsConstraintTemplate", "Name": "C0", "Outer": me, "Properties": {"DefaultInstance": {
            "JointName": "spine", "ConstraintBone1": "spine", "ConstraintBone2": "pelvis",
            "ProfileInstance": {"ConeLimit": {"Swing1Motion": "ACM_Limited", "Swing2Motion": "ACM_Limited", "Swing1LimitDegrees": 30.0},
                                "TwistLimit": {"TwistMotion": "ACM_Limited", "TwistLimitDegrees": 10.0}}}}},
        {"Type": "PhysicsConstraintTemplate", "Name": "C1", "Outer": me, "Properties": {"DefaultInstance": {
            "JointName": "knee", "ConstraintBone1": "calf", "ConstraintBone2": "thigh",
            "ProfileInstance": {"ConeLimit": {"Swing1Motion": "ACM_Limited", "Swing2Motion": "ACM_Locked"},
                                "TwistLimit": {"TwistMotion": "ACM_Locked"}}}}},
    ]


def data_pkgs() -> dict[str, list[dict]]:
    return {
        "Game/Content/Data/DT_Weapons": [{"Type": "DataTable", "Name": "DT_Weapons",
            "Properties": {"RowStruct": ref("ScriptStruct", "WeaponRow", "/Script/Game")},
            "Rows": {"Pistol": {"Damage": 10, "Fire": {"Rate": 4.0}, "Icon": ref("Texture2D", "T_Pistol", "Game/Content/UI/T_Pistol.0")},
                     "Rifle": {"Damage": 25, "Fire": {"Rate": 8.0}, "Icon": None}}}],
        "Game/Content/Data/CT_Scaling": [{"Type": "CurveTable", "Name": "CT_Scaling", "CurveTableMode": "RichCurves",
            "Rows": {"HP": {"Keys": [{"Time": 1, "Value": 100.0}, {"Time": 10, "Value": 400.0}]}}}],
        "Game/Content/Data/C_Recoil": [{"Type": "CurveFloat", "Name": "C_Recoil",
            "Properties": {"FloatCurve": {"Keys": [{"Time": 0.0, "Value": 1.0}, {"Time": 0.5, "Value": 0.0}]}}}],
        "Game/Content/Data/ED_Grunt": [{"Type": "EnemyDescriptor", "Name": "ED_Grunt",
            "Properties": {"Health": 120.0, "Rarity": "ERarity::Common", "Class": ref("Class", "Grunt", "/Script/Game")}}],
    }


def write_ws(tmp_path) -> tuple[Path, dict]:
    ws = tmp_path / "ws"
    out = ws / "assets" / "unreal" / "json"
    pkgs = {PKG: hero_bp(), "Game/Content/Hero/SK_Hero_Skeleton": skeleton_pkg(), "Game/Content/Hero/SK_Hero": mesh_pkg(),
            "Game/Content/Hero/A_Hero_Jump": anim_pkg(), "Game/Content/Hero/AM_Hero_Attack": montage_pkg(),
            "Game/Content/Hero/BS_Hero_Move": blendspace_pkg(), "Game/Content/Hero/ABP_Hero": abp_pkg(),
            "Game/Content/Hero/PA_Hero": physics_pkg(), **data_pkgs()}
    for pkg, exports in pkgs.items():
        f = out / (pkg + ".json")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(exports), encoding="utf-8")
    (out / "_folder.gs.json").write_text("{}", encoding="utf-8")
    (ws / "assets" / "inventory.jsonl").write_text(json.dumps({"path": "Game/Content/Hero/AM_Other_Montage.uasset",
                                                               "container": "c.pak"}) + "\n", encoding="utf-8")
    game = {"slug": "g", "name": "G", "buildid": "1", "engine": {"key": "unreal", "version": "4.27.2"}}
    return ws, game


# --- readers ---------------------------------------------------------------------------------------------------

def test_refs_and_values():
    r = ref("SoundCue", "Hit_Cue", "Game/Content/Audio/Hit_Cue.0")
    assert uj.ref_text(r) == "SoundCue'Game/Content/Audio/Hit_Cue.Hit_Cue'"
    assert uj.ref_text(ref("Class", "Grunt", "/Script/Game")) == "Class'/Script/Game.Grunt'"
    assert uj.ref_name(ref("PlayerHealthComponent", "Default__BP_C:Health", "P.3")) == "Health"
    assert uj.ref_index(ref("X", "Y", "Pkg/A.12")) == 12 and uj.ref_index(ref("Class", "C", "/Script/Game")) is None
    assert uj.ref_text({"AssetPathName": "/Game/A.A_C", "SubPathString": ""}) == "/Game/A.A_C"
    leaves = {p: (v, k) for p, v, k in uj.leaves({"A": 1, "B": [True, {"C": "EFoo::Bar"}], "R": r, "N": None,
                                                  "T": {"Key": "k", "SourceString": "s", "LocalizedString": "l"}, "S": "ECR_Block",
                                                  "F": 0.5, "Name": "Hello"})}
    assert leaves["A"] == (1, "int") and leaves["B[0]"] == (True, "bool") and leaves["B[1].C"] == ("EFoo::Bar", "enum")
    assert leaves["R"][1] == "ref" and "N" not in leaves and leaves["T"] == ("l", "text") and leaves["S"][1] == "enum"
    assert leaves["F"] == (0.5, "float") and leaves["Name"] == ("Hello", "name")
    assert uj.values_of({"A": 1, "R": r, "Name": "x", "E": "EFoo::Bar"}) == {"A": 1, "E": "EFoo::Bar"}


def test_blueprint_cdo_components_and_values():
    bp = uj.blueprint(hero_bp(), PKG)
    assert bp["class"] == "BP_Hero_C" and bp["parent"] == "HeroCharacter" and bp["native_parent"] and bp["cdo"] == "Default__BP_Hero_C"
    comps = {c["name"]: c for c in bp["components"]}
    assert comps["CharMove"] == {"name": "CharMove", "class": "HeroMovement", "origin": "native", "attach": None}
    assert comps["Root"]["origin"] == "scs" and comps["Root"]["class"] == "SceneComponent" and comps["Root"]["attach"] is None
    assert comps["Sense"]["attach"] == "Root" and comps["Sense"]["class"] == "SphereComponent"
    assert comps["Mesh"]["origin"] == "inherited" and comps["Mesh"]["owner"] == "BP_Base_C"
    v = bp["values"]
    assert v["MaxAmmo"] == 30 and v["bCanDash"] is True and v["Team"] == "ETeam::Blue" and v["Dash.Speed"] == 900.0
    assert v["Dash.Cooldown[1]"] == 2 and v["CharMove.MaxWalkSpeed"] == 600.0 and v["CharMove.JumpZVelocity"] == 420.0
    assert v["Sense.SphereRadius"] == 250.0 and v["Mesh.bCastShadow"] is False and "HitSound" not in v
    d = bp["defaults"]
    assert "UberGraphFrame" not in d and d["HitSound"] == "SoundCue'Game/Content/Audio/Hit_Cue.Hit_Cue'"
    assert d["Title"] == "Hero" and d["Spawnable"] == "/Game/Hero/BP_Gun.BP_Gun_C" and d["Missing"] is None
    assert bp["component_defaults"]["Mesh"]["SkeletalMesh"] == "SkeletalMesh'Game/Content/Hero/SK_Hero.SK_Hero'"
    assert bp["curves"] == [{"name": "CurveFloat_0", "class": "CurveFloat", "keys": [[0.0, 0.0], [1.0, 2.5]]}]
    assert bp["functions"] == ["Dash"] and bp["variables"] == ["MaxAmmo"]
    assert uj.blueprint(data_pkgs()["Game/Content/Data/ED_Grunt"], "x") is None


def test_skeleton_mesh_and_animation_readers():
    sk_exports = skeleton_pkg()
    sk = uj.skeleton(sk_exports, sk_exports[0])
    assert sk["bones"] == 5 and sk["root"] == "root" and sk["depth"] == 4 and sk["leaves"] == 2 and sk["roots"] == 1
    assert sk["sockets"] == [{"name": "weapon", "bone": "hand_r", "location": {"X": 1.0, "Y": 0.0, "Z": 0.0}, "rotation": None}]
    assert sk["virtual_bones"][0]["name"] == "VB_hands" and sk["curves"] == ["Blink", "FootIK"]
    assert sk["slot_groups"] == [{"group": "DefaultGroup", "slots": ["DefaultSlot", "Upper"]}]
    m = uj.skeletal_mesh(mesh_pkg()[0])
    assert m["lods"][0] == {"sections": 2, "verts": 5000, "tris": 4000, "max_influences": 8} and len(m["lods"]) == 2
    assert m["lod_info"] == 2 and m["morph_targets"] == ["Smile"] and m["materials"][0]["slot"] == "Body"
    assert m["skeleton"] == "Skeleton'Game/Content/Hero/SK_Hero_Skeleton.SK_Hero_Skeleton'" and m["bones"] is None
    a_exports = anim_pkg()
    a = uj.anim_sequence(a_exports, a_exports[0])
    assert a["fps"] == 30.0 and a["keys"] == 31 and a["length"] == 1.0 and a["tracks"] == 3 and a["root_motion"]
    assert a["notifies"] == [{"name": "FootStep", "class": "AnimNotify_PlaySound", "time": 0.25, "duration": 0.0, "track": None}]
    ue5 = uj.anim_sequence([], {"Name": "X", "Properties": {"SequenceLength": 2.0, "NumberOfSampledKeys": 121,
                                                            "SamplingFrameRate": {"Numerator": 60, "Denominator": 1}}})
    assert ue5["fps"] == 60.0 and ue5["keys"] == 121
    mo = uj.anim_montage([], montage_pkg()[0])
    assert [s["name"] for s in mo["sections"]] == ["Start", "Loop"] and mo["slots"][0]["slot"] == "Upper"
    assert mo["slots"][0]["segments"][0]["rate"] == 1.25 and mo["blend_out"] == 0.1 and mo["blend_in"] == 0.25
    assert mo["defaulted"] == ["BlendIn"] and mo["blend_out_trigger"] == -1.0
    bs = uj.blend_space(blendspace_pkg()[0])
    assert [a["name"] for a in bs["axes"]] == ["Speed", "Direction"] and bs["axes"][0]["grid"] == 4
    assert bs["samples"][1] == {"anim": "AnimSequence'Game/Content/Hero/A_Run.A_Run'", "x": 600.0, "y": 0.0, "rate": 1.1}
    abp = uj.anim_blueprint(abp_pkg(), "Game/Content/Hero/ABP_Hero")
    m0 = abp["machines"][0]
    assert m0["name"] == "Locomotion" and m0["initial"] == "Idle" and m0["states"] == ["Idle", "Run", "Jump"]
    assert m0["transitions"][0] == {"from": "Idle", "to": "Run", "crossfade": 0.2, "blend": "EAlphaBlendOption::Linear", "logic": None}
    assert abp["nodes"] == {"BlendSpacePlayer": 1, "SequencePlayer": 2, "StateMachine": 1}
    assert abp["assets"] == ["AnimSequence'Game/Content/Hero/A_Idle.A_Idle'", "BlendSpace'Game/Content/Hero/BS_Hero_Move.BS_Hero_Move'"]
    assert abp["values"] == {"LeanAmount": 0.5} and abp["parent"] == "AnimInstance"


def test_physics_asset_bodies_and_constraint_defaults():
    pa_exports = physics_pkg()
    pa = uj.physics_asset(pa_exports, pa_exports[0])
    assert [b["bone"] for b in pa["bodies"]] == ["pelvis", "spine"]
    assert pa["bodies"][0]["shapes"] == {"capsule": 1, "box": 1} and pa["bodies"][0]["mass"] == 30.0
    assert pa["bodies"][1]["mass"] is None  # MassInKgOverride without bOverrideMass is not the mass
    assert pa["shape_counts"] == {"box": 1, "capsule": 1, "sphere": 1} and pa["mass_override_total"] == 30.0
    c0, c1 = pa["constraints"]
    assert c0["type"] == "cone-twist" and c0["swing1"] == {"motion": "limited", "limit": 30.0}
    assert c0["swing2"] == {"motion": "limited", "limit": 45.0} and "Swing2LimitDegrees" in c0["defaulted"]
    assert c0["twist"]["limit"] == 10.0 and c0["linear"] == {"x": "locked", "y": "locked", "z": "locked", "limit": 0.0}
    assert c1["type"] == "hinge" and c1["bone1"] == "calf" and c1["bone2"] == "thigh"
    assert pa["joint_types"] == {"cone-twist": 1, "hinge": 1}
    assert uj.joint_type("free", "free", "free") == "ball" and uj.joint_type("locked", "locked", "locked") == "fixed"
    assert uj.joint_type("limited", "free", "locked") == "mixed"


def test_tables_and_curves():
    dt = uj.datatable(data_pkgs()["Game/Content/Data/DT_Weapons"][0])
    assert dt["row_struct"] == "ScriptStruct'/Script/Game.WeaponRow'"
    assert dt["rows"]["Pistol"]["Icon"] == "Texture2D'Game/Content/UI/T_Pistol.T_Pistol'"
    assert uj.columns(dt["rows"]) == {"Damage": {"min": 10, "max": 25, "rows": 2}, "Fire.Rate": {"min": 4.0, "max": 8.0, "rows": 2}}
    ct = uj.curvetable(data_pkgs()["Game/Content/Data/CT_Scaling"][0])
    assert ct == {"mode": "RichCurves", "rows": {"HP": [[1, 100.0], [10, 400.0]]}}


# --- analyses --------------------------------------------------------------------------------------------------

def test_unreal_values_analysis(tmp_path):
    ws, game = write_ws(tmp_path)
    r = analysis.run(ws, game, "unreal_values")
    assert r["status"] == "done", r.get("reason")
    doc = json.loads((ws / "analysis" / "unreal_values.json").read_text(encoding="utf-8"))
    s, d = doc["summary"], doc["data"]
    assert s["blueprints"] == 2 and s["datatables"] == 1 and s["datatable_rows"] == 2 and s["curve_tables"] == 1
    assert s["data_assets"] == 1 and s["curves"] == 2
    abp, bp = d["blueprints"]  # sorted by package: ABP_Hero, BP_Hero
    assert abp["values"] == {"LeanAmount": 0.5} and abp["type"] == "AnimBlueprintGeneratedClass"
    assert bp["values"]["CharMove.MaxWalkSpeed"] == 600.0 and bp["parent"] == "HeroCharacter"
    detail = json.loads((ws / bp["detail"]).read_text(encoding="utf-8"))
    assert detail["defaults"]["HitSound"].startswith("SoundCue'") and detail["file"].endswith("BP_Hero.json")
    assert bp["detail"] == "analysis/unreal_values/blueprints/Game/Content/Hero/BP_Hero.json"
    rows = json.loads((ws / d["datatables"][0]["detail"]).read_text(encoding="utf-8"))["rows"]
    assert rows["Rifle"]["Damage"] == 25
    assert d["data_assets"][0]["class"] == "EnemyDescriptor" and d["data_assets"][0]["values"] == {"Health": 120.0, "Rarity": "ERarity::Common"}
    assert {c["name"] for c in d["curves"]} == {"C_Recoil", "CurveFloat_0"}
    md = (ws / "analysis" / "unreal_values.md").read_text(encoding="utf-8")
    assert "CharMove.MaxWalkSpeed" in md and "## DataTables" in md
    r2 = analysis.run(ws, game, "unreal_values", {"name": "Data/"})
    assert r2["summary"]["blueprints"] == 0 and r2["summary"]["datatables"] == 1


def test_unreal_animation_analysis(tmp_path):
    ws, game = write_ws(tmp_path)
    r = analysis.run(ws, game, "unreal_animation")
    assert r["status"] == "done", r.get("reason")
    doc = json.loads((ws / "analysis" / "unreal_animation.json").read_text(encoding="utf-8"))
    s, d = doc["summary"], doc["data"]
    assert s == {"skeletons": 1, "bones_max": 5, "skeletal_meshes": 1, "sequences": 1, "montages": 1, "blendspaces": 1,
                 "anim_blueprints": 1, "state_machines": 1, "states": 3, "physics_assets": 1, "bodies": 2, "constraints": 2}
    assert d["meshes"][0]["skeleton_bones"] == 5  # taken from the exported skeleton
    side = json.loads((ws / d["skeletons"][0]["detail"]).read_text(encoding="utf-8"))
    assert [b["name"] for b in side["bone_list"]] == ["root", "pelvis", "spine", "hand_l", "hand_r"]
    assert "bone_list" not in d["skeletons"][0]
    md = (ws / "analysis" / "unreal_animation.md").read_text(encoding="utf-8")
    assert "State machine **Locomotion**" in md and "## PhysicsAsset PA_Hero" in md


def test_unreal_animation_notes_types_uas_skipped(tmp_path):
    ws, game = write_ws(tmp_path)
    (ws / "assets" / "unreal" / "json" / "Game" / "Content" / "Hero" / "AM_Hero_Attack.json").unlink()
    r = analysis.run(ws, game, "unreal_animation")
    doc = json.loads((ws / "analysis" / "unreal_animation.json").read_text(encoding="utf-8"))
    assert any("--all-types" in n for n in doc.get("notes") or []), r


def test_needs_exported_json(tmp_path):
    ws = tmp_path / "ws"
    (ws / "assets" / "unreal").mkdir(parents=True)
    (ws / "assets" / "unreal" / "containers.json").write_text("{}", encoding="utf-8")
    r = analysis.run(ws, {"slug": "u", "engine": {"key": "unreal"}}, "unreal_values")
    assert r["status"] == "skipped" and "unreal.json" in r["reason"]
    names = [s.name for s in analysis.for_engine("unreal")]
    assert "unreal_values" in names and "unreal_animation" in names


# --- library and notes -----------------------------------------------------------------------------------------

def test_library_facets_read_unreal_animation(tmp_path):
    from gst.library import facets

    ws, game = write_ws(tmp_path)
    assert analysis.run(ws, game, "unreal_animation")["status"] == "done"
    rows: dict[str, list] = {}
    rd, present = facets.extract(ws, lambda table, row: rows.setdefault(table, []).append(row))
    assert "unreal_animation" in present
    rigs = {r["kind"]: r for r in rows["rigs"]}
    assert rigs["unreal-skeleton"]["bones"] == 5 and rigs["unreal-skeleton"]["root_bone"] == "root"
    assert rigs["unreal-skelmesh"]["bones"] == 5 and rigs["unreal-skelmesh"]["tris"] == 4000 and rigs["unreal-skelmesh"]["blendshapes"] == 1
    clips = {r["clip_kind"]: r for r in rows["clips"]}
    assert clips["sequence"]["fps"] == 30.0 and clips["sequence"]["events"] == 1 and clips["montage"]["length"] == 1.5
    graphs = {r["kind"]: r for r in rows["anim_graphs"]}
    assert graphs["unreal-abp"]["states"] == 3 and graphs["unreal-abp"]["transitions"] == 3 and graphs["unreal-blendspace"]["clips"] == 2
    rag = rows["ragdolls"][0]
    assert rag["bodies"] == 2 and rag["joints"] == 2 and json.loads(rag["joint_types"]) == {"cone-twist": 1, "hinge": 1}
    assert {j["connected"] for j in rows["joints"]} == {"spine -> pelvis", "calf -> thigh"}
    assert all(r["source"] == "analysis/unreal_animation.json" for t in rows.values() for r in t if "source" in r and t is not rows.get("audio_files"))
    gaps = facets.gaps("unreal", set(present), "g")
    assert any(g["facet"] == "rigs" and g["kind"] == "partial" for g in gaps)
    assert not any(g["analysis"] == "unreal_animation" for g in gaps)


def test_mecanica_tunables_block_lists_unreal_values(tmp_path):
    from gst import notetemplates

    ws, game = write_ws(tmp_path)
    analysis.run(ws, game, "unreal_values")
    an = notetemplates._Analyses(ws)
    text = notetemplates.block_tunables(an, "", {"match": "Walk|Jump"})
    assert "`CharMove.MaxWalkSpeed`" in text and "600.0" in text and "`CharMove.JumpZVelocity`" in text
    assert "MaxAmmo" not in text and "unreal_values.json" in text
    assert "`Health`" in notetemplates.block_tunables(an, "", {"match": "ED_Grunt"})


# --- uas and IoStore -------------------------------------------------------------------------------------------

def test_uas_all_types_only_in_json_mode(tmp_path):
    args = uas.export_args(tmp_path, tmp_path / "o", "json", "GAME_UE4_27", log=tmp_path / "l", all_types=True)
    assert "--no-skip-types" in args
    assert "--no-skip-types" not in uas.export_args(tmp_path, tmp_path / "o", "models", "GAME_UE4_27", log=tmp_path / "l",
                                                    all_types=True)
    assert "--no-skip-types" not in uas.export_args(tmp_path, tmp_path / "o", "json", "GAME_UE4_27", log=tmp_path / "l")


def _utoc(path: Path, *, encrypted: bool, global_: bool = False) -> None:
    from test_unreal_m6 import make_utoc

    make_utoc(path, flags=(2 if encrypted else 0) | 8, container_id=paks.GLOBAL_CONTAINER_ID if global_ else 0x1234)


@pytest.fixture
def iostore(tmp_path):
    root = tmp_path / "Game"
    p = root / "Proj" / "Content" / "Paks"
    p.mkdir(parents=True)
    ws = tmp_path / "ws"
    (ws / "notes").mkdir(parents=True)
    game = {"slug": "g", "buildid": "1", "install_dir": str(root), "verdict": "offline-ok",
            "engine": {"key": "unreal", "version": "5.4.1"}}
    return root, p, ws, game


def test_iostore_plan_all_encrypted_is_blocked_with_global_hint(iostore):
    from gst.engines.unreal import export

    root, p, ws, game = iostore
    _utoc(p / "global.utoc", encrypted=False, global_=True)
    _utoc(p / "pakchunk0-Windows.utoc", encrypted=True)
    _utoc(p / "pakchunk1-Windows.utoc", encrypted=True)
    with pytest.raises(GsError) as e:
        export.iostore_plan(ws, game, paks.containers(p))
    msg = str(e.value)
    assert e.value.code == 3 and "pakchunk0-Windows.utoc" in msg and "pakchunk1-Windows.utoc" in msg
    assert "gst analyze unreal_assets" in msg and "rule 4" in msg


def test_iostore_plan_encrypted_global_blocks_everything(iostore):
    from gst.engines.unreal import export

    root, p, ws, game = iostore
    _utoc(p / "global.utoc", encrypted=True, global_=True)
    _utoc(p / "pakchunk0-Windows.utoc", encrypted=False)
    with pytest.raises(GsError) as e:
        export.iostore_plan(ws, game, paks.containers(p))
    assert e.value.code == 3 and str(e.value).startswith("global.utoc is encrypted")


def test_iostore_mixed_converts_plain_containers_from_a_mount(iostore, monkeypatch):
    from gst.engines.unreal import export

    root, p, ws, game = iostore
    _utoc(p / "global.utoc", encrypted=False, global_=True)
    _utoc(p / "pakchunk0-Windows.utoc", encrypted=True)
    _utoc(p / "pakchunk5-Windows.utoc", encrypted=False)
    plan = export.iostore_plan(ws, game, paks.containers(p))
    assert plan["mixed"] and plan["convert"] == ["pakchunk5-Windows.utoc"] and plan["key"] is None
    assert [b["name"] for b in plan["blocked"]] == ["pakchunk0-Windows.utoc"]
    calls = []

    def fake_run(cmd, timeout=None):
        calls.append(cmd)
        if Path(cmd[0]).stem == "retoc":
            Path(cmd[-1]).write_bytes(b"pak")
            return SimpleNamespace(returncode=0, stderr="", stdout="Extracted 2 (0 failed) legacy assets to x")
        args = Path(cmd[1][1:]).read_text(encoding="utf-8").splitlines()
        out = Path(args[args.index("--output") + 1])
        (out / "Proj").mkdir(parents=True, exist_ok=True)
        (out / "Proj" / "A.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ue, "run", fake_run)
    monkeypatch.setattr(ue, "tool_bin", lambda tool: Path(f"C:/tools/{tool}.exe"))
    monkeypatch.setattr(ue, "tool_version", lambda tool: "1.0")
    before = sorted((f.name, f.stat().st_size, f.stat().st_mtime_ns) for f in p.iterdir())
    r = export.to_legacy(ws, root, game, filters=None, modes=["json"], name="legacy-x", all_types=True)
    retoc_cmd = next(c for c in calls if Path(c[0]).stem == "retoc")
    mount = ws / "dumps" / "unreal" / "mount" / "legacy-x"
    assert Path(retoc_cmd[-2]) == mount and "--no-shaders" in retoc_cmd and "--aes-key" not in retoc_cmd
    assert sorted(f.name for f in mount.iterdir()) == ["global.ucas", "global.utoc", "pakchunk5-Windows.ucas", "pakchunk5-Windows.utoc"]
    assert r["input"] in ("symlinks", "copies") and r["blocked"][0]["name"] == "pakchunk0-Windows.utoc" and r["exported"] == 1
    rsp = Path(next(c for c in calls if Path(c[0]).stem == "uas")[1][1:]).read_text(encoding="utf-8").splitlines()
    assert "--no-skip-types" in rsp
    side = json.loads((ws / "assets" / "unreal" / "iostore" / "json" / "_folder.gs.json").read_text(encoding="utf-8"))
    assert side["converted"] == ["pakchunk5-Windows.utoc"] and side["blocked"][0]["name"] == "pakchunk0-Windows.utoc"
    assert sorted((f.name, f.stat().st_size, f.stat().st_mtime_ns) for f in p.iterdir()) == before  # install untouched


def test_iostore_job_blocked_policy_when_all_encrypted(iostore, monkeypatch):
    from gst.engines.unreal.extractors import UnrealIoStore
    from gst.extractors.base import Context

    root, p, ws, game = iostore
    _utoc(p / "global.utoc", encrypted=False, global_=True)
    _utoc(p / "pakchunk0-Windows.utoc", encrypted=True)
    monkeypatch.setattr(ue, "run", lambda cmd, timeout=None: pytest.fail("no tool may run"))
    ctx = Context(workspace=ws, game=game, install_dir=root, out_root=ws, policy="offline-ok", options={})
    job = UnrealIoStore()
    unit = next(iter(job.discover(ctx)))
    res = job.run(unit, ctx)
    assert res.status == "blocked-policy" and "gst analyze unreal_assets" in res.error


def test_retoc_without_a_count_line_is_unknown_not_zero(tmp_path, monkeypatch):
    from gst.engines.unreal import retoc

    def fake_run(cmd, timeout=None):
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"pak")
        return SimpleNamespace(returncode=0, stderr="", stdout="done\n")

    monkeypatch.setattr(ue, "run", fake_run)
    monkeypatch.setattr(ue, "tool_bin", lambda tool: Path(f"C:/tools/{tool}.exe"))
    r = retoc.to_legacy(tmp_path / "Paks", tmp_path / "o" / "l.pak", tmp_path / "retoc.log")
    assert r["extracted"] is None and r["paks"] == [str(tmp_path / "o" / "l.pak")]
