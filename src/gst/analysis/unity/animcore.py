"""Pure helpers shared by the Unity animation analyses (animation, controller, avatar, rig, joints).

Everything here works on plain dicts (UnityPy typetrees) so the tests feed synthetic objects:

- :func:`crc` / :func:`path_map`: Unity binds animation curves and Avatar/TOS names by CRC32 of a path relative to
  the Animator's GameObject (``"Armature/Hip/Leg_L"``; the Animator itself is ``""`` = 0).
- :func:`quat_to_euler` / :func:`euler_to_quat`: Unity's ``eulerAngles`` convention (Z, then X, then Y).
- :func:`decode_clip` / :func:`clip_bindings` / :func:`summarize_bindings`: an AnimationClip's MuscleClip
  (Streamed + Dense + Constant curves) and its generic bindings, ported from ``repo/animlib.py``.
- :func:`iter_typed`: stream the objects of some classes, one install file (and one CAB) at a time.
"""

from __future__ import annotations

import math
import re
import struct
import zlib
from typing import Any, Iterable

# Unity class IDs that show up in clip bindings
CLASS_IDS = {1: "GameObject", 4: "Transform", 8: "Behaviour", 20: "Camera", 23: "MeshRenderer", 25: "Renderer",
             33: "MeshFilter", 54: "Rigidbody", 64: "MeshCollider", 65: "BoxCollider", 82: "AudioSource", 95: "Animator",
             96: "TrailRenderer", 108: "Light", 114: "MonoBehaviour", 120: "LineRenderer", 135: "SphereCollider",
             136: "CapsuleCollider", 137: "SkinnedMeshRenderer", 198: "ParticleSystem", 199: "ParticleSystemRenderer",
             212: "SpriteRenderer", 222: "CanvasRenderer", 224: "RectTransform", 225: "CanvasGroup"}
TRANSFORM_ATTR = {1: "pos", 2: "rot", 3: "scale", 4: "euler"}
# BindingCustomType (uTinyRipper/AssetStudio): which animated property family a generic binding drives
CUSTOM_MUSCLE = 8
CUSTOM_BLENDSHAPE = 20
PARAM_TYPES = {1: "Float", 3: "Int", 4: "Bool", 9: "Trigger"}
CONDITION_MODES = {1: "If", 2: "IfNot", 3: "Greater", 4: "Less", 6: "Equals", 7: "NotEqual"}
BLEND_TYPES = {0: "1D", 1: "SimpleDirectional2D", 2: "FreeformDirectional2D", 3: "FreeformCartesian2D", 4: "Direct"}
NO_CLIP = 0xFFFFFFFF
SELECTOR_BASE = 30000  # m_DestinationState >= 30000: a selector (sub-state-machine entry/exit) state


def crc(text: str) -> int:
    """Unity's ``Animator.StringToHash`` / path hash: CRC32 of the UTF-8 bytes."""
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


def un(x: Any) -> Any:
    """Drop the ``{"data": ...}`` OffsetPtr wrappers of Mecanim typetrees, recursively."""
    if isinstance(x, dict):
        if len(x) == 1 and "data" in x:
            return un(x["data"])
        return {k: un(v) for k, v in x.items()}
    if isinstance(x, list):
        return [un(i) for i in x]
    return x


def tos_map(tree: dict) -> dict[int, str]:
    """``m_TOS`` (hash -> string table of AnimatorController/Avatar) as a dict; pairs or {first, second}."""
    out: dict[int, str] = {}
    for p in tree.get("m_TOS") or []:
        if isinstance(p, dict):
            k, v = p.get("first"), p.get("second")
        else:
            k, v = p[0], p[1]
        if k is not None:
            out[int(k) & 0xFFFFFFFF] = str(v)
    return out


def f32(word: int) -> float:
    return struct.unpack("<f", struct.pack("<I", int(word) & 0xFFFFFFFF))[0]


# --- rotations -----------------------------------------------------------------------------------

