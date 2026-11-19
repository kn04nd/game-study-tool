"""``gst analyze gltf_summary``: batch summary of every ``.glb``/``.gltf`` in the workspace (any engine, stdlib only).

Port of ``pratfall/glb_all.py`` + ``glb_catalog.py`` + ``glb_summary.py``: per file the asset block (generator,
copyright), extensions, nodes, meshes (tris, verts, primitives, morph targets), materials, textures/images, skins
(joints), the bone-influence histogram (``JOINTS_0/WEIGHTS_0``), animations (channels by path, keys, duration,
smallest time step, interpolation), vertex colors, the POSITION bounding box (node transforms ignored) and the
scale of the inverse bind matrices; then families (folder of the file under the common root) with totals.

A glb recovered by GDRE carries GDRE's generator, not the author's: :func:`generator_note` says so.

Options: ``root`` (folder under the workspace to scan, default ``assets``), ``family_depth`` (folder levels that
name a family, default 2), ``detail`` (regex: also write the full per-file summary with bone names to
``analysis/gltf_summary/<file>.json``).
"""

from __future__ import annotations

import base64
import collections
import json
import math
import os
import re
import struct
from pathlib import Path

from gst.analysis import ANY, Report, analysis, counted, md_table, stats
from gst.common import contained

CT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
GLB_MAGIC = b"glTF"
SKIP_DIRS = {"analysis", ".partial", "_thumbs", "pcm"}


# --- reading ----------------------------------------------------------------------------------------

def load(path: Path | str, *, with_bin: bool = True) -> tuple[dict, list[bytes | None]]:
    """``(json, buffers)`` of a .glb or .gltf. Buffers: the GLB BIN chunk, data: URIs, files next to a .gltf
    (None when missing or when ``with_bin`` is False)."""
    path = Path(path)
    if path.suffix.lower() == ".glb":
        with open(path, "rb") as fh:
            head = fh.read(12)
            if head[:4] != GLB_MAGIC:
                raise ValueError("not a GLB (no glTF magic)")
            j, binc = None, None
            while True:
                ch = fh.read(8)
                if len(ch) < 8:
                    break
                ln, ty = struct.unpack("<II", ch)
                if ty == 0x4E4F534A:
                    j = json.loads(fh.read(ln).decode("utf-8"))
                    if not with_bin:
                        break
                elif ty == 0x004E4942 and with_bin:
                    binc = fh.read(ln)
                else:
                    fh.seek(ln, 1)
        if j is None:
            raise ValueError("GLB without a JSON chunk")
        bufs: list[bytes | None] = []
        for i, b in enumerate(j.get("buffers", [])):
            bufs.append(binc if (i == 0 and "uri" not in b) else _uri(path, b.get("uri"), with_bin))
        return j, bufs
    j = json.loads(path.read_bytes().decode("utf-8-sig"))
    return j, [_uri(path, b.get("uri"), with_bin) for b in j.get("buffers", [])]


def _uri(path: Path, uri: str | None, with_bin: bool) -> bytes | None:
    if not uri or not with_bin:
        return None
    if uri.startswith("data:"):
        try:
            return base64.b64decode(uri.split(",", 1)[1])
        except (ValueError, IndexError):
            return None
    p = contained(path.parent, uri.replace("%20", " "))  # the uri comes from the game: no absolute/UNC/.. reads
    if p is None:
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None


def read_accessor(j: dict, bufs: list, i: int) -> list[tuple]:
    """Every element of accessor ``i`` as a tuple (sparse accessors and normalization are ignored)."""
    a = j["accessors"][i]
    if "bufferView" not in a:
        return [(0,) * NC[a["type"]]] * a.get("count", 0)
    bv = j["bufferViews"][a["bufferView"]]
    buf = bufs[bv.get("buffer", 0)] if bv.get("buffer", 0) < len(bufs) else None
    if buf is None:
        raise ValueError("buffer not available")
    fmt, size = CT[a["componentType"]]
    n = NC[a["type"]]
    elem = size * n
    stride = bv.get("byteStride") or elem
    base = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    count = a["count"]
    st = struct.Struct("<" + fmt * n)
    if stride == elem:
        return list(st.iter_unpack(buf[base:base + elem * count]))
    return [st.unpack_from(buf, base + k * stride) for k in range(count)]


# --- one file ---------------------------------------------------------------------------------------

