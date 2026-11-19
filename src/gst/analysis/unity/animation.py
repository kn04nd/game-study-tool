"""``gst analyze animation``: every AnimationClip decoded (port of ``repo/animlib.py`` + ``player_anim.py``,
``safari-tv/mdl_clips.py``, ``peak/mdl_clips.py``).

Per clip: length, sample rate, loop, legacy/compressed, Streamed/Dense/Constant curve counts, generic bindings
(class, kind, keys, constant?, value range; rotations as Euler degrees), humanoid (muscle curves, named after
Unity's ``HumanTrait`` muscles and Root/IK goal/TDoF curves: ``analysis.unity.humanoid``) vs generic
(Transform curves) vs blendshape vs property clips, root motion (``m_HasGenericRootTransform``, motion curves,
``m_DeltaPose`` displacement, average speed), AnimationEvents and which transforms the clip moves.

Binding paths are CRC32 hashes of the path relative to the Animator's GameObject. They are resolved with the
maps built from the prefab trees (``analysis:prefabs`` details): first the Animators whose controller (or
override controller) lists the clip (``analysis:controller``) or the legacy Animation components that list it,
then every Animator of the game (``resolved_global``). What stays unresolved is reported as ``#xxxxxxxx``.
Blendshape bindings are named with ``analysis:rig`` (``blendShape.<name>``) when it ran.

Outputs: ``analysis/animation.json`` (one row per clip + per-controller totals) and, per clip,
``analysis/animation/<file>/<clip>__<path id>.json`` with every binding (``bindings=0`` turns them off).
Options: ``clip`` (regex on clip names), ``file`` (regex on install relpaths).
"""

from __future__ import annotations

import collections
import json
import re
import shutil

from gst.analysis import Report, UNITY, analysis, counted, md_table, stats
from gst.analysis.unity import basename, builtin
from gst.analysis.unity.animcore import (clip_bindings, clip_kind, clip_key, crc, decode_clip, iter_typed,
                                         legacy_bindings, short_float, summarize_bindings, animator_maps)

# property names worth trying on non-Transform float bindings (attribute = CRC32 of the property path)
PROPERTY_NAMES = ("m_IsActive", "m_Enabled", "m_Volume", "m_Pitch", "m_Intensity", "m_Range", "m_SpotAngle",
                  "m_Color.r", "m_Color.g", "m_Color.b", "m_Color.a", "m_Sprite", "m_Alpha", "m_FlipX", "m_FlipY",
                  "m_LocalPosition.x", "m_LocalPosition.y", "m_LocalPosition.z", "m_SizeDelta.x", "m_SizeDelta.y",
                  "m_AnchoredPosition.x", "m_AnchoredPosition.y", "m_Weight", "m_FieldOfView", "material._Color.r",
                  "material._Color.g", "material._Color.b", "material._Color.a", "material._EmissionColor.r",
                  "material._EmissionColor.g", "material._EmissionColor.b", "material._EmissionColor.a")


class Maps:
    """Path maps of every Animator/Animation of the detailed prefabs, indexed by controller and by legacy clip."""

    def __init__(self, entries: list[dict], bases: dict[str, str] | None = None):
        self.entries = entries
        self.bases = dict(bases or {})  # override controller -> base controller (an Animator may hold either)
        self.by_controller: dict[str, list[dict]] = collections.defaultdict(list)
        self.by_clip: dict[str, list[dict]] = collections.defaultdict(list)
        self.global_map: dict[int, str] = {}
        for e in entries:
            if e.get("controller"):
                self.by_controller[e["controller"]].append(e)
            for c in e.get("clips") or []:
                self.by_clip[c].append(e)
            for h, p in e["map"].items():
                old = self.global_map.get(h)
                if old is None or (len(p), p) < (len(old), old):
                    self.global_map[h] = p

    def own(self, controllers: list[str], clip_name: str) -> tuple[dict[int, str], str]:
        merged: dict[int, str] = {}
        how = ""
        for c in list(controllers) + [self.bases[c] for c in controllers if c in self.bases]:
            for e in self.by_controller.get(c, []):
                merged.update(e["map"])
                how = "controller"
        for e in self.by_clip.get(clip_name, []):
            merged.update(e["map"])
            how = how or "animation"
        return merged, how


