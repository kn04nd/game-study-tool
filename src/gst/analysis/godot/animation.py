"""``gst analyze godot_animation``: the Godot counterpart of the Unity controller/animation/rig/joints analyses, over
the recovered project's text scenes/resources (``gst.engines.godot.animtree``):

- ``animations``: every ``Animation`` resource (sub-resource or ``.tres``): length, loop, tracks by type, key counts,
  method-track calls, audio-track streams; ``players``: AnimationPlayer nodes and their libraries.
- ``trees``: AnimationTree nodes (``tree_root``, ``anim_player``, ``parameters/*``) and standalone AnimationNode
  resources: state machines (states, transitions with advance mode/condition/expression, xfade), blend trees,
  blend spaces, with counts per node class.
- ``skeletons`` (Skeleton3D bones saved in the scene), ``attachments`` (BoneAttachment3D, IK/modifiers),
  ``ragdolls`` (PhysicalBone3D bodies with their joints, per scene) and ``joints`` (every Joint3D/Joint2D node).
- ``characters``: one link per skeleton a scene ends up with, following instances and import remaps
  (:mod:`gst.analysis.godot.links`): scene -> model file (.glb...) -> imported scene -> Skeleton3D (bones) ->
  skinned meshes -> AnimationPlayer/AnimationTree whose tracks reach it -> PhysicalBone3D bodies under it.
Text and binary (``.scn``/``.res``, RSRC reader) scenes are read alike. Animations embedded in imported models (glb)
are in ``gltf_summary``; the characters list names them per AnimationPlayer.
"""

from __future__ import annotations

import collections
import re

from gst.analysis import Report, analysis, counted, md_table
from gst.analysis.godot import GEOMETRY_TYPES, SCENE_EXTS, Docs, bulk_option, iter_files, project_root
from gst.analysis.godot.links import Project

MODIFIERS = ("BoneAttachment3D", "SkeletonIK3D", "LookAtModifier3D", "CCDIK3D", "FABRIK3D", "SkeletonModifier3D",
             "PhysicalBoneSimulator3D", "RetargetModifier3D", "SpringBoneSimulator3D", "XRBodyModifier3D",
             "Skeleton2D", "Bone2D", "BoneAttachment2D")


@analysis("godot_animation", engines=("godot",), needs=("godot.project",), version=2, category="animation",
          description="Godot animation: Animation tracks, AnimationTree graphs, skeletons, ragdolls, scene -> model -> "
                      "skeleton links",
          options={"name": "regex on the res:// path (only matching files)",
                   "tracks": "1 (default) = per-track detail in the JSON; 0 = counts only",
                   "bulk": "binary packed float/vector/byte arrays of this many bytes or more are counted, not decoded "
                           "(default 4096; 'all' = decode everything)"})
