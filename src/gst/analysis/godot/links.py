"""Import remaps and rig links of a recovered Godot project: which model file (``.glb``/``.gltf``/``.fbx``/``.blend``...)
a scene instances and which imported scene Godot really loads for it (``<file>.import`` ``[remap] path``, else the
Godot naming rule ``.godot/imported/<file>-<md5(res path)>.<ext>``), then, following instances recursively, the
Skeleton3D nodes a scene ends up with, the skinned meshes bound to them, the AnimationPlayer/AnimationTree nodes
whose tracks reach them and the PhysicalBone3D bodies under them (ragdoll).

``Project.walk(res)`` gives one scene's rig parts with node paths relative to its root (instanced scenes are walked
once and their parts prefixed with the instance node path); ``Project.chains(res)`` turns that into one link per
skeleton: scene -> model file -> imported scene -> skeleton (bones) -> animation players/trees -> ragdoll bodies.
"""

from __future__ import annotations

import hashlib
import re

from gst.analysis.godot import IMPORT_DIRS, SCENE_EXTS, Docs
from gst.engines.godot import animtree, tres
from gst.engines.godot.tres import Ref, ref_path

MODEL_EXTS = (".glb", ".gltf", ".fbx", ".blend", ".dae", ".obj", ".escn")
_IMPORTED_NAME = re.compile(r"^(.+-[0-9a-f]{32})\.")  # <source file name>-<md5 of its res path>.<ext...>
SCENE_IMPORTERS = ("scene", "fbx", "gltf", "blend", "collada", "wavefront", "ufbx")


def _join(prefix: str, path: str) -> str:
    if prefix in ("", "."):
        return path
    if path in ("", "."):
        return prefix
    return f"{prefix}/{path}"


def _node_part(track_path: str) -> str:
    return str(track_path).split(":", 1)[0]


