"""``gst analyze shaders``: every Unity Shader (name, properties, keywords, subshaders/passes, fallback) and how many
Materials use each, grouped by family (the name before the first ``/``).

Port of ``peak/mdl_shaders.py`` (``m_ParsedForm``) and the shader half of ``repo/tex_scan.py``/``safari/char_materials.py``,
for every file instead of a hand-picked list. Signatures noted per shader: Amplify Shader Editor (``__dirty`` /
``_texcoord`` properties), Shader Graph (``Shader Graphs/`` name or ``_BUILTIN_``/``_QueueControl`` props), TextMeshPro,
post-processing (``Hidden/``).

Materials are read with the builtin typetree (``m_Shader`` PPtr, keywords, texture slots, floats/colors names) and
joined to the shader by ``(file, path id)``; ``data.shader_index`` maps those keys to names for other analyses
(``textures`` uses it).

Options: ``file`` (regex on install relpaths), ``materials=0`` (skip the Material pass).
"""

from __future__ import annotations

import collections
import re

from gst.analysis import Report, UNITY, analysis, counted, md_table
from gst.analysis.unity import UnityFile, basename, builtin, ptr


def key_of(sf_name: str, pid: int) -> str:
    return f"{basename(sf_name).lower()}|{pid}"


def ref_key(sf, value) -> str | None:
    """``file|pid`` of a PPtr read in ``sf`` (external file ids resolved by name), None for null."""
    from gst.engines.unity import loader

    fid, pid = ptr(value)
    if not pid:
        return None
    if fid == 0:
        return key_of(str(sf.name), pid)
    name = loader.external_name(sf, fid)
    return key_of(name, pid) if name else f"?{fid}|{pid}"


def family(name: str) -> str:
    return name.split("/", 1)[0] if "/" in name else name


def signatures(name: str, props: list[str]) -> list[str]:
    out = []
    ps = set(props)
    if "__dirty" in ps or "_texcoord" in ps:
        out.append("amplify")
    if name.startswith("Shader Graphs/") or any(p.startswith("_BUILTIN_") for p in ps) or "_QueueControl" in ps:
        out.append("shader_graph")
    if name.startswith("TextMeshPro/"):
        out.append("textmeshpro")
    if name.startswith("Hidden/"):
        out.append("hidden")
    if name.startswith(("Universal Render Pipeline/", "HDRP/", "HDRenderPipeline/")):
        out.append("srp")
    return out


PROP_TYPES = {0: "Color", 1: "Vector", 2: "Float", 3: "Range", 4: "Texture", 5: "Int"}


def shader_info(shader) -> dict:
    """Plain dict of a UnityPy Shader object (``m_ParsedForm``)."""
    pf = getattr(shader, "m_ParsedForm", None)
    if pf is None:
        return {"name": getattr(shader, "m_Name", "") or "", "parsed": False}
    props = [(p.m_Name, PROP_TYPES.get(int(getattr(p, "m_Type", -1)), str(getattr(p, "m_Type", "?"))))
             for p in getattr(getattr(pf, "m_PropInfo", None), "m_Props", None) or []]
    passes, lightmodes = [], collections.Counter()
    subs = getattr(pf, "m_SubShaders", None) or []
    kw = set(getattr(pf, "m_KeywordNames", None) or [])
    for ss in subs:
        for p in getattr(ss, "m_Passes", None) or []:
            state = getattr(p, "m_State", None)
            pname = getattr(state, "m_Name", "") if state is not None else ""
            passes.append(pname or getattr(p, "m_UseName", "") or "")
            tags = getattr(getattr(state, "m_Tags", None), "tags", None) if state is not None else None
            for t in tags or []:
                k, v = (t[0], t[1]) if isinstance(t, (list, tuple)) else (getattr(t, "first", None), getattr(t, "second", None))
                if k == "LightMode" and v:
                    lightmodes[v] += 1
    name = getattr(pf, "m_Name", "") or getattr(shader, "m_Name", "") or ""
    return {"name": name, "parsed": True, "props": [p[0] for p in props],
            "prop_types": counted(collections.Counter(t for _, t in props)),
            "keywords": sorted(kw), "subshaders": len(subs), "passes": len(passes),
            "pass_names": sorted({p for p in passes if p}), "lightmodes": counted(lightmodes),
            "fallback": getattr(pf, "m_FallbackName", "") or "", "custom_editor": getattr(pf, "m_CustomEditorName", "") or ""}


