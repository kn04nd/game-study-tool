"""``gst analyze textures``: Texture2D sizes and formats, normal maps, atlas hints and textures per material family.

Port of ``repo/tex_scan.py`` (size/format distribution, the biggest textures) and the texture half of
``safari/char_materials.py`` (material -> slot -> texture), for every file:

- per texture: size, format, mips, color space, in-file or streamed (``.resS``) bytes;
- distributions: sizes (``WxH``), formats, power-of-two / square shares;
- normal maps: used in a normal slot of a Material (``_BumpMap``, ``*Normal*``) and/or named like one (``_N``,
  ``_Normal``, ``_nrm``); the two are reported apart (evidence each);
- atlas hints: one texture used by several Materials, ``atlas`` in the name, ``SpriteAtlas`` objects;
- per material family (the shader family from ``analysis/shaders.json`` when present): materials, textures,
  slots, median texture size.

Options: ``file`` (regex on install relpaths), ``atlas_min`` (materials sharing a texture to call it an atlas
hint, default 3), ``top`` (rows in the biggest/most-shared tables, default 25).
"""

from __future__ import annotations

import collections
import re

from gst.analysis import Report, UNITY, analysis, counted, md_table, stats
from gst.analysis.unity import basename
from gst.analysis.unity.shaders import family, iter_materials, iter_objects, key_of

NORMAL_SLOT = re.compile(r"(?i)(bump|normal|nrm)")
NORMAL_NAME = re.compile(r"(?i)(_n|_nrm|_norm|_normal|normalmap|_nm|_ddn)(\.\w+)?$|normal")
ATLAS_NAME = re.compile(r"(?i)atlas")


def texture_info(tex) -> dict:
    fmt = getattr(tex, "m_TextureFormat", None)
    fmt_name = getattr(fmt, "name", None)
    if fmt_name is None:
        try:
            from UnityPy.enums import TextureFormat

            fmt_name = TextureFormat(int(fmt)).name
        except Exception:
            fmt_name = str(fmt)
    stream = getattr(tex, "m_StreamData", None)
    streamed = int(getattr(stream, "size", 0) or 0) if stream is not None else 0
    w, h = int(getattr(tex, "m_Width", 0) or 0), int(getattr(tex, "m_Height", 0) or 0)
    return {"name": getattr(tex, "m_Name", "") or "", "w": w, "h": h, "format": fmt_name,
            "mips": int(getattr(tex, "m_MipCount", 0) or 0), "color_space": getattr(tex, "m_ColorSpace", None),
            "bytes": int(getattr(tex, "m_CompleteImageSize", 0) or 0), "streamed": streamed > 0}


def pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


@analysis("textures", engines=UNITY, needs=("unity.inventory", "unity.install"), uses=("analysis:shaders",),
          description="Textures: sizes, formats, normal maps, atlas hints, textures per material family", version=1,
          options={"file": "regex on install relpaths", "atlas_min": "materials sharing a texture to flag it (default 3)",
                   "top": "rows in the biggest / most shared tables (default 25)"})
def textures(ctx) -> Report:
    from gst.engines.unity import inventory

    fpat = re.compile(ctx.opt("file"), re.I) if ctx.opt("file") else None
    texs: dict[str, dict] = {}
    for rel, sf, obj in iter_objects(ctx, ("Texture2D",), fpat):
        try:
            info = texture_info(obj.read(check_read=False))
        except Exception as exc:
            from gst.engines.unity import loader

            info = {"name": loader.peek_name(obj), "error": f"{exc.__class__.__name__}: {exc}"}
        info.update(file=rel, path_id=obj.path_id)
        texs[key_of(str(sf.name), obj.path_id)] = info
    sh = ctx.load("shaders")
    index = (sh or {}).get("data", {}).get("shader_index", {})
    mats = []
    for rel, _sf, obj, m in iter_materials(ctx, fpat):
        m["file"] = rel
        m["shader_name"] = index.get(m["shader"] or "", "?" if sh else None)
        mats.append(m)
    atlases = sum(s.get("types", {}).get("SpriteAtlas", 0) for s in inventory.summaries(ctx.inventory_dir()))
    return build_report(texs, mats, have_shaders=sh is not None, atlas_min=int(ctx.opt("atlas_min", 3)),
                        top=int(ctx.opt("top", 25)), sprite_atlases=atlases)


