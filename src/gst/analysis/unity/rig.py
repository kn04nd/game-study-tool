"""``gst analyze rig``: skinning rigs of the detailed prefabs (ports of ``peak/mdl_rig.py``, ``safari-tv/char_meshes.py``).

Per SkinnedMeshRenderer: mesh, bone count, root bone, the bone hierarchy (parent, depth), bindposes (scales, first
bindpose rotation: an FBX import shows up as -90 X / scale 0.01 or 100), max influences and the influences-per-vertex
histogram (vertex data decoded with UnityPy's MeshHandler, which works when ``m_Skin`` is None), the weight each
bone gets, deform bones (weight > 0) vs helper bones (none), blendshape names; per prefab, Animation Rigging / Final
IK / Unity constraint components with their targets named.

The bone index -> name mapping is validated two ways, because a wrong mapping makes the deform/helper split
meaningless: (1) ``Mesh.m_BoneNameHashes`` (CRC32 of each bone's name or path, in the mesh's bone order) against the
SMR's ``m_Bones`` names; (2) geometry: each bone's position from its inverse bindpose against the centroid of the
vertices it dominates (median distance / mesh size). ``mapping_ok`` = hashes match (when present) and the
geometric median is under 0.25.
"""

from __future__ import annotations

import collections
import json
import math
import shutil

from gst.analysis import Report, UNITY, analysis, counted, md_table
from gst.analysis.unity import Resolver, UnityFile, basename, builtin, ptr
from gst.analysis.unity.animcore import crc, file_filter, invert_affine, matrix_scale_rotation, short_float

CONSTRAINT_SCRIPTS = {
    # Animation Rigging
    "TwoBoneIKConstraint", "MultiAimConstraint", "ChainIKConstraint", "DampedTransform", "MultiParentConstraint",
    "MultiPositionConstraint", "MultiRotationConstraint", "MultiReferentialConstraint", "OverrideTransform",
    "TwistChainConstraint", "TwistCorrection", "BlendConstraint", "Rig", "RigBuilder", "BoneRenderer",
    # Final IK
    "LimbIK", "FullBodyBipedIK", "AimIK", "CCDIK", "FABRIK", "FABRIKRoot", "BipedIK", "LookAtIK", "TrigonometricIK",
    "GrounderFBBIK", "GrounderIK", "GrounderBipedIK", "VRIK", "LegIK", "ArmIK", "HitReaction", "Recoil",
}
CONSTRAINT_TYPES = {"ParentConstraint", "PositionConstraint", "RotationConstraint", "AimConstraint", "LookAtConstraint",
                    "ScaleConstraint"}
SKIP_KEYS = {"m_GameObject", "m_Script", "m_ObjectHideFlags", "m_CorrespondingSourceObject", "m_PrefabInstance",
             "m_PrefabAsset", "m_EditorHideFlags", "m_EditorClassIdentifier"}


def _mat(m) -> dict:
    if isinstance(m, dict):
        return {k: float(v) for k, v in m.items() if k.startswith("e")}
    return {f"e{r}{c}": float(getattr(m, f"e{r}{c}", 0.0)) for r in range(4) for c in range(4)}


