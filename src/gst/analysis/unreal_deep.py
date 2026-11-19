"""Unreal analyses over the uas JSON exports: the tunable values and the skeletal/animation/physics assets.

- ``unreal_values``: per Blueprint its parent class, components (native default subobjects it configures, components
  added by its construction script, inherited components it overrides) and the default values of its class default
  object and component templates (numbers, flags, enums; references as ``Class'Package.Object'`` paths); DataTables
  as rows, CurveTables and curve assets as keys, data assets (any other asset object with properties) as values.
- ``unreal_animation``: Skeleton (bones, hierarchy, sockets, virtual bones, slots), SkeletalMesh (LODs, materials,
  morph targets, skeleton, PhysicsAsset), AnimSequence (length, frames, rate, notifies), AnimMontage (sections, slots,
  blends), BlendSpace (axes, samples), AnimBlueprint (baked state machines, node types, referenced animations) and
  PhysicsAsset = ragdoll (bodies per bone with shape types, constraints with their angular/linear limits).

Both read every ``assets/unreal/**.json`` uas wrote (each package once), one file at a time. uas skips SkeletalMesh
and AnimMontage in JSON mode by default: ``gst assets extract --types json --all-types`` exports them too.
Cooked packages store only values that differ from the parent class (C++ constructor or parent Blueprint), so an
absent value is inherited; constraint limits absent from the JSON take the engine's struct defaults (listed per
constraint in ``defaulted``).
"""

from __future__ import annotations

import collections
from pathlib import Path

from gst.analysis import Report, analysis, counted, md_table

SKIP_DATA_TYPES = {  # main exports read elsewhere, or with no tunable payload
    "BlueprintGeneratedClass", "WidgetBlueprintGeneratedClass", "AnimBlueprintGeneratedClass", "DataTable", "CurveTable",
    "Skeleton", "SkeletalMesh", "StaticMesh", "AnimSequence", "AnimMontage", "AnimComposite", "BlendSpace", "BlendSpace1D",
    "AimOffsetBlendSpace", "AimOffsetBlendSpace1D", "PoseAsset", "PhysicsAsset", "Material", "MaterialInstanceConstant",
    "MaterialFunction", "MaterialParameterCollection", "Texture2D", "TextureCube", "TextureRenderTarget2D", "SoundWave",
    "SoundCue", "SoundAttenuation", "SoundClass", "SoundMix", "World", "Level", "Font", "FontFace", "BehaviorTree",
    "BlackboardData", "UserDefinedStruct", "UserDefinedEnum", "ParticleSystem", "NiagaraSystem", "NiagaraEmitter",
    "Function", "StringTable",
}


def _rel(ctx, path: Path) -> str:
    return path.relative_to(ctx.workspace).as_posix()


def _side(package: str) -> list[str]:
    parts = package.split("/")
    return [*parts[:-1], parts[-1] + ".json"]


def _packages(ctx):
    from gst.analysis.unreal import iter_exported_json
    from gst.engines.unreal import uasjson

    for key, path, data in iter_exported_json(ctx.workspace):
        exports = uasjson.exports_of(data)
        if exports:
            yield key, path, exports


@analysis("unreal_values", engines=("unreal",), needs=("unreal.json",), version=1, category="data",
          description="Unreal default values: Blueprint class defaults and components, DataTable rows, curves, data assets",
          options={"name": "regex on the package path (default: every exported package)"})