def names_of(j: dict) -> dict[str, list[str]]:
    """Names by kind (node, mesh, material, clip, bone, texture/image) for the DCC analysis."""
    nodes = j.get("nodes", [])
    bones = {i for s in j.get("skins", []) for i in s.get("joints", [])}
    return {"node": [n.get("name", "") for k, n in enumerate(nodes) if k not in bones and n.get("name")],
            "bone": [nodes[i].get("name", "") for i in sorted(bones) if i < len(nodes) and nodes[i].get("name")],
            "mesh": [m.get("name", "") for m in j.get("meshes", []) if m.get("name")],
            "material": [m.get("name", "") for m in j.get("materials", []) if m.get("name")],
            "clip": [a.get("name", "") for a in j.get("animations", []) if a.get("name")],
            "texture": [im.get("name") or os.path.basename(im.get("uri", "")) for im in j.get("images", [])
                        if im.get("name") or (im.get("uri") and not str(im["uri"]).startswith("data:"))]}


def node_signature(node: dict) -> list[str]:
    """FBX-style import signatures of one node: -90/+90 degrees on X, scale 100 / 0.01."""
    out = []
    r = node.get("rotation")
    if r and len(r) == 4:
        x, y, z, w = r
        if abs(y) < 1e-3 and abs(z) < 1e-3 and abs(abs(w) - 0.70710678) < 1e-3 and abs(abs(x) - 0.70710678) < 1e-3:
            out.append("rotX-90" if x * w < 0 else "rotX+90")
    s = node.get("scale")
    if s and len(s) == 3:
        if all(abs(v - 100) < 0.01 for v in s):
            out.append("scale100")
        elif all(abs(v - 0.01) < 1e-4 for v in s):
            out.append("scale0.01")
    return out


def summarize(path: Path | str, *, rel: str | None = None, bones: bool = False) -> dict:
    j, bufs = load(path)
    acc = j.get("accessors", [])
    nodes = j.get("nodes", [])
    r: dict = {"file": rel or Path(path).as_posix(), "bytes": os.path.getsize(path), "asset": j.get("asset") or {},
               "extensions": sorted(j.get("extensionsUsed", [])), "nodes": len(nodes)}
    meshes, tris, verts, vcol = [], 0, 0, False
    mn, mx = [math.inf] * 3, [-math.inf] * 3
    for m in j.get("meshes", []):
        t = v = 0
        prims = m.get("primitives", [])
        for p in prims:
            at = p.get("attributes", {})
            mode = p.get("mode", 4)
            if "POSITION" in at:
                pa = acc[at["POSITION"]]
                v += pa.get("count", 0)
                if "min" in pa and "max" in pa:
                    for k in range(3):
                        mn[k], mx[k] = min(mn[k], pa["min"][k]), max(mx[k], pa["max"][k])
            n_idx = acc[p["indices"]]["count"] if "indices" in p else (acc[at["POSITION"]]["count"] if "POSITION" in at else 0)
            if mode == 4:
                t += n_idx // 3
            elif mode in (5, 6):
                t += max(0, n_idx - 2)
            if "COLOR_0" in at:
                vcol = True
        meshes.append({"name": m.get("name"), "tris": t, "verts": v, "prims": len(prims),
                       "targets": len(prims[0].get("targets", [])) if prims else 0})
        tris += t
        verts += v
    r.update(meshes=meshes, n_meshes=len(meshes), tris=tris, verts=verts, vertex_colors=vcol,
             materials=[m.get("name") for m in j.get("materials", [])], textures=len(j.get("textures", [])),
             images=len(j.get("images", [])),
             bbox=[round(x, 3) for x in mn] + [round(x, 3) for x in mx] if mn[0] != math.inf else None)
    skins = j.get("skins", [])
    r["skins"] = [len(s.get("joints", [])) for s in skins]
    if bones:
        r["bones"] = [nodes[i].get("name") for s in skins for i in s.get("joints", []) if i < len(nodes)]
    anims = []
    for a in j.get("animations", []):
        kinds = collections.Counter()
        keys, dur = 0, 0.0
        for c in a.get("channels", []):
            kinds[c.get("target", {}).get("path", "?")] += 1
            s = a["samplers"][c["sampler"]]
            ia = acc[s["input"]]
            keys += ia.get("count", 0)
            if "max" in ia:
                dur = max(dur, ia["max"][0])
        dt_min = None
        if a.get("samplers"):
            try:
                ts = read_accessor(j, bufs, a["samplers"][0]["input"])[:51]
                dts = [round(ts[k + 1][0] - ts[k][0], 5) for k in range(len(ts) - 1)]
                dt_min = min([d for d in dts if d > 0], default=None)
            except (ValueError, KeyError, IndexError, struct.error):
                pass
        anims.append({"name": a.get("name"), "channels": len(a.get("channels", [])),
                      **{k: kinds.get(k, 0) for k in ("translation", "rotation", "scale", "weights")},
                      "keys": keys, "dur": round(dur, 3), "dt_min": dt_min,
                      "interp": sorted({s.get("interpolation", "LINEAR") for s in a.get("samplers", [])})})
    r["n_anims"] = len(anims)
    r["anims"] = anims
    hist: collections.Counter = collections.Counter()
    multi = tot = maxj = 0
    for m in j.get("meshes", []):
        for p in m.get("primitives", []):
            at = p.get("attributes", {})
            if "JOINTS_0" in at and "WEIGHTS_0" in at:
                try:
                    J, W = read_accessor(j, bufs, at["JOINTS_0"]), read_accessor(j, bufs, at["WEIGHTS_0"])
                except (ValueError, KeyError, struct.error):
                    r["skin_error"] = "weights not readable"
                    continue
                wmax = 255.0 if acc[at["WEIGHTS_0"]]["componentType"] == 5121 else (
                    65535.0 if acc[at["WEIGHTS_0"]]["componentType"] == 5123 else 1.0)
                for jj, ww in zip(J, W):
                    n = sum(1 for w in ww if w / wmax > 1e-4)
                    hist[n] += 1
                    tot += 1
                    multi += n > 1
                    maxj = max(maxj, max(jj))
                if "JOINTS_1" in at:
                    r["joints_1"] = True
    if tot:
        r.update(skin_verts=tot, infl_hist={str(k): v for k, v in sorted(hist.items())}, verts_multi_bone=multi,
                 max_joint_index=maxj)
    if skins and "inverseBindMatrices" in skins[0]:
        try:
            sc = []
            for m in read_accessor(j, bufs, skins[0]["inverseBindMatrices"]):
                sc.append([math.sqrt(m[c * 4] ** 2 + m[c * 4 + 1] ** 2 + m[c * 4 + 2] ** 2) for c in range(3)])
            if sc:
                r["ibm_scale_min"] = round(min(min(s) for s in sc), 4)
                r["ibm_scale_max"] = round(max(max(s) for s in sc), 4)
        except (ValueError, KeyError, struct.error):
            pass
    sig = collections.Counter(s for n in nodes for s in node_signature(n))
    if sig:
        r["node_signatures"] = dict(sorted(sig.items()))
    dup = [n.get("name", "") for n in nodes if re.search(r"[._]0\d\d$", n.get("name", ""))]
    if dup:
        r["dup_suffix_nodes"] = len(dup)
    return r


