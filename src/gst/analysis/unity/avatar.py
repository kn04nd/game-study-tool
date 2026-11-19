"""``gst analyze avatar``: Avatars (port of ``safari-tv/mdl_avatar.py``).

Per Avatar: skeleton size, skeleton node names (``m_SkeletonNameIDArray`` through ``m_TOS``: paths relative to the
Animator), humanoid or generic, the humanoid mapping (``m_Human.m_HumanBoneIndex`` -> human skeleton node, named by
its ``m_ID`` path hash or through ``m_HumanSkeletonIndexArray``; Mecanim's internal bone order, and the finger bones of
``m_LeftHand``/``m_RightHand``), the root motion bone, the muscle
settings (twist, stretch, feet spacing, translation DoF), the 95 muscles (``HumanTrait.MuscleName``, the bone each
moves and the skeleton node it lands on) and, when the build kept it, ``m_HumanDescription`` (human name -> bone
name, modified muscle limits), which is used to check the index mapping. Skeleton ids are CRC32 of bone names:
they are named from the ``m_TOS`` path whose last segment hashes to them. Which Animators use each Avatar comes from
``analysis:prefabs``.
"""

from __future__ import annotations

import collections

from gst.analysis import Report, UNITY, analysis, md_table
from gst.analysis.unity import basename, builtin
from gst.analysis.unity.animcore import crc, iter_typed, short_float, tos_map, un
from gst.analysis.unity.humanoid import muscle_table

# Mecanim's internal HumanBone order (m_HumanBoneIndex has 25 entries; UpperChest after Chest)
BODY_BONES = ("Hips", "LeftUpperLeg", "RightUpperLeg", "LeftLowerLeg", "RightLowerLeg", "LeftFoot", "RightFoot",
              "Spine", "Chest", "UpperChest", "Neck", "Head", "LeftShoulder", "RightShoulder", "LeftUpperArm",
              "RightUpperArm", "LeftLowerArm", "RightLowerArm", "LeftHand", "RightHand", "LeftToes", "RightToes",
              "LeftEye", "RightEye", "Jaw")
# Older layout without UpperChest (24 entries)
BODY_BONES_24 = tuple(b for b in BODY_BONES if b != "UpperChest")
FINGERS = tuple(f"{f} {p}" for f in ("Thumb", "Index", "Middle", "Ring", "Little")
                for p in ("Proximal", "Intermediate", "Distal"))
SETTINGS = ("m_Scale", "m_ArmTwist", "m_ForeArmTwist", "m_UpperLegTwist", "m_LegTwist", "m_ArmStretch", "m_LegStretch",
            "m_FeetSpacing", "m_HasLeftHand", "m_HasRightHand", "m_HasTDoF")
HD_SETTINGS = ("m_ArmTwist", "m_ForeArmTwist", "m_UpperLegTwist", "m_LegTwist", "m_ArmStretch", "m_LegStretch",
               "m_FeetSpacing", "m_GlobalScale", "m_RootMotionBoneName", "m_HasTranslationDoF", "m_HasExtraRoot",
               "m_SkeletonHasParents")


def _human_name(hb: str) -> str:
    """``LeftUpperLeg`` -> ``Left Upper Leg`` (how ``m_HumanDescription`` spells them; fingers keep their space)."""
    out = []
    for i, ch in enumerate(hb):
        if ch.isupper() and i and hb[i - 1] != " ":
            out.append(" ")
        out.append(ch)
    return "".join(out)


def skeleton_names(tos: dict[int, str], ids: list[int], hd_skeleton: list[dict] | None = None) -> list[str]:
    """Names of ``m_SkeletonNameIDArray``. The ids are CRC32 of the bone *name*; ``m_TOS`` holds paths, so a node is
    named by the TOS path whose last segment hashes to the id (else the id itself is a path hash, else a
    ``m_HumanDescription.m_Skeleton`` name, else ``#id``)."""
    by_leaf: dict[int, str] = {}
    for path in sorted(tos.values(), key=lambda p: (p.count("/"), p)):
        by_leaf.setdefault(crc(path.rsplit("/", 1)[-1]), path)
    hd = {crc(str(b.get("m_Name") or "")): str(b.get("m_Name") or "") for b in hd_skeleton or []}
    out = []
    for i in ids:
        name = by_leaf.get(i)
        if name is None:
            name = tos.get(i)
        if name is None:
            name = hd.get(i, f"#{i}")
        out.append(name)
    return out


