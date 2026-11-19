"""Readers of the JSON that ``uas export Json`` (CUE4Parse) writes: one list of exports per package.

Stdlib only, plain dicts in and out, so the analyses (``unreal_values``, ``unreal_animation``) and their tests work on
synthetic uas-shaped JSON. Shapes followed (CUE4Parse 1.x through uas 0.5.0):

- every export: ``Type`` (class name), ``Name``, ``Flags``, ``Class``, ``Outer`` (a reference), ``Template``,
  ``Properties`` (tagged properties: only values that differ from the class/struct default are serialized);
- a reference: ``{"ObjectName": "Class'Name'", "ObjectPath": "<package>.<export index>"}`` (``/Script/<Module>`` for
  native objects); the index points into the same package's export list;
- soft references: ``{"AssetPathName": "/Game/...", "SubPathString": ""}``; texts: ``{"Namespace", "Key",
  "SourceString", "LocalizedString"}``; fixed-size arrays: ``Name``, ``Name[1]``, ``Name[2]``.

Asset readers return small dicts (never the raw property trees, except where noted) with every list sorted.
"""

from __future__ import annotations

import re
from collections import Counter

REF_RX = re.compile(r"^(\w+)'(.*)'$")
ENUM_RX = re.compile(r"^(?:[A-Za-z_]\w*::\w+|[A-Z]{2,6}_\w+)$")  # EFoo::Bar, or the old ECR_Block/ACM_Limited style
GEN_SUFFIX = "_GEN_VARIABLE"

# exports that are machinery of a Blueprint class, not components or values
BP_MACHINERY = {"Function", "SCS_Node", "SimpleConstructionScript", "InheritableComponentHandler", "TimelineTemplate",
                "ComponentDelegateBinding", "InputAxisKeyDelegateBinding", "InputKeyDelegateBinding",
                "InputActionDelegateBinding", "InputTouchDelegateBinding", "InputVectorAxisDelegateBinding",
                "WidgetTree", "WidgetAnimation", "MovieScene", "DelegateFunction", "SparseDelegateFunction"}


# --- references and values ------------------------------------------------------------------------------------

def is_ref(value) -> bool:
    return isinstance(value, dict) and "ObjectName" in value and set(value) <= {"ObjectName", "ObjectPath"}


def ref_parts(value: dict) -> tuple[str | None, str, str]:
    """(class, object name, package path) of a reference; the package of a native object is ``/Script/<Module>``."""
    name = str(value.get("ObjectName") or "")
    m = REF_RX.match(name)
    cls, obj = (m.group(1), m.group(2)) if m else (None, name)
    path = str(value.get("ObjectPath") or "")
    pkg = path if path.startswith("/Script/") else re.sub(r"\.\d+$", "", path)
    return cls, obj, pkg


def ref_index(value: dict) -> int | None:
    """Export index of an in-package reference (``<package>.<index>``)."""
    m = re.search(r"\.(\d+)$", str(value.get("ObjectPath") or ""))
    return int(m.group(1)) if m and not str(value.get("ObjectPath")).startswith("/Script/") else None


def ref_text(value) -> str | None:
    """A reference as an Unreal path string: ``Class'Package.Object'`` (``None`` for a null reference)."""
    if value is None:
        return None
    if isinstance(value, dict) and "AssetPathName" in value:  # soft object path
        path = str(value.get("AssetPathName") or "")
        sub = str(value.get("SubPathString") or "")
        return (path + (":" + sub if sub else "")) or None
    cls, obj, pkg = ref_parts(value)
    if not obj and not pkg:
        return None
    full = f"{pkg}.{obj}" if pkg and obj else (pkg or obj)
    return f"{cls}'{full}'" if cls else full


def ref_name(value) -> str | None:
    """Short object name of a reference (``Default__X_C:Health`` -> ``Health``)."""
    if not isinstance(value, dict):
        return None
    if "AssetPathName" in value:
        return str(value.get("AssetPathName") or "").rsplit(".", 1)[-1] or None
    _cls, obj, _pkg = ref_parts(value)
    return re.split(r"[:.]", obj)[-1] or None


