"""Godot animation, AnimationTree, skeleton and ragdoll structures from parsed text resources (``tres.parse``).
Stdlib; pure functions over the parsed document, so they test on synthetic text.

- :func:`animation` - an ``Animation`` resource: length, loop, step, tracks (type, path, key count, interpolation),
  method-track calls, audio-track streams, animation-track clips. Key counts: dict tracks (value/method/audio/
  bezier/animation) = ``len(keys["times"])``; packed 3D tracks = flat array / stride (position 5, rotation 6,
  scale 5, blend shape 3; Godot 3 transform 12).
- :func:`libraries` - the animations an ``AnimationPlayer`` / ``AnimationLibrary`` holds (Godot 4 ``libraries`` /
  ``_data``, Godot 3 ``anims/<name>``).
- :func:`graph` - an AnimationTree node graph (``tree_root``): state machines (states, transitions with
  advance mode/condition/expression, xfade, switch mode, priority), blend trees (nodes, connections), blend spaces
  (points), animation leaves, filters; :func:`graph_stats` counts it.
- :func:`skeleton`, :func:`physical_bone`, :func:`joint` - Skeleton3D bones, PhysicalBone3D bodies/joints, Joint3D/2D
  nodes (node_a/node_b resolved to scene paths).
"""

from __future__ import annotations

import collections
import re
from typing import Any

from gst.engines.godot.tres import Bulk, Ctor, NodePath, Ref, StringName, ref_path, short, to_json

PACKED_STRIDE = {"position_3d": 5, "rotation_3d": 6, "scale_3d": 5, "blend_shape": 3, "transform": 12}
JOINT_TYPES = {0: "none", 1: "pin", 2: "cone", 3: "hinge", 4: "slider", 5: "6dof"}
JOINT_NODES = ("Generic6DOFJoint3D", "HingeJoint3D", "PinJoint3D", "ConeTwistJoint3D", "SliderJoint3D",
               "PinJoint2D", "GrooveJoint2D", "DampedSpringJoint2D", "Joint3D", "Joint2D")
ANIM_NODE_PREFIX = "AnimationNode"
_TRACK = re.compile(r"^tracks/(\d+)/(.+)$")


def _seq(v: Any) -> list:
    if isinstance(v, Ctor):
        return list(v.args)
    if isinstance(v, (list, tuple)):
        return list(v)
    return []


def _len(v: Any) -> int:
    return v.count if isinstance(v, Bulk) else len(_seq(v))


def track_keys(ttype: str, keys: Any) -> int:
    if isinstance(keys, dict):
        times = keys.get("times")
        return _len(times) if times is not None else _len(keys.get("values"))
    n = _len(keys)
    stride = PACKED_STRIDE.get(ttype)
    return n // stride if stride else n


def animation(doc: dict, props: dict, name: str | None = None) -> dict:
    """Summary of one Animation resource (``props`` of its section)."""
    tracks: dict[int, dict] = {}
    for k, v in props.items():
        m = _TRACK.match(k)
        if m:
            tracks.setdefault(int(m.group(1)), {})[m.group(2)] = v
    out_tracks = []
    by_type: collections.Counter = collections.Counter()
    methods: collections.Counter = collections.Counter()
    audio, clips = [], []
    total = 0
    for idx in sorted(tracks):
        t = tracks[idx]
        ttype = str(t.get("type", "value"))
        keys = t.get("keys")
        n = track_keys(ttype, keys)
        total += n
        by_type[ttype] += 1
        item = {"index": idx, "type": ttype, "path": str(t.get("path", "")), "keys": n}
        for k in ("interp", "loop_wrap", "enabled", "update", "imported", "use_blend"):
            if k in t:
                item[k] = to_json(t[k])
        if isinstance(keys, dict):
            if "update" in keys:
                item["update"] = keys["update"]
            if ttype == "method":
                calls = [str(v.get("method")) for v in _seq(keys.get("values")) if isinstance(v, dict)]
                item["methods"] = calls
                methods.update(calls)
            elif ttype == "audio":
                streams = []
                for c in _seq(keys.get("clips")):
                    if isinstance(c, dict):
                        s = c.get("stream")
                        streams.append(ref_path(doc, s) or (f"SubResource({s.id})" if isinstance(s, Ref) else short(s)))
                item["streams"] = streams
                audio += streams
            elif ttype == "animation":
                names = [str(c) for c in _seq(keys.get("clips"))]
                item["clips"] = names
                clips += names
        out_tracks.append(item)
    loop = props.get("loop_mode", props.get("loop", 0))
    return {"name": str(props.get("resource_name", name or "")) or name, "length": props.get("length", 1.0),
            "loop_mode": to_json(loop), "step": props.get("step"), "tracks": out_tracks, "track_count": len(out_tracks),
            "keys": total, "by_type": dict(sorted(by_type.items())), "methods": dict(sorted(methods.items())),
            "audio": audio, "animation_clips": clips}