def _limit(limits: dict, bone: str) -> str:
    lim = limits.get(bone) or limits.get(_human_name(bone))
    if not lim:
        return "default"
    mn, mx = lim.get("min") or {}, lim.get("max") or {}
    return " ".join(f"{a} {short_float(mn.get(a), 1)}/{short_float(mx.get(a), 1)}" for a in "xyz")


def decode_avatar(tree: dict) -> dict:
    tos = tos_map(tree)
    av = un(tree.get("m_Avatar") or {})
    hd = un(tree.get("m_HumanDescription") or {})
    ids = [int(i) & 0xFFFFFFFF for i in av.get("m_SkeletonNameIDArray") or []]
    names = skeleton_names(tos, ids, hd.get("m_Skeleton"))
    nodes = ((av.get("m_AvatarSkeleton") or {}).get("m_Node")) or []
    human = av.get("m_Human") or {}
    hbi = [int(b) for b in human.get("m_HumanBoneIndex") or []]
    rmb = int(av.get("m_RootMotionBoneIndex", -1) if av.get("m_RootMotionBoneIndex") is not None else -1)

    def bone(i: int):
        return names[i] if 0 <= i < len(names) else None

    # m_HumanBoneIndex / m_HandBoneIndex index the *human* skeleton (m_Human.m_Skeleton): its m_ID are CRC32 of the
    # full paths (m_TOS), and m_HumanSkeletonIndexArray maps it to the avatar skeleton
    hskel = un(human.get("m_Skeleton") or {})
    h_ids = [int(i) & 0xFFFFFFFF for i in hskel.get("m_ID") or []]
    h_to_av = [int(i) for i in av.get("m_HumanSkeletonIndexArray") or []]

    def human_bone(i: int):
        if 0 <= i < len(h_ids) and h_ids[i] in tos:
            return tos[h_ids[i]]
        if 0 <= i < len(h_to_av):
            return bone(h_to_av[i])
        return bone(i) if not h_ids and not h_to_av else None

    out = {"name": tree.get("m_Name") or "", "skeleton_nodes": len(nodes), "skeleton": names,
           "humanoid": any(b >= 0 for b in hbi), "root_motion_bone": bone(rmb),
           "unnamed_nodes": sum(1 for n in names if n.startswith("#"))}
    if out["humanoid"]:
        order = BODY_BONES if len(hbi) >= len(BODY_BONES) else BODY_BONES_24
        mapping = {(order[i] if i < len(order) else f"bone{i}"): human_bone(b) for i, b in enumerate(hbi) if b >= 0}
        for side in ("Left", "Right"):
            hand = human.get(f"m_{side}Hand") or {}
            for k, b in enumerate(hand.get("m_HandBoneIndex") or []):
                if int(b) >= 0:
                    mapping[f"{side} {FINGERS[k] if k < len(FINGERS) else k}"] = human_bone(int(b))
        out["mapping"] = mapping
        out["human_bones_mapped"] = sum(1 for b in hbi if b >= 0)
        out["finger_bones_mapped"] = len(mapping) - out["human_bones_mapped"]
        out["settings"] = {k[2:]: short_float(human.get(k), 4) if not isinstance(human.get(k), bool) else human.get(k)
                           for k in SETTINGS if k in human}
        out["muscles"] = muscle_table(mapping)
    if hd.get("m_Human"):
        desc = {b.get("m_HumanName"): b.get("m_BoneName") for b in hd["m_Human"]}
        limits = {b.get("m_HumanName"): {k: (b.get("m_Limit") or {}).get(f"m_{k.title()}") for k in ("min", "max", "value")}
                  for b in hd["m_Human"] if (b.get("m_Limit") or {}).get("m_Modified")}
        if limits:
            out["hd_limits"] = dict(sorted(limits.items()))  # per human bone, degrees on the muscle axes x/y/z
        out["human_description"] = dict(sorted(desc.items()))
        out["hd_skeleton_bones"] = len(hd.get("m_Skeleton") or [])
        out["hd_settings"] = {k[2:]: hd.get(k) for k in HD_SETTINGS if k in hd}
        if out.get("mapping"):
            agree = disagree = 0
            for hb, path in out["mapping"].items():
                want = desc.get(hb) or desc.get(_human_name(hb))
                if want is None or path is None:
                    continue
                if path.rsplit("/", 1)[-1] == want:
                    agree += 1
                else:
                    disagree += 1
            out["mapping_check"] = {"agree": agree, "disagree": disagree}
    return out