def skin_stats(weights, indices, vertices, bindposes: list[dict]) -> dict:
    """Influences histogram, per-bone weight / dominated vertices, geometric check of the index mapping."""
    hist: collections.Counter = collections.Counter()
    wsum: dict[int, float] = collections.defaultdict(float)
    dom: dict[int, list] = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    dev = 0.0
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for k, (ws, bs) in enumerate(zip(weights or [], indices or [])):
        n = 0
        best, bw = None, -1.0
        for w, b in zip(ws, bs):
            w = float(w)
            if w > 1e-4:
                n += 1
                wsum[int(b)] += w
                if w > bw:
                    best, bw = int(b), w
        hist[n] += 1
        dev = max(dev, abs(sum(float(w) for w in ws) - 1.0)) if n else dev
        if best is not None and vertices is not None and k < len(vertices):
            v = vertices[k]
            d = dom[best]
            d[0] += 1
            for a in range(3):
                d[a + 1] += float(v[a])
                lo[a] = min(lo[a], float(v[a]))
                hi[a] = max(hi[a], float(v[a]))
    out = {"influences_hist": {str(k): v for k, v in sorted(hist.items())}, "max_influences": max(hist) if hist else 0,
           "max_weight_sum_deviation": round(dev, 4), "bone_weight": {b: round(w, 3) for b, w in sorted(wsum.items())},
           "dominant_vertices": {b: d[0] for b, d in sorted(dom.items())}}
    size = math.sqrt(sum((hi[a] - lo[a]) ** 2 for a in range(3))) if dom else 0.0
    positions = []
    for bp in bindposes:
        inv = invert_affine(bp)
        positions.append([inv[r][3] for r in range(3)])
    centroids = {b: [d[a + 1] / d[0] for a in range(3)] for b, d in dom.items() if d[0]}
    geo = geometry_check(centroids, positions, size)
    if geo:
        out["bone_distance"] = geo.pop("distances")
        out["bone_rank"] = geo.pop("ranks")
        out["geometry"] = geo
    return out


