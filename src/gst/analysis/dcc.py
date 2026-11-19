"""``gst analyze dcc``: where the 3D content came from (Blender, Maya, 3ds Max, Mixamo, Rigify, Synty, Reallusion,
asset-store packs), from names and transform signatures. Any engine.

Port of ``repo/dcc_names.py`` + ``dcc_transforms.py``, generalised with the rules the PEAK, Safari-TV and Pratfall
dossiers used by hand. Sources, whichever the workspace has:

- Unity inventory names (``Mesh``, ``AnimationClip``, ``Material``, ``Texture2D``, ``GameObject`` = nodes and bones);
- Unity ``Transform``/``RectTransform`` raw bytes: localRotation -90/+90 degrees on X ((-0.7071,0,0,0.7071)) and
  localScale 100 / 0.01, the FBX import signature (option ``transforms=0`` skips the scan);
- glTF files (node, bone, mesh, material, clip and image names; node signatures; ``asset.generator``);
- Godot text scenes (``.tscn``: node names, ``bone_name``, skeleton ``bones/N/name``) and ``.import`` files
  (``source_file`` extension: a ``.blend`` source is direct evidence).

Output: per rule the matching objects, unique names and samples (**evidence**), per family a verdict computed from
the strong rules (**inference**, labelled so), and the caveats that keep it an inference. The compiled game never
stores the DCC: names and transforms are clues, never proof.

Options: ``transforms=0`` (skip the Unity Transform scan), ``file`` (regex on Unity install relpaths for the scan),
``samples`` (names kept per rule, default 12).
"""

from __future__ import annotations

import collections
import os
import re
import struct
from pathlib import Path

from gst.analysis import ANY, Report, analysis, counted, md_table

STRONG, WEAK, AMBIGUOUS = "strong", "weak", "ambiguous"
ALL = ("mesh", "clip", "material", "texture", "gameobject", "node", "bone")
NODES = ("gameobject", "node", "bone")

