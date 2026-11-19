"""Godot shading language, read shallowly: ``shader_type``, ``render_mode``, uniforms (scope, type, hints with their
arguments, default, ``group_uniforms`` group), ``#include`` files, the functions a shader defines, and a rough feature
list by keywords (toon, outline, dissolve, vertex displacement, ...). Works on ``.gdshader``/``.gdshaderinc`` text,
the ``code`` of an embedded Shader resource and Godot 3 ``.shader`` files. Stdlib only; not a compiler: macros are not
expanded and the feature list is an inference from names and built-ins, not a proof of what a shader renders.

Visual shaders keep no code in the resource: :func:`visual` reads their node graph (mode, node classes, parameter
nodes as uniforms).
"""

from __future__ import annotations

import collections
import re
from typing import Any

SHADER_TYPES = ("spatial", "canvas_item", "particles", "sky", "fog")
VISUAL_MODES = {0: "spatial", 1: "canvas_item", 2: "particles", 3: "sky", 4: "fog"}
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
_PREPROC = re.compile(r"^\s*#[^\n]*", re.M)
_UNIFORM = re.compile(r"^(?:(global|instance)\s+)?uniform\s+(?:(lowp|mediump|highp)\s+)?(\w+)\s+(\w+)\s*(\[[^\]]*\])?\s*(.*)$",
                      re.S)
_FUNC = re.compile(r"(\w+)\s*\([^()]*\)\s*$")

# feature -> regexes searched in the code (comments included: names often say it). Keyword inference only.
FEATURES: dict[str, tuple[str, ...]] = {
    "toon": (r"\btoon|_toon|toon_|\bcel\b|cel_?shad|\bramp\b|_ramp|ramp_|\bbands?\b|posteriz",),
    "outline": (r"outline|silhouette|inverted.?hull",),
    "dissolve": (r"dissolve|burn_?edge|disintegrat",),
    "vertex_displacement": (),  # VERTEX written in vertex(): see analyse()
    "wind_sway": (r"\bwind|wind_|sway|foliage|grass",),
    "fresnel_rim": (r"fresnel|\brim\b|rim_|_rim\b|\bRIM\b",),
    "triplanar": (r"triplanar",),
    "water": (r"water|ocean|\bwaves?\b|caustic|foam",),
    "uv_scroll": (r"\bUV\b[^;\n]*\bTIME\b|\bTIME\b[^;\n]*\bUV\b|scroll",),
    "screen_texture": (r"SCREEN_TEXTURE|hint_screen_texture|SCREEN_UV",),
    "depth_texture": (r"DEPTH_TEXTURE|hint_depth_texture",),
    "transparency": (r"\bALPHA\s*[-+*/]?=(?!=)",),
    "alpha_clip": (r"\bdiscard\b|ALPHA_SCISSOR",),
    "emission": (r"\bEMISSION\s*[-+*/]?=(?!=)",),
    "normal_map": (r"\bNORMAL_MAP\b|hint_normal\b",),
    "noise": (r"noise|simplex|perlin|voronoi|worley",),
    "billboard": (r"billboard|INV_VIEW_MATRIX\s*\[|MODELVIEW_MATRIX\s*=",),
    "hologram": (r"hologram|scanline",),
    "pixelate": (r"pixelat|pixel_size|pixel_art",),
    "dither": (r"dither|bayer",),
    "blur": (r"\bblur",),
    "vignette": (r"vignette",),
    "chromatic_aberration": (r"chromatic|aberration",),
    "flash_hit": (r"\bflash|hit_?color|damage_?color",),
    "gradient_map": (r"gradient",),
    "instancing": (r"INSTANCE_CUSTOM|INSTANCE_ID|\binstance\s+uniform",),
    "time_animated": (r"\bTIME\b",),
}
FEATURE_RENDER_MODES = {"unshaded": "unshaded", "diffuse_toon": "toon", "specular_toon": "toon",
                        "cull_front": "outline_candidate", "blend_add": "additive", "depth_draw_always": "depth_always",
                        "world_vertex_coords": "world_vertex_coords", "skip_vertex_transform": "skip_vertex_transform"}
