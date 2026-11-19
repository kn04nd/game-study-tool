"""``gst analyze godot_scenes``: every text scene/resource of the recovered Godot project as JSON, plus a summary:
node types, scripts per node, signal connections, the instanced sub-scene graph, and the exported script variables
with the values the scenes give them (the tunables: what a designer set on each component, next to the default
in code). Custom data resources (``.tres`` / sub-resources with a script) are listed with their values too.

Inputs: ``assets/godot/project`` (GDRE recovery; ``.tscn``/``.tres`` outside ``.godot/``), script sources in the
project, ``decomp/godot`` (GDScript) and ``decomp/cs`` (C#, ilspycmd). Per file: ``analysis/godot_scenes/<res path>.json``.
"""

from __future__ import annotations

import collections
import shutil

from gst.analysis import Report, analysis, counted, md_table
from gst.analysis.godot import IMPORT_DIRS, SCENE_EXTS, Docs, bulk_option, iter_files, project_root
from gst.analysis.godot.links import Project

SKIP_PROPS = {"script", "unique_name_in_owner", "editor_description"}
PACKED_DEFAULT = 256


def _skip(prop: str) -> bool:
    return prop in SKIP_PROPS or prop.startswith(("metadata/", "_import_", "__meta__"))


def node_kind(node: dict) -> str:
    if node.get("type"):
        return node["type"]
    if "instance" in node:
        return "(instance)"
    if "instance_placeholder" in node:
        return "(placeholder)"
    return "(override)"  # a node of an instanced/inherited scene, only with changed properties


def scene_json(res: str, doc: dict, packed_max: int | None) -> dict:
    from gst.engines.godot.tres import ref_path, to_json

    def j(v):
        return to_json(v, packed_max=packed_max)

    nodes = []
    for n in doc["nodes"]:
        item = {"path": n["path"], "name": n["name"], "type": n.get("type"), "parent": n.get("parent"), "line": n["line"]}
        if "instance" in n:
            item["instance"] = ref_path(doc, n["instance"]) or j(n["instance"])
        if "script" in n["props"]:
            item["script"] = ref_path(doc, n["props"]["script"]) or j(n["props"]["script"])
        for k in ("groups", "unique_id", "index", "owner"):
            if k in n:
                item[k] = j(n[k])
        item["props"] = {k: j(v) for k, v in n["props"].items()}
        nodes.append(item)
    out = {"path": res, "kind": doc["kind"], "header": j(doc["header"]),
           "ext_resources": [{"id": rid, **{k: v for k, v in e.items() if v is not None}} for rid, e in doc["ext_resources"].items()],
           "sub_resources": [{"id": rid, "type": doc["sub_resources"][rid]["type"], "line": doc["sub_resources"][rid]["line"],
                              "props": {k: j(v) for k, v in doc["sub_resources"][rid]["props"].items()}}
                             for rid in doc["sub_order"]],
           "nodes": nodes,
           "connections": [{k: j(v) for k, v in c.items()} for c in doc["connections"]],
           "editable": doc["editable"]}
    if doc["resource"] is not None:
        out["resource"] = {"type": doc["resource"]["type"], "line": doc["resource"]["line"],
                           "props": {k: j(v) for k, v in doc["resource"]["props"].items()}}
    return out


@analysis("godot_scenes", engines=("godot",), needs=("godot.project",), uses=("code.index",), version=2, category="prefab_scene",
          description="Godot scenes (text and binary): node types, scripts, signals, instanced scenes and models, tunables",
          options={"name": "regex on the res:// path (only matching files)",
                   "packed": f"Packed*Array items kept in the per-file JSON (default {PACKED_DEFAULT}; 'all' = every item)",
                   "files": "1 (default) = write analysis/godot_scenes/<res path>.json per file; 0 = summary only",
                   "bulk": "binary packed float/vector/byte arrays of this many bytes or more are counted, not decoded "
                           "(default 4096; 'all' = decode everything)",
                   "imported": "headers (default) = type/version of every binary file in .godot/imported (+ .ctex "
                               "headers); parse = also read each one fully (errors listed); 0 = skip"})