# (family, rule id, regex, kinds, strength, what it means). Strong = specific to that tool; weak = a convention
# the tool favours but others share; ambiguous = shared with the engine itself (listed so the reader sees it).
RULES: list[tuple[str, str, str, tuple[str, ...], str, str]] = [
    # not a decimal size in the name ("Floor Museum 0.125x0.125", "Floor_1x0.125": 0.NNN after a separator or "<n>x")
    ("blender", "dup_suffix_.NNN", r"^(?!.*(?:^|[\s_(\-]|\d[xX])0\.\d{3}$).*\.\d{3}$", ALL, STRONG,
     "Blender duplicate suffix (Unity uses ' 1'/' (1)')"),
    ("blender", "blender_only_primitive", r"^(Icosphere|Torus|Cone|Suzanne|Circle|B[eé]zierCurve|B[eé]zierCircle|NurbsPath)([._]\d+)?$",
     ("mesh",) + NODES, STRONG, "Blender primitive the engine does not have"),
    ("blender", "shared_primitive", r"^(Cube|Plane|Cylinder|Sphere)(\.\d+)?$", ("mesh",) + NODES, AMBIGUOUS,
     "default primitive name of both Blender and Unity"),
    ("blender", "armature", r"^Armature([._]\d+)?$", NODES, STRONG, "Blender's default armature object"),
    ("blender", "armature_action_clip", r"^[^|]+\|[^|]+", ("clip",), STRONG, "'Armature|Action' take naming of Blender FBX"),
    ("blender", "mirror_.L/.R", r"\.(L|R|l|r)(\.\d{3})?$", NODES, STRONG, "Blender X-mirror bone naming"),
    ("blender", "leaf_bone_end", r"[._]end$", NODES, WEAK, "'Add Leaf Bones' of the Blender FBX exporter (UE-style rigs too)"),
    ("blender", "default_material_.NNN", r"^Material\.\d{3}$", ("material",), STRONG, "Blender default material name"),
    ("blender", "godot_dup_suffix_0NN", r"_0\d\d$", ("node", "bone", "mesh", "material", "clip"), AMBIGUOUS,
     "'.001' becomes '_001' after a Godot import (also a common manual suffix)"),
    ("rigify", "ORG-/DEF-/MCH-", r"^(ORG|DEF|MCH)-", NODES, STRONG, "Rigify bone layers"),
    ("rigify", "rigify_like_ORG_", r"^(ORG|DEF|MCH)_", NODES, WEAK, "Rigify-like prefixes with '_' (custom rig)"),
    ("mixamo", "mixamorig", r"^mixamorig\d*[:_]", NODES, STRONG, "Mixamo auto-rig skeleton"),
    ("mixamo", "mixamo_clip", r"mixamo\.com", ("clip",), STRONG, "Mixamo download take name"),
    ("maya", "maya_primitive", r"^(pCube|pSphere|pCylinder|pPlane|polySurface|pCone|pTorus|pPipe|pHelix)\d*", ("mesh",) + NODES,
     STRONG, "Maya polygon primitive / polySurface naming"),
    ("maya", "joint_N", r"^joint\d+$", NODES, STRONG, "Maya default joint name"),
    ("maya", "Jnt_suffix", r"(SHJnt|_jnt|Jnt)$", NODES, WEAK, "Maya-style joint suffix"),
    ("maya", "bind_prefix", r"^bind_", NODES, WEAK, "Maya bind-joint prefix"),
    ("3dsmax", "biped_Bip01", r"^Bip0?\d+([ _]|$)", NODES, STRONG, "3ds Max Biped skeleton"),
    ("3dsmax", "max_object_NNN", r"^(Box|Object|Line|Teapot|GeoSphere|Sphere|Cylinder|Plane|Cone|Torus|Tube|Pyramid|Dummy|Point|Bone)\d{3}$",
     ("mesh",) + NODES, STRONG, "3ds Max object naming (Box001)"),
    ("fbx_take", "Take_001", r"^Take ?\d+$", ("clip",), WEAK, "generic FBX take name (Maya/Max/MotionBuilder)"),
    ("synty", "synty_SM_SK", r"^(SM|SK)_(Env|Prop|Chr|Bld|Veh|Wep|Item|Generic|Icon|Primitive|FX)_", ("mesh", "material") + NODES,
     STRONG, "Synty POLYGON prefab/mesh naming"),
    ("synty", "synty_A_POLY", r"^A_POLY_", ("clip",), STRONG, "Synty POLYGON animation clips"),
    ("synty", "synty_Polygon", r"^Polygon[A-Z]\w*", ("material", "texture"), STRONG, "Synty POLYGON pack material/texture"),
    ("reallusion", "RL_CC_Base", r"^(RL_|CC_Base_|CC3_Base|CC4_)", NODES + ("mesh", "material"), STRONG,
     "Reallusion Character Creator / iClone"),
    ("kevin_iglesias", "ki_pack_at_clip", r"^(BasicMotions|DancePack|HumanBasicMotions|MeleeWarrior|HumanMeleeWarrior)@", ("clip",),
     STRONG, "Kevin Iglesias animation packs"),
    ("asset_store", "model_at_take", r"^[A-Za-z0-9_]+@\w", ("clip",), WEAK, "Unity 'model@take' FBX naming used by store packs"),
    ("asset_store", "fimpossible_FAnim", r"^FAnim - ", ("clip",), STRONG, "FImpossible Creations demo clips"),
    ("asset_store", "purchased_folder", r"(?i)purchased", ALL, STRONG, "'Purchased' in a name"),
    ("unreal_style", "ue_prefixes", r"^(SM|SK|T|M|MI|BP|ABP|A)_", ("mesh", "material", "texture", "clip") + NODES, WEAK,
     "Unreal-style asset prefixes (also Synty)"),
    ("unreal_style", "ue_mannequin_bones", r"^(pelvis|spine_0[1-5]|clavicle_[lr]|upperarm_[lr]|lowerarm_[lr]|thigh_[lr]|calf_[lr]|ball_[lr]|ik_foot_root|ik_hand_root)$",
     NODES, WEAK, "UE mannequin bone names"),
    ("substance", "substance_maps", r"_(BaseColor|Base_color|Base_Color|basecolor|OcclusionRoughnessMetallic|Roughness|Metallic|Normal|Height|Emissive|Mixed_AO)$",
     ("texture",), WEAK, "Substance Painter export map names"),
    ("unity", "unity_dup_(N)", r" \(\d+\)$", ("mesh",) + NODES, WEAK, "Unity editor duplicate suffix ' (1)'"),
    ("unity", "combined_mesh", r"(?i)combined mesh", ("mesh",) + NODES, STRONG, "Unity static batching / mesh combine"),
    ("ambiguous", "dup_space_or_underscore_N", r" \d+$|_\d+$", ("mesh", "clip") + NODES, AMBIGUOUS,
     "' 1' / '_1' suffix (Unity, Maya or 3ds Max)"),
]
_COMPILED = [(f, rid, re.compile(rx, re.I if rid in ("mixamorig", "mixamo_clip", "armature") else 0), kinds, s, what)
             for f, rid, rx, kinds, s, what in RULES]