def generator_note(generators: dict) -> str | None:
    if any("gdre" in g.lower() for g in generators):
        return ("asset.generator is GDRE Tools: the glbs were re-exported by GDRE from Godot's imported scenes, so the "
                "generator names the recovery tool, not the author's DCC")
    return None


# --- the analysis -----------------------------------------------------------------------------------

def find_files(root: Path) -> list[Path]:
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(files):
            if f.lower().endswith((".glb", ".gltf")):
                out.append(Path(dirpath) / f)
    return out


def family_of(rel: str, base: str, depth: int) -> str:
    parent = os.path.dirname(rel)
    if base and (parent == base or parent.startswith(base + "/")):
        parent = parent[len(base):].lstrip("/")
    parts = [p for p in parent.split("/") if p]
    return "/".join(parts[:depth]) or "."


def families(rows: list[dict], depth: int) -> dict:
    ok = [r for r in rows if "error" not in r]
    dirs = [os.path.dirname(r["file"]) for r in ok]
    base = os.path.commonpath(dirs).replace("\\", "/") if dirs else ""
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in ok:
        groups[family_of(r["file"], base, depth)].append(r)
    out = {}
    for k in sorted(groups):
        L = groups[k]
        tr = sorted(r["tris"] for r in L)
        sk = [r for r in L if r["skins"]]
        an = [r["n_anims"] for r in L]
        out[k] = {"files": len(L), "tris": sum(tr), "tris_median": tr[len(tr) // 2], "verts": sum(r["verts"] for r in L),
                  "skinned": len(sk), "joints": counted(collections.Counter(r["skins"][0] for r in sk)),
                  "anims_min": min(an), "anims_max": max(an), "vertex_colors": sum(1 for r in L if r["vertex_colors"]),
                  "bytes": sum(r["bytes"] for r in L)}
    return {"base": base, "families": out}


@analysis("gltf_summary", engines=(ANY,), description="Summary of every glb/gltf in the workspace: meshes, tris, skins, "
          "influences, animations, bbox, generator, families", version=1,
          options={"root": "folder under the workspace to scan (default assets)",
                   "family_depth": "folder levels under the common root that name a family (default 2)",
                   "detail": "regex on file paths: also write the full summary (bone names) to analysis/gltf_summary/"})
def gltf_summary(ctx) -> Report:
    import shutil

    root = ctx.workspace / str(ctx.opt("root", "assets"))
    depth = int(ctx.opt("family_depth", 2))
    detail = re.compile(ctx.opt("detail"), re.I) if ctx.opt("detail") else None
    if ctx.side_dir().is_dir():
        shutil.rmtree(ctx.side_dir(), ignore_errors=True)
    ctx.use("gltf files", root)
    rows = []
    for p in find_files(root) if root.is_dir() else []:
        rel = p.relative_to(ctx.workspace).as_posix()
        try:
            want = bool(detail and detail.search(rel))
            row = summarize(p, rel=rel, bones=want)
            if want:
                ctx.write_side_json(row, rel.replace("/", "__") + ".json")
                row.pop("bones", None)
        except Exception as exc:  # one broken file never stops the batch
            row = {"file": rel, "error": f"{exc.__class__.__name__}: {exc}"}
        rows.append(row)
    return build_report(rows, depth)


def build_report(rows: list[dict], depth: int = 2) -> Report:
    ok = [r for r in rows if "error" not in r]
    gens = collections.Counter(str(r["asset"].get("generator") or "?") for r in ok)
    fam = families(rows, depth)
    infl: collections.Counter = collections.Counter()
    for r in ok:
        for k, v in (r.get("infl_hist") or {}).items():
            infl[k] += v
    joints = collections.Counter(n for r in ok for n in r["skins"])
    summary = {"files": len(rows), "errors": len(rows) - len(ok), "tris": sum(r["tris"] for r in ok),
               "verts": sum(r["verts"] for r in ok), "skinned": sum(1 for r in ok if r["skins"]),
               "animated": sum(1 for r in ok if r["n_anims"]), "anims": sum(r["n_anims"] for r in ok),
               "vertex_colors": sum(1 for r in ok if r["vertex_colors"]), "families": len(fam["families"]),
               "generators": counted(gens)}
    notes = [n for n in [generator_note(gens)] if n]
    data = {"files": rows, "families": fam["families"], "family_base": fam["base"], "generators": counted(gens),
            "extensions": counted(collections.Counter(e for r in ok for e in r["extensions"])),
            "joint_counts": {str(k): v for k, v in sorted(joints.items())},
            "influences_hist": {k: infl[k] for k in sorted(infl, key=int)},
            "tris_stats": stats([r["tris"] for r in ok])}
    md = [f"{summary['files']} glTF files ({summary['errors']} unreadable), {summary['tris']:,} tris, {summary['verts']:,} "
          f"verts; {summary['skinned']} skinned, {summary['animated']} with animations ({summary['anims']} clips), "
          f"{summary['vertex_colors']} with vertex colors. Generators: "
          + ", ".join(f"`{g}` {n}" for g, n in gens.most_common()) + ".", ""]
    md += [f"## Families (folders under `{fam['base'] or '.'}`)", "",
           md_table(["Family", "Files", "Tris", "Tris median", "Verts", "Skinned", "Joints (count: files)", "Clips min-max",
                     "Vertex colors", "KiB"],
                    [(f"`{k}`", e["files"], e["tris"], e["tris_median"], e["verts"], e["skinned"],
                      ", ".join(f"{j}: {n}" for j, n in e["joints"].items()) or "-", f"{e['anims_min']}-{e['anims_max']}",
                      e["vertex_colors"], e["bytes"] // 1024) for k, e in fam["families"].items()]), ""]
    if infl:
        md += ["Bone influences per skinned vertex: " + ", ".join(f"{k}: {v}" for k, v in data["influences_hist"].items()), ""]
    big = sorted(ok, key=lambda r: -r["tris"])[:25]
    md += ["## Largest files", "", md_table(["File", "Tris", "Verts", "Meshes", "Materials", "Joints", "Clips", "IBM scale"],
                                            [(f"`{r['file']}`", r["tris"], r["verts"], r["n_meshes"], len(r["materials"]),
                                              ",".join(map(str, r["skins"])) or "-", r["n_anims"],
                                              f"{r['ibm_scale_min']}-{r['ibm_scale_max']}" if "ibm_scale_min" in r else "-")
                                             for r in big]), ""]
    skinned = [r for r in ok if r["skins"]][:40]
    if skinned:
        md += ["## Skinned files", "", md_table(["File", "Joints", "Skin verts", "Influences", "Clips", "Clip names (first 6)"],
                                                [(f"`{r['file']}`", ",".join(map(str, r["skins"])), r.get("skin_verts", 0),
                                                  ", ".join(f"{k}:{v}" for k, v in (r.get("infl_hist") or {}).items()) or "-",
                                                  r["n_anims"], ", ".join(str(a["name"]) for a in r["anims"][:6]) or "-")
                                                 for r in skinned]), ""]
    errs = [r for r in rows if "error" in r]
    if errs:
        md += ["## Unreadable", ""] + [f"- `{r['file']}`: {r['error']}" for r in errs] + [""]
    return Report(data=data, markdown="\n".join(md), summary=summary, notes=notes)