def load_maps(ctx, bases: dict[str, str] | None = None) -> Maps:
    pref = ctx.load("prefabs")
    entries: list[dict] = []
    if not pref:
        return Maps(entries, bases)
    from gst.analysis.unity.prefabs import detailed_rows

    for row in detailed_rows(pref):  # prefab roots, then the animated objects of scenes (prefabs scenes=animators)
        if not (row.get("animators") or (row.get("components") or {}).get("Animation")):
            continue
        p = ctx.workspace / row["detail"]
        try:
            with open(p, encoding="utf-8") as fh:
                nodes = json.load(fh).get("nodes") or []
        except (OSError, ValueError):
            continue
        for e in animator_maps(nodes):
            e["prefab"] = row["name"]
            e["file"] = row["file"]
            entries.append(e)
    return Maps(entries, bases)


def load_clip_controllers(ctx) -> dict[str, list[str]]:
    """clip key -> names of the controllers / override controllers that list it (``analysis:controller``)."""
    doc = ctx.load("controller")
    out: dict[str, set] = collections.defaultdict(set)
    if not doc:
        return {}
    for c in (doc.get("data") or {}).get("controllers") or []:
        for clip in c.get("clips") or []:
            if clip.get("key"):
                out[clip["key"]].add(c["name"])
    return {k: sorted(v) for k, v in out.items()}


def attr_names(ctx, extra: list[str]) -> dict[int, str]:
    names = {crc(n): n for n in PROPERTY_NAMES}
    for n in extra:
        names.setdefault(crc(n), n)
    rig = ctx.load("rig")
    for n in ((rig or {}).get("data") or {}).get("blendshape_names") or []:
        names.setdefault(crc("blendShape." + n), "blendShape." + n)
    return names


def analyze_clip(tree: dict, *, controllers: list[str], maps: Maps, attrs: dict[int, str]) -> tuple[dict, list[dict]]:
    """Summary row + binding rows of one AnimationClip typetree."""
    name = tree.get("m_Name") or ""
    mc = tree.get("m_MuscleClip") or {}
    legacy = bool(tree.get("m_Legacy"))
    own, how = maps.own(controllers, name)
    counts = {"own": 0, "global": 0, "missing": 0}

    def resolve_path(h: int):
        if h in own:
            counts["own"] += 1
            return own[h]
        if h in maps.global_map:
            counts["global"] += 1
            return maps.global_map[h]
        counts["missing"] += 1
        return None

    def resolve_attr(b: dict):
        return attrs.get(b["attr"])

    if legacy and not clip_bindings(tree):
        rows = legacy_bindings(tree)
        decoded = {"nstream": 0, "ndense": 0, "nconst": 0, "frames": 0}
        how = "legacy"
    else:
        decoded = decode_clip(mc)
        rows = summarize_bindings(tree, decoded, resolve_path, resolve_attr)
    moved = sorted({r["path"] for r in rows if r["class"] == "Transform" and r["resolved"] and not r["const"]})
    by_kind = collections.Counter(r["kind"] for r in rows)
    by_class = collections.Counter(r["class"] for r in rows)
    dp = (((mc.get("m_DeltaPose") or {}).get("m_RootX") or {}).get("t")) or {}
    avg = mc.get("m_AverageSpeed") or {}
    delta = [short_float(dp.get(k, 0.0), 3) for k in "xyz"]
    speed = [short_float(avg.get(k, 0.0), 3) for k in "xyz"] if isinstance(avg, dict) else []
    root = {"generic_root": bool(tree.get("m_HasGenericRootTransform")),
            "motion_curves": bool(tree.get("m_HasMotionFloatCurves")), "delta": delta, "avg_speed": speed,
            "avg_angular_speed": short_float(mc.get("m_AverageAngularSpeed", 0.0), 3)}
    root["root_motion"] = bool(root["generic_root"] or root["motion_curves"] or by_kind.get("muscle") and
                               any(abs(v) > 1e-4 for v in delta if isinstance(v, float)))
    events = [{"time": short_float(e.get("time"), 3), "function": e.get("functionName"),
               **({"string": e["data"]} if e.get("data") else {}),
               **({"int": e["intParameter"]} if e.get("intParameter") else {}),
               **({"float": short_float(e["floatParameter"])} if e.get("floatParameter") else {})}
              for e in tree.get("m_Events") or []]
    start, stop = float(mc.get("m_StartTime") or 0.0), float(mc.get("m_StopTime") or 0.0)
    row = {"name": name, "length": round(stop - start, 3), "fps": short_float(tree.get("m_SampleRate"), 3),
           "loop": bool(mc.get("m_LoopTime")), "loop_blend": bool(mc.get("m_LoopBlend")), "legacy": legacy,
           "compressed": bool(tree.get("m_Compressed")), "wrap_mode": tree.get("m_WrapMode"), "kind": clip_kind(rows),
           "curves": {"streamed": decoded["nstream"], "dense": decoded["ndense"], "constant": decoded["nconst"]},
           "bindings": len(rows), "animated": sum(1 for r in rows if r["const"] is False),
           "by_kind": counted(by_kind), "by_class": counted(by_class),
           "transforms_moved": len(moved), "bones_moved": sorted({p.rsplit("/", 1)[-1] for p in moved if p}),
           **({"muscles_moved": [r["attribute"] for r in sorted((r for r in rows if r["kind"] == "muscle"),
                                                                key=lambda r: r["index"])
                                 if r["group"] in ("muscle", "finger") and not r["const"]],
               "humanoid_groups": counted(collections.Counter(r["group"] for r in rows if r["kind"] == "muscle"))}
              if by_kind.get("muscle") else {}),
           "unresolved": sum(1 for r in rows if not r["resolved"]), "resolved_global": counts["global"],
           "mapped_by": how or ("global" if counts["global"] else "none"), "controllers": controllers,
           "root": root, "events": events}
    return row, rows


