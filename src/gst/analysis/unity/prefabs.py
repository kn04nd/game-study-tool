"""``gst analyze prefabs``: every prefab root of the Unity build, summarised, plus a full JSON per interesting prefab.

Roots (ports of the study scripts ``repo/prefab_scan.py`` and ``safari-tv/mdl_roots.py``): every Transform without
a father in a non-scene file (``resources.assets``, ``sharedassets*``, bundle CABs).

Scene files (``levelN``, ``BuildPlayer-*`` CABs) by default (``scenes=animators``) give only their animated objects:
every GameObject with an Animator or a legacy Animation becomes a ``scene_animators`` row with its own detail JSON
(the subtree under it), so clips and controllers used only in scenes get their binding paths (``analysis:animation``)
and their Avatar users (``analysis:avatar``). Scenes without an Animator/Animation are skipped before their
Transforms are read. ``scenes=all`` (or ``1``) adds every scene root to ``prefabs``; ``scenes=0`` skips scenes.
Each root gets a summary row: GameObjects, components by type, MonoBehaviour classes (whole tree), marker scripts,
MeshFilter/SkinnedMeshRenderer counts, verts/tris, colliders, rigidbodies (mass), joints, Animator controllers,
LODGroups, lights, particle systems, materials, and its ``Resources/`` path when it has one.

Per-prefab JSON (``safari-tv/mdl_prefab.py``, ``repo/multi_tree.py``/``prefab_tree.py``) in
``analysis/prefabs/<file>/<name>__<go>.json``: the flat TRS tree (path, depth, local position/rotation/scale,
active, layer) with every component detailed: SMR (mesh, verts, tris, bindposes, bones and their names, root
bone, max influences, bones used, blendshapes, materials), MeshFilter, MeshRenderer materials (shader, textures),
LODGroup, colliders, Animator (controller, avatar), Animation clips, Rigidbody, joints, MonoBehaviour classes.
Which prefabs get it: ``detail=auto`` (default: roots with a SkinnedMeshRenderer, Animator, Animation, LODGroup or a
``marker`` script, or matching ``name``), ``detail=all``, ``detail=none``.

Options: ``marker`` (MonoBehaviour class names that classify roots, e.g. ``EnemyParent``; repeatable/comma list),
``detail``, ``scenes``, ``file`` (regex on install relpaths), ``name`` (regex on root names, detail only).
Streaming: one install file at a time (``UnityFile``), the Transform/GameObject graph of one CAB in RAM.
"""

from __future__ import annotations

import collections
import re
from typing import Any, Callable, Iterable

from gst.analysis import Report, UNITY, analysis, counted, md_table, stats
from gst.analysis.unity import COLLIDERS, JOINTS, TRANSFORMS, Resolver, UnityFile, basename, builtin, is_scene, ptr

RENDERERS = ("MeshRenderer", "SkinnedMeshRenderer", "SpriteRenderer", "LineRenderer", "TrailRenderer", "BillboardRenderer")
DETAIL_TYPES = ("SkinnedMeshRenderer", "Animator", "Animation", "LODGroup")


def _vec(v: Any, keys: str) -> list | None:
    if not isinstance(v, dict):
        return None
    try:
        return [round(float(v[k]), 5) for k in keys]
    except (KeyError, TypeError, ValueError):
        return None