class Project:
    def __init__(self, root, docs: Docs):
        self.root = root
        self.docs = docs
        self._imports: dict[str, dict | None] = {}
        self._walks: dict[str, dict] = {}
        self._imported: dict[str, list[str]] | None = None  # "<file>-<md5>" -> files in the import caches
        self.instances: dict[str, set[str]] = {}  # walked scene -> scenes/models it instances

    # --- imports
    def import_info(self, res: str) -> dict | None:
        """``{importer, type, uid, imported, source, via}`` of a source file Godot imports (None = not imported)."""
        if res in self._imports:
            return self._imports[res]
        info = None
        f = self.docs.file(res + ".import")
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            try:
                secs = {s.tag: s.props for s in tres.parse_sections(text)}
            except tres.TresError as exc:
                self.docs.errors[res + ".import"] = str(exc)
                secs = {}
            remap = secs.get("remap", {})
            deps = secs.get("deps", {})
            imported = remap.get("path")
            if not imported:  # per-platform variants: path.s3tc, path.etc2, ...
                imported = next((v for k, v in sorted(remap.items()) if k.startswith("path.")), None)
            if not imported and isinstance(deps.get("dest_files"), list) and deps["dest_files"]:
                imported = deps["dest_files"][0]
            info = {"importer": remap.get("importer"), "type": remap.get("type"), "uid": remap.get("uid"),
                    "imported": str(imported) if imported else None, "source": res, "via": res + ".import"}
        elif not res.lower().endswith(SCENE_EXTS):  # scenes/resources are loaded as they are
            name = res.rsplit("/", 1)[-1]
            digest = hashlib.md5(res.encode("utf-8")).hexdigest()
            hits = self._import_cache().get(f"{name}-{digest}", [])
            if hits:
                info = {"importer": None, "type": None, "uid": None, "imported": hits[0], "source": res,
                        "via": "md5 naming rule"}
        self._imports[res] = info
        return info

    def _import_cache(self) -> dict[str, list[str]]:
        if self._imported is None:
            self._imported = {}
            for d in IMPORT_DIRS:
                folder = self.root / d
                if not folder.is_dir():
                    continue
                for p in sorted(folder.iterdir()):
                    m = _IMPORTED_NAME.match(p.name)
                    if m and p.suffix.lower() not in (".md5", ".import") and p.is_file():
                        self._imported.setdefault(m.group(1), []).append(f"res://{d}/{p.name}")
        return self._imported

    def loadable(self, res: str) -> str:
        """What Godot loads for ``res``: the imported file when ``res`` is an imported source, else ``res``."""
        info = self.import_info(res)
        if info and info.get("imported") and self.docs.file(info["imported"]).is_file():
            return info["imported"]
        return res

    def is_model(self, res: str) -> bool:
        info = self.import_info(res)
        if info and info.get("importer") in SCENE_IMPORTERS:
            return True
        return res.lower().endswith(MODEL_EXTS) and info is not None

    # --- rig parts of one scene
    def walk(self, res: str, _stack: tuple = ()) -> dict:
        if res in self._walks:
            return self._walks[res]
        out: dict = {"file": res, "loaded": None, "types": {}, "models": [], "skeletons": [], "meshes": [],
                     "players": [], "trees": [], "bodies": [], "simulators": [], "errors": []}
        if res in _stack:
            out["errors"].append(f"instance cycle through {res}")
            return out
        target = self.loadable(res)
        out["loaded"] = target
        doc = self.docs.get(target)
        if doc is None or doc["kind"] != "scene":
            if target in self.docs.errors:
                out["errors"].append(f"{target}: {self.docs.errors[target]}")
            self._walks[res] = out
            return out
        types = out["types"]
        for n in doc["nodes"]:
            path = n["path"]
            t = n.get("type") or types.get(path)
            if t:
                types[path] = t
            inst = ref_path(doc, n.get("instance")) if isinstance(n.get("instance"), Ref) else None
            if inst:
                self.instances.setdefault(res, set()).add(inst)
                sub = self.walk(inst, _stack + (res,))
                self._merge(out, sub, path)
                if self.is_model(inst):
                    info = self.import_info(inst) or {}
                    out["models"].append({"node": path, "model": inst, "imported": info.get("imported"),
                                          "importer": info.get("importer"), "defined_in": res})
                for k, v in sub["types"].items():
                    types.setdefault(_join(path, k), v)
            if not n.get("type"):
                continue  # override of an instanced node: its definition came with the instance
            props = n["props"]
            where = res if target == res else f"{res} ({target})"
            if t == "Skeleton3D":
                sk = animtree.skeleton(props)
                out["skeletons"].append({"node": path, "defined_in": where, "bones": sk["bones"], "roots": sk["roots"],
                                         "names": sk["names"]})
            elif t in ("MeshInstance3D", "SoftBody3D"):
                skel = props.get("skeleton", "..")
                out["meshes"].append({"node": path, "skeleton": animtree.resolve_node_path(path, str(skel)) if str(skel) else None,
                                      "skin": "skin" in props, "defined_in": where})
            elif t == "AnimationPlayer":
                libs, targets = self._libraries(doc, props)
                out["players"].append({"node": path, "defined_in": where,
                                       "root_node": animtree.resolve_node_path(path, str(props.get("root_node", ".."))),
                                       "libraries": libs, "animations": sum(len(v) for v in libs.values()),
                                       "targets": sorted(targets)})
            elif t == "AnimationTree":
                tr = props.get("tree_root")
                tt, tp, te = animtree._resolve(doc, tr)
                item = {"node": path, "defined_in": where,
                        "root_node": animtree.resolve_node_path(path, str(props.get("root_node", ".."))),
                        "anim_player": animtree.resolve_node_path(path, str(props["anim_player"]))
                        if str(props.get("anim_player", "")) else None, "root_type": tt}
                gdoc = doc
                if tp is None and te:  # tree_root saved as its own resource
                    item["tree_resource"] = te
                    gdoc = self.docs.get(self.loadable(te))
                    if gdoc is not None and gdoc.get("resource") is not None:
                        tt, tp = gdoc["resource"]["type"], gdoc["resource"]["props"]
                        item["root_type"] = tt
                if tp is not None and animtree.is_anim_node(tt):
                    st = animtree.graph_stats(animtree.graph(gdoc, tt, tp))
                    item.update(states=st["states"], transitions=st["transitions"], state_machines=st["state_machines"],
                                animations_used=len(st["animations"]))
                libs, targets = self._libraries(doc, props)
                if targets:
                    item["targets"] = sorted(targets)
                out["trees"].append(item)
            elif t == "PhysicalBone3D":
                b = {"node": path, "defined_in": where, **animtree.physical_bone(props)}
                if "script" in props:
                    b["script"] = ref_path(doc, props["script"])
                out["bodies"].append(b)
            elif t == "PhysicalBoneSimulator3D":
                out["simulators"].append({"node": path, "defined_in": where})
        self._walks[res] = out
        return out

    def _merge(self, out: dict, sub: dict, prefix: str) -> None:
        def pre(item: dict, keys) -> dict:
            item = dict(item)
            for k in keys:
                if item.get(k):  # relative to the sub-scene root = relative to the instance node
                    item[k] = animtree.resolve_node_path(prefix, item[k])
            return item

        out["models"] += [pre(m, ("node",)) for m in sub["models"]]
        out["skeletons"] += [pre(s, ("node",)) for s in sub["skeletons"]]
        out["meshes"] += [pre(m, ("node", "skeleton")) for m in sub["meshes"]]
        out["players"] += [pre(p, ("node", "root_node")) for p in sub["players"]]
        out["trees"] += [pre(t, ("node", "root_node", "anim_player")) for t in sub["trees"]]
        out["bodies"] += [pre(b, ("node",)) for b in sub["bodies"]]
        out["simulators"] += [pre(s, ("node",)) for s in sub["simulators"]]
        out["errors"] += sub["errors"]

    def _libraries(self, doc: dict, props: dict) -> tuple[dict, set]:
        """``{library: [animation names]}`` and the node paths the animations' tracks target (relative to the
        player's root node), following external AnimationLibrary/Animation resources."""
        libs: dict[str, list] = {}
        targets: set = set()
        for lname, entries in animtree.libraries(doc, props).items():
            key = lname or "[default]"
            if "@library" in entries:
                d2, lp = self._resolve_res(doc, entries["@library"])
                if lp is None:
                    libs[key] = []
                    continue
                inner = animtree.libraries(d2, lp).get("", {})
            else:
                d2, inner = doc, entries
            libs[key] = sorted(str(k) for k in inner)
            for v in inner.values():
                d3, ap = self._resolve_res(d2, v)
                if ap is None:
                    continue
                for k, tp in ap.items():
                    if k.startswith("tracks/") and k.endswith("/path"):
                        targets.add(_node_part(tp))
        return libs, targets

    def _resolve_res(self, doc: dict, v) -> tuple[dict | None, dict | None]:
        """A resource reference -> (the document it lives in, its properties); ext refs are loaded."""
        if not isinstance(v, Ref):
            return None, None
        if v.kind == "sub":
            sr = doc["sub_resources"].get(v.id)
            return (doc, sr["props"]) if sr else (None, None)
        path = ref_path(doc, v)
        if not path:
            return None, None
        d = self.docs.get(self.loadable(path))
        if d is None or d.get("resource") is None:
            return None, None
        return d, d["resource"]["props"]

    # --- links per skeleton
    def chains(self, res: str) -> list[dict]:
        w = self.walk(res)
        out = []
        for sk in w["skeletons"]:
            spath = sk["node"]
            model = max((m for m in w["models"] if spath == m["node"] or spath.startswith(m["node"] + "/")),
                        key=lambda m: len(m["node"]), default=None)
            meshes = [m for m in w["meshes"] if m["skeleton"] == spath]
            players = [p for p in w["players"] if _acts(p["root_node"], p["targets"], spath)]
            pnodes = {p["node"] for p in players}
            trees = [t for t in w["trees"] if t.get("anim_player") in pnodes
                     and _acts(t["root_node"], next(p["targets"] for p in players if p["node"] == t["anim_player"]), spath)
                     or _acts(t["root_node"], t.get("targets") or [], spath)]
            bodies = [b for b in w["bodies"] if b["node"].startswith(spath + "/")]
            sims = [s for s in w["simulators"] if s["node"].startswith(spath + "/")]
            jt: dict = {}
            for b in bodies:
                jt[str(b["joint_type"])] = jt.get(str(b["joint_type"]), 0) + 1
            out.append({"scene": res, "skeleton": spath, "skeleton_defined_in": sk["defined_in"], "bones": sk["bones"],
                        "roots": sk["roots"], "bone_names": sk["names"],
                        "model": model["model"] if model else None, "model_node": model["node"] if model else None,
                        "imported": model["imported"] if model else None,
                        "skinned_meshes": [m["node"] for m in meshes if m["skin"]],
                        "meshes": [m["node"] for m in meshes],
                        "animation_players": [{"node": p["node"], "defined_in": p["defined_in"], "root_node": p["root_node"],
                                               "libraries": {k: len(v) for k, v in p["libraries"].items()},
                                               "animations": p["animations"],
                                               "names": sorted({a for v in p["libraries"].values() for a in v})}
                                              for p in players],
                        "animation_trees": [{k: v for k, v in t.items() if k != "targets"} for t in trees],
                        "ragdoll": {"bodies": len(bodies), "bones": [b["bone_name"] for b in bodies],
                                    "simulators": [s["node"] for s in sims],
                                    "defined_in": sorted({b["defined_in"] for b in bodies}),
                                    "joint_types": dict(sorted(jt.items())),
                                    "mass_total": round(sum(float(b.get("mass") or 0) for b in bodies), 6)}})
        return out


def _acts(root: str | None, targets, skeleton: str) -> bool:
    """Do tracks with these node paths (relative to ``root``) reach the skeleton node?"""
    if not root:
        return False
    return any(animtree.resolve_node_path(root, t) == skeleton for t in targets)