def godot_scenes(ctx) -> Report:
    import re

    from gst.engines.godot.scripts import ScriptIndex
    from gst.engines.godot.tres import iter_refs, ref_path, short, to_json

    root = project_root(ctx.workspace)
    rx = re.compile(ctx.opt("name"), re.I) if ctx.opt("name") else None
    packed = str(ctx.opt("packed", PACKED_DEFAULT))
    packed_max = None if packed.lower() == "all" else int(packed)
    write_files = str(ctx.opt("files", "1")) != "0"
    side = ctx.side_dir()
    if side.exists():
        shutil.rmtree(side)
    roots_src = [root, ctx.workspace / "decomp" / "cs", ctx.workspace / "decomp" / "godot"]
    for r in roots_src[1:]:
        if r.is_dir():
            ctx.use(f"sources {r.name}", r)
    scripts = ScriptIndex(root, roots_src)
    docs = Docs(root, bulk=bulk_option(ctx))
    project = Project(root, docs)

    files = [(res, p) for res, p in iter_files(root, SCENE_EXTS) if not rx or rx.search(res)]
    node_types: collections.Counter = collections.Counter()
    script_use: dict[str, dict] = {}
    rows, connections, edges = [], [], collections.Counter()
    roots: dict[str, dict] = {}       # res -> {"type", "script", "instance"} of the scene root (inheritance)
    candidates = []                   # (scene, node path, kind, script, instance, props)
    data_items = []                   # (file, where, script, type, props)
    signals: collections.Counter = collections.Counter()
    for res, p in files:
        doc = docs.get(res)
        if doc is None:
            continue
        nodes = doc["nodes"]
        root_node = nodes[0] if nodes else None
        scripts_here: collections.Counter = collections.Counter()
        inst_here: collections.Counter = collections.Counter()
        models_here: list = []
        for n in nodes:
            kind = node_kind(n)
            node_types[kind] += 1
            script = ref_path(doc, n["props"].get("script")) if "script" in n["props"] else None
            inst = ref_path(doc, n["instance"]) if "instance" in n else None
            if script:
                scripts_here[script] += 1
                u = script_use.setdefault(script, {"nodes": 0, "scenes": set(), "node_types": collections.Counter()})
                u["nodes"] += 1
                u["scenes"].add(res)
                u["node_types"][n.get("type") or kind] += 1
            if inst:
                inst_here[inst] += 1
                edges[(res, inst)] += 1
                if project.is_model(inst):
                    info = project.import_info(inst) or {}
                    models_here.append({"node": n["path"], "model": inst, "imported": info.get("imported"),
                                        "importer": info.get("importer")})
            props = {k: (short(v, doc), any(True for _r in iter_refs(v))) for k, v in n["props"].items() if not _skip(k)}
            if (script or inst or kind == "(override)") and props:
                candidates.append((res, n["path"], kind, script, inst, n.get("type"), props))
        if root_node is not None:
            roots[res] = {"type": root_node.get("type"),
                          "script": ref_path(doc, root_node["props"].get("script")) if "script" in root_node["props"] else None,
                          "instance": ref_path(doc, root_node["instance"]) if "instance" in root_node else None}
        for c in doc["connections"]:
            item = {"scene": res, "signal": str(c.get("signal")), "from": str(c.get("from")), "to": str(c.get("to")),
                    "method": str(c.get("method")), "line": c["line"]}
            if c.get("flags") is not None:
                item["flags"] = c["flags"]
            if c.get("binds"):
                item["binds"] = to_json(c["binds"])
            connections.append(item)
            signals[item["signal"]] += 1
        # custom data resources: [resource] or sub_resources with a script
        res_items = []
        if doc["resource"] is not None and "script" in doc["resource"]["props"]:
            res_items.append(("[resource]", doc["resource"]["type"], doc["resource"]["props"]))
        for rid in doc["sub_order"]:
            sr = doc["sub_resources"][rid]
            if "script" in sr["props"]:
                res_items.append((f"SubResource({rid})", sr["type"], sr["props"]))
        for where, rtype, props in res_items:
            script = ref_path(doc, props.get("script"))
            if script:
                data_items.append((res, where, script, rtype, {k: v for k, v in props.items() if not _skip(k)}))
        sub_types = collections.Counter(doc["sub_resources"][r]["type"] for r in doc["sub_order"])
        ext_types = collections.Counter(e.get("type") for e in doc["ext_resources"].values())
        row = {"path": res, "kind": doc["kind"], "type": doc["header"].get("type") if doc["kind"] == "resource" else None,
               "nodes": len(nodes), "root": root_node["name"] if root_node else None,
               "root_type": roots.get(res, {}).get("type"), "root_script": roots.get(res, {}).get("script"),
               "inherits": roots.get(res, {}).get("instance"), "instances": counted(inst_here),
               "scripts": counted(scripts_here), "connections": len(doc["connections"]), "editable": len(doc["editable"]),
               "sub_resources": counted(sub_types), "ext_resources": counted(ext_types)}
        if models_here:
            row["models"] = models_here
        if "binary" in doc:
            row["binary"] = {k: doc["binary"][k] for k in ("ver_major", "ver_minor", "ver_format", "compression")}
        rows.append(row)
        if write_files:
            parts = res[len("res://"):].split("/")
            ctx.write_side_json(scene_json(res, doc, packed_max), *parts[:-1], parts[-1] + ".json")
        del doc

    # instance graph
    instanced_by: dict[str, set] = collections.defaultdict(set)
    for (src, dst), _n in edges.items():
        instanced_by[dst].add(src)
    scene_set = {r["path"] for r in rows if r["kind"] == "scene"}
    top = sorted(s for s in scene_set if s not in instanced_by)
    most = counted({dst: len(srcs) for dst, srcs in instanced_by.items()})

    def script_of(scene_res: str, seen=()) -> str | None:
        info = roots.get(scene_res)
        if not info or scene_res in seen:
            return None
        return info["script"] or (script_of(info["instance"], seen + (scene_res,)) if info["instance"] else None)

    # tunables: exported script variables with the values scenes give them
    tunables: dict[str, dict] = {}
    unresolved_scripts = set()
    for scene, npath, kind, script, inst, ntype, props in candidates:
        eff = script or (script_of(inst) if inst else None)
        if not eff:
            continue  # override node without a known script: engine properties of an instanced node
        info = scripts.exports(eff)
        exports = info["exports"]
        t = tunables.setdefault(eff, {"source": None, "class": info["class"], "base": info["base"], "exports": {},
                                      "engine": {}, "unclassified": {}, "nodes": 0})
        if info["source"] is not None:
            try:
                t["source"] = info["source"].relative_to(ctx.workspace).as_posix()
            except ValueError:
                t["source"] = str(info["source"])
        else:
            unresolved_scripts.add(eff)
        t["nodes"] += 1
        for k, v in props.items():
            if exports and k in exports:
                bucket = t["exports"].setdefault(k, {"type": exports[k]["type"], "default": exports[k]["default"],
                                                     "declared_in": exports[k].get("declared_in"), "values": {}})
            elif info["source"] is not None:
                if not script:  # an override on an instance root: only exported props are interesting here
                    continue
                bucket = t["engine"].setdefault(k, {"values": {}})
            else:
                bucket = t["unclassified"].setdefault(k, {"values": {}})
            sv, has_ref = v
            val = bucket["values"].setdefault(sv, {"count": 0, "where": []})
            val["count"] += 1
            val["where"].append(f"{scene}::{npath}" + (f" (instance of {inst})" if inst and not script else ""))
            if has_ref:
                bucket["refs"] = True
    for t in tunables.values():
        for group in ("exports", "engine", "unclassified"):
            t[group] = dict(sorted(t[group].items()))
            for b in t[group].values():
                b["values"] = dict(sorted(b["values"].items(), key=lambda kv: (-kv[1]["count"], kv[0])))
    tunables = dict(sorted(tunables.items()))

    data_resources: dict[str, dict] = {}
    for file, where, script, rtype, props in data_items:
        d = data_resources.setdefault(script, {"items": 0, "files": []})
        d["items"] += 1
        d["files"].append({"file": file, "where": where, "type": rtype,
                           "values": {k: to_json(v, packed_max=packed_max) for k, v in sorted(props.items())}})
    data_resources = dict(sorted(data_resources.items()))

    binary = sorted(r["path"] for r in rows if "binary" in r)
    binary_types = counted(collections.Counter(r["type"] or "PackedScene" for r in rows if "binary" in r))
    imported = _imported(root, str(ctx.opt("imported", "headers")).lower(), docs.bulk)
    models = counted(collections.Counter(m["model"] for r in rows for m in r.get("models", [])))
    scripts_table = {s: {"nodes": u["nodes"], "scenes": len(u["scenes"]), "node_types": counted(u["node_types"])}
                     for s, u in sorted(script_use.items())}
    n_scenes = sum(1 for r in rows if r["kind"] == "scene")
    n_exports = sum(len(t["exports"]) for t in tunables.values())
    changed = sum(1 for t in tunables.values() for b in t["exports"].values()
                  if b["default"] is not None and any(_differs(v, b["default"]) for v in b["values"]))
    summary = {"files": len(rows), "scenes": n_scenes, "resources": len(rows) - n_scenes,
               "nodes": sum(node_types.values()), "node_types": len(node_types), "scripts": len(scripts_table),
               "connections": len(connections), "instance_edges": len(edges), "tunable_scripts": len(tunables),
               "exported_props_set": n_exports, "exported_props_off_default": changed,
               "data_resource_scripts": len(data_resources), "binary_files": len(binary),
               "binary_errors": sum(1 for v in docs.errors.values() if v.startswith("binary:")),
               "models_instanced": len(models), "imported_files": imported.get("files", 0),
               "imported_binary": imported.get("binary", 0), "imported_parsed": imported.get("parsed", 0),
               "imported_errors": len(imported.get("errors", {})), "parse_errors": len(docs.errors)}
    data = {"root": root.relative_to(ctx.workspace).as_posix() if root.is_relative_to(ctx.workspace) else str(root),
            "files": rows, "node_types": counted(node_types), "scripts": scripts_table,
            "connections": connections, "signals": counted(signals),
            "instance_graph": {"edges": [{"from": a, "to": b, "count": n} for (a, b), n in sorted(edges.items())],
                               "most_instanced": most, "top_level": top},
            "tunables": tunables, "data_resources": data_resources,
            "unresolved_scripts": sorted(unresolved_scripts), "binary": binary, "binary_types": binary_types,
            "models": models, "imported": imported, "parse_errors": docs.errors}
    notes = []
    if binary:
        notes.append(f"{len(binary)} binary .scn/.res outside .godot/ (not converted to text by GDRE) read with the "
                     "stdlib RSRC reader")
    if imported.get("errors"):
        notes.append(f"{len(imported['errors'])} files of the import cache did not read: data.imported.errors")
    if unresolved_scripts:
        notes.append(f"{len(unresolved_scripts)} scripts without a source (decomp/cs, decomp/godot, project): their "
                     "properties are under 'unclassified'")
    return Report(data=data, markdown=_markdown(data, summary), summary=summary, notes=notes)