class Graph:
    """Transforms and GameObjects of one serialized file (plain dicts) + component types and scripts."""

    def __init__(self, type_of: Callable[[int], str | None], script_of: Callable[[int], str]):
        self.tfs: dict[int, dict] = {}
        self.gos: dict[int, dict] = {}
        self.type_of = type_of
        self._script_of = script_of
        self._scripts: dict[int, str] = {}

    @classmethod
    def from_objects(cls, objects: Iterable, read: Callable[[Any], dict | None], type_of, script_of,
                     type_name: Callable[[Any], str]) -> "Graph":
        g = cls(type_of, script_of)
        for obj in objects:
            t = type_name(obj)
            if t in TRANSFORMS:
                tree = read(obj)
                if tree:
                    g.tfs[obj.path_id] = {"go": ptr(tree.get("m_GameObject"))[1], "father": ptr(tree.get("m_Father"))[1],
                                          "children": [ptr(c)[1] for c in tree.get("m_Children") or []],
                                          "pos": _vec(tree.get("m_LocalPosition"), "xyz"),
                                          "rot": _vec(tree.get("m_LocalRotation"), "xyzw"),
                                          "scale": _vec(tree.get("m_LocalScale"), "xyz"), "type": t}
            elif t == "GameObject":
                tree = read(obj)
                if tree:
                    comps = []
                    for c in tree.get("m_Component") or []:
                        comps.append(ptr(c.get("component") if isinstance(c, dict) and "component" in c else c)[1])
                    g.gos[obj.path_id] = {"name": tree.get("m_Name") or "", "active": tree.get("m_IsActive"),
                                          "layer": tree.get("m_Layer"), "tag": tree.get("m_Tag"), "comps": comps}
        return g

    def script(self, pid: int) -> str:
        if pid not in self._scripts:
            self._scripts[pid] = self._script_of(pid) or "?"
        return self._scripts[pid]

    def roots(self) -> list[int]:
        return sorted(t for t, tf in self.tfs.items() if not tf["father"] or tf["father"] not in self.tfs)

    def walk(self, root_tf: int):
        """``(transform pid, depth, path)`` in tree order (iterative: deep rigs do not hit the recursion limit)."""
        stack = [(root_tf, 0, "")]
        seen = set()
        while stack:
            tpid, depth, parent_path = stack.pop()
            if tpid in seen or tpid not in self.tfs:
                continue
            seen.add(tpid)
            tf = self.tfs[tpid]
            name = self.gos.get(tf["go"], {}).get("name", "")
            path = f"{parent_path}/{name}"
            yield tpid, depth, path
            for child in reversed(tf["children"]):
                stack.append((child, depth + 1, path))

    def components(self, tpid: int) -> list[tuple[int, str]]:
        tf = self.tfs[tpid]
        go = self.gos.get(tf["go"], {})
        out = []
        for c in go.get("comps", []):
            if c == tpid:
                continue
            t = self.type_of(c)
            if t is not None:
                out.append((c, t))
        return out


class Details:
    """Reads the component facts a summary or a per-prefab JSON needs (typetrees via ``read``, PPtrs via the resolver)."""

    def __init__(self, sf, objects: dict, read: Callable[[Any], dict | None], resolver: Resolver | None):
        self.sf = sf
        self.objects = objects
        self.read = read
        self.res = resolver
        self._trees: dict[int, dict | None] = {}

    def tree(self, pid: int) -> dict | None:
        if pid not in self._trees:
            obj = self.objects.get(pid)
            self._trees[pid] = self.read(obj) if obj is not None else None
        return self._trees[pid]

    def mesh(self, value, skin=False) -> dict | None:
        return self.res.mesh(self.sf, value, skin=skin) if self.res is not None else None

    def material_names(self, tree: dict) -> list[str]:
        out = []
        for m in tree.get("m_Materials") or []:
            if ptr(m)[1] and self.res is not None:
                n = self.res.name(self.sf, m)
                out.append(n if n is not None else f"?{ptr(m)[1]}")
        return out


