"""``gst analyze joints``: physics joints per prefab root and third-party ragdoll components (ports of
``peak/char_bodyparts.py`` + ``char_tables.py``, ``repo/hinge_shader_scan.py``, ``safari-tv/char_ragdoll.py``).

Every Configurable/Hinge/Character/Fixed/Spring joint (3D and 2D) of the non-scene files (``scenes=1`` adds scenes),
grouped by prefab root: the joint's GameObject and path, the connected body's GameObject, anchors and axis,
limits, springs, motors and drives by joint type, break force/torque, the Rigidbody on the same GameObject
(mass, damping, kinematic, interpolation) and the MonoBehaviour classes next to it (``Bodypart``...).

Ragdoll frameworks are found by MonoBehaviour class name (``analysis:prefabs`` ``data.scripts``): FIMSpace
``RagdollAnimator``/``RagdollAnimator2``, RootMotion ``PuppetMaster``/``BehaviourPuppet``, and anything named
``*Ragdoll*``; their serialized settings (scalars, chains, bones named through the prefab tree) are dumped per
prefab to ``analysis/joints/<file>/<prefab>__<go>.json``.
"""

from __future__ import annotations

import collections
import json
import re
import shutil
from typing import Any

from gst.analysis import Report, UNITY, analysis, counted, md_table
from gst.analysis.unity import JOINTS, UnityFile, basename, builtin, is_scene, ptr
from gst.analysis.unity.animcore import file_filter, short_float

RAGDOLL_RX = re.compile(r"ragdoll|puppetmaster|behaviourpuppet|behaviourfall|activeragdoll|muscle(?:s)?controller", re.I)
MOTION = {0: "Locked", 1: "Limited", 2: "Free"}


def _v3(v) -> list | None:
    if not isinstance(v, dict):
        return None
    try:
        return [round(float(v[k]), 4) for k in "xyz" if k in v] or None
    except (TypeError, ValueError):
        return None


def _f(v):
    return short_float(v, 4)


def _limit(v: Any) -> Any:
    return _f(v.get("limit")) if isinstance(v, dict) else _f(v)


def _drive(v: Any) -> dict | None:
    if not isinstance(v, dict):
        return None
    return {"spring": _f(v.get("positionSpring")), "damper": _f(v.get("positionDamper")),
            "max_force": _f(v.get("maximumForce"))}