def libraries(doc: dict, props: dict) -> dict[str, dict]:
    """``{library name: {animation name: reference}}`` of an AnimationPlayer node or AnimationLibrary resource."""
    out: dict[str, dict] = {}
    if "_data" in props and isinstance(props["_data"], dict):  # AnimationLibrary
        out[""] = {str(k): v for k, v in props["_data"].items()}
    libs = props.get("libraries")
    if isinstance(libs, dict):
        for k, v in libs.items():
            out[str(k)] = {"@library": v}
    for k, v in props.items():
        if k.startswith("anims/"):  # Godot 3: anims/<name> = SubResource(...)
            out.setdefault("", {})[k[len("anims/"):]] = v
        elif k.startswith("libraries/"):  # one property per library (Godot 4.6 binary scenes: "libraries/" = default)
            out[k[len("libraries/"):]] = {"@library": v}
    return out


def _resolve(doc: dict, v: Any) -> tuple[str | None, dict | None, str | None]:
    """A reference -> (type, props, ext path). Sub-resources resolve in ``doc``; ext ones give only the path."""
    if isinstance(v, Ref):
        if v.kind == "sub":
            sr = doc["sub_resources"].get(v.id)
            return (sr["type"], sr["props"], None) if sr else (None, None, None)
        e = doc["ext_resources"].get(v.id) or {}
        return e.get("type"), None, e.get("path")
    return None, None, None


def graph(doc: dict, ntype: str | None, props: dict | None, ext: str | None = None, _depth: int = 0,
          _stack: tuple = ()) -> dict:
    """One AnimationTree node and its children, recursively (cycles cut by the reference stack)."""
    if props is None:
        return {"type": ntype, "external": ext} if ext else {"type": ntype}
    out: dict[str, Any] = {"type": ntype}
    key = id(props)
    if key in _stack:
        return {"type": ntype, "cycle": True}
    stack = _stack + (key,)
    if ntype == "AnimationNodeAnimation":
        out["animation"] = str(props.get("animation", ""))
        for k in ("play_mode", "use_custom_timeline", "timeline_length", "loop_mode"):
            if k in props:
                out[k] = to_json(props[k])
        return out
    if ntype == "AnimationNodeStateMachine":
        states: dict[str, dict] = {}
        for k, v in props.items():
            if k.startswith("states/") and k.endswith("/node"):
                sname = k[len("states/"):-len("/node")]
                if v is None:
                    states[sname] = {"type": None}
                    continue
                t, p, e = _resolve(doc, v)
                states[sname] = graph(doc, t, p, e, _depth + 1, stack)
        trans = []
        raw = _seq(props.get("transitions"))
        for i in range(0, len(raw) - 2, 3):
            frm, to, ref = raw[i], raw[i + 1], raw[i + 2]
            _t, tp, _e = _resolve(doc, ref)
            tp = tp or {}
            item = {"from": str(frm), "to": str(to)}
            for k in ("advance_mode", "advance_condition", "advance_expression", "xfade_time", "switch_mode", "priority",
                      "reset", "break_loop_at_end", "xfade_curve"):
                if k in tp:
                    item[k] = to_json(tp[k]) if not isinstance(tp[k], Ref) else short(tp[k])
            item.setdefault("advance_mode", 1)  # Godot 4 default: enabled (auto needs advance_mode 2)
            trans.append(item)
        out.update(states=states, transitions=trans,
                   state_machine_type=to_json(props.get("state_machine_type", 0)),
                   allow_transition_to_self=bool(props.get("allow_transition_to_self", False)))
        return out
    if ntype == "AnimationNodeBlendTree":
        nodes: dict[str, dict] = {}
        for k, v in props.items():
            if k.startswith("nodes/") and k.endswith("/node"):
                t, p, e = _resolve(doc, v)
                nodes[k[len("nodes/"):-len("/node")]] = graph(doc, t, p, e, _depth + 1, stack)
        conns = []
        raw = _seq(props.get("node_connections"))
        for i in range(0, len(raw) - 2, 3):
            conns.append({"to": str(raw[i]), "port": raw[i + 1], "from": str(raw[i + 2])})
        out.update(nodes=nodes, connections=conns)
        return out
    if ntype in ("AnimationNodeBlendSpace1D", "AnimationNodeBlendSpace2D"):
        pts: dict[int, dict] = {}
        for k, v in props.items():
            m = re.match(r"^blend_point_(\d+)/(node|pos)$", k)
            if m:
                pts.setdefault(int(m.group(1)), {})[m.group(2)] = v
        points = []
        for i in sorted(pts):
            t, p, e = _resolve(doc, pts[i].get("node"))
            points.append({"pos": to_json(pts[i].get("pos")), "node": graph(doc, t, p, e, _depth + 1, stack)})
        out["points"] = points
        for k in ("min_space", "max_space", "snap", "x_label", "y_label", "value_label", "blend_mode", "sync"):
            if k in props:
                out[k] = to_json(props[k])
        return out
    # other nodes (OneShot, TimeScale, Add2, Blend2, Sub2, Transition, Seek, TimeSeek, ...): their settings
    settings = {}
    for k, v in props.items():
        if k == "filters":
            out["filters"] = len(_seq(v))
        elif k.startswith("input_") or k in ("fadein_time", "fadeout_time", "autorestart", "autorestart_delay",
                                                "autorestart_random_delay", "mix_mode", "filter_enabled",
                                                "xfade_time", "allow_transition_to_self", "break_loop_at_end"):
            settings[k] = to_json(v)
    if settings:
        out["settings"] = settings
    return out