def godot_animation(ctx) -> Report:
    from gst.engines.godot import animtree
    from gst.engines.godot.tres import Ref, ref_path, to_json

    root = project_root(ctx.workspace)
    rx = re.compile(ctx.opt("name"), re.I) if ctx.opt("name") else None
    keep_tracks = str(ctx.opt("tracks", "1")) != "0"
    docs = Docs(root, bulk=bulk_option(ctx), skip=GEOMETRY_TYPES)
    project = Project(root, docs)
    anims, players, trees, skeletons, attachments, joints = [], [], [], [], [], []
    ragdolls, characters, binary_files = [], [], []
    for res, _p in iter_files(root, SCENE_EXTS):
        if rx and not rx.search(res):
            continue
        doc = docs.get(res)
        if doc is None:
            continue
        if "binary" in doc:
            binary_files.append(res)
        if doc["kind"] == "scene":
            characters += project.chains(res)
        subs = doc["sub_resources"]
        # Animation resources
        for rid in doc["sub_order"]:
            sr = subs[rid]
            if sr["type"] == "Animation":
                a = animtree.animation(doc, sr["props"], rid)
                anims.append({"file": res, "where": f"SubResource({rid})", **a})
        if doc["resource"] is not None and doc["header"].get("type") == "Animation":
            a = animtree.animation(doc, doc["resource"]["props"], res.rsplit("/", 1)[-1])
            anims.append({"file": res, "where": "[resource]", **a})

        def lib_names(lib: dict) -> dict:
            out = {}
            for lname, entries in lib.items():
                if "@library" in entries:
                    ref = entries["@library"]
                    t, p, e = animtree._resolve(doc, ref)
                    if p is not None:
                        inner = animtree.libraries(doc, p)
                        out[lname or "[default]"] = sorted(str(k) for k in inner.get("", {}))
                    else:
                        out[lname or "[default]"] = {"external": e or (ref_path(doc, ref) if isinstance(ref, Ref) else None)}
                else:
                    out[lname or "[default]"] = sorted(str(k) for k in entries)
            return out

        if doc["resource"] is not None and doc["header"].get("type") == "AnimationLibrary":
            players.append({"file": res, "node": "[resource AnimationLibrary]",
                            "libraries": lib_names(animtree.libraries(doc, doc["resource"]["props"]))})
        # standalone AnimationNode resources (tree_root saved as .tres)
        htype = doc["header"].get("type")
        if doc["resource"] is not None and animtree.is_anim_node(htype):
            g = animtree.graph(doc, htype, doc["resource"]["props"])
            trees.append({"file": res, "node": "[resource]", "type": htype, "stats": animtree.graph_stats(g), "graph": g,
                          "sub_resource_types": counted(collections.Counter(subs[r]["type"] for r in doc["sub_order"]))})
        bones_in_scene, joints_in_scene, sims = [], [], []
        for n in doc["nodes"]:
            t = n.get("type")
            props = n["props"]
            if t == "AnimationPlayer" or (t is None and ("libraries" in props or any(k.startswith("anims/") for k in props))):
                item = {"file": res, "node": n["path"], "libraries": lib_names(animtree.libraries(doc, props))}
                for k in ("autoplay", "root_node", "playback_default_blend_time", "callback_mode_process",
                          "callback_mode_method", "speed_scale"):
                    if k in props:
                        item[k] = to_json(props[k])
                if t is None:
                    item["override"] = True
                players.append(item)
            if t == "AnimationTree" or (t is None and "tree_root" in props):
                tr = props.get("tree_root")
                tt, tp, te = animtree._resolve(doc, tr)
                item = {"file": res, "node": n["path"], "type": tt,
                        "anim_player": str(props.get("anim_player", "")) or None,
                        "parameters": {k[len("parameters/"):]: to_json(v) for k, v in props.items() if k.startswith("parameters/")}}
                for k in ("active", "callback_mode_process", "root_motion_track", "advance_expression_base_node", "deterministic"):
                    if k in props:
                        item[k] = to_json(props[k])
                if tp is not None:
                    g = animtree.graph(doc, tt, tp)
                    item.update(stats=animtree.graph_stats(g), graph=g)
                elif te:
                    item["tree_resource"] = te
                trees.append(item)
            if t == "Skeleton3D":
                sk = animtree.skeleton(props)
                if sk["bones"]:
                    skeletons.append({"file": res, "node": n["path"], **sk})
            if t in MODIFIERS:
                a = {"file": res, "node": n["path"], "type": t}
                if "bone_name" in props:
                    a["bone_name"] = str(props["bone_name"])
                attachments.append(a)
                if t == "PhysicalBoneSimulator3D":
                    sims.append(n["path"])
            if t == "PhysicalBone3D":
                b = {"node": n["path"], **animtree.physical_bone(props)}
                if "script" in props:
                    b["script"] = ref_path(doc, props["script"])
                bones_in_scene.append(b)
            if t in animtree.JOINT_NODES:
                j = {"file": res, **animtree.joint(n)}
                joints.append(j)
                joints_in_scene.append(j)
        if bones_in_scene:
            jt = collections.Counter(b["joint_type"] for b in bones_in_scene)
            ragdolls.append({"file": res, "physical_bones": len(bones_in_scene), "simulators": sims,
                             "builtin_joint_types": dict(sorted((str(k), v) for k, v in jt.items())),
                             "builtin_joints": sum(v for k, v in jt.items() if k != "none"),
                             "joint_nodes": len(joints_in_scene),
                             "joint_node_types": counted(collections.Counter(j["type"] for j in joints_in_scene)),
                             "mass_total": round(sum(float(b.get("mass") or 0) for b in bones_in_scene), 6),
                             "bones": bones_in_scene,
                             "joints": [{k: v for k, v in j.items() if k != "file"} for j in joints_in_scene]})
        del doc

    tops = _top_level(project)
    for c in characters:
        c["top_level"] = c["scene"] in tops
    if not keep_tracks:
        for a in anims:
            a.pop("tracks", None)
    node_types: collections.Counter = collections.Counter()
    sm = st = trn = 0
    conds: collections.Counter = collections.Counter()
    for t in trees:
        s = t.get("stats")
        if s:
            node_types.update(s["node_types"])
            sm += s["state_machines"]
            st += s["states"]
            trn += s["transitions"]
            conds.update(s["conditions"])
    track_types: collections.Counter = collections.Counter()
    for a in anims:
        track_types.update(a["by_type"])
    summary = {"animations": len(anims), "tracks": sum(a["track_count"] for a in anims), "keys": sum(a["keys"] for a in anims),
               "method_tracks": track_types.get("method", 0), "audio_tracks": track_types.get("audio", 0),
               "players": len(players), "trees": len(trees), "state_machines": sm, "states": st, "transitions": trn,
               "conditions": len(conds), "skeletons": len(skeletons),
               "max_bones": max((s["bones"] for s in skeletons), default=0), "ragdoll_scenes": len(ragdolls),
               "physical_bones": sum(r["physical_bones"] for r in ragdolls), "joints": len(joints),
               "characters": len(characters),
               "characters_animated": sum(1 for c in characters if c["animation_players"] or c["animation_trees"]),
               "characters_ragdoll": sum(1 for c in characters if c["ragdoll"]["bodies"]),
               "characters_max_bones": max((c["bones"] for c in characters), default=0),
               "binary_files": len(binary_files), "parse_errors": len(docs.errors)}
    data = {"animations": anims, "track_types": counted(track_types), "players": players, "trees": trees,
            "tree_node_types": counted(node_types), "conditions": counted(conds), "skeletons": skeletons,
            "attachments": attachments, "ragdolls": ragdolls, "joints": joints, "characters": characters,
            "binary_files": binary_files, "parse_errors": docs.errors}
    notes = []
    if binary_files:
        notes.append(f"{len(binary_files)} binary .scn/.res read with the RSRC reader")
    return Report(data=data, markdown=_markdown(data, summary), summary=summary, notes=notes)