@analysis("animation", engines=UNITY, needs=("unity.inventory", "unity.install"),
          uses=("analysis:prefabs", "analysis:controller", "analysis:rig"),
          description="AnimationClips: curves (Streamed/Dense/Constant), CRC32 path map, kinds, root motion, events",
          version=2,
          options={"clip": "regex on clip names", "file": "regex on install relpaths",
                   "bindings": "0 = no per-clip JSON with every binding (default 1)"})
def animation(ctx) -> Report:
    clip_rx = re.compile(ctx.opt("clip"), re.I) if ctx.opt("clip") else None
    per_clip = str(ctx.opt("bindings", "1")) not in ("0", "false", "no")
    clip_ctrl = load_clip_controllers(ctx)
    ctrl_doc = ctx.load("controller")
    bases = {c["name"]: c["base_name"] for c in ((ctrl_doc or {}).get("data") or {}).get("controllers") or []
             if c.get("kind") == "override" and c.get("base_name")}
    maps = load_maps(ctx, bases)
    param_names = [p["name"] for c in ((ctrl_doc or {}).get("data") or {}).get("controllers") or []
                   for p in c.get("params") or [] if not str(p.get("name", "#")).startswith("#")]
    attrs = attr_names(ctx, param_names)
    side = ctx.side_dir()
    if side.is_dir():
        shutil.rmtree(side, ignore_errors=True)
    rows: list[dict] = []
    for rel, _uf, sf, cab, obj, _t in iter_typed(ctx, ("AnimationClip",)):
        tree = builtin(obj)
        if tree is None:
            rows.append({"file": rel, **({"cab": cab} if cab else {}), "path_id": obj.path_id, "name": "?",
                         "error": "typetree read failed"})
            continue
        if clip_rx and not clip_rx.search(tree.get("m_Name") or ""):
            continue
        key = clip_key(sf, 0, obj.path_id)
        row, bindings = analyze_clip(tree, controllers=clip_ctrl.get(key, []), maps=maps, attrs=attrs)
        row = {"file": rel, **({"cab": cab} if cab else {}), "path_id": obj.path_id, "key": key, **row}
        if per_clip:
            folder = basename(rel) + (f"__{cab}" if cab else "")
            p = ctx.write_side_json({"file": rel, "cab": cab, "path_id": obj.path_id, "summary": row,
                                     "bindings": bindings}, folder, f"{row['name'] or 'unnamed'}__{obj.path_id}.json")
            row["detail"] = p.relative_to(ctx.workspace).as_posix()
        rows.append(row)
    return build_report(rows, maps)