_FEATURE_RX = {k: [re.compile(p, re.I if k not in ("transparency", "emission", "normal_map", "screen_texture",
                                                     "depth_texture", "instancing", "time_animated") else 0)
                   for p in pats] for k, pats in FEATURES.items()}


def _split_top(text: str, sep: str) -> list[str]:
    """Split at ``sep`` outside parentheses/brackets."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _find_top(text: str, ch: str) -> int:
    depth = 0
    for i, c in enumerate(text):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == ch and depth == 0:
            return i
    return -1


def _statements(code: str):
    """Top-level statements (``;``-terminated) and function definitions ``(name, body)`` of comment-free code."""
    stmts, funcs = [], []
    depth = 0
    start = 0
    head = ""
    body_start = 0
    for i, ch in enumerate(code):
        if ch == "{":
            if depth == 0:
                head = code[start:i]
                body_start = i + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                m = _FUNC.search(head.strip())
                if m:
                    funcs.append((m.group(1), code[body_start:i]))
                else:  # a struct or anything else with braces: keep it as a statement
                    stmts.append((head + code[body_start - 1:i + 1]).strip())
                start = i + 1
        elif ch == ";" and depth == 0:
            s = code[start:i].strip()
            if s:
                stmts.append(s)
            start = i + 1
    return stmts, funcs


def parse(code: str) -> dict:
    """Shader text -> ``{shader_type, render_modes, uniforms, includes, functions, varyings, constants, features,
    lines}``. Uniform = ``{name, type, scope ('' | global | instance), precision, array, hints, default, group}``."""
    includes = _INCLUDE.findall(code)
    clean = _PREPROC.sub("", _COMMENT.sub(" ", code))
    stmts, funcs = _statements(clean)
    out: dict[str, Any] = {"shader_type": None, "render_modes": [], "uniforms": [], "includes": includes,
                           "functions": [f for f, _b in funcs], "varyings": 0, "constants": 0, "structs": 0,
                           "lines": code.count("\n") + 1}
    group = ""
    for s in stmts:
        s1 = " ".join(s.split())
        if s1.startswith("shader_type "):
            out["shader_type"] = s1.split(" ", 1)[1].strip()
        elif s1.startswith("render_mode "):
            out["render_modes"] += [m.strip() for m in s1[len("render_mode "):].split(",") if m.strip()]
        elif s1.startswith("group_uniforms"):
            group = s1[len("group_uniforms"):].strip()
        elif s1.startswith("varying ") or s1.startswith("flat varying") or " varying " in f" {s1} ":
            out["varyings"] += 1
        elif s1.startswith("const "):
            out["constants"] += 1
        elif s1.startswith("struct "):
            out["structs"] += 1
        else:
            m = _UNIFORM.match(s1)
            if m:
                out["uniforms"].append(_uniform(m, group))
    out["features"] = features(code, out, dict(funcs))
    return out


def _uniform(m: re.Match, group: str) -> dict:
    scope, precision, utype, name, array, rest = m.groups()
    default = None
    eq = _find_top(rest, "=")
    if eq >= 0:
        default = rest[eq + 1:].strip()
        rest = rest[:eq]
    hints: list[str] = []
    colon = _find_top(rest, ":")
    if colon >= 0:
        hints = [h.strip() for h in _split_top(rest[colon + 1:], ",") if h.strip()]
    u = {"name": name, "type": utype, "scope": scope or "", "hints": hints, "default": default, "group": group}
    if precision:
        u["precision"] = precision
    if array:
        u["array"] = array.strip("[] ") or "?"
    return u


def features(code: str, info: dict, bodies: dict | None = None) -> list[str]:
    found = []
    for name, rxs in _FEATURE_RX.items():
        if any(rx.search(code) for rx in rxs):
            found.append(name)
    vertex = (bodies or {}).get("vertex", "")
    if re.search(r"\bVERTEX\b(\.\w+)?\s*[-+*/]?=(?!=)", vertex):
        found.append("vertex_displacement")
    if "light" in (bodies or {}):
        found.append("custom_lighting")
    for mode in info.get("render_modes") or []:
        f = FEATURE_RENDER_MODES.get(mode)
        if f and f not in found:
            found.append(f)
    if any(u["scope"] == "instance" for u in info.get("uniforms") or []) and "instancing" not in found:
        found.append("instancing")
    if info.get("shader_type") == "particles":
        found.append("particles")
    return sorted(set(found))


# --- visual shaders ------------------------------------------------------------------------------------

_PARAM_TYPES = (("Float", "float"), ("Int", "int"), ("UInt", "uint"), ("Boolean", "bool"), ("Bool", "bool"),
                ("Vec2", "vec2"), ("Vec3", "vec3"), ("Vec4", "vec4"), ("Color", "vec4"), ("Transform", "mat4"),
                ("Texture2DArray", "sampler2DArray"), ("Texture3D", "sampler3D"), ("Cubemap", "samplerCube"),
                ("Texture", "sampler2D"), ("Scalar", "float"), ("Vector", "vec3"))


def _param_type(cls: str) -> str:
    core = cls[len("VisualShaderNode"):] if cls.startswith("VisualShaderNode") else cls
    for key, t in _PARAM_TYPES:
        if core.startswith(key):
            return t
    return core


def visual(props: dict, sub_types: dict, sub_props: dict) -> dict:
    """A VisualShader resource: ``mode`` -> shader_type, node classes per stage, parameter nodes as uniforms.
    ``sub_types``/``sub_props``: id -> class / properties of the document's sub-resources (``nodes/<stage>/<n>/node``
    point at them)."""
    from gst.engines.godot.tres import Ref, short

    mode = props.get("mode", 0)
    node_types: collections.Counter = collections.Counter()
    stages: collections.Counter = collections.Counter()
    uniforms = []
    for k, v in props.items():
        m = re.match(r"^nodes/(\w+)/(\d+)/node$", k)
        if not m or not isinstance(v, Ref) or v.kind != "sub":
            continue
        cls = sub_types.get(v.id) or "?"
        node_types[cls] += 1
        stages[m.group(1)] += 1
        p = sub_props.get(v.id) or {}
        pname = p.get("parameter_name", p.get("uniform_name"))
        if pname is not None and (cls.endswith("Parameter") or cls.endswith("Uniform")):
            u = {"name": str(pname), "type": _param_type(cls), "scope": "", "hints": [], "default": None, "group": "",
                 "node": cls}
            if p.get("qualifier"):
                u["scope"] = {1: "global", 2: "instance"}.get(p["qualifier"], str(p["qualifier"]))
            if "default_value" in p and p.get("default_value_enabled"):
                u["default"] = short(p["default_value"])
            if "hint" in p:
                u["hints"] = [f"hint {p['hint']}"]
            uniforms.append(u)
    text = " ".join(list(node_types) + [u["name"] for u in uniforms])
    info = {"shader_type": VISUAL_MODES.get(mode, str(mode)), "render_modes": [], "uniforms": uniforms,
            "includes": [], "functions": sorted(stages), "node_types": dict(sorted(node_types.items())),
            "nodes": sum(node_types.values())}
    for k, v in props.items():
        if k.startswith("modes/") or k.startswith("flags/"):
            if v:
                info["render_modes"].append(f"{k.split('/', 1)[1]}={short(v)}" if not isinstance(v, bool) else k.split("/", 1)[1])
    info["features"] = features(text, info, {"vertex": ""})
    return info
