"""``gst analyze unreal_assets``: what an Unreal study has listed and exported, summarized for studying the game.

- Asset families by folder: from the inventory (every packed path) by naming convention (``BP_`` Blueprint, ``DT_``
  DataTable, ``M_``/``MI_`` material, ``SK_``/``SKEL_``/``A_``/``ABP_``/``PA_`` skeletal pipeline, ``SC_``/``SW_``
  sound...), and the real export classes of every uas JSON already exported (``assets/unreal/**.json``).
- DataTables: row struct, rows and every numeric field per row (the tunable numbers), with min/max per column;
  one side file per table in ``analysis/unreal_assets/datatables/``.
- Blueprints: generated class, parent class (native ``/Script/<Module>`` or another Blueprint), functions,
  components (SCS nodes) and variables, from the exported JSON.
- IoStore games: the script objects of a plain ``global.utoc`` (``retoc print-script-objects``): native modules
  and their classes, readable even when every other container is encrypted.

Reads the workspace and the install (read-only); no option caps anything.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

from gst.analysis import Report, analysis, counted, md_table

# naming conventions (Epic's style guide and common practice); a guess, the exported JSON gives the real class
PREFIXES = [
    ("ABP_", "AnimBlueprint"), ("BP_", "Blueprint"), ("WBP_", "WidgetBlueprint"), ("W_", "WidgetBlueprint"),
    ("DT_", "DataTable"), ("CT_", "CurveTable"), ("DA_", "DataAsset"), ("MI_", "MaterialInstance"),
    ("MF_", "MaterialFunction"), ("M_", "Material"), ("SKEL_", "Skeleton"), ("SK_", "SkeletalMesh"), ("SM_", "StaticMesh"),
    ("PA_", "PhysicsAsset"), ("PHYS_", "PhysicsAsset"), ("AM_", "AnimMontage"), ("BS_", "BlendSpace"), ("AS_", "AnimSequence"),
    ("A_", "AnimSequence"), ("SC_", "SoundCue"), ("SW_", "SoundWave"), ("T_", "Texture"), ("P_", "ParticleSystem"),
    ("NS_", "NiagaraSystem"), ("BT_", "BehaviorTree"), ("BB_", "BlackboardData"), ("E_", "Enum"), ("F_", "Struct"),
]
SUFFIXES = [("_Skeleton", "Skeleton"), ("_PhysicsAsset", "PhysicsAsset"), ("_Physics", "PhysicsAsset"), ("_Cue", "SoundCue"),
            ("_AnimBP", "AnimBlueprint"), ("_Montage", "AnimMontage"), ("_BlendSpace", "BlendSpace")]
FAMILIES = {
    "Blueprint": "Blueprints", "WidgetBlueprint": "Blueprints", "AnimBlueprint": "Animation", "DataTable": "Data",
    "CurveTable": "Data", "DataAsset": "Data", "Material": "Materials", "MaterialInstance": "Materials",
    "MaterialFunction": "Materials", "SkeletalMesh": "Skeletal", "Skeleton": "Skeletal", "PhysicsAsset": "Skeletal",
    "AnimSequence": "Animation", "AnimMontage": "Animation", "BlendSpace": "Animation", "SoundCue": "Sound",
    "SoundWave": "Sound", "StaticMesh": "Meshes", "Texture": "Textures",
}
EXPORT_FAMILY = {  # uas JSON "Type" of the main export -> family
    "BlueprintGeneratedClass": "Blueprints", "WidgetBlueprintGeneratedClass": "Blueprints",
    "AnimBlueprintGeneratedClass": "Animation", "DataTable": "Data", "CurveTable": "Data", "Material": "Materials",
    "MaterialInstanceConstant": "Materials", "MaterialFunction": "Materials", "SkeletalMesh": "Skeletal",
    "Skeleton": "Skeletal", "PhysicsAsset": "Skeletal", "AnimSequence": "Animation", "AnimMontage": "Animation",
    "BlendSpace": "Animation", "BlendSpace1D": "Animation", "SoundCue": "Sound", "SoundWave": "Sound",
    "StaticMesh": "Meshes", "Texture2D": "Textures",
}


def guess_class(path: str) -> str | None:
    stem = Path(path).stem
    for suffix, cls in SUFFIXES:  # SK_Hero_Skeleton is a Skeleton, SK_Hero_PhysicsAsset a PhysicsAsset
        if stem.lower().endswith(suffix.lower()):
            return cls
    for prefix, cls in PREFIXES:
        if stem.startswith(prefix):
            return cls
    return None


def folder_of(path: str, depth: int = 3) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth])


def flatten_numbers(value, prefix: str = "") -> dict[str, float]:
    """Every numeric leaf of a row as ``a.b.c -> number`` (bools excluded, lists indexed)."""
    out: dict[str, float] = {}
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        out[prefix or "value"] = value
    elif isinstance(value, dict):
        for k, v in value.items():
            out.update(flatten_numbers(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.update(flatten_numbers(v, f"{prefix}[{i}]"))
    return out


def main_export(exports: list[dict], stem: str) -> dict | None:
    for e in exports:
        if isinstance(e, dict) and e.get("Type") in ("BlueprintGeneratedClass", "WidgetBlueprintGeneratedClass",
                                                     "AnimBlueprintGeneratedClass"):
            return e
    named = [e for e in exports if isinstance(e, dict) and e.get("Name") in (stem, f"{stem}_C")]
    return named[0] if named else next((e for e in exports if isinstance(e, dict)), None)


def datatable(e: dict) -> dict:
    rows = e.get("Rows") or {}
    struct = ((e.get("Properties") or {}).get("RowStruct") or {}).get("ObjectName")
    numeric = {name: flatten_numbers(row) for name, row in rows.items()} if isinstance(rows, dict) else {}
    columns: dict[str, list[float]] = collections.defaultdict(list)
    for nums in numeric.values():
        for k, v in nums.items():
            columns[k].append(v)
    ranges = {k: {"min": min(v), "max": max(v), "rows": len(v)} for k, v in sorted(columns.items())}
    return {"row_struct": struct, "rows": len(rows) if isinstance(rows, dict) else 0, "columns": ranges, "numbers": numeric}


def blueprint(e: dict, exports: list[dict]) -> dict:
    sup = e.get("SuperStruct") or {}
    parent = re.sub(r"^\w+'(.*)'$", r"\1", str(sup.get("ObjectName") or "")) or None
    funcs = sorted(x.get("Name") for x in exports if isinstance(x, dict) and x.get("Type") == "Function" and x.get("Name"))
    scs = sum(1 for x in exports if isinstance(x, dict) and x.get("Type") == "SCS_Node")
    variables = sorted(p.get("Name") for p in (e.get("ChildProperties") or []) if isinstance(p, dict) and p.get("Name")
                       and not str(p.get("Name")).startswith(("K2Node_", "UberGraphFrame", "Temp_")))
    return {"class": e.get("Name"), "parent": parent, "parent_path": sup.get("ObjectPath"),
            "native_parent": str(sup.get("ObjectPath") or "").startswith("/Script/"), "functions": funcs,
            "components": scs, "variables": variables}


def iter_exported_json(ws: Path):
    """(packed path without extension, exports) of every uas JSON under assets/unreal/ (each package once)."""
    root = ws / "assets" / "unreal"
    seen: set[str] = set()
    if not root.is_dir():
        return
    for p in sorted(root.rglob("*.json")):
        if p.name.startswith((".", "_")) or p.name.endswith(".gs.json") or p.name == "containers.json":
            continue
        rel = p.relative_to(root).as_posix()
        m = re.search(r"(?:^|/)([^/]+/Content/.*|Engine/.*)\.json$", rel)
        key = m.group(1) if m else rel[:-5]
        if key in seen:
            continue
        try:
            exports = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(exports, list):
            seen.add(key)
            yield key, p, exports


def script_objects(ctx) -> dict | None:
    """Native modules and classes from a plain global.utoc (retoc), or None."""
    from gst.common import GsError
    from gst.engines.unreal import paks as paks_mod
    from gst.engines.unreal import retoc

    try:
        paks = paks_mod.paks_dir(ctx.install_dir)
    except (GsError, TypeError):
        return None
    g = paks / "global.utoc"
    head = paks_mod.utoc_header(g) if g.exists() else None
    if not head or head.get("encrypted"):
        return None
    try:
        objects = retoc.script_objects(g)
    except (GsError, OSError) as exc:
        return {"error": str(exc)}
    ctx.use("install:global.utoc", g)
    from gst.engines.unreal import tool_version

    ctx.tools["retoc"] = tool_version("retoc") or "?"
    modules = retoc.script_modules(objects)
    return {"objects": len(objects), "modules": modules}


@analysis("unreal_assets", engines=("unreal",), needs=("unreal.inventory",), version=2, category="data",
          description="Unreal asset families by folder, DataTable rows (tunable numbers), Blueprint parents, script objects",
          options={"depth": "folder depth for the family table (default 3: Game/Content/<Folder>)",
                   "script_objects": "0 = do not read global.utoc with retoc (default 1 when it is plain)"})
def unreal_assets(ctx) -> Report:
    depth = int(ctx.opt("depth", 3))
    inv = ctx.workspace / "assets" / "inventory.jsonl"  # recorded by the framework as the unreal.inventory need
    by_folder: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    by_class: collections.Counter = collections.Counter()
    containers: collections.Counter = collections.Counter()
    total = 0
    with open(inv, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            path = row["path"]
            if not path.endswith((".uasset", ".umap")):
                continue
            total += 1
            containers[row.get("container", "")] += 1
            cls = "Map" if path.endswith(".umap") else (guess_class(path) or "other")
            by_class[cls] += 1
            by_folder[folder_of(path, depth)][FAMILIES.get(cls, "Maps" if cls == "Map" else "other")] += 1
    exported_types: collections.Counter = collections.Counter()
    exported_by_folder: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    tables, blueprints = [], []
    for key, path, exports in iter_exported_json(ctx.workspace):
        stem = key.rsplit("/", 1)[-1]
        e = main_export(exports, stem)
        if e is None:
            continue
        t = str(e.get("Type"))
        exported_types[t] += 1
        exported_by_folder[folder_of(key + ".uasset", depth)][EXPORT_FAMILY.get(t, t)] += 1
        if t == "DataTable":
            dt = datatable(e)
            dt.update(package=key, file=path.relative_to(ctx.workspace).as_posix())
            ctx.write_side_json(dt, "datatables", f"{stem}.json")
            tables.append({k: v for k, v in dt.items() if k != "numbers"})
        elif t.endswith("BlueprintGeneratedClass"):
            bp = blueprint(e, exports)
            bp.update(package=key, file=path.relative_to(ctx.workspace).as_posix())
            blueprints.append(bp)
    natives = None
    if str(ctx.opt("script_objects", "1")) != "0":
        natives = script_objects(ctx)
    families = sorted({f for c in by_folder.values() for f in c})
    folder_rows = sorted(by_folder.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    parents = counted(collections.Counter(b["parent"] for b in blueprints if b["parent"]))
    summary = {"packages": total, "folders": len(by_folder), "guessed": counted(by_class), "exported_packages":
               sum(exported_types.values()), "datatables": len(tables), "datatable_rows": sum(t["rows"] for t in tables),
               "blueprints_exported": len(blueprints), "native_parents": sum(1 for b in blueprints if b["native_parent"])}
    if natives and "modules" in natives:
        summary.update(script_objects=natives["objects"], script_modules=len(natives["modules"]))
    md = [f"{total} packages (.uasset/.umap) in {len(by_folder)} folders (depth {depth}); classes guessed from names, "
          "the exported JSON gives the real ones.", "", "## Families by folder (guessed from names)", "",
          md_table(["Folder", "Total"] + families, [(f, sum(c.values()), *[c.get(x, 0) for x in families]) for f, c in folder_rows]),
          "", "## Guessed classes", "", md_table(["Class", "Packages"], counted(by_class).items())]
    if exported_types:
        md += ["", "## Exported JSON: main export class", "", md_table(["Type", "Packages"], counted(exported_types).items())]
    if tables:
        md += ["", "## DataTables (tunable numbers)", ""]
        for t in sorted(tables, key=lambda x: x["package"]):
            md += [f"### {t['package']}", "", f"Row struct `{t['row_struct']}`, {t['rows']} rows; per-row values in "
                   f"`analysis/unreal_assets/datatables/{t['package'].rsplit('/', 1)[-1]}.json`.", "",
                   md_table(["Column", "Min", "Max", "Rows"], [(k, v["min"], v["max"], v["rows"]) for k, v in t["columns"].items()]), ""]
    if blueprints:
        md += ["", "## Blueprints", "", md_table(["Class", "Parent", "Functions", "Components", "Variables"],
               [(b["class"], f"{b['parent']} ({b['parent_path']})", len(b["functions"]), b["components"], len(b["variables"]))
                for b in sorted(blueprints, key=lambda x: x["package"])]),
               "", "Parents: " + ", ".join(f"{k} {v}" for k, v in parents.items())]
    if natives:
        if "error" in natives:
            md += ["", f"Script objects of global.utoc not read: {natives['error']}"]
        else:
            md += ["", "## Native modules (script objects of global.utoc)", "",
                   md_table(["Module", "Objects", "Classes"], sorted(((m, v["objects"], len(v["classes"]))
                                                                       for m, v in natives["modules"].items()), key=lambda r: (-r[1], r[0])))]
            ctx.write_side_json(natives, "script_objects.json")
    data = {"by_folder": {f: counted(c) for f, c in folder_rows}, "guessed": counted(by_class), "containers": counted(containers),
            "exported": {"types": counted(exported_types), "by_folder": {f: counted(c) for f, c in sorted(exported_by_folder.items())}},
            "datatables": sorted(tables, key=lambda x: x["package"]), "blueprints": sorted(blueprints, key=lambda x: x["package"]),
            "blueprint_parents": parents,
            "script_modules": ({m: {"objects": v["objects"], "classes": len(v["classes"])} for m, v in natives["modules"].items()}
                               if natives and "modules" in natives else None)}
    return Report(data=data, markdown="\n".join(md), summary=summary)