UNITY_KINDS = {"Mesh": "mesh", "AnimationClip": "clip", "Material": "material", "Texture2D": "texture",
               "GameObject": "gameobject"}
R = 0.70710678
TRANSFORM = struct.Struct("<iq4f3f3f")


# --- names ------------------------------------------------------------------------------------------

class NameTable:
    """Name counters per kind (``kind -> Counter(name -> objects)``) and where they came from."""

    def __init__(self):
        self.names: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        self.sources: collections.Counter = collections.Counter()

    def add(self, kind: str, name: str, n: int = 1, source: str = "") -> None:
        if name:
            self.names[kind][name] += n
            if source:
                self.sources[source] += n

    def totals(self) -> dict:
        return {k: {"objects": sum(c.values()), "unique": len(c)} for k, c in sorted(self.names.items())}


def classify(table: NameTable, samples: int = 12) -> dict:
    """Evidence per rule: ``{rule id: {family, strength, what, kinds: {kind: {objects, unique, sample}}}}``."""
    out = {}
    for fam, rid, rx, kinds, strength, what in _COMPILED:
        per = {}
        for kind in kinds:
            counter = table.names.get(kind)
            if not counter:
                continue
            hits = [(n, c) for n, c in counter.items() if rx.search(n)]
            if hits:
                per[kind] = {"objects": sum(c for _, c in hits), "unique": len(hits),
                             "sample": sorted(n for n, _ in hits)[:samples],
                             "top": dict(sorted(hits, key=lambda nc: (-nc[1], nc[0]))[:samples])}
        out[rid] = {"family": fam, "strength": strength, "what": what, "kinds": per,
                    "objects": sum(v["objects"] for v in per.values()), "unique": sum(v["unique"] for v in per.values())}
    return out


def verdicts(rules: dict, extra: dict[str, dict] | None = None) -> dict:
    """Per family: strong/weak/ambiguous unique names and an inference label (present / weak / absent)."""
    fams: dict[str, dict] = {}
    for rid, r in rules.items():
        f = fams.setdefault(r["family"], {"strong": 0, "weak": 0, "ambiguous": 0, "rules": {}})
        f[r["strength"]] += r["unique"]
        if r["unique"]:
            f["rules"][rid] = r["unique"]
    for fam, ev in (extra or {}).items():
        f = fams.setdefault(fam, {"strong": 0, "weak": 0, "ambiguous": 0, "rules": {}})
        for k in (STRONG, WEAK, AMBIGUOUS):
            f[k] += ev.get(k, 0)
        f["rules"].update(ev.get("rules", {}))
    for fam, f in fams.items():
        f["verdict"] = "present" if f["strong"] else ("weak" if f["weak"] else ("ambiguous" if f["ambiguous"] else "absent"))
        f["kind"] = "inference"
    return dict(sorted(fams.items(), key=lambda kv: (-kv[1]["strong"], -kv[1]["weak"], kv[0])))


# --- sources ----------------------------------------------------------------------------------------

def unity_names(ctx, table: NameTable) -> None:
    for row in ctx.rows(types=tuple(UNITY_KINDS)):
        table.add(UNITY_KINDS[row["type"]], row.get("name") or "", source=f"unity:{row['type']}")