def build_report(rows: list[dict], maps: Maps) -> Report:
    rows.sort(key=lambda r: (r["file"], r.get("cab") or "", r["name"], r["path_id"]))
    ok = [r for r in rows if "error" not in r]
    by_ctrl: dict[str, dict] = {}
    for r in ok:
        for c in r["controllers"]:
            e = by_ctrl.setdefault(c, {"clips": 0, "seconds": 0.0, "unresolved": 0, "events": 0, "fps": collections.Counter()})
            e["clips"] += 1
            e["seconds"] = round(e["seconds"] + r["length"], 3)
            e["unresolved"] += r["unresolved"]
            e["events"] += len(r["events"])
            e["fps"][str(r["fps"])] += 1
    for e in by_ctrl.values():
        e["fps"] = counted(e["fps"])
    by_ctrl = dict(sorted(by_ctrl.items()))
    kinds = collections.Counter(r["kind"] for r in ok)
    fps = collections.Counter(str(r["fps"]) for r in ok)
    summary = {"clips": len(ok), "errors": len(rows) - len(ok), "kinds": counted(kinds), "fps": counted(fps),
               "legacy": sum(1 for r in ok if r["legacy"]), "compressed": sum(1 for r in ok if r["compressed"]),
               "with_events": sum(1 for r in ok if r["events"]),
               "root_motion": sum(1 for r in ok if r["root"]["root_motion"]),
               "bindings": sum(r["bindings"] for r in ok), "unresolved_bindings": sum(r["unresolved"] for r in ok),
               "resolved_global": sum(r["resolved_global"] for r in ok),
               "clips_with_controller": sum(1 for r in ok if r["controllers"]), "animator_maps": len(maps.entries)}
    data = {"controllers": by_ctrl, "clips": rows,
            "maps": [{"prefab": e.get("prefab"), "file": e.get("file"), "node": e["node"], "kind": e["kind"],
                      "controller": e.get("controller"), "paths": len(e["map"])} for e in maps.entries]}
    md = [f"{summary['clips']} AnimationClips ({', '.join(f'{k} {v}' for k, v in summary['kinds'].items()) or '-'}); "
          f"sample rates {', '.join(f'{k} fps x{v}' for k, v in summary['fps'].items()) or '-'}; "
          f"{summary['legacy']} legacy, {summary['compressed']} compressed, {summary['with_events']} with events, "
          f"{summary['root_motion']} with root motion. Bindings {summary['bindings']}: "
          f"{summary['unresolved_bindings']} unresolved, {summary['resolved_global']} resolved only through another "
          f"Animator's hierarchy. Path maps from {summary['animator_maps']} Animator/Animation components of the "
          "detailed prefabs.", ""]
    if not maps.entries:
        md += ["> No prefab trees (`gst analyze prefabs` first): binding paths stay as CRC32 hashes.", ""]
    md += ["## Controllers", "", md_table(["Controller", "Clips", "Seconds", "Unresolved", "Events", "FPS"],
                                          [(f"`{c}`", e["clips"], e["seconds"], e["unresolved"], e["events"],
                                            ", ".join(f"{k} x{v}" for k, v in e["fps"].items()))
                                           for c, e in by_ctrl.items()]), ""]
    lengths = stats([r["length"] for r in ok])
    if lengths:
        md += [f"Length (s): min {lengths['min']}, median {lengths['median']}, p90 {lengths['p90']}, max {lengths['max']}.", ""]
    ev = [(r["name"], e["time"], e["function"], e.get("string") or e.get("int") or e.get("float") or "")
          for r in ok for e in r["events"]]
    if ev:
        md += [f"## AnimationEvents ({len(ev)} in {summary['with_events']} clips)", "",
               md_table(["Clip", "Time", "Function", "Parameter"], ev), ""]
    human = [r for r in ok if r.get("muscles_moved") is not None]
    if human:
        from gst.analysis.unity.humanoid import MUSCLE_NAMES, MUSCLE_BONE

        used = collections.Counter(m for r in human for m in r["muscles_moved"])
        groups = collections.Counter()
        for r in human:
            groups.update(r.get("humanoid_groups") or {})
        md += [f"## Humanoid curves ({len(human)} clips)", "",
               "Curve groups (bindings): " + ", ".join(f"{k} {v}" for k, v in sorted(groups.items())) + ". Muscles "
               "named after Unity's `HumanTrait.MuscleName` (index = muscle number 0-94); clips = clips where the "
               "muscle curve is not constant.", "",
               md_table(["#", "Muscle", "Bone", "Clips"], [(i, m, MUSCLE_BONE[m], used.get(m, 0))
                                                          for i, m in enumerate(MUSCLE_NAMES)]), ""]
    md += ["## Clips", "", "Kind: humanoid = muscle curves, generic = Transform curves. Moved = transforms with "
           "non-constant curves. Per-clip bindings in `detail`.", "",
           md_table(["Clip", "File", "Len", "FPS", "Loop", "Kind", "Bind", "Anim", "Moved", "Unres", "Root", "Events",
                     "Controllers"],
                    [(f"`{r['name']}`", basename(r["file"]) + (f" {r['cab']}" if r.get("cab") else ""), r.get("length"),
                      r.get("fps"), "yes" if r.get("loop") else "", r.get("kind", r.get("error")), r.get("bindings"),
                      r.get("animated"), r.get("transforms_moved"), r.get("unresolved"),
                      "yes" if (r.get("root") or {}).get("root_motion") else "", len(r.get("events") or []),
                      ", ".join(r.get("controllers") or [])) for r in rows]), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)