def unreal_values(ctx) -> Report:
    import re

    from gst.engines.unreal import uasjson as uj

    rx = re.compile(ctx.opt("name"), re.I) if ctx.opt("name") else None
    blueprints, tables, curve_tables, curves, data_assets = [], [], [], [], []
    files = 0
    for key, path, exports in _packages(ctx):
        if rx and not rx.search(key):
            continue
        files += 1
        stem = key.rsplit("/", 1)[-1]
        file = _rel(ctx, path)
        bp = uj.blueprint(exports, key)
        if bp is not None:
            detail = ctx.write_side_json({**bp, "file": file}, "blueprints", *_side(key))
            blueprints.append({k: bp[k] for k in ("package", "class", "type", "parent", "parent_class", "parent_path",
                                                   "native_parent", "cdo", "components", "values")}
                              | {"file": file, "curves": len(bp["curves"]), "timelines": bp["timelines"],
                                 "functions": len(bp["functions"]), "variables": len(bp["variables"]),
                                 "detail": _rel(ctx, detail)})
            for c in bp["curves"]:
                curves.append({"package": key, "file": file, "owner": bp["class"], "name": c["name"], "class": c["class"],
                               "keys": c["keys"]})
            continue
        e = uj.main_export(exports, stem)
        if e is None:
            continue
        t = str(e.get("Type"))
        if t == "DataTable":
            dt = uj.datatable(e)
            detail = ctx.write_side_json({"package": key, "file": file, **dt}, "datatables", *_side(key))
            tables.append({"package": key, "file": file, "row_struct": dt["row_struct"], "rows": len(dt["rows"]),
                           "columns": uj.columns(dt["rows"]), "detail": _rel(ctx, detail)})
        elif t == "CurveTable":
            ct = uj.curvetable(e)
            curve_tables.append({"package": key, "file": file, "mode": ct["mode"], "rows": ct["rows"]})
        elif t.startswith("Curve") and ("FloatCurve" in uj.props(e) or "FloatCurves" in uj.props(e)):
            p = uj.props(e)
            keys = uj.curve_keys(p) if "FloatCurve" in p else [uj.curve_keys(c) for c in p.get("FloatCurves") or []
                                                               if isinstance(c, dict)]
            curves.append({"package": key, "file": file, "owner": None, "name": e.get("Name"), "class": t, "keys": keys})
        elif t not in SKIP_DATA_TYPES and uj.props(e):
            values = uj.values_of(uj.props(e))
            detail = ctx.write_side_json({"package": key, "file": file, "class": t, "name": e.get("Name"),
                                          "properties": uj.simplify(uj.props(e))}, "data_assets", *_side(key))
            data_assets.append({"package": key, "file": file, "class": t, "name": e.get("Name"), "values": values,
                                "detail": _rel(ctx, detail)})
    for lst in (blueprints, tables, curve_tables, curves, data_assets):
        lst.sort(key=lambda x: (x["package"], str(x.get("name") or "")))
    parents = counted(collections.Counter(b["parent"] for b in blueprints if b["parent"]))
    summary = {"packages_read": files, "blueprints": len(blueprints),
               "blueprint_values": sum(len(b["values"]) for b in blueprints),
               "components": sum(len(b["components"]) for b in blueprints), "datatables": len(tables),
               "datatable_rows": sum(t["rows"] for t in tables), "curve_tables": len(curve_tables), "curves": len(curves),
               "data_assets": len(data_assets)}
    md = [f"{files} exported packages read; values are what each asset sets (cooked packages omit what equals the "
          "parent class default)."]
    for b in blueprints:
        md += ["", f"## {b['class']} (`{b['package']}`)", "",
               f"Parent `{b['parent']}` ({b['parent_path']}{', native' if b['native_parent'] else ''}); "
               f"{len(b['components'])} components, {len(b['values'])} values, {b['functions']} functions; "
               f"full defaults in `{b['detail']}`.", ""]
        if b["components"]:
            md += [md_table(["Component", "Class", "Origin", "Attach"],
                            [(c["name"], c["class"], c["origin"], c.get("attach") or c.get("owner")) for c in b["components"]]), ""]
        if b["values"]:
            md += [md_table(["Value", "Default"], b["values"].items()), ""]
    if tables:
        md += ["", "## DataTables", ""]
        for t in tables:
            md += [f"### {t['package']}", "", f"Row struct `{t['row_struct']}`, {t['rows']} rows (rows in `{t['detail']}`).", "",
                   md_table(["Column", "Min", "Max", "Rows"], [(k, v["min"], v["max"], v["rows"]) for k, v in t["columns"].items()]), ""]
    if curve_tables:
        md += ["", "## CurveTables", "", md_table(["Table", "Rows", "Keys"], [(c["package"], len(c["rows"]),
                                                    sum(len(k) for k in c["rows"].values())) for c in curve_tables])]
    if curves:
        md += ["", "## Curves", "", md_table(["Package", "Owner", "Curve", "Class", "Keys"],
                                             [(c["package"], c["owner"], c["name"], c["class"], len(c["keys"])) for c in curves])]
    if data_assets:
        md += ["", "## Data assets", "", md_table(["Package", "Class", "Values"],
                                                  [(d["package"], d["class"], len(d["values"])) for d in data_assets])]
    data = {"blueprints": blueprints, "blueprint_parents": parents, "datatables": tables, "curve_tables": curve_tables,
            "curves": curves, "data_assets": data_assets}
    return Report(data=data, markdown="\n".join(md), summary=summary)


