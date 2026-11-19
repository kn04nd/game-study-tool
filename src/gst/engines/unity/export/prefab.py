"""Prefab and scene hierarchy -> JSONL (one root GameObject tree per line).

From the Transforms (``m_Father``/``m_Children``/``m_GameObject``) and GameObjects (``m_Name``,
``m_Component``) of one serialized file; components listed by type (MonoBehaviours by script name).
Built-in typetrees only (no generator needed). Scene files (``levelN``, ``BuildPlayer-*`` CABs) are
scenes; other files hold prefabs/asset roots.
"""

from __future__ import annotations

import json
import os

from gst.engines.unity import loader, typetree

TRANSFORMS = ("Transform", "RectTransform")


def _pid(ptr) -> int:
    return int((ptr or {}).get("m_PathID", 0)) if isinstance(ptr, dict) else int(getattr(ptr, "m_PathID", 0) or 0)


def export_hierarchy(s, sf) -> list:
    tfs: dict[int, dict] = {}
    gos: dict[int, dict] = {}
    for obj in list(sf.objects.values()):
        t = loader.type_name(obj)
        if t in TRANSFORMS:
            tree = typetree.read_builtin(obj)
            if tree:
                tfs[obj.path_id] = {"go": _pid(tree.get("m_GameObject")), "father": _pid(tree.get("m_Father")),
                                    "children": [_pid(c) for c in tree.get("m_Children", []) or []],
                                    "pos": tree.get("m_LocalPosition"), "rot": tree.get("m_LocalRotation"),
                                    "scale": tree.get("m_LocalScale"), "rect": t == "RectTransform"}
        elif t == "GameObject":
            tree = typetree.read_builtin(obj)
            if tree:
                comps = []
                for c in tree.get("m_Component", []) or []:
                    ptr = c.get("component") if isinstance(c, dict) else c
                    comps.append(_pid(ptr))
                gos[obj.path_id] = {"name": tree.get("m_Name", ""), "active": tree.get("m_IsActive"),
                                    "layer": tree.get("m_Layer"), "tag": tree.get("m_Tag"), "comps": comps}
    if not tfs:
        return []

    def comp_label(pid: int) -> str:
        reader = sf.objects.get(pid)
        if reader is None:
            return "?"
        t = loader.type_name(reader)
        if t == "MonoBehaviour":
            info = s.scripts.script_of(reader)
            return f"MonoBehaviour:{info.class_name}" if info else "MonoBehaviour"
        return t

    def node(tpid: int, depth: int = 0) -> dict:
        tf = tfs[tpid]
        go = gos.get(tf["go"], {})
        out = {"name": go.get("name", ""), "go": tf["go"], "transform": tpid,
               "components": [comp_label(c) for c in go.get("comps", []) if c != tpid],
               "pos": tf["pos"], "rot": tf["rot"], "scale": tf["scale"]}
        if go.get("active") is False:
            out["active"] = False
        kids = [c for c in tf["children"] if c in tfs]
        if kids:
            out["children"] = [node(c, depth + 1) for c in kids]
        return out

    roots = [pid for pid, tf in tfs.items() if not tf["father"] or tf["father"] not in tfs]
    scene = sf.name.startswith(("level", "BuildPlayer-")) and not sf.name.endswith(".sharedAssets")
    label = sf.name if sf.name != os.path.basename(s.relpath) else os.path.basename(s.relpath)
    folder = "Scene" if scene else "Prefab"
    final = s.w.target(folder, label, ".jsonl", label)
    total = [0]

    def write(tmp):
        import sys

        sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for r in roots:
                fh.write(json.dumps(node(r), ensure_ascii=False, default=str) + "\n")
                total[0] += 1

    s.w.commit(final, writer=write, meta={"res_path": f"{s.relpath}#{sf.name}", "type": folder, "name": label,
                                          "roots": len(roots), "gameobjects": len(gos), "transforms": len(tfs)})
    return [final]