def geometry_check(centroids: dict[int, list], positions: list[list], size: float) -> dict | None:
    """Bone b's position (inverse bindpose) vs the centroid of the vertices it dominates: the rank of bone b among
    all bone positions sorted by distance to that centroid. With the right index -> bone mapping the bone itself (or
    a neighbour of the chain: rank <= 2) is among the nearest for most bones; a wrong mapping gives ranks spread over
    all bones (expected ``near`` = 3/n). ``near`` = fraction of bones with rank <= 2; ``median`` = distance over the
    mesh size. Rigs with fewer than 6 weighted bones are not judged (any mapping looks near)."""
    n = len(positions)
    ids = [b for b in centroids if 0 <= b < n]
    if n < 2 or not ids:
        return None

    def d(p, c) -> float:
        return math.sqrt(sum((p[a] - c[a]) ** 2 for a in range(3)))

    ranks, dists = {}, {}
    for b in ids:
        cen = centroids[b]
        own = d(positions[b], cen)
        ranks[b] = sum(1 for j in range(n) if j != b and d(positions[j], cen) < own)
        dists[b] = own / (size or 1.0)
    vals = sorted(dists.values())
    near = sum(1 for r in ranks.values() if r <= 2) / len(ranks)
    return {"bones": len(ids), "near": round(near, 3), "random_near": round(min(1.0, 3 / n), 3),
            "median": round(vals[len(vals) // 2], 4), "mesh_size": round(size, 4),
            "judged": len(ids) >= 6, "distances": {b: round(dists[b], 4) for b in sorted(ids)},
            "ranks": {b: ranks[b] for b in sorted(ids)}}


def hash_check(hashes: list[int], bone_nodes: list[dict | None], root_hash: int = 0, root_index: int | None = None) -> dict:
    """``Mesh.m_BoneNameHashes[i]`` (CRC32 of the bone path in the imported model) against the SMR's bone ``i``:
    CRC32 of its name or of a path suffix (only matches when the prefab kept the model's hierarchy), and whether
    ``m_RootBoneNameHash`` sits at the index of the SMR's root bone."""
    if not hashes:
        return {"hashes": 0}
    def cands(node: dict) -> set[int]:
        parts = node["path"].strip("/").split("/")
        return {crc(node["name"])} | {crc("/".join(parts[i:])) for i in range(len(parts))}

    owner: dict[int, int] = {}
    for i, node in enumerate(bone_nodes):
        if node is not None:
            for c in cands(node):
                owner.setdefault(c, i)
    match = other = compared = 0
    for i, (h, node) in enumerate(zip(hashes, bone_nodes)):
        if node is None:
            continue
        compared += 1
        h = int(h) & 0xFFFFFFFF
        if h in cands(node):
            match += 1
        elif h in owner:
            other += 1  # the hash names another bone of the SMR: the index order is off
    out = {"hashes": len(hashes), "compared": compared, "match": match, "match_other_index": other,
           "count_ok": len(hashes) == len(bone_nodes)}
    hs = [int(h) & 0xFFFFFFFF for h in hashes]
    if root_hash and root_index is not None and (int(root_hash) & 0xFFFFFFFF) in hs:
        out["root_index_ok"] = hs.index(int(root_hash) & 0xFFFFFFFF) == root_index
    return out


def mapping_verdict(hc: dict, geo: dict | None, out_of_range: list) -> bool | None:
    """True/False when there is evidence, None when nothing could be checked."""
    if out_of_range:
        return False
    votes = []
    if hc.get("hashes"):
        if not hc.get("count_ok"):
            return False
        if hc.get("match_other_index", 0) > hc.get("match", 0):
            return False
        if hc.get("compared") and hc.get("match", 0) >= 0.9 * hc["compared"]:
            votes.append(True)  # below 90 %: the prefab renamed bones of the model, no vote
        if "root_index_ok" in hc:
            votes.append(hc["root_index_ok"])
    if geo and geo.get("judged"):
        votes.append(geo["near"] >= 0.5 and geo["near"] > 2 * geo["random_near"])
    return all(votes) if votes else None


def mesh_rig(reader) -> dict:
    """Skinning facts of a Mesh reader (cached by the caller)."""
    from gst.analysis.unity import mesh_stats
    from gst.engines.unity import loader

    out = {k: v for k, v in mesh_stats(reader).items() if k in ("mesh", "file", "path_id", "verts", "tris", "submeshes",
                                                                   "bindposes", "blendshapes", "error")}
    if "error" in out:
        return out
    try:
        m = reader.read(check_read=False)
    except Exception as exc:
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        return out
    shapes = getattr(m, "m_Shapes", None)
    out["blendshape_names"] = [ch.name for ch in (getattr(shapes, "channels", None) or [])]
    bps = [_mat(b) for b in getattr(m, "m_BindPose", None) or []]
    out["bone_name_hashes"] = [int(h) for h in getattr(m, "m_BoneNameHashes", None) or []]
    out["root_bone_name_hash"] = int(getattr(m, "m_RootBoneNameHash", 0) or 0)
    if bps:
        srs = [matrix_scale_rotation(b) for b in bps]
        out["bindpose_scales"] = sorted({round(s, 4) for sr in srs for s in sr["scale"]})[:8]
        out["bindpose0"] = srs[0]
    out["_bindposes"] = bps
    if bps:
        try:
            h = loader.mesh_handler(m)
            out.update(skin_stats(h.m_BoneWeights, h.m_BoneIndices, h.m_Vertices, bps))
        except Exception as exc:
            out["skin_error"] = f"{exc.__class__.__name__}: {exc}"
    return out


def refs_named(x, names: dict[int, str], depth: int = 0):
    """A MonoBehaviour typetree with same-file PPtrs replaced by node paths (bounded)."""
    if depth > 8:
        return "..."
    if isinstance(x, dict):
        if "m_PathID" in x and "m_FileID" in x and len(x) <= 3:
            fid, pid = ptr(x)
            if not pid:
                return None
            return names.get(pid, f"#{pid}") if fid == 0 else f"ext{fid}:{pid}"
        return {k: refs_named(v, names, depth + 1) for k, v in x.items() if k not in SKIP_KEYS}
    if isinstance(x, list):
        return [refs_named(i, names, depth + 1) for i in x[:64]] + ([f"... {len(x)}"] if len(x) > 64 else [])
    if isinstance(x, float):
        return short_float(x, 4)
    return x


def smr_row(tree: dict, mesh: dict | None, nodes_by_tf: dict[int, dict], node: dict) -> tuple[dict, list[dict]]:
    bones_pp = tree.get("m_Bones") or []
    bone_nodes = []
    for b in bones_pp:
        fid, pid = ptr(b)
        bone_nodes.append(nodes_by_tf.get(pid) if fid == 0 else None)
    rb = ptr(tree.get("m_RootBone"))
    root = nodes_by_tf.get(rb[1]) if rb[1] and rb[0] == 0 else None
    row = {"node": node["path"], "bones": len(bones_pp), "root_bone": root["name"] if root else None,
           "enabled": tree.get("m_Enabled") not in (0, False), "quality": tree.get("m_Quality"),
           "update_when_offscreen": bool(tree.get("m_UpdateWhenOffscreen")), "hash_check": {}, "geometry_check": None,
           "mapping_ok": None}
    bones_out: list[dict] = []
    if mesh is None:
        row["mesh"] = None  # assigned at runtime (no mesh in the prefab)
        return row, bones_out
    row["mesh"] = {k: mesh.get(k) for k in ("mesh", "file", "path_id", "verts", "tris", "submeshes", "bindposes",
                                            "blendshapes", "max_influences", "influences_hist", "bindpose_scales",
                                            "bindpose0", "error", "skin_error") if mesh.get(k) is not None}
    row["blendshape_names"] = mesh.get("blendshape_names") or []
    bw = mesh.get("bone_weight") or {}
    used = set(bw)
    names = [(n["name"] if n else None) for n in bone_nodes]
    row["deform_bones"] = sum(1 for i in range(len(names)) if i in used)
    row["helper_bones"] = sorted({names[i] or f"#{i}" for i in range(len(names)) if i not in used})
    row["out_of_range_indices"] = sorted(b for b in used if b >= len(names))
    root_index = next((i for i, n in enumerate(bone_nodes) if n is not None and root is not None
                       and n["transform"] == root["transform"]), None)
    hc = hash_check(mesh.get("bone_name_hashes") or [], bone_nodes, mesh.get("root_bone_name_hash") or 0, root_index)
    row["hash_check"] = hc
    geo = mesh.get("geometry")
    row["geometry_check"] = geo
    row["mapping_ok"] = mapping_verdict(hc, geo, row["out_of_range_indices"])
    depth0 = min((n["depth"] for n in bone_nodes if n), default=0)
    by_path = {n["path"]: n for n in bone_nodes if n}
    for i, n in enumerate(bone_nodes):
        parent = None
        if n:
            pp = n["path"].rsplit("/", 1)[0]
            parent = by_path[pp]["name"] if pp in by_path else None
        bones_out.append({"index": i, "name": n["name"] if n else None, "path": n["path"] if n else None,
                          "depth": (n["depth"] - depth0) if n else None, "parent": parent,
                          "weight": bw.get(i, 0.0), "dominant_vertices": (mesh.get("dominant_vertices") or {}).get(i, 0),
                          "bindpose_distance": (mesh.get("bone_distance") or {}).get(i),
                          "bindpose_rank": (mesh.get("bone_rank") or {}).get(i)})
    return row, bones_out


@analysis("rig", engines=UNITY, needs=("unity.inventory", "unity.install", "analysis:prefabs"),
          description="Rigs: SMR bones, bindposes, influences, deform vs helper bones (validated), blendshapes, IK constraints",
          version=1, options={"file": "regex on install relpaths", "name": "regex on prefab names"})
def rig(ctx) -> Report:
    import re

    pref = ctx.load("prefabs") or {}
    name_rx = re.compile(ctx.opt("name"), re.I) if ctx.opt("name") else None
    fpat = file_filter(ctx)
    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for row in (pref.get("data") or {}).get("prefabs") or []:
        if not row.get("detail"):
            continue
        if not row.get("smr") and not set(row.get("scripts") or {}) & CONSTRAINT_SCRIPTS:
            continue
        if fpat and not fpat.search(row["file"]):
            continue
        if name_rx and not name_rx.search(row["name"]):
            continue
        groups[(row["file"], row.get("cab") or "")].append(row)
    side = ctx.side_dir()
    if side.is_dir():
        shutil.rmtree(side, ignore_errors=True)
    smrs: list[dict] = []
    constraints: list[dict] = []
    meshes: dict[tuple, dict] = {}
    for (rel, cab), rows in sorted(groups.items()):
        ctx.log(f"reading {rel} {cab}".rstrip())
        with UnityFile(ctx, rel) as uf:
            res = Resolver(uf)
            for sf in uf.files():
                if (cab and sf.name != cab) or (not cab and basename(sf.name) != basename(rel)):
                    continue
                for row in sorted(rows, key=lambda r: (r["name"], r["go"])):
                    try:
                        with open(ctx.workspace / row["detail"], encoding="utf-8") as fh:
                            nodes = json.load(fh).get("nodes") or []
                    except (OSError, ValueError):
                        continue
                    by_tf = {n["transform"]: n for n in nodes}
                    names = {n["transform"]: n["path"] for n in nodes} | {n["go"]: n["path"] for n in nodes}
                    detail = {"file": rel, "cab": cab or None, "prefab": row["name"], "go": row["go"], "smr": [],
                              "constraints": []}
                    for n in nodes:
                        for c in n.get("components") or []:
                            obj = sf.objects.get(c.get("path_id"))
                            if obj is None:
                                continue
                            if c["type"] == "SkinnedMeshRenderer":
                                tree = builtin(obj) or {}
                                reader = res.reader(sf, tree.get("m_Mesh")) if ptr(tree.get("m_Mesh"))[1] else None
                                mesh = None
                                if reader is not None:
                                    mk = (basename(str(reader.assets_file.name)), reader.path_id)
                                    if mk not in meshes:
                                        meshes[mk] = mesh_rig(reader)
                                    mesh = meshes[mk]
                                srow, bones = smr_row(tree, mesh, by_tf, n)
                                detail["smr"].append({**srow, "bones_detail": bones})
                                smrs.append({"file": rel, **({"cab": cab} if cab else {}), "prefab": row["name"],
                                             "prefab_go": row["go"], "path_id": c["path_id"], **srow})
                            elif (c["type"] == "MonoBehaviour" and c.get("script") in CONSTRAINT_SCRIPTS) or \
                                    c["type"] in CONSTRAINT_TYPES:
                                tree = uf.mono(obj) if c["type"] == "MonoBehaviour" else builtin(obj)
                                con = {"file": rel, "prefab": row["name"], "node": n["path"],
                                       "type": c.get("script") or c["type"], "path_id": c["path_id"],
                                       "data": refs_named(tree, names) if tree is not None else None}
                                detail["constraints"].append(con)
                                constraints.append(con)
                    if detail["smr"] or detail["constraints"]:
                        folder = basename(rel) + (f"__{cab}" if cab else "")
                        p = ctx.write_side_json(detail, folder, f"{row['name'] or 'unnamed'}__{row['go']}.json")
                        for s in smrs:
                            if s["prefab_go"] == row["go"] and s["file"] == rel and "detail" not in s:
                                s["detail"] = p.relative_to(ctx.workspace).as_posix()
    return build_report(smrs, constraints, meshes)


def build_report(smrs: list[dict], constraints: list[dict], meshes: dict) -> Report:
    smrs.sort(key=lambda s: (s["file"], s.get("cab") or "", s["prefab"], s["prefab_go"], s["node"]))
    constraints.sort(key=lambda c: (c["file"], c["prefab"], c["node"], c["type"]))
    blend = sorted({n for m in meshes.values() for n in m.get("blendshape_names") or []})
    mesh_rows = sorted(({k: v for k, v in m.items() if not k.startswith("_") and k not in ("bone_weight", "dominant_vertices",
                                                                                         "bone_distance", "bone_rank")}
                        for m in meshes.values()), key=lambda m: (m.get("file") or "", m.get("mesh") or "", m.get("path_id") or 0))
    checks = collections.Counter("ok" if s.get("mapping_ok") else ("unchecked" if s.get("mapping_ok") is None else "suspect")
                                 for s in smrs)
    summary = {"smr": len(smrs), "prefabs": len({(s["file"], s["prefab_go"]) for s in smrs}), "meshes": len(meshes),
               "max_influences": max((s["mesh"] or {}).get("max_influences", 0) or 0 for s in smrs) if smrs else 0,
               "bones": counted(collections.Counter(s["bones"] for s in smrs)),
               "mapping_checks": counted(checks), "blendshape_names": len(blend), "constraints": len(constraints),
               "constraint_types": counted(collections.Counter(c["type"] for c in constraints))}
    data = {"smr": smrs, "meshes": mesh_rows, "constraints": constraints, "blendshape_names": blend}
    md = [f"{summary['smr']} SkinnedMeshRenderers in {summary['prefabs']} prefabs ({summary['meshes']} meshes); max "
          f"{summary['max_influences']} influences per vertex; bone counts {', '.join(f'{k}x{v}' for k, v in summary['bones'].items())}. "
          f"Bone index mapping: {', '.join(f'{k} {v}' for k, v in summary['mapping_checks'].items()) or '-'}. "
          f"{summary['constraints']} constraint components.", "",
          "Deform = bones with vertex weight; helper = bones of the SMR without weight. `hash` = mesh bone name hashes "
          "matching the SMR bone names/paths (0 when the prefab renamed the model's hierarchy) and whether the root "
          "bone hash sits at the root bone's index; `geo` = fraction of bones whose own bindpose position is among the 3 "
          "nearest to the centroid of the vertices they dominate (right mapping: most; wrong: about 3/bones; not judged "
          "under 6 weighted bones).", "",
          md_table(["Prefab", "Node", "Mesh", "Verts", "Bones", "Root", "Deform", "Helper", "Max infl", "Influences",
                    "Bindpose scale", "Blendshapes", "hash", "geo", "OK"],
                   [(f"`{s['prefab']}`", s["node"].split("/", 2)[-1], (s["mesh"] or {}).get("mesh"),
                     (s["mesh"] or {}).get("verts"), s["bones"], s["root_bone"], s.get("deform_bones"),
                     len(s.get("helper_bones") or []), (s["mesh"] or {}).get("max_influences"),
                     " ".join(f"{k}:{v}" for k, v in ((s["mesh"] or {}).get("influences_hist") or {}).items()),
                     ", ".join(str(x) for x in ((s["mesh"] or {}).get("bindpose_scales") or [])[:3]),
                     len(s.get("blendshape_names") or []),
                     (f"{s['hash_check'].get('match')}/{s['hash_check'].get('compared')}"
                      + {True: " root ok", False: " ROOT", None: ""}[s["hash_check"].get("root_index_ok")])
                     if s["hash_check"].get("hashes") else "-",
                     (s.get("geometry_check") or {}).get("near"),
                     {True: "yes", False: "NO", None: "-"}[s.get("mapping_ok")]) for s in smrs]), ""]
    if constraints:
        md += ["## Constraints (Animation Rigging / Final IK / Unity)", "",
               md_table(["Prefab", "Node", "Type", "Weight", "Targets"],
                        [(f"`{c['prefab']}`", c["node"].split("/", 2)[-1], c["type"], _weight(c.get("data")),
                          _targets(c.get("data"))) for c in constraints]), ""]
    if blend:
        md += [f"## Blendshape names ({len(blend)})", "", ", ".join(f"`{b}`" for b in blend[:300]), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)


def _weight(d) -> str:
    if not isinstance(d, dict):
        return ""
    w = d.get("m_Weight")
    return str(w) if w is not None else ""


def _targets(d) -> str:
    if not isinstance(d, dict):
        return ""
    data = d.get("m_Data") if isinstance(d.get("m_Data"), dict) else d
    parts = [f"source={s['sourceTransform'].rsplit('/', 1)[-1]}" for s in d.get("m_Sources") or []
             if isinstance(s, dict) and isinstance(s.get("sourceTransform"), str)]
    for k, v in data.items():
        if isinstance(v, str) and v.startswith("/"):
            parts.append(f"{k.removeprefix('m_')}={v.rsplit('/', 1)[-1]}")
    return ", ".join(parts[:8])