@analysis("avatar", engines=UNITY, needs=("unity.inventory", "unity.install"), uses=("analysis:prefabs",),
          description="Avatars: skeleton names, humanoid mapping (HumanBoneIndex + TOS), root motion bone, muscle settings",
          version=2, options={"file": "regex on install relpaths"})
def avatar(ctx) -> Report:
    users: dict[str, list[str]] = collections.defaultdict(list)
    animators_without = 0
    seen: set[tuple] = set()
    pref = ctx.load("prefabs")
    if pref:
        import json

        from gst.analysis.unity.prefabs import detailed_rows

        for row in detailed_rows(pref):
            if not row.get("animators"):
                continue
            try:
                with open(ctx.workspace / row["detail"], encoding="utf-8") as fh:
                    nodes = json.load(fh).get("nodes") or []
            except (OSError, ValueError):
                continue
            for n in nodes:
                for c in n.get("components") or []:
                    if c.get("type") == "Animator":
                        key = (row["file"], row.get("cab") or "", c.get("path_id"))
                        if c.get("path_id") is not None and key in seen:
                            continue  # an Animator under another scene Animator is in both details
                        seen.add(key)
                        if c.get("avatar"):
                            users[c["avatar"]].append(f"{row.get('scene_path') or row['name']}:{n['path']}")
                        else:
                            animators_without += 1
    rows = []
    for rel, _uf, _sf, cab, obj, _t in iter_typed(ctx, ("Avatar",)):
        tree = builtin(obj)
        if tree is None:
            rows.append({"file": rel, "cab": cab, "path_id": obj.path_id, "name": "?", "error": "typetree read failed"})
            continue
        d = decode_avatar(tree)
        d["animators"] = sorted(users.get(d["name"], []))
        rows.append({"file": rel, **({"cab": cab} if cab else {}), "path_id": obj.path_id, **d})
    rows.sort(key=lambda r: (r["file"], r.get("cab") or "", r["name"], r["path_id"]))
    ok = [r for r in rows if "error" not in r]
    summary = {"avatars": len(ok), "humanoid": sum(1 for r in ok if r["humanoid"]),
               "generic": sum(1 for r in ok if not r["humanoid"]), "errors": len(rows) - len(ok),
               "animators_with_avatar": sum(len(v) for v in users.values()),
               "animators_without_avatar": animators_without if pref else None}
    md = [f"{summary['avatars']} Avatars: {summary['humanoid']} humanoid, {summary['generic']} generic. "
          + (f"Animators in the detailed prefabs: {summary['animators_with_avatar']} with an Avatar, "
             f"{summary['animators_without_avatar']} without (curves bound by hierarchy path only)." if pref
             else "No `analysis/prefabs.json`: Animator usage unknown."), ""]
    if ok:
        md += [md_table(["Avatar", "File", "Nodes", "Humanoid", "Mapped", "Fingers", "Root motion bone", "HD check",
                         "Animators"],
                        [(f"`{r['name']}`", basename(r["file"]), r["skeleton_nodes"], "yes" if r["humanoid"] else "",
                          r.get("human_bones_mapped"), r.get("finger_bones_mapped"), r.get("root_motion_bone"),
                          (f"{r['mapping_check']['agree']} ok / {r['mapping_check']['disagree']} differ"
                           if r.get("mapping_check") else "-"), len(r["animators"])) for r in ok]), ""]
        for r in [r for r in ok if r["humanoid"]][:10]:
            md += [f"## `{r['name']}` humanoid mapping", "",
                   md_table(["Human bone", "Skeleton node"], [(k, v) for k, v in r["mapping"].items()]), ""]
            md += [f"### `{r['name']}` muscles (`HumanTrait.MuscleName`; curve = humanoid clip curve index)", "",
                   md_table(["#", "Muscle", "Bone", "Skeleton node", "Curve", "Limit min/max (HumanDescription)"],
                            [(m["index"], m["name"], m["bone"], m["node"] or "-", m["curve"],
                              _limit(r.get("hd_limits", {}), m["bone"])) for m in r["muscles"]]), ""]
    return Report(data={"avatars": rows, "users": {k: sorted(v) for k, v in sorted(users.items())}},
                  markdown="\n".join(md), summary=summary)