def summarize(g: Graph, root_tf: int, d: Details, markers: set[str]) -> dict:
    """Summary row of one prefab root (whole tree)."""
    comps: collections.Counter = collections.Counter()
    scripts: collections.Counter = collections.Counter()
    cols: collections.Counter = collections.Counter()
    joints: collections.Counter = collections.Counter()
    mats: set[str] = set()
    rbs, anims, meshes = [], [], []
    nodes = 0
    convex = 0
    for tpid, _depth, _path in g.walk(root_tf):
        nodes += 1
        go_name = g.gos.get(g.tfs[tpid]["go"], {}).get("name", "")
        for cpid, t in g.components(tpid):
            comps[t] += 1
            if t == "MonoBehaviour":
                scripts[g.script(cpid)] += 1
            elif t in ("MeshFilter", "SkinnedMeshRenderer"):
                tree = d.tree(cpid) or {}
                m = d.mesh(tree.get("m_Mesh"))
                if m and not m.get("missing"):
                    meshes.append(m)
                if t == "SkinnedMeshRenderer":
                    mats.update(d.material_names(tree))
            elif t == "MeshRenderer":
                mats.update(d.material_names(d.tree(cpid) or {}))
            elif t in COLLIDERS:
                cols[t] += 1
                if t == "MeshCollider" and (d.tree(cpid) or {}).get("m_Convex"):
                    convex += 1
            elif t in JOINTS:
                joints[t] += 1
            elif t == "Rigidbody":
                tree = d.tree(cpid) or {}
                rbs.append({"go": go_name, "mass": _num(tree.get("m_Mass")), "kinematic": bool(tree.get("m_IsKinematic")),
                            "gravity": bool(tree.get("m_UseGravity", True))})
            elif t == "Animator":
                tree = d.tree(cpid) or {}
                anims.append(d.res.name(d.sf, tree.get("m_Controller")) if d.res is not None and ptr(tree.get("m_Controller"))[1]
                             else None)
    root_go = g.tfs[root_tf]["go"]
    root = g.gos.get(root_go, {})
    return {"name": root.get("name", ""), "go": root_go, "transform": root_tf, "gameobjects": nodes,
            "markers": sorted(set(scripts) & markers), "components": counted(comps), "scripts": counted(scripts),
            "mesh_filters": comps.get("MeshFilter", 0), "smr": comps.get("SkinnedMeshRenderer", 0),
            "renderers": sum(comps.get(r, 0) for r in RENDERERS),
            "verts": sum(int(m.get("verts") or 0) for m in meshes), "tris": sum(int(m.get("tris") or 0) for m in meshes),
            "colliders": counted(cols), "mesh_colliders_convex": convex, "rigidbodies": rbs, "joints": counted(joints),
            "animators": sorted(a or "" for a in anims), "lod_groups": comps.get("LODGroup", 0),
            "lights": comps.get("Light", 0), "particle_systems": comps.get("ParticleSystem", 0),
            "materials": sorted(mats)}