def material_info(tree: dict, sf) -> dict:
    """Shader key, keywords, texture slots (slot -> texture key), float/color names of a Material typetree."""
    sp = tree.get("m_SavedProperties") or {}

    def pairs(v):
        if isinstance(v, dict):
            return list(v.items())
        out = []
        for e in v or []:
            if isinstance(e, (list, tuple)) and len(e) == 2:
                out.append((e[0], e[1]))
            elif isinstance(e, dict):
                out.append((e.get("first"), e.get("second")))
        return out

    tex = {}
    for slot, val in pairs(sp.get("m_TexEnvs")):
        k = ref_key(sf, (val or {}).get("m_Texture"))
        if k:
            tex[str(slot)] = k
    kw = tree.get("m_ValidKeywords")
    if kw is None:
        kw = str(tree.get("m_ShaderKeywords") or "").split()
    return {"name": tree.get("m_Name") or "", "shader": ref_key(sf, tree.get("m_Shader")), "keywords": sorted(kw or []),
            "textures": dict(sorted(tex.items())), "floats": sorted(str(k) for k, _ in pairs(sp.get("m_Floats"))),
            "colors": sorted(str(k) for k, _ in pairs(sp.get("m_Colors"))), "queue": tree.get("m_CustomRenderQueue")}


def iter_objects(ctx, types: tuple[str, ...], fpat):
    """``(relpath, sf, obj)`` for every object of ``types`` in the inventoried files, one file at a time."""
    from gst.engines.unity import loader

    for rel in ctx.inventory_files(types):
        if fpat and not fpat.search(rel):
            continue
        ctx.log(f"reading {rel}")
        with UnityFile(ctx, rel) as uf:
            for sf in uf.files():
                for obj in list(sf.objects.values()):
                    if loader.type_name(obj) in types:
                        yield rel, sf, obj


def iter_materials(ctx, fpat):
    for rel, sf, obj in iter_objects(ctx, ("Material",), fpat):
        tree = builtin(obj)
        if tree is None:
            continue
        yield rel, sf, obj, material_info(tree, sf)


@analysis("shaders", engines=UNITY, needs=("unity.inventory", "unity.install"),
          description="Shaders: properties, keywords, passes, families and Material usage", version=1,
          options={"file": "regex on install relpaths", "materials": "0 = skip the Material pass (no usage counts)"})
def shaders(ctx) -> Report:
    fpat = re.compile(ctx.opt("file"), re.I) if ctx.opt("file") else None
    rows = {}
    for rel, sf, obj in iter_objects(ctx, ("Shader",), fpat):
        try:
            info = shader_info(obj.read(check_read=False))
        except Exception as exc:
            from gst.engines.unity import loader

            info = {"name": loader.peek_name(obj), "parsed": False, "error": f"{exc.__class__.__name__}: {exc}"}
        info.update(file=rel, cab=basename(str(sf.name)) if "/" in rel and rel.endswith(".bundle") else None,
                    path_id=obj.path_id)
        rows[key_of(str(sf.name), obj.path_id)] = info
    usage: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    mats = 0
    mat_kw: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    samples: dict[str, list[str]] = collections.defaultdict(list)
    if str(ctx.opt("materials", "1")) not in ("0", "false", "no"):
        for rel, _sf, _obj, m in iter_materials(ctx, fpat):
            mats += 1
            k = m["shader"] or "(none)"
            usage[k][rel] += 1
            for kw in m["keywords"]:
                mat_kw[k][kw] += 1
            if len(samples[k]) < 8:
                samples[k].append(m["name"])
    return build_report(rows, usage, mat_kw, samples, mats)


