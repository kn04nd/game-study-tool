"""``gst analyze godot_shaders``: every shader of the recovered Godot project and who uses it.

- Shader files: ``.gdshader``, ``.gdshaderinc`` (kind ``include``) and Godot 3 ``.shader``.
- Shader resources inside scenes/resources (text or binary): a ``Shader`` sub-resource or ``.tres``/``.res`` main
  resource with ``code`` (kind ``embedded``/``resource``), and ``VisualShader`` graphs (kind ``visual_shader``: mode,
  node classes, parameter nodes as uniforms; the generated code is not stored in the resource).
- ``ShaderMaterial`` resources (``.tres`` files and sub-resources): their shader, ``shader_parameter/*`` values
  (Godot 3: ``shader_param/*``), ``next_pass``, and the files that use them (the file holding an embedded material,
  every file with an ext_resource to a material file).

Per shader: shader_type, render_modes, uniforms (scope, type, hints, default, group), includes, functions, a rough
feature list by keywords (:mod:`gst.engines.godot.shader`; inference), materials using it and the scenes/resources
those materials reach. Materials of imported models (glb) are StandardMaterial3D, not shaders, and are not listed.
"""

from __future__ import annotations

import collections
import re

from gst.analysis import Report, analysis, counted, md_table
from gst.analysis.godot import GEOMETRY_TYPES, SCENE_EXTS, Docs, bulk_option, iter_files, project_root

SHADER_EXTS = (".gdshader", ".gdshaderinc", ".shader")
PARAM_PREFIXES = ("shader_parameter/", "shader_param/")


@analysis("godot_shaders", engines=("godot",), needs=("godot.project",), version=1, category="material_shader",
          description="Godot shaders: .gdshader files and embedded/visual shaders, uniforms, materials, users, features",
          options={"name": "regex on the res:// path (only matching files)",
                   "bulk": "binary packed arrays of this many bytes or more are counted, not decoded (default 4096)"})