def _num(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def component_detail(g: Graph, cpid: int, t: str, d: Details) -> dict:
    out: dict[str, Any] = {"type": t, "path_id": cpid}
    if t == "MonoBehaviour":
        out["script"] = g.script(cpid)
        return out
    tree = d.tree(cpid)
    if tree is None:
        return out
    if "m_Enabled" in tree and tree.get("m_Enabled") in (0, False):
        out["enabled"] = False
    if t == "SkinnedMeshRenderer":
        out["mesh"] = d.mesh(tree.get("m_Mesh"), skin=True)
        bones = []
        for b in tree.get("m_Bones") or []:
            bp = ptr(b)
            tf = g.tfs.get(bp[1]) if bp[0] == 0 else None
            bones.append(g.gos.get(tf["go"], {}).get("name") if tf else None)
        out["bones"] = len(bones)
        out["bone_names"] = bones
        rb = ptr(tree.get("m_RootBone"))
        rtf = g.tfs.get(rb[1]) if rb[1] and rb[0] == 0 else None
        out["root_bone"] = g.gos.get(rtf["go"], {}).get("name") if rtf else None
        out["materials"] = [d.res.material(d.sf, m) for m in tree.get("m_Materials") or [] if ptr(m)[1]] if d.res else []
        out["update_when_offscreen"] = tree.get("m_UpdateWhenOffscreen")
        out["quality"] = tree.get("m_Quality")
    elif t == "MeshFilter":
        out["mesh"] = d.mesh(tree.get("m_Mesh"))
    elif t == "MeshRenderer":
        out["materials"] = [d.res.material(d.sf, m) for m in tree.get("m_Materials") or [] if ptr(m)[1]] if d.res else []
    elif t == "LODGroup":
        out["lods"] = [{"height": _num(lod.get("screenRelativeHeight")), "renderers": len(lod.get("renderers") or [])}
                       for lod in tree.get("m_LODs") or []]
        out["fade_mode"] = tree.get("m_FadeMode")
    elif t in COLLIDERS:
        out["trigger"] = bool(tree.get("m_IsTrigger"))
        for key, name in (("m_Size", "size"), ("m_Center", "center")):
            if key in tree:
                out[name] = _vec(tree[key], "xyz")
        for key, name in (("m_Radius", "radius"), ("m_Height", "height"), ("m_Direction", "direction")):
            if key in tree:
                out[name] = _num(tree[key])
        if t == "MeshCollider":
            out["convex"] = bool(tree.get("m_Convex"))
            m = d.mesh(tree.get("m_Mesh"))
            out["mesh"] = {k: m.get(k) for k in ("mesh", "verts", "tris")} if m else None
    elif t == "Animator":
        out["controller"] = d.res.name(d.sf, tree.get("m_Controller")) if d.res and ptr(tree.get("m_Controller"))[1] else None
        out["avatar"] = d.res.name(d.sf, tree.get("m_Avatar")) if d.res and ptr(tree.get("m_Avatar"))[1] else None
        out["apply_root_motion"] = bool(tree.get("m_ApplyRootMotion"))
        out["culling_mode"] = tree.get("m_CullingMode")
    elif t == "Animation":
        out["clips"] = [d.res.name(d.sf, c) if d.res else ptr(c)[1] for c in tree.get("m_Animations") or [] if ptr(c)[1]]
        out["clip"] = d.res.name(d.sf, tree.get("m_Animation")) if d.res and ptr(tree.get("m_Animation"))[1] else None
    elif t == "Rigidbody":
        for key in ("m_Mass", "m_Drag", "m_AngularDrag", "m_LinearDamping", "m_AngularDamping"):
            if key in tree:
                out[key[2:].lower()] = _num(tree[key])
        out["kinematic"] = bool(tree.get("m_IsKinematic"))
        out["gravity"] = bool(tree.get("m_UseGravity", True))
        out["interpolate"] = tree.get("m_Interpolate")
        out["collision_detection"] = tree.get("m_CollisionDetection")
    elif t in JOINTS:
        out["connected"] = bool(ptr(tree.get("m_ConnectedBody"))[1])
        for key in ("m_UseLimits", "m_UseSpring", "m_UseMotor", "m_EnableCollision", "m_BreakForce"):
            if key in tree:
                out[key[2:3].lower() + key[3:]] = tree[key] if not isinstance(tree[key], float) else _num(tree[key])
    elif t == "Light":
        out["light_type"] = tree.get("m_Type")
        out["range"] = _num(tree.get("m_Range"))
        out["intensity"] = _num(tree.get("m_Intensity"))
    return out


def detail_tree(g: Graph, root_tf: int, d: Details) -> list[dict]:
    nodes = []
    for tpid, depth, path in g.walk(root_tf):
        tf = g.tfs[tpid]
        go = g.gos.get(tf["go"], {})
        node = {"path": path, "depth": depth, "name": go.get("name", ""), "go": tf["go"], "transform": tpid,
                "pos": tf["pos"], "rot": tf["rot"], "scale": tf["scale"], "layer": go.get("layer"), "tag": go.get("tag")}
        if go.get("active") in (0, False):
            node["active"] = False
        if tf.get("type") == "RectTransform":
            node["rect"] = True
        node["components"] = [component_detail(g, c, t, d) for c, t in g.components(tpid)]
        nodes.append(node)
    return nodes


ANIMATED = ("Animator", "Animation")


def scene_animated(g: Graph) -> list[int]:
    """Transforms of the GameObjects that carry an Animator or a legacy Animation."""
    return [tpid for tpid in sorted(g.tfs) if any(t in ANIMATED for _c, t in g.components(tpid))]


def scene_path(g: Graph, tpid: int) -> str:
    names, seen = [], set()
    while tpid in g.tfs and tpid not in seen:
        seen.add(tpid)
        names.append(g.gos.get(g.tfs[tpid]["go"], {}).get("name", ""))
        tpid = g.tfs[tpid]["father"]
    return "/" + "/".join(reversed(names))


def analyze_scene_animators(ctx, relpath: str, g: Graph, d: Details, *, markers: set[str], cab: str | None) -> list[dict]:
    rows = []
    for tpid in scene_animated(g):
        row = summarize(g, tpid, d, markers)
        row = {"file": relpath, **({"cab": cab} if cab else {}), "scene": True, "scene_path": scene_path(g, tpid), **row}
        folder = "scenes__" + basename(relpath) + (f"__{cab}" if cab else "")
        p = ctx.write_side_json({"file": relpath, "cab": cab, "name": row["name"], "go": row["go"], "transform": tpid,
                                 "scene_path": row["scene_path"], "summary": row, "nodes": detail_tree(g, tpid, d)},
                                folder, f"{row['name'] or 'unnamed'}__{row['go']}.json")
        row["detail"] = p.relative_to(ctx.workspace).as_posix()
        rows.append(row)
    return rows


def detailed_rows(doc: dict | None) -> list[dict]:
    """Rows of a ``prefabs.json`` with a detail JSON: prefab roots, then scene animators."""
    data = (doc or {}).get("data") or {}
    return [r for r in (data.get("prefabs") or []) + (data.get("scene_animators") or []) if r.get("detail")]


def wants_detail(row: dict, mode: str, name_rx: re.Pattern | None) -> bool:
    if mode == "all":
        return True
    if mode == "none":
        return False
    if name_rx is not None and name_rx.search(row["name"]):
        return True
    return bool(row["markers"]) or any(row["components"].get(t) for t in DETAIL_TYPES)


def analyze_file(ctx, relpath: str, sf, g: Graph, d: Details, *, markers: set[str], mode: str, name_rx, res_map: dict,
                 cab: str | None) -> list[dict]:
    rows = []
    for root in g.roots():
        row = summarize(g, root, d, markers)
        row = {"file": relpath, **({"cab": cab} if cab else {}), **row}
        container = res_map.get((basename(relpath).lower(), row["go"])) if not cab else None
        if container:
            row["container"] = container
        if wants_detail(row, mode, name_rx):
            nodes = detail_tree(g, root, d)
            folder = basename(relpath) + (f"__{cab}" if cab else "")
            p = ctx.write_side_json({"file": relpath, "cab": cab, "name": row["name"], "go": row["go"],
                                     "transform": root, "summary": row, "nodes": nodes},
                                    folder, f"{row['name'] or 'unnamed'}__{row['go']}.json")
            row["detail"] = p.relative_to(ctx.workspace).as_posix()
        rows.append(row)
    return rows


def scene_mode(value) -> str:
    v = str(value or "animators").lower()
    if v in ("1", "true", "yes", "all"):
        return "all"
    return "none" if v in ("0", "false", "no", "none") else "animators"


@analysis("prefabs", engines=UNITY, needs=("unity.inventory", "unity.install"),
          description="Prefab roots: tree, meshes, SMR/bones, colliders, rigidbodies, joints, animators, scripts", version=2,
          options={"marker": "MonoBehaviour classes that classify roots (repeatable; e.g. EnemyParent)",
                   "detail": "auto|all|none: which prefabs get analysis/prefabs/<file>/<name>__<go>.json (default auto)",
                   "scenes": "animators (default: Animator/Animation objects of scene files) | all (every scene "
                             "root) | 0 (skip scenes)", "file": "regex on install relpaths",
                   "name": "regex on root names (always detailed)"})
def prefabs(ctx) -> Report:
    from gst.engines.unity import inventory, loader

    markers = set(ctx.opt_list("marker"))
    mode = str(ctx.opt("detail") or "auto")
    scenes = scene_mode(ctx.opt("scenes"))
    fpat = re.compile(ctx.opt("file"), re.I) if ctx.opt("file") else None
    name_rx = re.compile(ctx.opt("name"), re.I) if ctx.opt("name") else None
    res_map = inventory.resources_map(ctx.inventory_dir())
    side = ctx.side_dir()
    if side.is_dir():
        import shutil

        shutil.rmtree(side, ignore_errors=True)  # a rerun replaces every per-prefab file
    rows: list[dict] = []
    scene_rows: list[dict] = []
    files_out = []
    for rel in ctx.inventory_files(TRANSFORMS):
        if fpat and not fpat.search(rel):
            continue
        ctx.log(f"reading {rel}")
        with UnityFile(ctx, rel) as uf:
            resolver = Resolver(uf)
            for sf in uf.files():
                cab = sf.name if basename(sf.name) != basename(rel) else None
                objects = sf.objects
                scene = is_scene(sf.name)
                if scene and (scenes == "none" or scenes == "animators" and not any(
                        loader.type_name(o) in ANIMATED for o in objects.values())):
                    files_out.append({"file": rel, "cab": cab, "scene": True, "roots": None})
                    continue

                def type_of(pid, _o=objects):
                    obj = _o.get(pid)
                    return loader.type_name(obj) if obj is not None else None

                def script_of(pid, _o=objects):
                    info = uf.script(_o[pid]) if pid in _o else None
                    return info.class_name if info else ""

                g = Graph.from_objects(list(objects.values()), builtin, type_of, script_of, loader.type_name)
                d = Details(sf, objects, builtin, resolver)
                if scene and scenes == "animators":
                    got = analyze_scene_animators(ctx, rel, g, d, markers=markers, cab=cab)
                    files_out.append({"file": rel, "cab": cab, "scene": True, "roots": None, "animators": len(got),
                                      "gameobjects": len(g.gos), "transforms": len(g.tfs)})
                    scene_rows += got
                    continue
                got = analyze_file(ctx, rel, sf, g, d, markers=markers, mode=mode, name_rx=name_rx, res_map=res_map, cab=cab)
                files_out.append({"file": rel, "cab": cab, "scene": is_scene(sf.name), "roots": len(got),
                                  "gameobjects": len(g.gos), "transforms": len(g.tfs)})
                rows += got
    return build_report(rows, files_out, markers, scene_rows)


def build_report(rows: list[dict], files_out: list[dict], markers: set[str], scene_rows: list[dict] | None = None) -> Report:
    scene_rows = sorted(scene_rows or [], key=lambda r: (r["file"], r.get("cab") or "", r["scene_path"], r["go"]))
    rows.sort(key=lambda r: (r["file"], r.get("cab") or "", r["name"], r["go"]))
    files_out.sort(key=lambda f: (f["file"], f.get("cab") or ""))
    by_script: dict[str, dict] = {}
    for r in rows:
        for s, n in r["scripts"].items():
            e = by_script.setdefault(s, {"prefabs": 0, "components": 0})
            e["prefabs"] += 1
            e["components"] += n
    by_script = dict(sorted(by_script.items(), key=lambda kv: (-kv[1]["prefabs"], kv[0])))
    marker_groups = {m: [r for r in rows if m in r["markers"]] for m in sorted(markers)}
    marker_stats = {m: {"prefabs": len(g), "tris": stats([r["tris"] for r in g]),
                        "gameobjects": stats([r["gameobjects"] for r in g]),
                        "with_smr": sum(1 for r in g if r["smr"]), "with_animator": sum(1 for r in g if r["animators"])}
                    for m, g in marker_groups.items()}
    summary = {"prefabs": len(rows), "detailed": sum(1 for r in rows if r.get("detail")),
               "files": len({f["file"] for f in files_out}),
               "with_smr": sum(1 for r in rows if r["smr"]), "with_animator": sum(1 for r in rows if r["animators"])}
    summary["scene_animators"] = len(scene_rows)
    summary.update({f"marker {m}": s["prefabs"] for m, s in marker_stats.items()})
    data = {"files": files_out, "markers": marker_stats, "scripts": by_script, "prefabs": rows,
            "scene_animators": scene_rows}
    md = [f"{summary['prefabs']} prefab roots in {summary['files']} files; {summary['detailed']} with a per-prefab JSON "
          f"(`analysis/prefabs/`); {summary['with_smr']} with a SkinnedMeshRenderer, {summary['with_animator']} with an Animator.", ""]
    md += ["## Files", "", md_table(["File", "CAB", "Scene", "Roots", "GameObjects"],
                                    [(f"`{f['file']}`", f.get("cab") or "", "yes" if f.get("scene") else "",
                                      f.get("roots") if f.get("roots") is not None else
                                      (f"{f['animators']} animated objects" if "animators" in f else "skipped"),
                                      f.get("gameobjects")) for f in files_out]), ""]
    if scene_rows:
        md += [f"## Animated objects in scenes ({len(scene_rows)})", "",
               "GameObjects of scene files with an Animator or Animation (`data.scene_animators`, detail = the subtree "
               "under them; `scenes=animators`).", "",
               md_table(["Scene object", "File", "GOs", "SMR", "Animator", "Detail"],
                        [(f"`{r['scene_path']}`", basename(r["file"]) + (f" {r['cab']}" if r.get("cab") else ""),
                          r["gameobjects"], r["smr"], ", ".join(a for a in r["animators"] if a) or "-", f"`{r['detail']}`")
                         for r in scene_rows]), ""]
    for m, group in marker_groups.items():
        st = marker_stats[m]
        tri = st["tris"]
        md += [f"## Marker `{m}`: {len(group)} prefabs", ""]
        if tri:
            md.append(f"Tris min {tri['min']}, median {tri['median']}, p90 {tri['p90']}, max {tri['max']}; "
                      f"GameObjects median {st['gameobjects']['median']}; with SMR {st['with_smr']}, with Animator "
                      f"{st['with_animator']}.")
            md.append("")
        md += [_prefab_table(group), ""]
    md += ["## Scripts on prefabs", "", "Which MonoBehaviour classes are on how many prefabs (full list in the JSON).", "",
           md_table(["Script", "Prefabs", "Components"], [(f"`{s}`", e["prefabs"], e["components"])
                                                          for s, e in list(by_script.items())[:60]]), ""]
    rigged = [r for r in rows if r["smr"] or r["animators"]]
    md += [f"## Prefabs with a SkinnedMeshRenderer or Animator ({len(rigged)})", "", _prefab_table(rigged), ""]
    biggest = sorted(rows, key=lambda r: (-r["tris"], r["name"]))[:40]
    md += ["## Largest prefabs by triangles (top 40; all in the JSON)", "", _prefab_table(biggest), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)


def _prefab_table(rows: list[dict]) -> str:
    return md_table(["Prefab", "File", "GOs", "MF", "SMR", "Verts", "Tris", "Colliders", "RB mass", "Joints", "Animator",
                     "Detail"],
                    [(f"`{r['name']}`", basename(r["file"]) + (f" {r['cab']}" if r.get("cab") else ""), r["gameobjects"],
                      r["mesh_filters"], r["smr"], r["verts"], r["tris"],
                      ", ".join(f"{k} {v}" for k, v in r["colliders"].items()) or "-",
                      ", ".join(str(b["mass"]) for b in r["rigidbodies"]) or "-",
                      sum(r["joints"].values()) or "-", ", ".join(a for a in r["animators"] if a) or "-",
                      f"`{r['detail']}`" if r.get("detail") else "-") for r in rows])