def _imported(root, mode: str, bulk) -> dict:
    """The import caches (``.godot/imported``, Godot 3 ``.import``): files by extension, binary resources by class
    (header of each; ``mode == "parse"`` reads each one fully), ``.ctex`` headers by stored/Image format."""
    from gst.engines.godot import rsrc

    if mode in ("0", "no", "off"):
        return {}
    by_ext: collections.Counter = collections.Counter()
    by_type: collections.Counter = collections.Counter()
    versions: collections.Counter = collections.Counter()
    compression: collections.Counter = collections.Counter()
    tex: collections.Counter = collections.Counter()
    tex_sizes: collections.Counter = collections.Counter()
    errors: dict[str, str] = {}
    n = binary = parsed = 0
    for d in IMPORT_DIRS:
        folder = root / d
        if not folder.is_dir():
            continue
        for p in sorted(folder.rglob("*")):
            if not p.is_file():
                continue
            n += 1
            by_ext[p.suffix.lower()] += 1
            res = f"res://{d}/{p.relative_to(folder).as_posix()}"
            try:
                with open(p, "rb") as fh:
                    head = fh.read(64)
                    if rsrc.is_binary(head):
                        data = head + fh.read()
                        binary += 1
                        info = rsrc.header_bytes(data)
                        by_type[info["type"]] += 1
                        versions[f"{info['ver_major']}.{info['ver_minor']} format {info['ver_format']}"] += 1
                        compression[info["compression"] or "none"] += 1
                        if mode == "parse":
                            rsrc.read_bytes(data, bulk=bulk)
                            parsed += 1
                    elif head[:4] == b"GDEC":
                        errors[res] = "encrypted (GDEC): not decrypted"
                        continue
                    elif head[:4] == b"GST2":
                        h = rsrc.ctex_header(head)
                        tex[f"{h['data_format']} {h['image_format']}"] += 1
                        tex_sizes[f"{h['width']}x{h['height']}"] += 1
            except (OSError, rsrc.RsrcError) as exc:
                errors[res] = str(exc)
    return {"files": n, "by_ext": counted(by_ext), "binary": binary, "parsed": parsed, "mode": mode,
            "binary_types": counted(by_type), "versions": counted(versions), "compression": counted(compression),
            "textures": counted(tex), "texture_sizes": counted(tex_sizes), "errors": errors}