def joint_detail(t: str, j: dict) -> dict:
    """Type-specific settings of a joint typetree."""
    out: dict[str, Any] = {"anchor": _v3(j.get("m_Anchor")), "connected_anchor": _v3(j.get("m_ConnectedAnchor")),
                           "axis": _v3(j.get("m_Axis")), "break_force": _f(j.get("m_BreakForce")),
                           "break_torque": _f(j.get("m_BreakTorque")), "enable_collision": bool(j.get("m_EnableCollision")),
                           "auto_anchor": bool(j.get("m_AutoConfigureConnectedAnchor")),
                           "mass_scale": _f(j.get("m_MassScale", 1.0)), "connected_mass_scale": _f(j.get("m_ConnectedMassScale", 1.0))}
    if t in ("HingeJoint", "HingeJoint2D"):
        lim = j.get("m_Limits") or j.get("m_AngleLimits") or {}
        out["use_limits"] = bool(j.get("m_UseLimits"))
        out["limits"] = [_f(lim.get("min", lim.get("m_LowerAngle"))), _f(lim.get("max", lim.get("m_UpperAngle")))] if lim else None
        out["use_spring"] = bool(j.get("m_UseSpring"))
        sp = j.get("m_Spring") or {}
        out["spring"] = {"spring": _f(sp.get("spring")), "damper": _f(sp.get("damper")),
                         "target": _f(sp.get("targetPosition"))} if isinstance(sp, dict) and sp else None
        out["use_motor"] = bool(j.get("m_UseMotor"))
        mo = j.get("m_Motor") or {}
        out["motor"] = {"velocity": _f(mo.get("targetVelocity", mo.get("m_MotorSpeed"))),
                        "force": _f(mo.get("force", mo.get("m_MaximumMotorForce"))), "free_spin": bool(mo.get("freeSpin"))} \
            if isinstance(mo, dict) and mo else None
    elif t == "ConfigurableJoint":
        out["motion"] = {k: MOTION.get(j.get(f"m_{k}Motion"), j.get(f"m_{k}Motion"))
                         for k in ("X", "Y", "Z", "AngularX", "AngularY", "AngularZ")}
        out["angular_x"] = [_limit(j.get("m_LowAngularXLimit")), _limit(j.get("m_HighAngularXLimit"))]
        out["angular_y"] = _limit(j.get("m_AngularYLimit"))
        out["angular_z"] = _limit(j.get("m_AngularZLimit"))
        out["linear_limit"] = _limit(j.get("m_LinearLimit"))
        out["drives"] = {k: _drive(j.get(f"m_{k}Drive")) for k in ("X", "Y", "Z", "AngularX", "AngularYZ", "Slerp")}
        out["rotation_drive_mode"] = {0: "XYAndZ", 1: "Slerp"}.get(j.get("m_RotationDriveMode"), j.get("m_RotationDriveMode"))
        out["projection"] = {"mode": j.get("m_ProjectionMode"), "distance": _f(j.get("m_ProjectionDistance")),
                             "angle": _f(j.get("m_ProjectionAngle"))}
        out["secondary_axis"] = _v3(j.get("m_SecondaryAxis"))
        out["world_space"] = bool(j.get("m_ConfiguredInWorldSpace"))
        out["swap_bodies"] = bool(j.get("m_SwapBodies"))
    elif t == "CharacterJoint":
        out["swing_axis"] = _v3(j.get("m_SwingAxis"))
        out["twist"] = [_limit(j.get("m_LowTwistLimit")), _limit(j.get("m_HighTwistLimit"))]
        out["swing"] = [_limit(j.get("m_Swing1Limit")), _limit(j.get("m_Swing2Limit"))]
        out["projection"] = bool(j.get("m_EnableProjection"))
    elif t in ("SpringJoint", "SpringJoint2D"):
        out.update({"spring": _f(j.get("m_Spring", j.get("m_Frequency"))), "damper": _f(j.get("m_Damper", j.get("m_DampingRatio"))),
                    "min_distance": _f(j.get("m_MinDistance")), "max_distance": _f(j.get("m_MaxDistance", j.get("m_Distance"))),
                    "tolerance": _f(j.get("m_Tolerance"))})
    elif t == "DistanceJoint2D":
        out.update({"distance": _f(j.get("m_Distance")), "max_distance_only": bool(j.get("m_MaxDistanceOnly"))})
    return {k: v for k, v in out.items() if v is not None}


def rigidbody(tree: dict | None) -> dict | None:
    if not tree:
        return None
    out = {"mass": _f(tree.get("m_Mass"))}
    for k in ("m_Drag", "m_AngularDrag", "m_LinearDamping", "m_AngularDamping"):
        if k in tree:
            out[k[2:].lower()] = _f(tree[k])
    out.update({"kinematic": bool(tree.get("m_IsKinematic")), "gravity": bool(tree.get("m_UseGravity", True)),
                "interpolate": tree.get("m_Interpolate"), "collision_detection": tree.get("m_CollisionDetection")})
    return out


def family(name: str) -> str:
    """Root name without clone/copy suffixes: ``Module - X (3)`` -> ``Module - X``."""
    return re.sub(r"(\s*\(\d+\)|\s*\(Clone\))+$", "", name or "").strip()


def mono_summary(tree: Any, names: dict[int, str], depth: int = 0) -> Any:
    """A ragdoll MonoBehaviour typetree with PPtrs named through the prefab (bounded depth and list sizes)."""
    if depth > 7:
        return "..."
    if isinstance(tree, dict):
        if "m_PathID" in tree and "m_FileID" in tree and len(tree) <= 3:
            fid, pid = ptr(tree)
            return None if not pid else (names.get(pid, f"#{pid}") if fid == 0 else f"ext{fid}:{pid}")
        return {k: mono_summary(v, names, depth + 1) for k, v in tree.items()
                if k not in ("m_GameObject", "m_Script", "m_ObjectHideFlags", "m_EditorHideFlags", "m_EditorClassIdentifier")}
    if isinstance(tree, list):
        return [mono_summary(v, names, depth + 1) for v in tree[:128]] + ([f"... {len(tree)}"] if len(tree) > 128 else [])
    if isinstance(tree, float):
        return _f(tree)
    return tree