def godot_shaders(ctx) -> Report:
    from gst.engines.godot import shader as sh
    from gst.engines.godot.tres import Ref, ref_path, short

    root = project_root(ctx.workspace)
    rx = re.compile(ctx.opt("name"), re.I) if ctx.opt("name") else None
    docs = Docs(root, bulk=bulk_option(ctx), skip=GEOMETRY_TYPES)
    shaders: dict[str, dict] = {}
    materials: list[dict] = []
    refs: dict[str, set] = collections.defaultdict(set)  # res path -> files with an ext_resource to it
    errors: dict[str, str] = {}

    for res, p in iter_files(root, SHADER_EXTS):
        if rx and not rx.search(res):
            continue
        try:
            info = sh.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError) as exc:
            errors[res] = str(exc)
            continue
        kind = "include" if p.suffix.lower() == ".gdshaderinc" else "file"
        shaders[res] = {"name": res, "kind": kind, "file": res, **info}

    for res, _p in iter_files(root, SCENE_EXTS):
        doc = docs.get(res)
        if doc is None:
            continue
        for e in doc["ext_resources"].values():
            if e.get("path"):
                refs[str(e["path"])].add(res)
        items = [(rid, sr["type"], sr["props"]) for rid, sr in doc["sub_resources"].items()]
        if doc.get("resource") is not None:
            items.append((None, doc["resource"]["type"], doc["resource"]["props"]))
        sub_types = {rid: sr["type"] for rid, sr in doc["sub_resources"].items()}
        sub_props = {rid: sr["props"] for rid, sr in doc["sub_resources"].items()}
        local = {}  # sub id -> shader name in this file
        for rid, rtype, props in items:
            name = res if rid is None else f"{res}::SubResource({rid})"
            if rtype == "Shader" and isinstance(props.get("code"), str):
                if rx and not rx.search(res):
                    continue
                shaders[name] = {"name": name, "kind": "resource" if rid is None else "embedded", "file": res,
                                 **sh.parse(props["code"])}
                local[rid] = name
            elif rtype == "VisualShader":
                if rx and not rx.search(res):
                    continue
                shaders[name] = {"name": name, "kind": "visual_shader", "file": res, **sh.visual(props, sub_types, sub_props)}
                local[rid] = name
        for rid, rtype, props in items:
            if rtype != "ShaderMaterial":
                continue
            where = res if rid is None else f"{res}::SubResource({rid})"
            s = props.get("shader")
            if isinstance(s, Ref):
                shader = ref_path(doc, s) if s.kind == "ext" else local.get(s.id, f"{res}::SubResource({s.id})")
            else:
                shader = None
            params = {k.split("/", 1)[1]: short(v, doc) for k, v in props.items() if k.startswith(PARAM_PREFIXES)}
            m = {"where": where, "file": res, "embedded": rid is not None, "shader": shader, "params": params}
            nxt = props.get("next_pass")
            if isinstance(nxt, Ref):
                m["next_pass"] = ref_path(doc, nxt) or f"{res}::SubResource({nxt.id})"
            if "render_priority" in props:
                m["render_priority"] = props["render_priority"]
            materials.append(m)
        del doc

    # users: an embedded material is used by its file; a material file by the files that reference it
    for m in materials:
        m["used_by"] = [m["file"]] if m["embedded"] else sorted(refs.get(m["file"], ()))
    by_shader: dict[str, list] = collections.defaultdict(list)
    for m in materials:
        if m["shader"]:
            by_shader[m["shader"]].append(m)
    for name, s in shaders.items():
        ms = by_shader.get(name, [])
        s["materials"] = len(ms)
        users = {u for m in ms for u in m["used_by"]}
        if s["kind"] in ("file", "resource", "include"):
            users |= {f for f in refs.get(s["file"], ()) if f not in {m["file"] for m in ms}}
        s["used_by"] = sorted(users)
        if s["kind"] == "include":
            s["included_by"] = sorted(n for n, o in shaders.items() if name in (o.get("includes") or []))
    rows = sorted(shaders.values(), key=lambda s: (-s["materials"], s["name"]))
    feats: collections.Counter = collections.Counter(f for s in rows for f in s["features"])
    types: collections.Counter = collections.Counter(s["shader_type"] or "-" for s in rows if s["kind"] != "include")
    kinds: collections.Counter = collections.Counter(s["kind"] for s in rows)
    hints: collections.Counter = collections.Counter(h.split("(", 1)[0] for s in rows for u in s["uniforms"] for h in u["hints"])
    unknown = sorted({m["shader"] for m in materials if m["shader"] and m["shader"] not in shaders})
    summary = {"shaders": len(rows), "files": kinds.get("file", 0), "includes": kinds.get("include", 0),
               "embedded": kinds.get("embedded", 0) + kinds.get("resource", 0), "visual": kinds.get("visual_shader", 0),
               "uniforms": sum(len(s["uniforms"]) for s in rows), "materials": len(materials),
               "materials_without_shader": sum(1 for m in materials if not m["shader"]),
               "shaders_unused": sum(1 for s in rows if s["kind"] != "include" and not s["materials"] and not s["used_by"]),
               "parse_errors": len(docs.errors) + len(errors)}
    data = {"shaders": rows, "materials": materials, "shader_types": counted(types), "kinds": counted(kinds),
            "features": counted(feats), "uniform_hints": counted(hints), "shaders_not_found": unknown,
            "parse_errors": {**docs.errors, **errors}}
    notes = ["features are keyword inferences over the shader text (names, built-ins, render modes), not a render test"]
    if unknown:
        notes.append(f"{len(unknown)} materials point at shaders outside the recovered project: data.shaders_not_found")
    return Report(data=data, markdown=_markdown(data, summary), summary=summary, notes=notes)


def _markdown(d: dict, s: dict) -> str:
    md = [f"{s['shaders']} shaders ({s['files']} files, {s['includes']} includes, {s['embedded']} embedded, "
          f"{s['visual']} visual), {s['uniforms']} uniforms, {s['materials']} ShaderMaterials. Features are keyword "
          "inferences.", "",
          "Shader types: " + (", ".join(f"{k} {v}" for k, v in d["shader_types"].items()) or "-") + ".", "",
          "Features: " + (", ".join(f"{k} {v}" for k, v in d["features"].items()) or "-") + ".", "",
          "## Shaders", "",
          md_table(["Shader", "Kind", "Type", "Render modes", "Uniforms", "Materials", "Used by (files)", "Features"],
                   [(f"`{x['name']}`", x["kind"], x["shader_type"] or "-", ", ".join(x["render_modes"]) or "-",
                     len(x["uniforms"]), x["materials"], len(x["used_by"]), ", ".join(x["features"]) or "-")
                    for x in d["shaders"]]), ""]
    for x in d["shaders"]:
        if not x["uniforms"]:
            continue
        md += [f"### `{x['name']}`", "",
               md_table(["Uniform", "Type", "Scope", "Hints", "Default", "Group"],
                        [(u["name"], u["type"], u["scope"] or "-", ", ".join(u["hints"]) or "-", u["default"] or "-",
                          u["group"] or "-") for u in x["uniforms"]]), ""]
    return "\n".join(md)