def _top_level(project: Project) -> set[str]:
    """Walked scenes that no other walked scene instances (gameplay roots rather than parts)."""
    inner = {i for subs in project.instances.values() for i in subs}
    return set(project._walks) - inner


def _markdown(d: dict, s: dict) -> str:
    md = [f"{s['animations']} Animation resources ({s['tracks']} tracks, {s['keys']} keys; method tracks "
          f"{s['method_tracks']}, audio tracks {s['audio_tracks']}), {s['players']} AnimationPlayers/libraries, "
          f"{s['trees']} AnimationTrees/graphs ({s['state_machines']} state machines, {s['states']} states, "
          f"{s['transitions']} transitions), {s['skeletons']} skeletons saved in scenes, {s['ragdoll_scenes']} scenes "
          f"with PhysicalBone3D ({s['physical_bones']} bones), {s['joints']} joint nodes.", "",
          "## Characters (scene -> model -> skeleton -> animation -> ragdoll)", "",
          f"{s['characters']} skeleton links ({s['characters_animated']} driven by an AnimationPlayer/Tree, "
          f"{s['characters_ragdoll']} with PhysicalBone3D bodies). Top level = the scene is not instanced by another.", "",
          md_table(["Scene", "Top level", "Model", "Skeleton", "Bones", "Root", "Skinned meshes", "Players (animations)",
                    "Trees", "Ragdoll bodies"],
                   [(f"`{c['scene']}`", "yes" if c.get("top_level") else "", f"`{c['model']}`" if c["model"] else "-",
                     c["skeleton"], c["bones"], ", ".join(c["roots"]),
                     len(c["skinned_meshes"]), ", ".join(f"{p['node']} ({p['animations']})" for p in c["animation_players"]) or "-",
                     ", ".join(t["node"] for t in c["animation_trees"]) or "-", c["ragdoll"]["bodies"])
                    for c in sorted(d["characters"], key=lambda c: (not c.get("top_level"), -c["bones"], c["scene"]))]), "",
          "## AnimationTrees", ""]
    rows = []
    for t in d["trees"]:
        st = t.get("stats") or {}
        rows.append((f"`{t['file']}`", t["node"], t.get("type") or "-", st.get("state_machines", "-"), st.get("states", "-"),
                     st.get("transitions", "-"), len(st.get("animations", {})) if st else "-",
                     ", ".join(st.get("conditions", {})) if st else (t.get("tree_resource") or "-")))
    md += [md_table(["File", "Node", "Root type", "State machines", "States", "Transitions", "Animations",
                     "Conditions / tree resource"], rows), "",
           "Node classes over all graphs: " + ", ".join(f"{k} {v}" for k, v in d["tree_node_types"].items()), ""]
    for t in d["trees"]:
        g = t.get("graph")
        if not g:
            continue
        for name, sm in _state_machines(g):
            md += [f"### {t['file']} :: {name}", "",
                   "States: " + ", ".join(k for k in sm["states"] if k not in ("Start", "End")), "",
                   md_table(["From", "To", "Mode", "Condition", "Expression", "Xfade"],
                            [(x["from"], x["to"], x.get("advance_mode"), x.get("advance_condition") or "-",
                              x.get("advance_expression") or "-", x.get("xfade_time", 0)) for x in sm["transitions"]]), ""]
    md += ["## Animations", "",
           md_table(["File", "Name", "Length", "Loop", "Tracks", "Keys", "By type", "Methods"],
                    [(f"`{a['file']}`", a["name"], a["length"], a["loop_mode"], a["track_count"], a["keys"],
                      ", ".join(f"{k} {v}" for k, v in a["by_type"].items()), ", ".join(a["methods"]) or "-")
                     for a in d["animations"]]), "",
           "## AnimationPlayers and libraries", "",
           md_table(["File", "Node", "Libraries"],
                    [(f"`{p['file']}`", p["node"], "; ".join(f"{k}: {v if isinstance(v, dict) else len(v)}"
                                                             for k, v in p["libraries"].items())) for p in d["players"]]), "",
           "## Skeletons saved in scenes", "",
           md_table(["File", "Node", "Bones", "Roots"], [(f"`{x['file']}`", x["node"], x["bones"], ", ".join(x["roots"]))
                                                         for x in d["skeletons"]]), "",
           "## Ragdolls (PhysicalBone3D)", "",
           md_table(["File", "Physical bones", "Built-in joints", "Joint nodes", "Joint node types", "Mass"],
                    [(f"`{r['file']}`", r["physical_bones"], r["builtin_joints"], r["joint_nodes"],
                      ", ".join(f"{k} {v}" for k, v in r["joint_node_types"].items()), r["mass_total"]) for r in d["ragdolls"]]),
           "", "## Joints", "",
           md_table(["File", "Node", "Type", "A", "B"],
                    [(f"`{j['file']}`", j["path"], j["type"], j.get("node_a", "-"), j.get("node_b", "-")) for j in d["joints"]])]
    return "\n".join(md)


def _state_machines(g: dict, prefix: str = ""):
    """(path, state machine) of every state machine inside a graph."""
    if g.get("type") == "AnimationNodeStateMachine":
        yield prefix or "(root)", g
        for name, s in g["states"].items():
            yield from _state_machines(s, f"{prefix}/{name}" if prefix else name)
    for name, n in (g.get("nodes") or {}).items():
        yield from _state_machines(n, f"{prefix}/{name}" if prefix else name)
    for i, p in enumerate(g.get("points") or []):
        yield from _state_machines(p["node"], f"{prefix}/point{i}")