def ref_class(value) -> str | None:
    return ref_parts(value)[0] if is_ref(value) else None


def is_text(value) -> bool:
    return isinstance(value, dict) and ("SourceString" in value or "LocalizedString" in value) and "Key" in value


def is_soft(value) -> bool:
    return isinstance(value, dict) and "AssetPathName" in value and set(value) <= {"AssetPathName", "SubPathString"}


def simplify(value):
    """Property tree -> plain JSON values: references and soft paths become ``Class'Package.Object'`` strings, texts
    their localized string; everything else keeps its shape."""
    if is_ref(value) or is_soft(value):
        return ref_text(value)
    if is_text(value):
        return value.get("LocalizedString") if value.get("LocalizedString") is not None else value.get("SourceString")
    if isinstance(value, dict):
        return {k: simplify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [simplify(v) for v in value]
    return value


def kind_of(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "enum" if ENUM_RX.match(value) else "name"
    return "other"


def leaves(value, prefix: str = ""):
    """Every leaf of a property tree as ``(path, value, kind)``; kinds: bool, int, float, enum, name, text, ref.
    Null references are skipped; lists are indexed (``Keys[0].Time``)."""
    if value is None:
        return
    if is_ref(value) or is_soft(value):
        text = ref_text(value)
        if text is not None:
            yield prefix or "value", text, "ref"
        return
    if is_text(value):
        yield prefix or "value", simplify(value), "text"
        return
    if isinstance(value, dict):
        for k, v in value.items():
            yield from leaves(v, f"{prefix}.{k}" if prefix else str(k))
        return
    if isinstance(value, list):
        for i, v in enumerate(value):
            yield from leaves(v, f"{prefix}[{i}]")
        return
    yield prefix or "value", value, kind_of(value)


VALUE_KINDS = ("bool", "int", "float", "enum")


def values_of(props: dict | None, prefix: str = "") -> dict:
    """Tunable leaves only (numbers, flags, enums) as ``{path: value}`` in property order."""
    return {path: v for path, v, kind in leaves(props or {}, prefix) if kind in VALUE_KINDS}


# --- package helpers ---------------------------------------------------------------------------------------------

def exports_of(data) -> list[dict]:
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def at(exports: list[dict], value) -> dict | None:
    """The export an in-package reference points to."""
    i = ref_index(value) if isinstance(value, dict) else None
    return exports[i] if i is not None and 0 <= i < len(exports) else None


def outer_name(e: dict) -> str | None:
    outer = e.get("Outer")
    return ref_parts(outer)[1] if isinstance(outer, dict) else None


def props(e: dict | None) -> dict:
    p = (e or {}).get("Properties")
    return p if isinstance(p, dict) else {}


def main_export(exports: list[dict], stem: str) -> dict | None:
    """The asset export of a package: the export named like the package (``X``), else its generated class (``X_C``)."""
    for name in (stem, f"{stem}_C"):
        for e in exports:
            if e.get("Name") == name and "Outer" not in e:
                return e
    return next((e for e in exports if "Outer" not in e), exports[0] if exports else None)


def fixed_array(p: dict, name: str) -> list:
    """``Name``, ``Name[1]``, ``Name[2]``... of a fixed-size array property, in index order."""
    out = []
    if name in p:
        out.append(p[name])
    i = 1
    while f"{name}[{i}]" in p:
        out.append(p[f"{name}[{i}]"])
        i += 1
    return out


def num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# --- Blueprints (class default object, components) -----------------------------------------------------------------

def generated_class(exports: list[dict]) -> dict | None:
    return next((e for e in exports if str(e.get("Type", "")).endswith("BlueprintGeneratedClass")), None)


def blueprint(exports: list[dict], package: str) -> dict | None:
    """Class, parent, components and default values of the Blueprint in ``exports`` (None when it has no generated class).

    ``defaults`` = the class default object's own properties (simplified), ``component_defaults`` = per component the
    properties its template sets, ``values`` = their tunable leaves as ``{"Prop": v, "Component.Prop": v}``. Cooked
    packages store only what differs from the parent (C++ constructor or parent Blueprint): an absent value is inherited.
    """
    gen = generated_class(exports)
    if gen is None:
        return None
    sup = gen.get("SuperStruct") or gen.get("Super") or {}
    parent_cls, parent, parent_pkg = ref_parts(sup) if isinstance(sup, dict) else (None, "", "")
    cdo_ref = gen.get("ClassDefaultObject")
    cdo = at(exports, cdo_ref) if isinstance(cdo_ref, dict) else None
    if cdo is None:
        cdo = next((e for e in exports if str(e.get("Name", "")).startswith("Default__") and "Outer" not in e), None)
    cdo_name = (cdo or {}).get("Name")
    gen_name = gen.get("Name")
    components: list[dict] = []
    comp_values: dict[str, dict] = {}

    def add(name: str, cls: str | None, origin: str, template: dict | None, **extra):
        components.append({"name": name, "class": cls, "origin": origin, **extra})
        p = props(template)
        if p:
            comp_values[name] = simplify(p)

    # native components (default subobjects of the CDO) that this Blueprint configures
    for e in exports:
        if cdo_name and outer_name(e) == cdo_name and e.get("Type") not in BP_MACHINERY:
            attach = ref_name(props(e).get("AttachParent"))
            add(str(e.get("Name")), e.get("Type"), "native", e, attach=attach)
    # components added in this Blueprint (SimpleConstructionScript nodes)
    parent_of: dict[int, str] = {}
    nodes = [(i, e) for i, e in enumerate(exports) if e.get("Type") == "SCS_Node"]
    names = {i: str(props(e).get("InternalVariableName") or e.get("Name")) for i, e in nodes}
    for i, e in nodes:
        for child in props(e).get("ChildNodes") or []:
            j = ref_index(child) if isinstance(child, dict) else None
            if j is not None:
                parent_of[j] = names[i]
    for i, e in nodes:
        p = props(e)
        template = at(exports, p.get("ComponentTemplate")) if isinstance(p.get("ComponentTemplate"), dict) else None
        cls = ref_parts(p["ComponentClass"])[1] if isinstance(p.get("ComponentClass"), dict) else (template or {}).get("Type")
        attach = parent_of.get(i) or (str(p["ParentComponentOrVariableName"]) if p.get("ParentComponentOrVariableName") else None)
        add(names[i], cls, "scs", template, attach=attach)
    # inherited components whose template this Blueprint overrides
    for e in exports:
        if e.get("Type") != "InheritableComponentHandler":
            continue
        for rec in props(e).get("Records") or []:
            if not isinstance(rec, dict):
                continue
            key = rec.get("ComponentKey") or {}
            template = at(exports, rec.get("ComponentTemplate")) if isinstance(rec.get("ComponentTemplate"), dict) else None
            name = str(key.get("SCSVariableName") or ref_name(rec.get("ComponentTemplate")) or "").removesuffix(GEN_SUFFIX)
            cls = ref_parts(rec["ComponentClass"])[1] if isinstance(rec.get("ComponentClass"), dict) else (template or {}).get("Type")
            add(name, cls, "inherited", template, owner=ref_name(key.get("OwnerClass")))
    curves = []
    for e in exports:
        if str(e.get("Type", "")).startswith("Curve") and outer_name(e) == gen_name:
            curves.append({"name": e.get("Name"), "class": e.get("Type"), "keys": curve_keys(props(e))})
    timelines = []
    for e in exports:
        if e.get("Type") == "TimelineTemplate":
            p = props(e)
            timelines.append({"name": str(e.get("Name") or "").removesuffix("_Template"), "length": num(p.get("TimelineLength")),
                              "loop": bool(p.get("bLoop")), "tracks": sum(len(p.get(k) or []) for k in
                                                                         ("FloatTracks", "VectorTracks", "LinearColorTracks", "EventTracks"))})
    defaults = simplify(props(cdo))
    defaults.pop("UberGraphFrame", None)
    # an AnimBlueprint's CDO also holds every anim graph node (read by anim_blueprint): values = its variables only
    values = values_of({k: v for k, v in props(cdo).items() if not k.startswith("AnimGraphNode_")})
    for name, p in comp_values.items():
        values.update(values_of(p, name))
    funcs = sorted(str(e.get("Name")) for e in exports if e.get("Type") == "Function" and e.get("Name"))
    variables = sorted(str(p.get("Name")) for p in (gen.get("ChildProperties") or []) if isinstance(p, dict) and p.get("Name")
                       and not str(p.get("Name")).startswith(("K2Node_", "UberGraphFrame", "Temp_", "CallFunc_")))
    return {"package": package, "class": gen_name, "type": gen.get("Type"), "parent": parent or None,
            "parent_class": parent_cls, "parent_path": parent_pkg or None, "native_parent": parent_pkg.startswith("/Script/"),
            "cdo": cdo_name, "components": components, "functions": funcs, "variables": variables,
            "defaults": defaults, "component_defaults": comp_values, "values": values,
            "curves": curves, "timelines": timelines}


# --- data ------------------------------------------------------------------------------------------------------

def curve_keys(p: dict) -> list[list]:
    """``[[time, value], ...]`` of a rich/simple curve (``FloatCurve`` or a curve dict with ``Keys``)."""
    curve = p.get("FloatCurve") if isinstance(p.get("FloatCurve"), dict) else p
    out = []
    for k in (curve or {}).get("Keys") or []:
        if isinstance(k, dict):
            out.append([num(k.get("Time")) or 0.0, num(k.get("Value")) if num(k.get("Value")) is not None else 0.0])
    return out


def datatable(e: dict) -> dict:
    rows = e.get("Rows") if isinstance(e.get("Rows"), dict) else {}
    struct = props(e).get("RowStruct")
    return {"row_struct": ref_text(struct) if isinstance(struct, dict) else None,
            "rows": {name: simplify(row) for name, row in rows.items()}}


def curvetable(e: dict) -> dict:
    rows = e.get("Rows") if isinstance(e.get("Rows"), dict) else {}
    return {"mode": e.get("CurveTableMode"), "rows": {name: curve_keys(row if isinstance(row, dict) else {})
                                                       for name, row in rows.items()}}


def columns(rows: dict) -> dict:
    """Numeric columns of table rows: ``{column: {min, max, rows}}``."""
    cols: dict[str, list] = {}
    for row in rows.values():
        for path, v in values_of(row if isinstance(row, dict) else {"value": row}).items():
            if not isinstance(v, bool) and isinstance(v, (int, float)):
                cols.setdefault(path, []).append(v)
    return {k: {"min": min(v), "max": max(v), "rows": len(v)} for k, v in sorted(cols.items())}


# --- skeleton, meshes, animation --------------------------------------------------------------------------------

def ref_skeleton(raw: dict | None) -> list[dict]:
    """``[{name, parent}]`` of a ReferenceSkeleton (``FinalRefBoneInfo``, else ``RawRefBoneInfo``)."""
    raw = raw or {}
    info = raw.get("FinalRefBoneInfo") or raw.get("RawRefBoneInfo") or []
    return [{"name": b.get("Name"), "parent": b.get("ParentIndex")} for b in info if isinstance(b, dict)]


def hierarchy(bones: list[dict]) -> dict:
    """Root, depth and leaf count of a bone list (parents by index, -1 = root)."""
    depth = [0] * len(bones)
    children = Counter()
    for i, b in enumerate(bones):
        p = b.get("parent")
        if isinstance(p, int) and 0 <= p < i:
            depth[i] = depth[p] + 1
            children[p] += 1
    roots = [b["name"] for b in bones if not (isinstance(b.get("parent"), int) and b["parent"] >= 0)]
    return {"root": roots[0] if roots else None, "roots": len(roots), "depth": max(depth) + 1 if depth else 0,
            "leaves": sum(1 for i in range(len(bones)) if not children[i])}


def skeleton(exports: list[dict], e: dict) -> dict:
    p = props(e)
    bones = ref_skeleton(e.get("ReferenceSkeleton"))
    sockets = []
    for ref in p.get("Sockets") or []:
        s = at(exports, ref) if isinstance(ref, dict) else None
        sp = props(s)
        if sp:
            sockets.append({"name": sp.get("SocketName"), "bone": sp.get("BoneName"),
                            "location": sp.get("RelativeLocation"), "rotation": sp.get("RelativeRotation")})
    virtual = [{"name": v.get("VirtualBoneName"), "source": v.get("SourceBoneName"), "target": v.get("TargetBoneName")}
               for v in p.get("VirtualBones") or [] if isinstance(v, dict)]
    curves = (((e.get("NameMappings") or {}).get("AnimationCurves") or {}).get("CurveMetaDataMap")) or {}
    slots = [{"group": g.get("GroupName"), "slots": g.get("SlotNames") or []} for g in p.get("SlotGroups") or []
             if isinstance(g, dict)]
    return {"name": e.get("Name"), "bones": len(bones), **hierarchy(bones), "sockets": sorted(sockets, key=lambda s: str(s["name"])),
            "virtual_bones": virtual, "slot_groups": slots, "curves": sorted(curves) if isinstance(curves, dict) else [],
            "guid": e.get("Guid"), "bone_list": bones}


def skeletal_mesh(e: dict) -> dict:
    p = props(e)
    lods = []
    for lod in e.get("LODModels") or []:
        if not isinstance(lod, dict):
            continue
        sections = [s for s in lod.get("Sections") or [] if isinstance(s, dict)]
        lods.append({"sections": len(sections), "verts": lod.get("NumVertices"),
                     "tris": sum(num(s.get("NumTriangles")) or 0 for s in sections),
                     "max_influences": max((num(s.get("MaxBoneInfluences")) or 0 for s in sections), default=None)})
    mats = [{"slot": m.get("MaterialSlotName"), "material": ref_text(m.get("Material")) if isinstance(m.get("Material"), dict) else None}
            for m in e.get("SkeletalMaterials") or [] if isinstance(m, dict)]
    bones = ref_skeleton(e.get("ReferenceSkeleton"))
    morphs = [ref_name(m) for m in p.get("MorphTargets") or [] if isinstance(m, dict)]
    return {"name": e.get("Name"), "skeleton": ref_text(p.get("Skeleton")) if isinstance(p.get("Skeleton"), dict) else None,
            "physics_asset": ref_text(p.get("PhysicsAsset")) if isinstance(p.get("PhysicsAsset"), dict) else None,
            "materials": mats, "lod_info": len(p.get("LODInfo") or []), "lods": lods,
            "bones": len(bones) or None, "root": hierarchy(bones)["root"] if bones else None,
            "morph_targets": morphs, "bounds_radius": num((e.get("ImportedBounds") or {}).get("SphereRadius"))}


def _notifies(exports: list[dict], p: dict) -> list[dict]:
    out = []
    for n in p.get("Notifies") or []:
        if not isinstance(n, dict):
            continue
        obj = n.get("Notify") if isinstance(n.get("Notify"), dict) else n.get("NotifyStateClass")
        target = at(exports, obj) if isinstance(obj, dict) else None
        link = n.get("LinkValue")
        time = num(link) if link is not None else num(n.get("DisplayTime_DEPRECATED"))
        out.append({"name": n.get("NotifyName") if n.get("NotifyName") not in (None, "None") else (ref_name(obj) or None),
                    "class": (target or {}).get("Type") or (ref_class(obj) if isinstance(obj, dict) else None),
                    "time": time, "duration": num(n.get("Duration")) or 0.0, "track": n.get("TrackIndex")})
    return out


def anim_sequence(exports: list[dict], e: dict) -> dict:
    p = props(e)
    length = num(p.get("SequenceLength"))
    rate = p.get("SamplingFrameRate") if isinstance(p.get("SamplingFrameRate"), dict) else None
    keys = num(p.get("NumberOfSampledKeys")) or num(p.get("NumFrames"))  # UE5 / UE4: sampled keys (frames + 1)
    if rate and num(rate.get("Numerator")) and num(rate.get("Denominator")):
        fps = rate["Numerator"] / rate["Denominator"]
    elif length and keys and keys > 1:
        fps = (keys - 1) / length  # UE4: NumFrames keys over SequenceLength seconds
    else:
        fps = None
    tracks = e.get("CompressedTrackToSkeletonMapTable") or p.get("TrackToSkeletonMapTable") or []
    return {"name": e.get("Name"), "skeleton": ref_text(p.get("Skeleton")) if isinstance(p.get("Skeleton"), dict) else None,
            "length": length, "keys": keys, "fps": round(fps, 3) if fps else None, "tracks": len(tracks),
            "notifies": _notifies(exports, p), "root_motion": bool(p.get("bEnableRootMotion")),
            "additive": p.get("AdditiveAnimType") if p.get("AdditiveAnimType") not in (None, "AAT_None") else None,
            "rate_scale": num(p.get("RateScale")) if "RateScale" in p else 1.0,
            "compression": (e.get("CompressedDataStructure") or {}).get("KeyEncodingFormat")
            if isinstance(e.get("CompressedDataStructure"), dict) else None}


def anim_montage(exports: list[dict], e: dict) -> dict:
    p = props(e)
    slots = []
    for t in p.get("SlotAnimTracks") or []:
        if not isinstance(t, dict):
            continue
        segs = [{"anim": ref_text(s.get("AnimReference")) if isinstance(s.get("AnimReference"), dict) else None,
                 "start": num(s.get("AnimStartTime")) or 0.0, "end": num(s.get("AnimEndTime")),
                 "rate": num(s.get("AnimPlayRate")) if "AnimPlayRate" in s else 1.0, "loops": num(s.get("LoopingCount")) or 1}
                for s in ((t.get("AnimTrack") or {}).get("AnimSegments") or []) if isinstance(s, dict)]
        slots.append({"slot": t.get("SlotName"), "segments": segs})
    sections = [{"name": s.get("SectionName"), "next": s.get("NextSectionName"),
                 "start": num(s.get("SegmentBeginTime")) or num(s.get("LinkValue")) or 0.0}
                for s in p.get("CompositeSections") or [] if isinstance(s, dict)]

    defaulted = []

    def blend(key: str):  # UAnimMontage constructor: BlendIn/BlendOut 0.25 s, BlendOutTriggerTime -1 (= blend out at the end)
        b = p.get(key)
        if isinstance(b, dict) and "BlendTime" in b:
            return num(b.get("BlendTime"))
        defaulted.append(key)
        return 0.25

    return {"name": e.get("Name"), "skeleton": ref_text(p.get("Skeleton")) if isinstance(p.get("Skeleton"), dict) else None,
            "length": num(p.get("SequenceLength")), "sections": sections, "slots": slots, "notifies": _notifies(exports, p),
            "blend_in": blend("BlendIn"), "blend_out": blend("BlendOut"),
            "blend_out_trigger": num(p.get("BlendOutTriggerTime")) if "BlendOutTriggerTime" in p else -1.0,
            "auto_blend_out": p.get("bEnableAutoBlendOut", True), "defaulted": defaulted}


def blend_space(e: dict) -> dict:
    p = props(e)
    axes = [{"name": a.get("DisplayName"), "min": num(a.get("Min")), "max": num(a.get("Max")), "grid": num(a.get("GridNum"))}
            for a in fixed_array(p, "BlendParameters") if isinstance(a, dict)]
    samples = []
    for s in p.get("SampleData") or []:
        if not isinstance(s, dict):
            continue
        v = s.get("SampleValue") or {}
        samples.append({"anim": ref_text(s.get("Animation")) if isinstance(s.get("Animation"), dict) else None,
                        "x": num(v.get("X")), "y": num(v.get("Y")), "rate": num(s.get("RateScale")) if "RateScale" in s else 1.0})
    interp = [num((i or {}).get("InterpolationTime")) for i in fixed_array(p, "InterpolationParam") if isinstance(i, dict)]
    return {"name": e.get("Name"), "class": e.get("Type"),
            "skeleton": ref_text(p.get("Skeleton")) if isinstance(p.get("Skeleton"), dict) else None,
            "axes": axes, "samples": samples, "interpolation": interp}


ANIM_ASSET_CLASSES = ("AnimSequence", "AnimMontage", "BlendSpace", "BlendSpace1D", "AimOffsetBlendSpace",
                      "AimOffsetBlendSpace1D", "AnimComposite", "PoseAsset")


def anim_blueprint(exports: list[dict], package: str) -> dict | None:
    gen = next((e for e in exports if e.get("Type") == "AnimBlueprintGeneratedClass"), None)
    if gen is None:
        return None
    gp = props(gen)
    sup = gen.get("SuperStruct") or {}
    machines = []
    for m in gp.get("BakedStateMachines") or []:
        if not isinstance(m, dict):
            continue
        states = [s for s in m.get("States") or [] if isinstance(s, dict)]
        names = [s.get("StateName") for s in states]

        def state(i):
            return names[i] if isinstance(i, int) and 0 <= i < len(names) else i

        trans = [{"from": state(t.get("PreviousState")), "to": state(t.get("NextState")),
                  "crossfade": num(t.get("CrossfadeDuration")), "blend": t.get("BlendMode"), "logic": t.get("LogicType")}
                 for t in m.get("Transitions") or [] if isinstance(t, dict)]
        machines.append({"name": m.get("MachineName"), "initial": state(m.get("InitialState")), "states": names,
                         "conduits": [s.get("StateName") for s in states if s.get("bIsAConduit")], "transitions": trans})
    cdo = next((e for e in exports if str(e.get("Name", "")).startswith("Default__") and "Outer" not in e), None)
    nodes: Counter = Counter()
    assets: set[str] = set()
    for key, value in props(cdo).items():
        if key.startswith("AnimGraphNode_"):
            nodes[re.sub(r"_\d+$", "", key)[len("AnimGraphNode_"):]] += 1
            for _path, v, kind in leaves(value):
                if kind == "ref" and v.split("'", 1)[0] in ANIM_ASSET_CLASSES:
                    assets.add(v)
    variables = values_of({k: v for k, v in props(cdo).items() if not k.startswith("AnimGraphNode_")})
    return {"package": package, "class": gen.get("Name"), "parent": ref_name(sup) if isinstance(sup, dict) else None,
            "parent_path": ref_parts(sup)[2] if isinstance(sup, dict) else None,
            "skeleton": ref_text(gp.get("TargetSkeleton")) if isinstance(gp.get("TargetSkeleton"), dict) else None,
            "machines": machines, "nodes": dict(sorted(nodes.items())), "assets": sorted(assets),
            "sync_groups": gp.get("SyncGroupNames") or [], "values": variables}


# --- physics ------------------------------------------------------------------------------------------------------

SHAPES = {"SphereElems": "sphere", "BoxElems": "box", "SphylElems": "capsule", "ConvexElems": "convex",
          "TaperedCapsuleElems": "tapered-capsule"}
# FConeConstraint / FTwistConstraint / FLinearConstraint constructor defaults (UE 4.x-5.x): cooked packages omit them
CONSTRAINT_DEFAULTS = {"Swing1Motion": "ACM_Free", "Swing2Motion": "ACM_Free", "TwistMotion": "ACM_Free",
                       "Swing1LimitDegrees": 45.0, "Swing2LimitDegrees": 45.0, "TwistLimitDegrees": 45.0,
                       "XMotion": "ACM_Locked", "YMotion": "ACM_Locked", "ZMotion": "ACM_Locked", "Limit": 0.0}


def _motion(m) -> str:
    return re.sub(r"^(?:ACM|LCM)_", "", str(m or "")).lower()


def joint_type(s1: str, s2: str, tw: str) -> str:
    """A name for the angular setup: fixed (all locked), hinge (one axis moves), ball (free), cone-twist (limited)."""
    moving = [m for m in (s1, s2, tw) if m != "locked"]
    if not moving:
        return "fixed"
    if len(moving) == 1:
        return "hinge"
    if all(m == "free" for m in (s1, s2, tw)):
        return "ball"
    if all(m == "limited" for m in (s1, s2, tw)):
        return "cone-twist"
    return "mixed"


def constraint(e: dict) -> dict:
    inst = props(e).get("DefaultInstance") or {}
    prof = inst.get("ProfileInstance") or {}
    cone, twist, lin = prof.get("ConeLimit") or {}, prof.get("TwistLimit") or {}, prof.get("LinearLimit") or {}
    defaulted = []

    def get(src: dict, key: str):
        if key in src:
            return src[key]
        defaulted.append(key)
        return CONSTRAINT_DEFAULTS[key]

    s1, s2 = _motion(get(cone, "Swing1Motion")), _motion(get(cone, "Swing2Motion"))
    tw = _motion(get(twist, "TwistMotion"))
    out = {"name": inst.get("JointName") or e.get("Name"), "bone1": inst.get("ConstraintBone1"), "bone2": inst.get("ConstraintBone2"),
           "type": joint_type(s1, s2, tw),
           "swing1": {"motion": s1, "limit": get(cone, "Swing1LimitDegrees")},
           "swing2": {"motion": s2, "limit": get(cone, "Swing2LimitDegrees")},
           "twist": {"motion": tw, "limit": get(twist, "TwistLimitDegrees")},
           "linear": {"x": _motion(get(lin, "XMotion")), "y": _motion(get(lin, "YMotion")), "z": _motion(get(lin, "ZMotion")),
                      "limit": get(lin, "Limit")}}
    drives = {k: simplify(v) for k, v in prof.items() if k in ("AngularDrive", "LinearDrive")}
    extra = {k: simplify(v) for k, v in prof.items() if k not in ("ConeLimit", "TwistLimit", "LinearLimit", "AngularDrive", "LinearDrive")}
    if drives:
        out["drives"] = drives
    if extra:
        out["profile"] = extra
    for key in ("bDisableCollision", "bEnableProjection"):
        if key in inst:
            out[key] = inst[key]
    out["defaulted"] = defaulted
    return out


def body(e: dict) -> dict:
    p = props(e)
    geom = p.get("AggGeom") or {}
    shapes = {SHAPES[k]: len(v) for k, v in geom.items() if k in SHAPES and isinstance(v, list) and v}
    inst = p.get("DefaultInstance") or {}
    mass = num(inst.get("MassInKgOverride")) if inst.get("bOverrideMass") else None
    out = {"bone": p.get("BoneName"), "shapes": shapes, "mass": mass,
           "physics_type": p.get("PhysicsType"), "collision": p.get("CollisionTraceFlag"),
           "material": ref_text(p.get("PhysMaterial")) if isinstance(p.get("PhysMaterial"), dict) else None}
    for key in ("bSimulatePhysics", "LinearDamping", "AngularDamping"):
        if key in inst:
            out[key] = inst[key]
    return out


def physics_asset(exports: list[dict], e: dict) -> dict:
    p = props(e)
    bodies = [body(b) for b in (at(exports, r) for r in p.get("SkeletalBodySetups") or [] if isinstance(r, dict)) if b]
    cons = [constraint(c) for c in (at(exports, r) for r in p.get("ConstraintSetup") or [] if isinstance(r, dict)) if c]
    if not bodies:  # older layouts: every SkeletalBodySetup export of the package
        bodies = [body(b) for b in exports if b.get("Type") in ("SkeletalBodySetup", "BodySetup")]
    if not cons:
        cons = [constraint(c) for c in exports if c.get("Type") == "PhysicsConstraintTemplate"]
    shapes: Counter = Counter()
    for b in bodies:
        shapes.update(b["shapes"])
    masses = [b["mass"] for b in bodies if b["mass"] is not None]
    return {"name": e.get("Name"), "bodies": bodies, "constraints": cons, "shape_counts": dict(sorted(shapes.items())),
            "joint_types": dict(sorted(Counter(c["type"] for c in cons).items())),
            "mass_override_total": round(sum(masses), 3) if masses else None, "mass_overrides": len(masses),
            "preview_mesh": ref_text(p.get("PreviewSkeletalMesh")) if isinstance(p.get("PreviewSkeletalMesh"), dict) else None}