def build_report(texs: dict, mats: list[dict], *, have_shaders: bool, atlas_min: int = 3, top: int = 25,
                 sprite_atlases: int = 0) -> Report:
    ok = {k: t for k, t in texs.items() if "error" not in t}
    dims = collections.Counter(f"{t['w']}x{t['h']}" for t in ok.values())
    fmts = collections.Counter(t["format"] for t in ok.values())
    users: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)  # texture key -> [(material, slot)]
    fams: dict[str, dict] = {}
    for m in mats:
        fam = family(m["shader_name"]) if m.get("shader_name") and m["shader_name"] != "?" else (
            "?" if have_shaders else "(no analysis/shaders.json)")
        f = fams.setdefault(fam, {"materials": 0, "textures": set(), "slots": collections.Counter(), "shaders": collections.Counter()})
        f["materials"] += 1
        f["shaders"][m.get("shader_name") or "?"] += 1
        for slot, k in m["textures"].items():
            users[k].append((m["name"], slot))
            f["textures"].add(k)
            f["slots"][slot] += 1
    normal_slot = {k for k, us in users.items() if any(NORMAL_SLOT.search(s) for _, s in us)}
    normal_name = {k for k, t in ok.items() if NORMAL_NAME.search(t["name"])}
    shared = {k: us for k, us in users.items() if len({m for m, _ in us}) >= atlas_min}
    atlas_named = {k for k, t in ok.items() if ATLAS_NAME.search(t["name"])}
    fam_out = {}
    for fam, f in sorted(fams.items(), key=lambda kv: (-kv[1]["materials"], kv[0])):
        sizes = [ok[k]["w"] * ok[k]["h"] for k in f["textures"] if k in ok]
        med = stats(sizes).get("median") if sizes else None
        fam_out[fam] = {"materials": f["materials"], "textures": len(f["textures"]),
                        "textures_resolved": sum(1 for k in f["textures"] if k in ok),
                        "normal_maps": len(f["textures"] & normal_slot), "slots": counted(f["slots"]),
                        "shaders": counted(f["shaders"]), "median_texels": med}
    rows = []
    for k in sorted(ok, key=lambda x: (ok[x]["file"], ok[x]["path_id"])):
        t = ok[k]
        rows.append({**t, "key": k, "materials": len({m for m, _ in users.get(k, [])}),
                     "slots": sorted({s for _, s in users.get(k, [])}), "normal_slot": k in normal_slot,
                     "normal_name": k in normal_name})
    unused = sum(1 for r in rows if not r["materials"])
    sq = sum(1 for t in ok.values() if t["w"] == t["h"])
    p2 = sum(1 for t in ok.values() if pow2(t["w"]) and pow2(t["h"]))
    summary = {"textures": len(texs), "errors": len(texs) - len(ok), "materials": len(mats),
               "streamed": sum(1 for t in ok.values() if t["streamed"]), "square": sq, "pow2": p2,
               "normal_maps_by_slot": len(normal_slot & set(ok)), "normal_maps_by_name": len(normal_name),
               "atlas_hints_shared": len(shared), "atlas_hints_named": len(atlas_named), "sprite_atlases": sprite_atlases,
               "not_used_by_materials": unused, "top_sizes": dict(list(counted(dims).items())[:8]),
               "formats": counted(fmts)}
    big = sorted(rows, key=lambda r: (-r["w"] * r["h"], r["name"]))[:top]
    most = sorted(((k, us) for k, us in shared.items()), key=lambda kv: (-len({m for m, _ in kv[1]}), kv[0]))[:top]
    data = {"textures": rows, "sizes": counted(dims), "formats": counted(fmts), "families": fam_out,
            "atlas_hints": {"shared": {k: {"name": ok.get(k, {}).get("name", "?"),
                                           "materials": len({m for m, _ in us}),
                                           "sample": sorted({m for m, _ in us})[:10]} for k, us in most},
                            "named": sorted(ok[k]["name"] for k in atlas_named)},
            "normal_maps": {"slot_and_name": len(normal_slot & normal_name), "slot_only": len(normal_slot - normal_name),
                            "name_only": len(normal_name - normal_slot)},
            "unresolved_texture_refs": sum(1 for k in users if k not in ok), "pixels": stats([t["w"] * t["h"] for t in ok.values()])}
    md = [f"{len(texs)} Texture2D ({summary['errors']} unreadable), {summary['streamed']} streamed from `.resS`; "
          f"{sq} square, {p2} power-of-two; {len(mats)} Materials; {unused} textures no Material uses (UI, sprites, "
          f"code-loaded). Normal maps: {summary['normal_maps_by_slot']} by slot, {summary['normal_maps_by_name']} by name "
          f"({data['normal_maps']['slot_and_name']} both). Atlas hints: {len(shared)} textures shared by >= {atlas_min} "
          f"materials, {len(atlas_named)} named *atlas*, {sprite_atlases} SpriteAtlas objects.", "",
          "## Sizes", "", md_table(["Size", "Textures"], list(counted(dims).items())[:20]), "",
          "## Formats", "", md_table(["Format", "Textures"], list(counted(fmts).items())), "",
          "## Per material family" + ("" if have_shaders else " (run `gst analyze shaders` first for shader names)"), "",
          md_table(["Family", "Materials", "Textures", "Normal maps", "Median texels", "Slots", "Shaders"],
                   [(f"`{f}`", e["materials"], e["textures"], e["normal_maps"],
                     f"{int(e['median_texels'])}" if e["median_texels"] else "-",
                     ", ".join(f"{s} {n}" for s, n in list(e["slots"].items())[:5]),
                     ", ".join(f"{s} {n}" for s, n in list(e["shaders"].items())[:3])) for f, e in fam_out.items()]), "",
          "## Biggest textures", "", md_table(["Texture", "File", "Size", "Format", "Mips", "Materials", "Slots"],
                                              [(f"`{r['name']}`", basename(r["file"]), f"{r['w']}x{r['h']}", r["format"],
                                                r["mips"], r["materials"], ", ".join(r["slots"][:3]) or "-") for r in big]), ""]
    if most:
        md += ["## Most shared textures (atlas hints)", "",
               md_table(["Texture", "Size", "Materials", "Sample materials"],
                        [(f"`{ok.get(k, {}).get('name', k)}`", f"{ok[k]['w']}x{ok[k]['h']}" if k in ok else "?",
                          len({m for m, _ in us}), ", ".join(sorted({m for m, _ in us})[:5])) for k, us in most]), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)