def graph_stats(g: dict) -> dict:
    """Counts over a graph: node classes, state machines, states, transitions, animations, conditions, expressions."""
    st = {"node_types": collections.Counter(), "state_machines": 0, "states": 0, "transitions": 0,
          "animations": collections.Counter(), "conditions": collections.Counter(), "expressions": [],
          "external": []}

    def walk(n: dict):
        t = n.get("type")
        if t:
            st["node_types"][t] += 1
        if n.get("external"):
            st["external"].append(n["external"])
        if t == "AnimationNodeAnimation" and n.get("animation"):
            st["animations"][n["animation"]] += 1
        if t == "AnimationNodeStateMachine":
            st["state_machines"] += 1
            st["states"] += sum(1 for s in n["states"] if s not in ("Start", "End"))
            st["transitions"] += len(n["transitions"])
            for tr in n["transitions"]:
                if tr.get("advance_condition"):
                    st["conditions"][str(tr["advance_condition"])] += 1
                if tr.get("advance_expression"):
                    st["expressions"].append(tr["advance_expression"])
            for s in n["states"].values():
                walk(s)
        for child in (n.get("nodes") or {}).values():
            walk(child)
        for pnt in n.get("points") or []:
            walk(pnt["node"])

    walk(g)
    return {"node_types": dict(sorted(st["node_types"].items())), "state_machines": st["state_machines"],
            "states": st["states"], "transitions": st["transitions"], "animations": dict(sorted(st["animations"].items())),
            "conditions": dict(sorted(st["conditions"].items())), "expressions": st["expressions"],
            "external": st["external"]}


def skeleton(props: dict) -> dict:
    bones: dict[int, dict] = {}
    for k, v in props.items():
        m = re.match(r"^bones/(\d+)/(\w+)$", k)
        if m:
            bones.setdefault(int(m.group(1)), {})[m.group(2)] = v
    out = []
    for i in sorted(bones):
        b = bones[i]
        out.append({"index": i, "name": str(b.get("name", "")) if "name" in b else None,
                    "parent": b.get("parent"), "enabled": b.get("enabled", True)})
    names = [b["name"] for b in out if b["name"]]
    return {"bones": len(out), "named": len(names), "names": names,
            "roots": [b["name"] for b in out if b["parent"] in (-1, None) and b["name"]], "detail": out}


def physical_bone(props: dict) -> dict:
    jt = props.get("joint_type", 0)
    out = {"bone_name": str(props.get("bone_name", "")), "joint_type": JOINT_TYPES.get(jt, jt),
           "mass": props.get("mass", 1.0)}
    for k in ("friction", "bounce", "gravity_scale", "linear_damp", "angular_damp", "can_sleep", "collision_layer",
              "collision_mask"):
        if k in props:
            out[k] = to_json(props[k])
    joint = {k[len("joint_constraints/"):]: to_json(v) for k, v in props.items() if k.startswith("joint_constraints/")}
    if joint:
        out["joint_constraints"] = joint
    return out


def resolve_node_path(base: str, rel: str) -> str:
    """A NodePath relative to the node at ``base`` (scene path, "." = root) -> scene path."""
    if rel.startswith("/"):
        return rel
    parts = [] if base in (".", "") else base.split("/")
    for seg in rel.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            else:
                return "../" + rel  # above the scene root
        else:
            parts.append(seg)
    return "/".join(parts) or "."


def joint(node: dict) -> dict:
    props = node["props"]
    out = {"path": node["path"], "type": node.get("type")}
    for side in ("node_a", "node_b"):
        v = props.get(side)
        if isinstance(v, (NodePath, str)) and str(v):
            out[side] = resolve_node_path(node["path"], str(v))
    limits = {k: to_json(v) for k, v in props.items()
              if k not in ("node_a", "node_b", "transform", "script") and not k.startswith("metadata/")}
    if limits:
        out["settings"] = limits
    return out


def is_anim_node(t: str | None) -> bool:
    return bool(t) and t.startswith(ANIM_NODE_PREFIX) and t not in ("AnimationNodeStateMachineTransition",)


__all__ = ["animation", "libraries", "graph", "graph_stats", "skeleton", "physical_bone", "joint", "resolve_node_path",
           "is_anim_node", "StringName"]