def transform_kind(raw: bytes) -> tuple[str, str, int] | None:
    """``(rotation class, scale class, GameObject path id)`` of a Transform's raw bytes."""
    if len(raw) < TRANSFORM.size:
        return None
    _fid, gid, qx, qy, qz, qw, _px, _py, _pz, sx, sy, sz = TRANSFORM.unpack_from(raw, 0)

    def near(a, b, e=1e-3):
        return abs(a - b) < e

    if near(qy, 0) and near(qz, 0) and near(qx, -R) and near(qw, R):
        rot = "rotX-90"
    elif near(qy, 0) and near(qz, 0) and near(qx, R) and near(qw, R):
        rot = "rotX+90"
    elif near(qx, 0) and near(qy, 0) and near(qz, 0) and near(abs(qw), 1):
        rot = "identity"
    else:
        rot = "other"
    if near(sx, 100, .01) and near(sy, 100, .01) and near(sz, 100, .01):
        sc = "scale100"
    elif near(sx, .01, 1e-4) and near(sy, .01, 1e-4) and near(sz, .01, 1e-4):
        sc = "scale0.01"
    elif near(sx, 1) and near(sy, 1) and near(sz, 1):
        sc = "scale1"
    else:
        sc = "scaleOther"
    return rot, sc, gid


class TransformTally:
    def __init__(self, keep: int = 200):
        self.n = 0
        self.counts: collections.Counter = collections.Counter()
        self.types: collections.Counter = collections.Counter()
        self.examples: dict[str, list[int]] = collections.defaultdict(list)
        self.keep = keep

    def add(self, raw: bytes, type_name: str = "Transform") -> None:
        k = transform_kind(raw)
        if k is None:
            return
        rot, sc, gid = k
        self.n += 1
        self.types[type_name] += 1
        self.counts[rot] += 1
        self.counts[sc] += 1
        if rot in ("rotX-90", "rotX+90") or sc in ("scale100", "scale0.01"):
            key = f"{rot}+{sc}"
            self.counts[key] += 1
            if len(self.examples[key]) < self.keep:
                self.examples[key].append(gid)


def unity_transforms(ctx, samples: int) -> dict:
    from gst.analysis.unity import UnityFile
    from gst.engines.unity import loader

    fpat = re.compile(ctx.opt("file"), re.I) if ctx.opt("file") else None
    out = {}
    for rel in ctx.inventory_files(("Transform", "RectTransform")):
        if fpat and not fpat.search(rel):
            continue
        ctx.log(f"transforms in {rel}")
        tally = TransformTally()
        with UnityFile(ctx, rel) as uf:
            for sf in uf.files():
                for obj in list(sf.objects.values()):
                    tn = loader.type_name(obj)
                    if tn in ("Transform", "RectTransform"):
                        try:
                            tally.add(obj.get_raw_data(), tn)
                        except Exception:
                            continue
        if not tally.n:
            continue
        wanted = {g for ids in tally.examples.values() for g in ids}
        names = {}
        if wanted:
            for row in ctx.rows(files=[rel], types=("GameObject",)):
                if row.get("path_id") in wanted:
                    names[row["path_id"]] = row.get("name") or ""
        out[rel] = {"transforms": tally.n, "by_type": counted(tally.types), "counts": counted(tally.counts),
                    "share": {k: round(tally.counts.get(k, 0) / tally.n, 4) for k in ("rotX-90", "rotX+90", "scale100", "scale0.01")},
                    "sample_names": {k: dict(list(counted(collections.Counter(names.get(g, "?") for g in ids)).items())[:samples])
                                     for k, ids in sorted(tally.examples.items())}}
    return out


def gltf_names(ctx, table: NameTable) -> dict:
    from gst.analysis.gltf_summary import find_files, load, names_of, node_signature

    root = ctx.workspace / "assets"
    gens: collections.Counter = collections.Counter()
    sig: collections.Counter = collections.Counter()
    files = 0
    for p in find_files(root) if root.is_dir() else []:
        try:
            j, _ = load(p, with_bin=False)
        except Exception:
            continue
        files += 1
        gens[str((j.get("asset") or {}).get("generator") or "?")] += 1
        for kind, names in names_of(j).items():
            for n in names:
                table.add(kind, n, source="gltf")
        for node in j.get("nodes", []):
            for s in node_signature(node):
                sig[s] += 1
    if files:
        ctx.use("gltf files", root)
    return {"files": files, "generators": counted(gens), "node_signatures": counted(sig)}