def quat_to_euler(x: float, y: float, z: float, w: float) -> list[float]:
    """Degrees (-180..180) in Unity's convention: ``q = qY * qX * qZ`` (Z applied first, then X, then Y)."""
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    sinp = max(-1.0, min(1.0, 2 * (w * x - y * z)))
    ex = math.asin(sinp)
    if abs(sinp) < 0.999999:
        ey = math.atan2(2 * (x * z + w * y), 1 - 2 * (x * x + y * y))
        ez = math.atan2(2 * (x * y + w * z), 1 - 2 * (x * x + z * z))
    else:  # gimbal lock: X = +-90, Y and Z share one axis; Z = 0 by convention
        ey = math.atan2(2 * (w * y - x * z), 1 - 2 * (y * y + z * z))
        ez = 0.0
    return [math.degrees(ex), math.degrees(ey), math.degrees(ez)]


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def euler_to_quat(ex: float, ey: float, ez: float) -> tuple[float, float, float, float]:
    """Inverse of :func:`quat_to_euler` (``Quaternion.Euler``): ``qY * qX * qZ``."""
    hx, hy, hz = (math.radians(a) / 2 for a in (ex, ey, ez))
    qx = (math.sin(hx), 0.0, 0.0, math.cos(hx))
    qy = (0.0, math.sin(hy), 0.0, math.cos(hy))
    qz = (0.0, 0.0, math.sin(hz), math.cos(hz))
    return _qmul(_qmul(qy, qx), qz)


