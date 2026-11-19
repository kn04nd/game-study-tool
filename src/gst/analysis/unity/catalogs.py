"""``gst analyze catalogs``: ScriptableObject catalogs, GameObject name families and Addressables, generically.

Ports of ``repo/cosmetics_scan.py``, ``cosmetic_assets.py``, ``bundle_scan.py`` and ``peak/mdl_cosmetics.py``,
``char_custom.py`` without game-specific names:

- ScriptableObjects (MonoBehaviours without a GameObject, found from the raw ``m_GameObject`` PPtr) grouped by
  script class: count, files, object names, field summary, the PPtrs of each field resolved to their target
  type (prefab roots counted apart: ``GameObject`` lists of an SO are the usual "catalog of prefabs"), and string
  fields whose values name a prefab or a ``Resources/`` path (``CosmeticAsset.prefab.resourcePath`` style joins).
- Name families: GameObjects grouped by the prefix before `` - `` (``Cosmetic - Hat 01`` -> ``Cosmetic``).
- Addressables: JSON and binary (``catalog.bin``, 1.21+/2.x) catalogs decoded (bundles, internal ids, providers, keys,
  address -> bundle -> dependencies from the active catalog); the
  ``StreamingAssets/aa`` bundles with their object types from the inventory.

Options: ``script`` (only these SO classes), ``file`` (regex on install relpaths), ``values=1`` (also write every
SO's fields to ``analysis/catalogs/<Script>.jsonl``).
"""

from __future__ import annotations

import collections
import os
import re
import shutil

from gst.analysis import Report, UNITY, analysis, counted, md_table
from gst.analysis.unity import UnityFile, basename, ptr
from gst.analysis.unity.fields import FieldStats, describe

FAMILY_SEP = " - "