_NODE = re.compile(r'^\[node name="([^"]+)"')
_BONE = re.compile(r'^(?:bones/\d+/name|bone_name) = "([^"]+)"')
_SRC = re.compile(r'^source_file="([^"]+)"')


def godot_names(ctx, table: NameTable) -> dict:
    root = ctx.workspace / "assets" / "godot"
    out = {"scenes": 0, "imports": 0, "import_sources": {}}
    if not root.is_dir():
        return out
    srcs: collections.Counter = collections.Counter()
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            p = Path(dirpath) / f
            if f.endswith((".tscn", ".escn")):
                out["scenes"] += 1
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    m = _NODE.match(line)
                    if m:
                        table.add("node", m.group(1), source="godot:tscn")
                        continue
                    m = _BONE.match(line)
                    if m:
                        table.add("bone", m.group(1), source="godot:tscn")
            elif f.endswith(".import"):
                out["imports"] += 1
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    m = _SRC.match(line)
                    if m:
                        ext = os.path.splitext(m.group(1))[1].lower()
                        if ext in (".blend", ".fbx", ".glb", ".gltf", ".obj", ".dae", ".escn", ".max", ".ma", ".mb"):
                            srcs[ext] += 1
                        break
    out["import_sources"] = counted(srcs)
    if out["scenes"] or out["imports"]:
        ctx.use("godot project", root)
    return out


def extra_evidence(tr: dict, gltf: dict, godot: dict) -> dict[str, dict]:
    """Families from non-name evidence: Blender .blend import sources (strong), FBX transform signature (weak)."""
    extra: dict[str, dict] = {}
    srcs = godot.get("import_sources", {})
    if srcs.get(".blend"):
        extra["blender"] = {STRONG: srcs[".blend"], "rules": {"godot_import_source_.blend": srcs[".blend"]}}
    if srcs.get(".fbx"):
        extra.setdefault("fbx_import", {"rules": {}})
        extra["fbx_import"][WEAK] = srcs[".fbx"]
        extra["fbx_import"]["rules"]["godot_import_source_.fbx"] = srcs[".fbx"]
    neg90 = sum(f["counts"].get("rotX-90", 0) for f in tr.values()) + gltf.get("node_signatures", {}).get("rotX-90", 0)
    s100 = sum(f["counts"].get("scale100", 0) + f["counts"].get("scale0.01", 0) for f in tr.values()) + \
        gltf.get("node_signatures", {}).get("scale100", 0) + gltf.get("node_signatures", {}).get("scale0.01", 0)
    if neg90 or s100:
        e = extra.setdefault("fbx_import", {"rules": {}})
        e[WEAK] = e.get(WEAK, 0) + neg90 + s100
        e["rules"].update({"transform_rotX-90": neg90, "transform_scale100_or_0.01": s100})
    return extra


CAVEATS = [
    "A compiled game never stores the DCC: names and transforms are clues (evidence), the verdicts are inferences.",
    "Unity 6 'Bake Axis Conversion' and glTF exports remove the -90 X rotation, so its absence proves nothing.",
    "Cube/Plane/Cylinder/Sphere exist in Unity too; Icosphere/Torus/Cone and '.001' do not.",
]


@analysis("dcc", engines=(ANY,), uses=("unity.inventory", "unity.install"),
          description="DCC/origin of the 3D content (Blender, Maya, 3ds Max, Mixamo, Rigify, Synty, Reallusion, packs) "
                      "from names and transform signatures", version=1,
          options={"transforms": "0 = skip the Unity Transform scan", "file": "regex on Unity install relpaths (scan)",
                   "samples": "names kept per rule (default 12)"})