@analysis("unreal_animation", engines=("unreal",), needs=("unreal.json",), version=1, category="animation",
          description="Unreal skeletons, skeletal meshes, AnimSequence/Montage/BlendSpace, AnimBlueprint state machines, "
                      "PhysicsAsset ragdolls",
          options={"name": "regex on the package path (default: every exported package)"})
def unreal_animation(ctx) -> Report:
    import re

    from gst.engines.unreal import uasjson as uj

    rx = re.compile(ctx.opt("name"), re.I) if ctx.opt("name") else None
    skeletons, meshes, sequences, montages, blendspaces, abps, physics = [], [], [], [], [], [], []
    for key, path, exports in _packages(ctx):
        if rx and not rx.search(key):
            continue
        file = _rel(ctx, path)
        abp = uj.anim_blueprint(exports, key)
        if abp is not None:
            abps.append({**abp, "file": file})
            continue
        e = uj.main_export(exports, key.rsplit("/", 1)[-1])
        t = str((e or {}).get("Type"))
        base = {"package": key, "file": file}
        if t == "Skeleton":
            sk = uj.skeleton(exports, e)
            detail = ctx.write_side_json({**base, **sk}, "skeletons", *_side(key))
            skeletons.append({**base, **{k: v for k, v in sk.items() if k != "bone_list"}, "detail": _rel(ctx, detail)})
        elif t == "SkeletalMesh":
            meshes.append({**base, **uj.skeletal_mesh(e)})
        elif t in ("AnimSequence", "AnimComposite"):
            sequences.append({**base, "class": t, **uj.anim_sequence(exports, e)})
        elif t == "AnimMontage":
            montages.append({**base, **uj.anim_montage(exports, e)})
        elif t.startswith(("BlendSpace", "AimOffsetBlendSpace")):
            blendspaces.append({**base, **uj.blend_space(e)})
        elif t == "PhysicsAsset":
            physics.append({**base, **uj.physics_asset(exports, e)})
    # a mesh without its own reference skeleton takes the bone count of its (exported) skeleton
    by_skeleton = {f"Skeleton'{s['package']}.{s['name']}'": s for s in skeletons}
    for m in meshes:
        sk = by_skeleton.get(m["skeleton"] or "")
        m["skeleton_bones"] = sk["bones"] if sk else None
    for lst in (skeletons, meshes, sequences, montages, blendspaces, abps, physics):
        lst.sort(key=lambda x: x["package"])
    notes = []
    inv = _guessed(ctx)
    for cls, have in (("SkeletalMesh", meshes), ("AnimMontage", montages)):
        if inv.get(cls) and not have:
            notes.append(f"{inv[cls]} {cls} packages in the inventory but none exported as JSON: uas skips them by default; "
                         f"export with gst assets extract --name <regex> --types json --all-types")
    summary = {"skeletons": len(skeletons), "bones_max": max((s["bones"] for s in skeletons), default=0),
               "skeletal_meshes": len(meshes), "sequences": len(sequences), "montages": len(montages),
               "blendspaces": len(blendspaces), "anim_blueprints": len(abps),
               "state_machines": sum(len(a["machines"]) for a in abps),
               "states": sum(len(m["states"]) for a in abps for m in a["machines"]),
               "physics_assets": len(physics), "bodies": sum(len(p["bodies"]) for p in physics),
               "constraints": sum(len(p["constraints"]) for p in physics)}
    md = []
    if skeletons:
        md += ["## Skeletons", "", md_table(["Skeleton", "Bones", "Root", "Depth", "Sockets", "Virtual bones", "Curves"],
               [(s["package"], s["bones"], s["root"], s["depth"], len(s["sockets"]), len(s["virtual_bones"]), len(s["curves"]))
                for s in skeletons]), ""]
    if meshes:
        md += ["## Skeletal meshes", "", md_table(["Mesh", "Skeleton", "Bones", "LODs", "Verts (LOD0)", "Tris (LOD0)",
                                                    "Max influences", "Materials", "Morph targets", "PhysicsAsset"],
               [(m["package"], m["skeleton"], m["bones"] or m["skeleton_bones"], len(m["lods"]) or m["lod_info"],
                 (m["lods"][0]["verts"] if m["lods"] else None), (m["lods"][0]["tris"] if m["lods"] else None),
                 (m["lods"][0]["max_influences"] if m["lods"] else None), len(m["materials"]), len(m["morph_targets"]),
                 m["physics_asset"]) for m in meshes]), ""]
    if sequences:
        md += ["## Animation sequences", "", md_table(["Sequence", "Length (s)", "Keys", "FPS", "Tracks", "Notifies", "Root motion"],
               [(s["package"], s["length"], s["keys"], s["fps"], s["tracks"],
                 ", ".join(f"{n['name']}@{n['time']}" for n in s["notifies"]) or "-", s["root_motion"]) for s in sequences]), ""]
    if montages:
        md += ["## Montages", "", md_table(["Montage", "Length (s)", "Sections", "Slots", "Blend in/out", "Notifies"],
               [(m["package"], m["length"], ", ".join(str(s["name"]) for s in m["sections"]),
                 ", ".join(str(s["slot"]) for s in m["slots"]), f"{m['blend_in']}/{m['blend_out']}", len(m["notifies"]))
                for m in montages]), ""]
    if blendspaces:
        md += ["## Blend spaces", "", md_table(["BlendSpace", "Class", "Axes", "Samples"],
               [(b["package"], b["class"], ", ".join(f"{a['name']} [{a['min']}, {a['max']}]" for a in b["axes"]), len(b["samples"]))
                for b in blendspaces]), ""]
    for a in abps:
        md += [f"## AnimBlueprint {a['class']}", "", f"Parent `{a['parent']}`, skeleton `{a['skeleton']}`; nodes: "
               + (", ".join(f"{k} {v}" for k, v in a["nodes"].items()) or "-") + f"; {len(a['assets'])} animation assets referenced.", ""]
        for m in a["machines"]:
            md += [f"State machine **{m['name']}** (initial `{m['initial']}`): {len(m['states'])} states, "
                   f"{len(m['transitions'])} transitions", "",
                   md_table(["From", "To", "Crossfade (s)", "Blend"], [(t["from"], t["to"], t["crossfade"], t["blend"])
                                                                       for t in m["transitions"]]), ""]
    for p in physics:
        md += [f"## PhysicsAsset {p['name']} (`{p['package']}`)", "",
               f"{len(p['bodies'])} bodies (" + (", ".join(f"{k} {v}" for k, v in p["shape_counts"].items()) or "-")
               + f"), {len(p['constraints'])} constraints (" + (", ".join(f"{k} {v}" for k, v in p["joint_types"].items()) or "-")
               + f"); mass overrides {p['mass_overrides']} totalling {p['mass_override_total']} kg.", "",
               md_table(["Constraint", "Bone 1", "Bone 2", "Type", "Swing1", "Swing2", "Twist"],
                        [(c["name"], c["bone1"], c["bone2"], c["type"], f"{c['swing1']['motion']} {c['swing1']['limit']}",
                          f"{c['swing2']['motion']} {c['swing2']['limit']}", f"{c['twist']['motion']} {c['twist']['limit']}")
                         for c in p["constraints"]]), ""]
    if notes:
        md += ["## Notes", ""] + [f"- {n}" for n in notes]
    data = {"skeletons": skeletons, "meshes": meshes, "sequences": sequences, "montages": montages,
            "blendspaces": blendspaces, "anim_blueprints": abps, "physics_assets": physics}
    return Report(data=data, markdown="\n".join(md) or "No skeletal or animation asset among the exported JSON.",
                  summary=summary, notes=notes)


def _guessed(ctx) -> dict:
    """Packages per class guessed from names in the inventory (to say what is listed but not exported)."""
    import json

    from gst.analysis.unreal import guess_class

    inv = ctx.workspace / "assets" / "inventory.jsonl"
    out: collections.Counter = collections.Counter()
    if not inv.is_file():
        return {}
    with open(inv, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                path = json.loads(line).get("path", "")
                if path.endswith(".uasset"):
                    cls = guess_class(path)
                    if cls:
                        out[cls] += 1
    return dict(out)