def walk_refs(value, path: str = ""):
    """``(field path, (file_id, path_id))`` of every non-null PPtr in a typetree value (lists as ``[]``)."""
    if isinstance(value, dict):
        if set(value) == {"m_FileID", "m_PathID"}:
            if value.get("m_PathID"):
                yield path, ptr(value)
            return
        for k, v in value.items():
            if k in ("m_GameObject", "m_Script"):
                continue
            yield from walk_refs(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for v in value:
            yield from walk_refs(v, f"{path}[]")


def walk_strings(value, path: str = ""):
    if isinstance(value, str):
        if value:
            yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            if k in ("m_Name", "m_EditorClassIdentifier"):
                continue
            yield from walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for v in value:
            yield from walk_strings(v, f"{path}[]")


def norm_res(path: str) -> str:
    p = path.replace("\\", "/").strip().lower()
    p = re.sub(r"^assets/resources/", "", p)
    return os.path.splitext(p)[0]


class ClassAgg:
    def __init__(self):
        self.stats = FieldStats()
        self.files: collections.Counter = collections.Counter()
        self.names: list[str] = []
        self.refs: dict[str, dict] = {}
        self.joins: dict[str, collections.Counter] = {}
        self.namespace = ""
        self.assembly = ""


class Joiner:
    """What a PPtr or a string points to: prefab roots (from the prefabs analysis) and Resources paths."""

    def __init__(self, prefab_roots: set[tuple[str, int]] | None, prefab_names: set[str], resources: set[str]):
        self.prefab_roots = prefab_roots
        self.prefab_names = prefab_names
        self.resources = resources

    def string(self, value: str) -> str | None:
        if value in self.prefab_names:
            return "prefab_name"
        if "/" in value and norm_res(value) in self.resources:
            return "resources_path"
        if value.lower() in self.resources:
            return "resources_path"
        return None


def add_object(agg: ClassAgg, file: str, tree: dict, resolve_ref, joiner: Joiner) -> None:
    """One ScriptableObject: field stats, names, resolved refs and string joins. ``resolve_ref((fid, pid))`` ->
    ``(type name, file basename, path id)`` or None."""
    agg.stats.add(tree)
    agg.files[file] += 1
    agg.names.append(str(tree.get("m_Name") or ""))
    for field, ref in walk_refs(tree):
        e = agg.refs.setdefault(field, {"targets": collections.Counter(), "prefab_roots": 0, "unresolved": 0})
        target = resolve_ref(ref)
        if target is None:
            e["unresolved"] += 1
            continue
        tname, tfile, tpid = target
        e["targets"][tname] += 1
        if tname == "GameObject" and joiner.prefab_roots is not None and (tfile.lower(), tpid) in joiner.prefab_roots:
            e["prefab_roots"] += 1
    for field, value in walk_strings(tree):
        kind = joiner.string(value)
        if kind:
            agg.joins.setdefault(field, collections.Counter())[kind] += 1


def name_families(rows) -> dict:
    fam: dict[str, dict] = {}
    for r in rows:
        name = r.get("name") or ""
        if FAMILY_SEP not in name:
            continue
        head = name.split(FAMILY_SEP, 1)[0].strip()
        if not head:
            continue
        e = fam.setdefault(head, {"gameobjects": 0, "files": collections.Counter(), "sub": collections.Counter()})
        e["gameobjects"] += 1
        e["files"][r.get("file", "")] += 1
        rest = name.split(FAMILY_SEP, 1)[1]
        if FAMILY_SEP in rest:
            e["sub"][rest.split(FAMILY_SEP, 1)[0].strip()] += 1
    out = {}
    for head in sorted(fam, key=lambda h: (-fam[h]["gameobjects"], h)):
        e = fam[head]
        if e["gameobjects"] < 2:
            continue
        out[head] = {"gameobjects": e["gameobjects"], "files": dict(sorted(e["files"].items())),
                     "subfamilies": counted({k: v for k, v in e["sub"].items() if v >= 2})}
    return out


def addressables(ctx) -> dict:
    """Addressables catalogs (JSON and binary, decoded to the same summary), the active one (``settings.json``), the
    asset address -> bundle -> dependencies map from it, and the ``aa/`` bundles with their object types and the
    addresses the catalog puts in each."""
    from gst.engines.unity.export.addressables import (active_catalog, bundle_file, compare_summaries, decode_catalog,
                                                       find_catalogs)

    out = {"catalogs": [], "bundles": [], "assets": [], "active": None}
    if ctx.install_dir is None:
        return out
    decoded: dict[str, dict] = {}
    for p in find_catalogs(ctx.install_dir):
        rel = p.relative_to(ctx.install_dir).as_posix()
        entry = {"file": rel, "bytes": p.stat().st_size}
        if p.name.startswith("catalog") and p.suffix in (".json", ".bin"):
            ctx.use(f"install:{rel}", p)
            try:
                dec = decode_catalog(p)
            except (ValueError, UnicodeDecodeError, KeyError, IndexError, struct.error) as exc:
                entry["error"] = f"{exc.__class__.__name__}: {exc}"
            else:
                decoded[rel] = dec
                entry.update(format=dec["format"], version=dec["version"], active=active_catalog(p.parent) == p.name,
                             locator_id=dec["locator_id"], build_version=dec["build_version"],
                             providers=sorted({x for x in dec["provider_ids"] if x}), internal_ids=len(dec["internal_ids"]),
                             bundles=len(dec["bundles"]), keys=len(dec["keys"]), locations=len(dec["locations"]),
                             assets=len(dec["assets"]), resource_types=sorted({t for t in dec["resource_types"] if t}))
        out["catalogs"].append(entry)
    if decoded:
        # the catalog the game loads: settings.json's, else the binary one (newer format), else the first
        pick = next((c["file"] for c in out["catalogs"] if c.get("active") and c["file"] in decoded), None) or \
            next((f for f, d in decoded.items() if d["format"] == "binary"), None) or next(iter(decoded))
        out["active"] = pick
        out["assets"] = [{k: a[k] for k in ("address", "type", "bundle", "dependencies", "keys", "internal_id")}
                         for a in decoded[pick]["assets"]]
        others = [f for f in decoded if f != pick and os.path.dirname(f) == os.path.dirname(pick)]
        if others:
            out["compare"] = {f: {k: {"both": v["both"], "only_active": len(v["only_second"]),
                                      "only_other": len(v["only_first"])}
                                  for k, v in compare_summaries(decoded[f], decoded[pick]).items()} for f in others}
    by_bundle: dict[str, list] = collections.defaultdict(list)
    for a in out["assets"]:
        if a["bundle"]:
            by_bundle[a["bundle"].lower()].append(str(a["address"]))
    active_bundles = {bundle_file(i).lower() for i in decoded[out["active"]]["bundles"]} if out["active"] else set()
    from gst.engines.unity import inventory

    for s in inventory.summaries(ctx.inventory_dir()):
        if "/aa/" in s["file"]:
            b = {"file": s["file"], "objects": s.get("objects", 0),
                 "types": dict(sorted(s.get("types", {}).items(), key=lambda kv: (-kv[1], kv[0]))),
                 "input_bytes": s.get("input_bytes", 0)}
            if out["active"]:
                name = bundle_file(s["file"]).lower()
                b["in_catalog"] = name in active_bundles
                b["addresses"] = sorted(by_bundle.get(name, []))
            out["bundles"].append(b)
    out["bundles"].sort(key=lambda b: b["file"])
    return out


@analysis("catalogs", engines=UNITY, needs=("unity.inventory", "unity.install"), uses=("analysis:prefabs",),
          description="ScriptableObject catalogs by script class, joins to prefabs, name families, Addressables",
          version=2, options={"script": "only these ScriptableObject classes (repeatable/comma list)",
                              "file": "regex on install relpaths", "values": "1 = write analysis/catalogs/<Script>.jsonl"})
def catalogs(ctx) -> Report:
    from gst.engines.unity import inventory, loader

    wanted = set(ctx.opt_list("script")) or None
    fpat = re.compile(ctx.opt("file"), re.I) if ctx.opt("file") else None
    write_values = str(ctx.opt("values", "0")) in ("1", "true", "yes")
    side = ctx.side_dir()
    if side.is_dir():
        shutil.rmtree(side, ignore_errors=True)
    pref = ctx.load("prefabs")
    prefab_roots, prefab_names, resources = None, set(), set()
    if pref:
        prefab_roots = {(basename(r["file"]).lower(), r["go"]) for r in pref["data"]["prefabs"] if not r.get("cab")}
        prefab_roots |= {(str(r["cab"]).lower(), r["go"]) for r in pref["data"]["prefabs"] if r.get("cab")}
        prefab_names = {r["name"] for r in pref["data"]["prefabs"] if r["name"]}
    res_map = inventory.resources_map(ctx.inventory_dir())
    resources = {norm_res(v) for v in res_map.values()}
    joiner = Joiner(prefab_roots, prefab_names, resources)
    aggs: dict[str, ClassAgg] = {}
    untyped: collections.Counter = collections.Counter()
    writer = None
    if write_values:
        from gst.analysis.unity.mono_values import ValueWriter

        writer = ValueWriter(str(side))
    files = []
    for s in inventory.summaries(ctx.inventory_dir()):
        if "MonoBehaviour" in s.get("types", {}) and not (fpat and not fpat.search(s["file"])):
            if wanted is None or s.get("scripts") is None or wanted & set(s["scripts"]):
                files.append(s["file"])
    try:
        for rel in sorted(files):
            ctx.log(f"reading {rel}")
            with UnityFile(ctx, rel) as uf:
                for sf in uf.files():
                    cache: dict[tuple, tuple | None] = {}

                    def resolve_ref(ref, _sf=sf):
                        if ref not in cache:
                            r = uf.catalog.resolve(_sf, *ref)
                            cache[ref] = None if r is None else (loader.type_name(r), basename(r.assets_file.name), r.path_id)
                        return cache[ref]

                    for obj in list(sf.objects.values()):
                        if loader.type_name(obj) != "MonoBehaviour" or loader.raw_gameobject_pid(obj):
                            continue  # components live on GameObjects: only ScriptableObjects here
                        info = uf.script(obj)
                        script = info.class_name if info else ""
                        if wanted is not None and script not in wanted:
                            continue
                        tree = uf.mono(obj, info)
                        if tree is None:
                            untyped[script or "?"] += 1
                            continue
                        agg = aggs.get(script)
                        if agg is None:
                            agg = aggs[script] = ClassAgg()
                            agg.namespace, agg.assembly = (info.namespace, info.assembly) if info else ("", "")
                        add_object(agg, rel, tree, resolve_ref, joiner)
                        if writer is not None:
                            from gst.analysis.unity.mono_values import clean

                            writer.write(script or "_unknown", {"file": rel, "path_id": obj.path_id,
                                                                "name": tree.get("m_Name"), "fields": clean(tree)})
    finally:
        if writer is not None:
            writer.close()
            for p in sorted(writer.paths.values()):
                ctx.outputs.append(os.path.relpath(p, ctx.workspace).replace("\\", "/"))
    families = name_families(ctx.rows(types=("GameObject",)))
    return build_report(aggs, untyped, families, addressables(ctx), pref is not None)


def build_report(aggs: dict[str, ClassAgg], untyped, families: dict, aa: dict, have_prefabs: bool) -> Report:
    classes = {}
    for name in sorted(aggs, key=lambda n: (-aggs[n].stats.count, n)):
        a = aggs[name]
        refs = {f: {"targets": counted(e["targets"]), **({"prefab_roots": e["prefab_roots"]} if have_prefabs else {}),
                    "unresolved": e["unresolved"]} for f, e in sorted(a.refs.items())}
        classes[name or "_unknown"] = {
            "count": a.stats.count, "namespace": a.namespace, "assembly": a.assembly,
            "files": dict(sorted(a.files.items())), "names": sorted(a.names), "fields": a.stats.as_dict(), "refs": refs,
            "string_joins": {f: dict(sorted(c.items())) for f, c in sorted(a.joins.items())}}
    summary = {"so_classes": len(classes), "scriptable_objects": sum(c["count"] for c in classes.values()),
               "name_families": len(families), "addressables_catalogs": len(aa["catalogs"]),
               "addressables_bundles": len(aa["bundles"])}
    data = {"scriptable_objects": classes, "untyped": counted(untyped), "name_families": families, "addressables": aa}
    md = [f"{summary['scriptable_objects']} ScriptableObjects in {summary['so_classes']} classes; {len(families)} GameObject "
          f"name families; {len(aa['catalogs'])} Addressables catalog files, {len(aa['bundles'])} bundles in `aa/`."
          + ("" if have_prefabs else " (no `analysis/prefabs.json`: PPtr targets are not split into prefab roots)"), ""]
    rows = []
    for n, c in classes.items():
        prefab_refs = sum(e.get("prefab_roots", 0) for e in c["refs"].values())
        go_refs = sum(e["targets"].get("GameObject", 0) for e in c["refs"].values())
        sprite_refs = sum(e["targets"].get("Sprite", 0) + e["targets"].get("Texture2D", 0) for e in c["refs"].values())
        joins = sum(sum(v.values()) for v in c["string_joins"].values())
        rows.append((f"`{n}`", c["count"], len(c["files"]), len(c["fields"]), go_refs, prefab_refs if have_prefabs else "-",
                     sprite_refs, joins))
    md += ["## ScriptableObject classes", "", md_table(["Class", "Objects", "Files", "Fields", "GameObject refs",
                                                        "Prefab roots", "Sprite/Texture refs", "Name/path joins"], rows), ""]
    for n, c in list(classes.items())[:25]:
        md += [f"### `{n}` ({c['count']})", ""]
        md += [md_table(["Field", "Summary", "Refs to", "Joins"],
                        [(f"`{f}`", describe(d), ", ".join(f"{t} {k}" for t, k in c["refs"].get(f, {}).get("targets", {}).items()) or "-",
                          ", ".join(f"{k} {v}" for k, v in c["string_joins"].get(f, {}).items()) or "-")
                         for f, d in c["fields"].items()]), ""]
        nested = {f: e for f, e in c["refs"].items() if f not in c["fields"]}
        nested_joins = {f: j for f, j in c["string_joins"].items() if f not in c["fields"]}
        if nested or nested_joins:
            md += ["Nested fields: " + "; ".join([f"`{f}` -> " + ", ".join(f"{t} {k}" for t, k in e["targets"].items())
                                                for f, e in nested.items()]
                                               + [f"`{f}` joins " + ", ".join(f"{k} {v}" for k, v in j.items())
                                                  for f, j in nested_joins.items()]), ""]
    md += ["## GameObject name families (`<family> - <name>`)", "",
           md_table(["Family", "GameObjects", "Files", "Sub-families"],
                    [(f"`{h}`", e["gameobjects"], ", ".join(f"{basename(f)} {n}" for f, n in e["files"].items()),
                      ", ".join(f"{k} {v}" for k, v in list(e["subfamilies"].items())[:8]) or "-")
                     for h, e in list(families.items())[:60]]), ""]
    if aa["catalogs"] or aa["bundles"]:
        md += ["## Addressables", "", md_table(["Catalog file", "Format", "Active", "Bundles", "Internal ids", "Keys",
                                                "Assets", "Providers"],
                                               [(f"`{c['file']}`", c.get("format", "-"), "yes" if c.get("active") else "-",
                                                 c.get("bundles", "-"), c.get("internal_ids", "-"), c.get("keys", "-"),
                                                 c.get("assets", "-"),
                                                 ", ".join(p.rsplit(".", 1)[-1] for p in c.get("providers", [])) or "-")
                                                for c in aa["catalogs"]]), ""]
        if aa.get("active"):
            md += [f"Asset -> bundle map from `{aa['active']}` (`data.addressables.assets`: address, type, bundle, "
                   f"dependency bundles, other keys): {len(aa['assets'])} assets.", ""]
        for other, cmp in aa.get("compare", {}).items():
            md += [f"Compared with `{other}`: " + "; ".join(f"{k} {v['both']} shared, {v['only_active']} only in the active "
                                                          f"catalog, {v['only_other']} only in the other"
                                                          for k, v in cmp.items()), ""]
        md += [md_table(["Bundle", "Objects", "Types", "In catalog", "Addresses"],
                        [(f"`{basename(b['file'])}`", b["objects"],
                          ", ".join(f"{t} {n}" for t, n in list(b["types"].items())[:6]),
                          ("yes" if b["in_catalog"] else "no") if "in_catalog" in b else "-",
                          len(b.get("addresses", [])) if "addresses" in b else "-")
                         for b in aa["bundles"]]), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)