@analysis("joints", engines=UNITY, needs=("unity.inventory", "unity.install"), uses=("analysis:prefabs",),
          description="Physics joints per prefab root (limits, drives, springs, masses) and ragdoll components", version=1,
          options={"file": "regex on install relpaths", "scenes": "1 = also scene files",
                   "ragdoll": "regex on MonoBehaviour class names treated as ragdoll components (adds to the default)"})
def joints(ctx) -> Report:
    from gst.analysis.unity.prefabs import Graph
    from gst.engines.unity import loader

    scenes = str(ctx.opt("scenes") or "") in ("1", "true", "yes")
    fpat = file_filter(ctx)
    extra = re.compile(ctx.opt("ragdoll"), re.I) if ctx.opt("ragdoll") else None
    pref = ctx.load("prefabs") or {}
    rag_classes = sorted(s for s in ((pref.get("data") or {}).get("scripts") or {})
                         if RAGDOLL_RX.search(s) or (extra and extra.search(s)))
    rag_prefabs: dict[tuple, dict] = {}
    for row in (pref.get("data") or {}).get("prefabs") or []:
        hit = sorted(set(row.get("scripts") or {}) & set(rag_classes))
        if hit:
            rag_prefabs[(row["file"], row.get("cab") or "", row["transform"])] = {"row": row, "classes": hit}
    side = ctx.side_dir()
    if side.is_dir():
        shutil.rmtree(side, ignore_errors=True)
    wanted_files = set(ctx.inventory_files(JOINTS)) | {k[0] for k in rag_prefabs}
    roots: dict[tuple, dict] = {}
    ragdolls: list[dict] = []
    for rel in sorted(wanted_files):
        if fpat and not fpat.search(rel):
            continue
        ctx.log(f"reading {rel}")
        with UnityFile(ctx, rel) as uf:
            for sf in uf.files():
                if is_scene(sf.name) and not scenes:
                    continue
                cab = sf.name if basename(sf.name) != basename(rel) else None
                objects = sf.objects
                types = {pid: loader.type_name(o) for pid, o in objects.items()}
                jpids = [pid for pid, t in types.items() if t in JOINTS]
                rag_here = {k: v for k, v in rag_prefabs.items() if k[0] == rel and k[1] == (cab or "")}
                if not jpids and not rag_here:
                    continue

                def script_of(pid, _o=objects):
                    info = uf.script(_o[pid]) if pid in _o else None
                    return info.class_name if info else ""

                g = Graph.from_objects(list(objects.values()), builtin, types.get, script_of, loader.type_name)
                go_tf = {tf["go"]: tpid for tpid, tf in g.tfs.items()}

                def path_of(tpid: int) -> tuple[str, int]:
                    parts, cur, seen = [], tpid, set()
                    while cur in g.tfs and cur not in seen:
                        seen.add(cur)
                        parts.append(g.gos.get(g.tfs[cur]["go"], {}).get("name", ""))
                        father = g.tfs[cur]["father"]
                        if not father or father not in g.tfs:
                            break
                        cur = father
                    return "/" + "/".join(reversed(parts)), cur

                rb_cache: dict[int, dict | None] = {}

                def body_of_go(go_pid: int):
                    for c in g.gos.get(go_pid, {}).get("comps", []):
                        if types.get(c) == "Rigidbody" or types.get(c) == "Rigidbody2D":
                            if c not in rb_cache:
                                rb_cache[c] = rigidbody(builtin(objects[c]))
                            return rb_cache[c]
                    return None

                for jp in sorted(jpids):
                    t = types[jp]
                    tree = builtin(objects[jp]) or {}
                    go_pid = ptr(tree.get("m_GameObject"))[1]
                    tpid = go_tf.get(go_pid)
                    path, root_tf = path_of(tpid) if tpid is not None else ("?", None)
                    root_go = g.tfs[root_tf]["go"] if root_tf in g.tfs else None
                    root_name = g.gos.get(root_go, {}).get("name", "?") if root_go is not None else "?"
                    cb = ptr(tree.get("m_ConnectedBody") or tree.get("m_ConnectedRigidBody"))
                    connected = None
                    if cb[1] and cb[0] == 0 and cb[1] in objects:
                        cgo = loader.raw_gameobject_pid(objects[cb[1]])
                        connected = g.gos.get(cgo, {}).get("name") if cgo else None
                    elif cb[1]:
                        connected = f"ext{cb[0]}:{cb[1]}"
                    comps = g.gos.get(go_pid, {}).get("comps", [])
                    scripts = sorted({script_of(c) for c in comps if types.get(c) == "MonoBehaviour"} - {""})
                    key = (rel, cab or "", root_tf)
                    r = roots.setdefault(key, {"file": rel, **({"cab": cab} if cab else {}), "root": root_name,
                                               "family": family(root_name), "transform": root_tf, "joints": []})
                    r["joints"].append({"type": t, "path_id": jp, "go": g.gos.get(go_pid, {}).get("name", "?"),
                                        "path": path, "depth": path.count("/") - 1, "connected": connected,
                                        "connected_body": bool(cb[1]), "body": body_of_go(go_pid), "scripts": scripts,
                                        **joint_detail(t, tree)})
                for key, r in roots.items():  # every Rigidbody of a jointed root (the root body has no joint)
                    if key[0] != rel or key[1] != (cab or "") or "bodies" in r or r["transform"] not in g.tfs:
                        continue
                    bodies = []
                    for tpid, _depth, p in g.walk(r["transform"]):
                        go_pid = g.tfs[tpid]["go"]
                        for c, ct in g.components(tpid):
                            if ct in ("Rigidbody", "Rigidbody2D"):
                                bodies.append({"go": g.gos.get(go_pid, {}).get("name", "?"), "path": p,
                                               **(body_of_go(go_pid) or {}),
                                               "joint": any(types.get(x) in JOINTS for x in g.gos.get(go_pid, {}).get("comps", [])),
                                               "scripts": sorted({script_of(x) for x in g.gos.get(go_pid, {}).get("comps", [])
                                                                  if types.get(x) == "MonoBehaviour"} - {""})})
                    r["bodies"] = bodies
                    masses = [b["mass"] for b in bodies if isinstance(b.get("mass"), (int, float))]
                    r["total_mass"] = round(sum(masses), 3) if masses else None
                for (_f_, _c, root_tf), info in sorted(rag_here.items()):
                    row = info["row"]
                    names = {}
                    for tpid, _depth, p in g.walk(root_tf):
                        names[tpid] = p
                        names[g.tfs[tpid]["go"]] = p
                    comps_out = []
                    for tpid, _depth, p in g.walk(root_tf):
                        for c, ct in g.components(tpid):
                            if ct == "MonoBehaviour" and script_of(c) in info["classes"]:
                                tree = uf.mono(objects[c])
                                comps_out.append({"class": script_of(c), "node": p, "path_id": c,
                                                  "data": mono_summary(tree, names) if tree is not None else None})
                    folder = basename(rel) + (f"__{cab}" if cab else "")
                    out = ctx.write_side_json({"file": rel, "cab": cab, "prefab": row["name"], "go": row["go"],
                                               "components": comps_out}, folder, f"{row['name'] or 'unnamed'}__{row['go']}.json")
                    ragdolls.append({"file": rel, **({"cab": cab} if cab else {}), "prefab": row["name"], "go": row["go"],
                                     "classes": info["classes"], "components": len(comps_out),
                                     "globals": _globals(comps_out), "detail": out.relative_to(ctx.workspace).as_posix()})
    return build_report(list(roots.values()), ragdolls, rag_classes)