def build_report(rows: dict, usage: dict, mat_kw: dict, samples: dict, mats: int) -> Report:
    shaders_out = []
    for k in sorted(rows, key=lambda x: (rows[x]["name"], x)):
        s = dict(rows[k])
        s["key"] = k
        s["materials"] = sum(usage.get(k, {}).values())
        s["materials_by_file"] = dict(sorted(usage.get(k, {}).items()))
        s["material_keywords"] = counted(mat_kw.get(k, {}))
        s["material_sample"] = sorted(samples.get(k, []))
        s["family"] = family(s["name"])
        s["signatures"] = signatures(s["name"], s.get("props", []))
        shaders_out.append({kk: v for kk, v in s.items() if v is not None})
    unresolved = {k: sum(c.values()) for k, c in usage.items() if k not in rows}
    fams: dict[str, dict] = {}
    for s in shaders_out:
        f = fams.setdefault(s["family"], {"shaders": 0, "materials": 0, "names": []})
        f["shaders"] += 1
        f["materials"] += s["materials"]
        f["names"].append(s["name"])
    for f in fams.values():
        f["names"] = sorted(set(f["names"]))
    fams = dict(sorted(fams.items(), key=lambda kv: (-kv[1]["materials"], -kv[1]["shaders"], kv[0])))
    by_file = counted(collections.Counter(s["file"] for s in shaders_out))
    sigs = counted(collections.Counter(g for s in shaders_out for g in s["signatures"]))
    names = collections.Counter(s["name"] for s in shaders_out)
    summary = {"shaders": len(shaders_out), "unique_names": len(names), "families": len(fams), "materials": mats,
               "materials_unresolved_shader": sum(unresolved.values()), "by_file": by_file, "signatures": sigs}
    index = {s["key"]: s["name"] for s in shaders_out}
    data = {"shaders": shaders_out, "families": fams, "unresolved_material_shaders": counted(unresolved),
            "shader_index": index}
    md = [f"{len(shaders_out)} Shader objects ({len(names)} names) in {len(by_file)} files; {mats} Materials, "
          f"{sum(unresolved.values())} pointing to a shader outside the inventoried files. Signatures: "
          + (", ".join(f"{k} {v}" for k, v in sigs.items()) or "none") + ".", "",
          "## Shaders per file", "", md_table(["File", "Shaders"], [(f"`{f}`", n) for f, n in by_file.items()]), "",
          "## Families (name before the first `/`)", "",
          md_table(["Family", "Shaders", "Materials", "Names"],
                   [(f"`{f}`", e["shaders"], e["materials"], ", ".join(f"`{n}`" for n in e["names"][:6])
                     + (" ..." if len(e["names"]) > 6 else "")) for f, e in fams.items()]), "",
          "## Shaders by Material use", "",
          md_table(["Shader", "File", "Materials", "Props", "Keywords", "SubShaders/passes", "Pass names", "Fallback", "Signatures"],
                   [(f"`{s['name']}`", basename(s["file"]), s["materials"], len(s.get("props", [])), len(s.get("keywords", [])),
                     f"{s.get('subshaders', '-')}/{s.get('passes', '-')}", ", ".join(s.get("pass_names", [])[:4]) or "-",
                     s.get("fallback") or "-", ", ".join(s["signatures"]) or "-")
                    for s in sorted(shaders_out, key=lambda s: (-s["materials"], s["name"]))]), ""]
    top = [s for s in sorted(shaders_out, key=lambda s: (-s["materials"], s["name"])) if s.get("props")][:12]
    for s in top:
        md += [f"### `{s['name']}` ({s['materials']} materials)", "", "Properties: " + ", ".join(f"`{p}`" for p in s["props"]), ""]
        if s["material_keywords"]:
            md += ["Material keywords: " + ", ".join(f"`{k}` {v}" for k, v in list(s["material_keywords"].items())[:12]), ""]
    if unresolved:
        md += ["## Material shaders not resolved", "", md_table(["Shader ref", "Materials"],
                                                                 [(f"`{k}`", v) for k, v in counted(unresolved).items()][:30]), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)