def dcc(ctx) -> Report:
    from gst.analysis import check_need

    samples = int(ctx.opt("samples", 12))
    table = NameTable()
    have_inv = check_need(ctx.workspace, ctx.game, "unity.inventory")[0]
    tr: dict = {}
    if have_inv:
        unity_names(ctx, table)
        if str(ctx.opt("transforms", "1")) not in ("0", "false", "no") and check_need(ctx.workspace, ctx.game, "unity.install")[0]:
            tr = unity_transforms(ctx, samples)
    gl = gltf_names(ctx, table)
    gd = godot_names(ctx, table)
    return build_report(table, tr, gl, gd, samples)


def build_report(table: NameTable, tr: dict, gl: dict, gd: dict, samples: int = 12) -> Report:
    rules = classify(table, samples)
    fams = verdicts(rules, extra_evidence(tr, gl, gd))
    notes = list(CAVEATS)
    if any("gdre" in g.lower() for g in gl.get("generators", {})):
        notes.append("glTF asset.generator is GDRE Tools (the recovery tool re-exported the scenes): not the author's DCC.")
    totals = table.totals()
    summary = {"names": {k: v["unique"] for k, v in totals.items()},
               "present": [f for f, v in fams.items() if v["verdict"] == "present"],
               "transforms": sum(f["transforms"] for f in tr.values()), "gltf_files": gl.get("files", 0),
               "godot_imports": gd.get("imports", 0)}
    data = {"totals": totals, "sources": counted(table.sources), "rules": rules, "families": fams,
            "transforms": tr, "gltf": gl, "godot": gd, "caveats": CAVEATS}
    md = ["Evidence = counts measured in the names/transforms below; verdict = inference from the strong rules.", ""]
    md += ["## Names read", "", md_table(["Kind", "Objects", "Unique names"],
                                         [(k, v["objects"], v["unique"]) for k, v in totals.items()]), ""]
    md += ["## Verdict per family (inference)", "",
           md_table(["Family", "Verdict", "Strong (unique names)", "Weak", "Ambiguous", "Rules hit"],
                    [(f, v["verdict"], v["strong"], v["weak"], v["ambiguous"],
                      ", ".join(f"{r} {n}" for r, n in v["rules"].items()) or "-") for f, v in fams.items()]), ""]
    md += ["## Evidence per rule", "",
           md_table(["Rule", "Family", "Strength", "Kind: objects/unique", "Sample"],
                    [(f"`{rid}`", r["family"], r["strength"],
                      ", ".join(f"{k} {v['objects']}/{v['unique']}" for k, v in r["kinds"].items()) or "0",
                      ", ".join(f"`{s}`" for s in sorted({s for v in r["kinds"].values() for s in v["sample"]})[:8]) or "-")
                     for rid, r in rules.items()]), ""]
    if tr:
        md += ["## Transform signature (Unity, raw localRotation/localScale)", "",
               md_table(["File", "Transforms", "rot -90 X", "rot +90 X", "scale 100", "scale 0.01", "identity rot",
                         "Top names (-90 X)"],
                        [(f"`{f}`", v["transforms"], v["counts"].get("rotX-90", 0), v["counts"].get("rotX+90", 0),
                          v["counts"].get("scale100", 0), v["counts"].get("scale0.01", 0), v["counts"].get("identity", 0),
                          ", ".join(f"{n} {c}" for n, c in list(v["sample_names"].get("rotX-90+scale1", {}).items())[:5]) or "-")
                         for f, v in tr.items()]), ""]
    if gl.get("files"):
        md += [f"## glTF ({gl['files']} files)", "", "Generators: " + ", ".join(f"`{g}` {n}" for g, n in gl["generators"].items())
               + "; node signatures: " + (", ".join(f"{k} {v}" for k, v in gl["node_signatures"].items()) or "none"), ""]
    if gd.get("scenes") or gd.get("imports"):
        md += [f"## Godot ({gd['scenes']} scenes, {gd['imports']} .import)", "",
               "Import source extensions: " + (", ".join(f"`{k}` {v}" for k, v in gd["import_sources"].items()) or "none"), ""]
    md += ["## Caveats", ""] + [f"- {c}" for c in notes] + [""]
    return Report(data=data, markdown="\n".join(md), summary=summary, notes=notes[len(CAVEATS):])