def _globals(comps: list[dict]) -> dict:
    """Scalar settings of the ragdoll components (top level and one level down, e.g. RagdollAnimator2.handler)."""
    out = {}
    for c in comps:
        d = c.get("data")
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            if isinstance(v, (int, float, bool, str)) and not k.startswith("m_"):
                out[f"{c['class']}.{k}"] = v
            elif isinstance(v, dict):
                for k2, v2 in v.items():
                    if isinstance(v2, (int, float, bool)) and not k2.startswith("m_"):
                        out[f"{c['class']}.{k}.{k2}"] = v2
    return dict(sorted(out.items())[:80])


def build_report(roots: list[dict], ragdolls: list[dict], rag_classes: list[str]) -> Report:
    for r in roots:
        r["joints"].sort(key=lambda j: (j["path"], j["type"], j["path_id"]))
        r["counts"] = counted(collections.Counter(j["type"] for j in r["joints"]))
        masses = [j["body"]["mass"] for j in r["joints"] if j.get("body") and isinstance(j["body"].get("mass"), (int, float))]
        r["joint_bodies_mass"] = round(sum(masses), 3) if masses else None
    roots.sort(key=lambda r: (r["file"], r.get("cab") or "", r["root"], r["transform"] or 0))
    ragdolls.sort(key=lambda r: (r["file"], r["prefab"], r["go"]))
    by_type = collections.Counter(j["type"] for r in roots for j in r["joints"])
    fams: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in roots:
        for j in r["joints"]:
            fams[r["family"].split(" - ")[0] if " - " in r["family"] else r["family"]][j["type"]] += 1
    families = sorted(({"family": f, "joints": sum(c.values()), "prefabs": sum(1 for r in roots if (r["family"].split(" - ")[0]
                                                                                                   if " - " in r["family"] else r["family"]) == f),
                        "types": counted(c)} for f, c in fams.items()), key=lambda x: (-x["joints"], x["family"]))
    summary = {"joints": sum(by_type.values()), "types": counted(by_type), "prefabs_with_joints": len(roots),
               "ragdoll_classes": rag_classes, "ragdoll_prefabs": len(ragdolls)}
    data = {"families": families, "prefabs": roots, "ragdolls": ragdolls}
    md = [f"{summary['joints']} joints ({', '.join(f'{k} {v}' for k, v in by_type.items()) or '-'}) in "
          f"{summary['prefabs_with_joints']} prefab roots. Ragdoll component classes: "
          f"{', '.join(f'`{c}`' for c in rag_classes) or 'none found'} ({len(ragdolls)} prefabs).", "",
          "## By name family (text before the first ` - `)", "",
          md_table(["Family", "Prefabs", "Joints", "Types"], [(f"`{f['family']}`", f["prefabs"], f["joints"],
                                                               ", ".join(f"{k} {v}" for k, v in f["types"].items()))
                                                              for f in families[:60]]), "",
          "## Prefabs", "", md_table(["Prefab", "File", "Joints", "Types", "Rigidbodies", "Total mass"],
                                     [(f"`{r['root']}`", basename(r["file"]), len(r["joints"]),
                                       ", ".join(f"{k} {v}" for k, v in r["counts"].items()), len(r.get("bodies") or []),
                                       r.get("total_mass")) for r in roots]), ""]
    rich = [r for r in roots if r["counts"].get("ConfigurableJoint") or r["counts"].get("CharacterJoint")]
    seen_fam = set()
    for r in rich:
        if r["family"] in seen_fam:
            continue
        seen_fam.add(r["family"])
        rows = []
        for j in r["joints"]:
            b = j.get("body") or {}
            if j["type"] == "ConfigurableJoint":
                d = j.get("drives") or {}
                ax = d.get("AngularX") or {}
                yz = d.get("AngularYZ") or {}
                lim = f"X {j.get('angular_x')} Y {j.get('angular_y')} Z {j.get('angular_z')}"
                drive = f"{ax.get('spring')}/{ax.get('damper')} ; {yz.get('spring')}/{yz.get('damper')}"
            elif j["type"] == "CharacterJoint":
                lim = f"twist {j.get('twist')} swing {j.get('swing')}"
                drive = "-"
            else:
                lim, drive = j.get("limits") or "-", "-"
            rows.append((j["go"], j["type"], j.get("connected") or "-", b.get("mass"),
                         b.get("angulardamping", b.get("angulardrag")), lim, drive, ", ".join(j.get("scripts") or [])))
        free = [f"{b['go']} ({b.get('mass')})" for b in r.get("bodies") or [] if not b.get("joint")]
        md += [f"## `{r['root']}` ({basename(r['file'])}): {len(r['joints'])} joints", "",
               f"{len(r.get('bodies') or [])} Rigidbodies, total mass {r.get('total_mass')}; without a joint: "
               f"{', '.join(free) or '-'}.", "",
               md_table(["GameObject", "Type", "Connected", "Mass", "Ang. damping", "Limits", "Drive X ; YZ (spring/damper)",
                         "Scripts"], rows), ""]
    if ragdolls:
        md += ["## Ragdoll components", "", md_table(["Prefab", "File", "Classes", "Components", "Detail"],
                                                    [(f"`{r['prefab']}`", basename(r["file"]), ", ".join(r["classes"]),
                                                      r["components"], f"`{r['detail']}`") for r in ragdolls]), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)