def _differs(value_short: str, default: str) -> bool:
    d = default.strip().rstrip("fFdDmM") if default else default
    v = value_short.strip()
    try:
        return float(v) != float(d)
    except (TypeError, ValueError):
        return v.strip('"&') != (d or "").strip('"@')


def _clip(text: str, width: int = 80) -> str:
    """Markdown cells only (the JSON keeps the full value)."""
    return text if len(text) <= width else text[:width - 3] + "..."


def _markdown(data: dict, s: dict) -> str:
    md = [f"{s['files']} text files ({s['scenes']} scenes, {s['resources']} resources), {s['nodes']} nodes of "
          f"{s['node_types']} types, {s['scripts']} scripts on nodes, {s['connections']} signal connections, "
          f"{s['instance_edges']} instance edges. Per file: `analysis/godot_scenes/<res path>.json`.", "",
          "## Node types", "", md_table(["Type", "Nodes"], list(data["node_types"].items())[:40]), "",
          "## Scripts on nodes", "",
          md_table(["Script", "Nodes", "Scenes", "Node types"],
                   [(f"`{k}`", v["nodes"], v["scenes"], ", ".join(f"{t} {n}" for t, n in list(v["node_types"].items())[:3]))
                    for k, v in sorted(data["scripts"].items(), key=lambda kv: (-kv[1]["nodes"], kv[0]))[:60]]), "",
          "## Tunables (exported script variables set in scenes)", "",
          "Value = what the scene sets; default = initializer in the script source. `(N)` = how many nodes.", ""]
    rows = []
    for script, t in data["tunables"].items():
        for prop, b in t["exports"].items():
            vals = ", ".join(f"{_clip(v)} ({d['count']})" for v, d in list(b["values"].items())[:4])
            rows.append((f"`{script.rsplit('/', 1)[-1]}`", prop, b.get("default") or "-", vals))
    md += [md_table(["Script", "Property", "Default (code)", "Scene values"], rows), ""]
    md += ["## Signals", "", md_table(["Signal", "Connections"], list(data["signals"].items())[:40]), "",
           "## Most instanced scenes", "",
           md_table(["Scene", "Instanced by (scenes)"], [(f"`{k}`", v) for k, v in list(data["instance_graph"]["most_instanced"].items())[:40]]),
           "", f"Top-level scenes (not instanced by another scene): {len(data['instance_graph']['top_level'])}.", "",
           "## Data resources (custom Resource scripts)", "",
           md_table(["Script", "Items", "Example"],
                    [(f"`{k}`", v["items"], v["files"][0]["file"]) for k, v in data["data_resources"].items()]), "",
           "## Instanced models (scene -> model file -> imported scene)", "",
           md_table(["Scene", "Node", "Model", "Imported (what Godot loads)"],
                    [(f"`{r['path']}`", m["node"], f"`{m['model']}`", f"`{m['imported']}`" if m.get("imported") else "-")
                     for r in data["files"] for m in r.get("models", [])]), "",
           "## Binary resources", "",
           f"{s['binary_files']} binary .scn/.res in the project read with the RSRC reader ({s['binary_errors']} errors): "
           + (", ".join(f"{k} {v}" for k, v in data["binary_types"].items()) or "none") + "."]
    imp = data.get("imported") or {}
    if imp:
        md += ["", f"Import cache: {imp['files']} files, {imp['binary']} binary resources ({imp['mode']}: "
               f"{imp['parsed']} fully read, {len(imp['errors'])} errors).", "",
               md_table(["Class", "Files"], list(imp["binary_types"].items())), "",
               "Versions: " + (", ".join(f"{k} ({v})" for k, v in imp["versions"].items()) or "-")
               + "; compression: " + (", ".join(f"{k} {v}" for k, v in imp["compression"].items()) or "-") + ".", "",
               "Compressed textures (.ctex, stored format + Image format): "
               + (", ".join(f"{k} {v}" for k, v in imp["textures"].items()) or "-") + "."]
    return "\n".join(md)
