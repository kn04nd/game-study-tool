"""AnimationClip -> raw clip JSON; AnimatorController / AnimatorOverrideController / Avatar -> JSON.

The clip JSON is the typetree as stored (``m_MuscleClip`` OffsetPtr unwrapped, streamed/dense/constant
data kept as numbers); decoding curves is ``gst analyze animation``. A summary (length, frame
rate, curve counts, events) goes in the sidecar.
"""

from __future__ import annotations

from gst.engines.unity import loader, typetree


def _clip_summary(tree: dict) -> dict:
    out = {}
    try:
        mc = loader.unwrap_offsetptr(tree.get("m_MuscleClip", {}))
        clip = loader.unwrap_offsetptr(mc.get("m_Clip", {}))
        out["streamed_curves"] = clip.get("m_StreamedClip", {}).get("curveCount")
        out["dense_curves"] = clip.get("m_DenseClip", {}).get("m_CurveCount")
        out["constant_values"] = len(clip.get("m_ConstantClip", {}).get("data", []) or [])
        out["length"] = round(float(mc.get("m_StopTime", 0)) - float(mc.get("m_StartTime", 0)), 4)
    except Exception:
        pass
    out["sample_rate"] = tree.get("m_SampleRate")
    out["legacy"] = tree.get("m_Legacy")
    out["events"] = len(tree.get("m_Events", []) or [])
    out["generic_bindings"] = len((tree.get("m_ClipBindingConstant", {}) or {}).get("genericBindings", []) or [])
    return {k: v for k, v in out.items() if v is not None}


def export_typetree_json(s, obj, folder: str, summary=None) -> list:
    tree = typetree.read_builtin(obj)
    if tree is None:
        raise RuntimeError("typetree read failed")
    name = tree.get("m_Name") or f"{folder}_{obj.path_id}"
    if "m_MuscleClip" in tree:
        tree["m_MuscleClip"] = loader.unwrap_offsetptr(tree["m_MuscleClip"])
    extra = summary(tree) if summary else {}
    path = s.w.target(folder, name, ".json", obj.path_id)
    s.w.json(path, tree, meta=s.meta(obj, tree.get("m_Name", ""), **extra))
    return [path]


def export_clip(s, obj) -> list:
    return export_typetree_json(s, obj, "AnimationClip", _clip_summary)


def export_controller(s, obj) -> list:
    return export_typetree_json(s, obj, loader.type_name(obj))