def matrix_scale_rotation(m: dict) -> dict:
    """Scale (column norms) and Euler rotation of a 4x4 ``Matrix4x4f`` dict (``e00``..``e33``), e.g. a bindpose."""
    cols = [[float(m.get(f"e{r}{c}", 0.0)) for r in range(3)] for c in range(3)]
    scale = [math.sqrt(sum(v * v for v in col)) for col in cols]
    r = [[cols[c][row] / (scale[c] or 1.0) for c in range(3)] for row in range(3)]  # r[row][col]
    # rotation matrix -> quaternion
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = ((r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s, 0.25 * s)
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        q = (0.25 * s, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s, (r[2][1] - r[1][2]) / s)
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        q = ((r[0][1] + r[1][0]) / s, 0.25 * s, (r[1][2] + r[2][1]) / s, (r[0][2] - r[2][0]) / s)
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
        q = ((r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, 0.25 * s, (r[1][0] - r[0][1]) / s)
    return {"scale": [round(v, 4) for v in scale], "euler": [round(v, 2) + 0.0 for v in quat_to_euler(*q)],
            "translation": [round(float(m.get(f"e{r_}3", 0.0)), 4) for r_ in range(3)]}


def invert_affine(m: dict) -> list[list[float]]:
    """Inverse of an affine 4x4 dict as a 3x4 row list (rotation/scale block inverted, translation solved)."""
    a = [[float(m.get(f"e{r}{c}", 0.0)) for c in range(3)] for r in range(3)]
    t = [float(m.get(f"e{r}3", 0.0)) for r in range(3)]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        return [[0.0] * 4 for _ in range(3)]
    inv = [[(a[(c + 1) % 3][(r + 1) % 3] * a[(c + 2) % 3][(r + 2) % 3]
             - a[(c + 1) % 3][(r + 2) % 3] * a[(c + 2) % 3][(r + 1) % 3]) / det for c in range(3)] for r in range(3)]
    ti = [-sum(inv[r][c] * t[c] for c in range(3)) for r in range(3)]
    return [inv[r] + [ti[r]] for r in range(3)]


# --- path maps -----------------------------------------------------------------------------------

def path_map(paths: Iterable[str], root: str) -> dict[int, str]:
    """CRC32 -> relative path for every node path under ``root`` (absolute prefab paths like ``/A/B/C``);
    the root itself is ``""`` (hash 0)."""
    root = root.rstrip("/")
    out = {0: ""}
    prefix = root + "/"
    for p in paths:
        if p.startswith(prefix):
            rel = p[len(prefix):]
            out[crc(rel)] = rel
    return out


def animator_maps(nodes: list[dict]) -> list[dict]:
    """Per Animator/Animation component of a prefab detail (``analysis/prefabs/...json`` nodes): the node path,
    controller / avatar / legacy clips and the CRC32 path map of everything under it."""
    paths = [n["path"] for n in nodes]
    out = []
    for n in nodes:
        for c in n.get("components") or []:
            if c.get("type") == "Animator":
                out.append({"node": n["path"], "kind": "Animator", "controller": c.get("controller"),
                            "avatar": c.get("avatar"), "map": path_map(paths, n["path"])})
            elif c.get("type") == "Animation":
                names = [x for x in (c.get("clips") or []) if isinstance(x, str)]
                if c.get("clip"):
                    names.append(c["clip"])
                out.append({"node": n["path"], "kind": "Animation", "clips": sorted(set(names)),
                            "map": path_map(paths, n["path"])})
    return out


# --- clips ---------------------------------------------------------------------------------------

def decode_clip(muscle_clip: dict) -> dict:
    """Curves of a MuscleClip: ``{"curves": {index: [(time|None, value)]}, "nstream", "ndense", "nconst", "frames",
    "sample_rate"}``. Streamed: frames of (time, count, count x (index, 4 coefficients)), value = coefficient 3;
    then the dense curves, then the constant ones (curve indices continue in that order)."""
    def unwrap(v):
        return v["data"] if isinstance(v, dict) and set(v) == {"data"} else v

    mc = muscle_clip or {}
    cl = unwrap(mc.get("m_Clip")) or {}
    st = cl.get("m_StreamedClip") or {}
    dn = cl.get("m_DenseClip") or {}
    cs = cl.get("m_ConstantClip") or {}
    cs = {"data": cs} if isinstance(cs, list) else cs
    curves: dict[int, list] = {}
    data = st.get("data") or []
    nstream = int(st.get("curveCount") or 0)
    pos = 0
    n_words = len(data)
    while pos + 1 < n_words:
        t = f32(data[pos])
        count = int(data[pos + 1])
        pos += 2
        tt = round(t, 4) if -1e30 < t < 1e30 else None
        for _ in range(count):
            if pos + 4 >= n_words:
                pos = n_words
                break
            idx = int(data[pos])
            curves.setdefault(idx, []).append((tt, f32(data[pos + 4])))
            pos += 5
    nd = int(dn.get("m_CurveCount") or 0)
    fc = int(dn.get("m_FrameCount") or 0)
    sr = float(dn.get("m_SampleRate") or 0) or 1.0
    bt = float(dn.get("m_BeginTime") or 0)
    arr = dn.get("m_SampleArray") or []
    for c in range(nd):
        series = curves.setdefault(nstream + c, [])
        for f in range(fc):
            i = f * nd + c
            if i < len(arr):
                series.append((round(bt + f / sr, 4), float(arr[i])))
    consts = cs.get("data") or []
    for i, v in enumerate(consts):
        curves.setdefault(nstream + nd + i, []).append((None, float(v)))
    return {"curves": curves, "nstream": nstream, "ndense": nd, "nconst": len(consts), "frames": fc,
            "sample_rate": sr}


def curve_count(binding: dict) -> int:
    if int(binding.get("typeID", 0)) == 4:
        return {1: 3, 2: 4, 3: 3, 4: 3}.get(int(binding.get("attribute", 0)), 1)
    return 1


def clip_bindings(tree: dict) -> list[dict]:
    """Generic bindings with the index of their first curve and curve count."""
    out = []
    i = 0
    for b in ((tree.get("m_ClipBindingConstant") or {}).get("genericBindings") or []):
        n = curve_count(b)
        out.append({"path": int(b.get("path", 0)) & 0xFFFFFFFF, "attr": int(b.get("attribute", 0)) & 0xFFFFFFFF,
                    "type_id": int(b.get("typeID", 0)), "custom": int(b.get("customType", 0) or 0),
                    "pptr": bool(b.get("isPPtrCurve")), "first": i, "n": n})
        i += n
    return out


def binding_kind(b: dict) -> str:
    t = b["type_id"]
    if t == 4:
        return TRANSFORM_ATTR.get(b["attr"], f"attr{b['attr']}")
    if t == 95 and b["custom"] == CUSTOM_MUSCLE:
        return "muscle"
    if t == 137 and b["custom"] == CUSTOM_BLENDSHAPE:
        return "blendshape"
    if t == 1:
        return "active"
    return "pptr" if b["pptr"] else "float"


def summarize_bindings(tree: dict, decoded: dict, resolve_path, resolve_attr=None) -> list[dict]:
    """One row per binding: path (resolved or ``#xxxxxxxx``), component class, kind, keys, constant?, value range
    (rotations as Euler degrees)."""
    rows = []
    curves = decoded["curves"]
    for b in clip_bindings(tree):
        series = [curves.get(b["first"] + k, []) for k in range(b["n"])]
        vals = [[v for _, v in s] for s in series]
        keys = max((len(s) for s in series), default=0)
        const = all(len({round(x, 5) for x in v}) <= 1 for v in vals)
        kind = binding_kind(b)
        if b["type_id"] == 4 and b["attr"] == 2 and all(vals):
            n = min(len(v) for v in vals)
            eul = [quat_to_euler(*(vals[k][j] for k in range(4))) for j in range(n)]
            rng = [[round(min(e[a] for e in eul), 3), round(max(e[a] for e in eul), 3)] for a in range(3)]
        else:
            rng = [[round(min(v), 3), round(max(v), 3)] for v in vals if v]
        path = resolve_path(b["path"])
        row = {"path": path if path is not None else f"#{b['path']:08x}", "resolved": path is not None,
               "class": CLASS_IDS.get(b["type_id"], f"ClassID{b['type_id']}"), "kind": kind, "keys": keys,
               "const": const, "range": rng}
        if kind == "muscle":  # humanoid curve: index into Mecanim's list (Root/IK goals/95 muscles/TDoF)
            from gst.analysis.unity.humanoid import clip_attribute

            row["attribute"], row["group"] = clip_attribute(b["attr"])
            row["index"] = b["attr"]
        elif b["type_id"] != 4:
            name = resolve_attr(b) if resolve_attr else None
            row["attribute"] = name if name is not None else f"#{b['attr']:08x}"
        rows.append(row)
    return rows


def legacy_bindings(tree: dict) -> list[dict]:
    """Editor-style curves of a legacy clip (``m_RotationCurves``..., paths are strings)."""
    rows = []
    for key, kind in (("m_PositionCurves", "pos"), ("m_RotationCurves", "rot"), ("m_EulerCurves", "euler"),
                      ("m_ScaleCurves", "scale"), ("m_FloatCurves", "float"), ("m_PPtrCurves", "pptr")):
        for cv in tree.get(key) or []:
            curve = cv.get("curve") or {}
            frames = curve.get("m_Curve") or cv.get("curve") or []
            frames = frames if isinstance(frames, list) else []
            row = {"path": cv.get("path") or "", "resolved": True,
                   "class": CLASS_IDS.get(int(cv.get("classID", 4) or 4), "Transform") if kind in ("float", "pptr") else "Transform",
                   "kind": kind, "keys": len(frames), "const": None, "range": []}
            if kind in ("float", "pptr"):
                row["attribute"] = cv.get("attribute")
            vals = [f.get("value") for f in frames if isinstance(f, dict)]
            if vals and isinstance(vals[0], dict):
                axes = [k for k in ("x", "y", "z", "w") if k in vals[0]]
                cols = [[float(v[a]) for v in vals] for a in axes]
                row["const"] = all(len({round(x, 5) for x in c}) <= 1 for c in cols)
                if kind == "rot" and len(axes) == 4:
                    eul = [quat_to_euler(*(c[j] for c in cols)) for j in range(len(vals))]
                    row["range"] = [[round(min(e[a] for e in eul), 3), round(max(e[a] for e in eul), 3)] for a in range(3)]
                else:
                    row["range"] = [[round(min(c), 3), round(max(c), 3)] for c in cols]
            elif vals and all(isinstance(v, (int, float)) for v in vals):
                row["const"] = len({round(float(v), 5) for v in vals}) <= 1
                row["range"] = [[round(min(vals), 3), round(max(vals), 3)]]
            rows.append(row)
    return rows


def clip_kind(rows: list[dict]) -> str:
    """humanoid (muscle curves) / generic (Transform curves) / blendshape / properties (other curves) / empty."""
    kinds = {r["kind"] for r in rows}
    if "muscle" in kinds:
        return "humanoid"
    if kinds & {"pos", "rot", "scale", "euler"}:
        return "generic"
    if "blendshape" in kinds:
        return "blendshape"
    return "properties" if rows else "empty"


# --- streaming objects -----------------------------------------------------------------------------

def file_filter(ctx) -> re.Pattern | None:
    return re.compile(ctx.opt("file"), re.I) if ctx.opt("file") else None


def iter_typed(ctx, types: tuple[str, ...], *, scenes: bool = True):
    """``(relpath, uf, sf, cab, obj, type_name)`` for every object of ``types``, one install file and one CAB at a
    time (the ``file`` option filters relpaths; ``scenes=False`` skips scene files)."""
    from gst.analysis.unity import UnityFile, basename, is_scene
    from gst.engines.unity import loader

    fpat = file_filter(ctx)
    wanted = set(types)
    for rel in ctx.inventory_files(types):
        if fpat and not fpat.search(rel):
            continue
        ctx.log(f"reading {rel}")
        with UnityFile(ctx, rel) as uf:
            for sf in uf.files():
                if not scenes and is_scene(sf.name):
                    continue
                cab = sf.name if basename(sf.name) != basename(rel) else None
                for obj in list(sf.objects.values()):
                    t = loader.type_name(obj)
                    if t in wanted:
                        yield rel, uf, sf, cab, obj, t


def clip_key(sf, file_id: int, path_id: int) -> str:
    """Identity of an object across files: ``<serialized file basename lower>:<path id>``."""
    from gst.analysis.unity import basename
    from gst.engines.unity import loader

    name = loader.external_name(sf, file_id) if file_id else None
    return f"{(name or basename(sf.name)).lower()}:{path_id}"


def short_float(v: Any, nd: int = 4):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if math.isinf(f) or abs(f) > 1e30:
        return "inf" if f > 0 else "-inf"
    return round(f, nd)
