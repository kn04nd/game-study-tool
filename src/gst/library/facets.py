"""Facet tables of the library: what each workspace's `analysis/*.json` (and `recon/recon.json`) gives per facet.

Every row carries ``game`` (library key), ``source`` (path relative to the workspace: `analysis/rig.json`) and
``ref`` (where in that JSON: `data.smr[12]`), so a comparison can cite each number. ``FACETS`` says which analyses
feed a facet per engine family; a missing one becomes a row of ``gaps``, never an error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from gst.common import read_json

# table -> columns (besides game, source, ref). Types are SQLite affinities.
TABLES: dict[str, list[tuple[str, str]]] = {
    "rigs": [("kind", "TEXT"), ("item", "TEXT"), ("node", "TEXT"), ("bones", "INTEGER"), ("deform_bones", "INTEGER"),
             ("root_bone", "TEXT"), ("bindposes", "INTEGER"), ("max_influences", "INTEGER"), ("verts", "INTEGER"),
             ("tris", "INTEGER"), ("blendshapes", "INTEGER"), ("mesh", "TEXT"), ("detail", "TEXT"), ("links", "TEXT")],
    "avatars": [("name", "TEXT"), ("humanoid", "INTEGER"), ("skeleton_nodes", "INTEGER"), ("human_bones", "INTEGER"),
                ("root_motion_bone", "TEXT"), ("animators", "INTEGER")],
    "constraints": [("item", "TEXT"), ("node", "TEXT"), ("type", "TEXT")],
    "clips": [("kind", "TEXT"), ("name", "TEXT"), ("owner", "TEXT"), ("length", "REAL"), ("fps", "REAL"),
              ("loop", "INTEGER"), ("events", "INTEGER"), ("tracks", "INTEGER"), ("keys", "INTEGER"),
              ("clip_kind", "TEXT"), ("root_motion", "INTEGER")],
    "anim_graphs": [("kind", "TEXT"), ("name", "TEXT"), ("file", "TEXT"), ("params", "INTEGER"), ("layers", "INTEGER"),
                    ("states", "INTEGER"), ("transitions", "INTEGER"), ("blend_trees", "INTEGER"), ("clips", "INTEGER")],
    "joints": [("kind", "TEXT"), ("family", "TEXT"), ("item", "TEXT"), ("path", "TEXT"), ("type", "TEXT"),
               ("connected", "TEXT"), ("mass", "REAL"), ("settings", "TEXT")],
    "ragdolls": [("kind", "TEXT"), ("item", "TEXT"), ("file", "TEXT"), ("bodies", "INTEGER"), ("joints", "INTEGER"),
                 ("joint_types", "TEXT"), ("mass_total", "REAL"), ("classes", "TEXT"), ("detail", "TEXT")],
    "audio": [("name", "TEXT"), ("kind", "TEXT"), ("category", "TEXT"), ("clips", "INTEGER"), ("files", "INTEGER"),
              ("users", "INTEGER"), ("defined_at", "TEXT")],
    "audio_files": [("ext", "TEXT"), ("format", "TEXT"), ("channels", "INTEGER"), ("rate", "INTEGER"),
                    ("files", "INTEGER"), ("seconds", "REAL"), ("bytes", "INTEGER")],
    "shaders": [("name", "TEXT"), ("family", "TEXT"), ("materials", "INTEGER"), ("passes", "INTEGER"),
                ("keywords", "INTEGER"), ("signatures", "TEXT"), ("uniforms", "INTEGER")],
    "middleware": [("name", "TEXT"), ("kind", "TEXT"), ("evidence", "TEXT")],
    "systems": [("label", "TEXT"), ("types", "INTEGER"), ("files", "INTEGER"), ("prefabs", "INTEGER"),
                ("score", "INTEGER"), ("top", "TEXT")],
    "dcc": [("family", "TEXT"), ("verdict", "TEXT"), ("strong", "INTEGER"), ("weak", "INTEGER"),
            ("ambiguous", "INTEGER"), ("rules", "TEXT")],
    "asset_counts": [("type", "TEXT"), ("count", "INTEGER")],
}

# facet -> engine family -> analyses that feed it ("*" = any engine; "recon" = recon/recon.json).
FACETS: dict[str, dict[str, tuple[str, ...]]] = {
    "rigs": {"unity": ("rig", "avatar"), "godot": ("gltf_summary", "godot_animation"),
             "unreal": ("unreal_animation", "unreal_assets")},
    "animation": {"unity": ("animation", "controller"), "godot": ("godot_animation", "gltf_summary"),
                  "unreal": ("unreal_animation", "unreal_assets")},
    "joints": {"unity": ("joints",), "godot": ("godot_animation",), "unreal": ("unreal_animation", "unreal_assets")},
    "audio": {"unity": ("audio_catalog",), "godot": ("audio_catalog",), "unreal": ("audio_catalog", "unreal_assets")},
    "shaders": {"unity": ("shaders",), "godot": ("godot_shaders",), "unreal": ("unreal_assets",)},
    "middleware": {"*": ("recon",)},
    "systems": {"*": ("systems",)},
    "dcc": {"*": ("dcc",)},
}
# What an analysis does not give, said once per (facet, engine) in the gaps. Gap reasons are pt-BR on purpose:
# `gst lib compare` writes them into the "Não verificado / lacunas" section of the (pt-BR) comparison file.
PARTIAL: dict[tuple[str, str], str] = {
    ("rigs", "godot"): "ossos = Skeleton3D ligado à cena de gameplay (godot_animation `characters`: cena -> .glb -> cena "
                       "importada -> esqueleto), juntas por skin dos .glb (gltf_summary) e Skeleton3D salvos em cenas; "
                       "sem bindposes nem Avatar",
    ("rigs", "unreal"): "ossos só dos Skeleton/SkeletalMesh exportados em JSON (unreal_animation; SkeletalMesh pede "
                        "`gst assets extract --types json --all-types`); os demais pacotes só contados pelo nome "
                        "(unreal_assets `guessed`); sem bindposes (influências = máximo por seção do LOD0)",
    ("animation", "unreal"): "clips e grafos só dos AnimSequence/AnimMontage/BlendSpace/AnimBlueprint exportados em JSON "
                             "(unreal_animation); os demais pacotes só contados pelo nome; loop é do nó do grafo, não do clip",
    ("joints", "unreal"): "corpos e constraints só dos PhysicsAsset exportados em JSON (unreal_animation); limites ausentes "
                          "no JSON = padrões da engine (lista `defaulted`); massa só dos corpos com MassInKgOverride",
    ("audio", "unreal"): "SoundCue/SoundWave contados pelo nome do pacote",
    ("shaders", "unreal"): "só contagem de Material/MaterialInstance/MaterialFunction pelo nome do pacote",
}
UNREAL_TYPES = {
    "rigs": ("SkeletalMesh", "Skeleton"), "joints": ("PhysicsAsset",),
    "animation": ("AnimSequence", "AnimMontage", "BlendSpace", "AnimBlueprint"),
    "audio": ("SoundCue", "SoundWave"), "shaders": ("Material", "MaterialInstance", "MaterialFunction"),
}


def facet_sources(facet: str, engine: str) -> tuple[str, ...] | None:
    """Analyses that feed ``facet`` for an engine family; None when the engine has no rule (unknown engine)."""
    rules = FACETS[facet]
    if "*" in rules:
        return rules["*"]
    return rules.get(engine)


def _j(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _calls(methods) -> int:
    """Method-track calls of a Godot Animation (`{method: count}`, or a list)."""
    if isinstance(methods, dict):
        return sum(v if isinstance(v, int) else 1 for v in methods.values())
    return len(methods or [])


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Reader:
    """Loads `analysis/<name>.json` of one workspace, once each; ``sources`` lists what was read (provenance)."""

    def __init__(self, ws: Path):
        self.ws = ws
        self.sources: dict[str, dict] = {}
        self.notes: list[dict] = []  # partial-data gaps an extractor finds (same keys as `gaps` rows)
        self._cache: dict[str, dict | None] = {}

    def rel(self, name: str) -> str:
        return "recon/recon.json" if name == "recon" else f"analysis/{name}.json"

    def get(self, name: str) -> dict | None:
        if name not in self._cache:
            path = self.ws / self.rel(name)
            data = read_json(path, None) if path.is_file() else None
            self._cache[name] = data if isinstance(data, dict) else None
            if self._cache[name] is not None:
                header = self._cache[name].get("header") or {}
                self.sources[name] = {"path": self.rel(name), "version": header.get("version"),
                                      "generated_at": header.get("generated_at") or self._cache[name].get("scanned_at"),
                                      "bytes": path.stat().st_size}
        return self._cache[name]

    def data(self, name: str) -> dict:
        found = self.get(name)
        data = (found or {}).get("data")
        return data if isinstance(data, dict) else {}


Emit = Callable[[str, dict], None]


# --- extractors: (reader, emit) -> None ---------------------------------------------------------------

def unity_rigs(rd: Reader, emit: Emit) -> None:
    src = rd.rel("rig")
    data = rd.data("rig")
    for i, s in enumerate(data.get("smr") or []):
        mesh = s.get("mesh") or {}
        emit("rigs", {"kind": "unity-smr", "item": s.get("prefab"), "node": s.get("node"), "bones": _int(s.get("bones")),
                      "deform_bones": _int(s.get("deform_bones")), "root_bone": s.get("root_bone"),
                      "bindposes": _int(mesh.get("bindposes")), "max_influences": _int(mesh.get("max_influences")),
                      "verts": _int(mesh.get("verts")), "tris": _int(mesh.get("tris")),
                      "blendshapes": _int(mesh.get("blendshapes")), "mesh": mesh.get("mesh"), "detail": s.get("detail"),
                      "source": src, "ref": f"data.smr[{i}]"})
    for i, c in enumerate(data.get("constraints") or []):
        emit("constraints", {"item": c.get("prefab"), "node": c.get("node"), "type": c.get("type"), "source": src,
                             "ref": f"data.constraints[{i}]"})
    for i, a in enumerate(rd.data("avatar").get("avatars") or []):
        if a.get("error"):
            continue
        emit("avatars", {"name": a.get("name"), "humanoid": int(bool(a.get("humanoid"))),
                         "skeleton_nodes": _int(a.get("skeleton_nodes")), "human_bones": _int(a.get("human_bones_mapped")),
                         "root_motion_bone": a.get("root_motion_bone"), "animators": len(a.get("animators") or []),
                         "source": rd.rel("avatar"), "ref": f"data.avatars[{i}]"})


def gltf_rigs(rd: Reader, emit: Emit) -> None:
    src = rd.rel("gltf_summary")
    for i, f in enumerate(rd.data("gltf_summary").get("files") or []):
        skins = f.get("skins") or []
        infl = f.get("infl_hist") or {}
        max_infl = max((_int(k) or 0 for k, v in infl.items() if v), default=None)
        for k, joints in enumerate(skins):
            emit("rigs", {"kind": "gltf-skin", "item": f.get("file"), "node": f"skin {k}", "bones": _int(joints),
                          "max_influences": max_infl, "verts": _int(f.get("verts")), "tris": _int(f.get("tris")),
                          "blendshapes": sum(_int(m.get("targets")) or 0 for m in f.get("meshes") or []),
                          "mesh": ", ".join(m.get("name") or "" for m in (f.get("meshes") or [])[:3]),
                          "source": src, "ref": f"data.files[{i}].skins[{k}]"})


def godot_skeletons(rd: Reader, emit: Emit) -> None:
    src = rd.rel("godot_animation")
    for i, s in enumerate(rd.data("godot_animation").get("skeletons") or []):
        roots = s.get("roots") or []
        emit("rigs", {"kind": "godot-skeleton", "item": s.get("file"), "node": s.get("node"), "bones": _int(s.get("bones")),
                      "root_bone": roots[0] if roots else None, "source": src, "ref": f"data.skeletons[{i}]"})


def godot_characters(rd: Reader, emit: Emit) -> None:
    """Skeletons linked to a gameplay scene (scene -> model -> imported scene -> Skeleton3D): rows for scenes that are
    top level or drive the skeleton (AnimationPlayer/Tree, PhysicalBone3D)."""
    src = rd.rel("godot_animation")
    for i, c in enumerate(rd.data("godot_animation").get("characters") or []):
        driven = c.get("animation_players") or c.get("animation_trees") or (c.get("ragdoll") or {}).get("bodies")
        if not (c.get("top_level") or driven):
            continue
        roots = c.get("roots") or []
        links = {"model": c.get("model"), "imported": c.get("imported"),
                 "players": len(c.get("animation_players") or []),
                 "animations": sum(_int(p.get("animations")) or 0 for p in c.get("animation_players") or []),
                 "trees": len(c.get("animation_trees") or []), "ragdoll_bodies": _int((c.get("ragdoll") or {}).get("bodies")),
                 "skinned_meshes": len(c.get("skinned_meshes") or []), "top_level": bool(c.get("top_level"))}
        emit("rigs", {"kind": "godot-character", "item": c.get("scene"), "node": c.get("skeleton"),
                      "bones": _int(c.get("bones")), "root_bone": roots[0] if roots else None, "mesh": c.get("model"),
                      "links": _j(links), "source": src, "ref": f"data.characters[{i}]"})


def unity_animation(rd: Reader, emit: Emit) -> None:
    src = rd.rel("animation")
    for i, c in enumerate(rd.data("animation").get("clips") or []):
        root = c.get("root") or {}
        emit("clips", {"kind": "unity", "name": c.get("name"), "owner": ", ".join(c.get("controllers") or []) or None,
                       "length": _num(c.get("length")), "fps": _num(c.get("fps")), "loop": int(bool(c.get("loop"))),
                       "events": len(c.get("events") or []), "tracks": _int(c.get("bindings")), "keys": None,
                       "clip_kind": c.get("kind"), "root_motion": int(bool(root.get("root_motion"))),
                       "source": src, "ref": f"data.clips[{i}]"})
    src = rd.rel("controller")
    for i, c in enumerate(rd.data("controller").get("controllers") or []):
        n = c.get("counts") or {}
        emit("anim_graphs", {"kind": f"unity-{c.get('kind') or 'controller'}", "name": c.get("name"), "file": c.get("file"),
                             "params": _int(n.get("params")), "layers": _int(n.get("layers")), "states": _int(n.get("states")),
                             "transitions": _int(n.get("transitions")), "blend_trees": _int(n.get("blend_trees")),
                             "clips": _int(n.get("clips")), "source": src, "ref": f"data.controllers[{i}]"})


def godot_animation(rd: Reader, emit: Emit) -> None:
    src = rd.rel("godot_animation")
    data = rd.data("godot_animation")
    for i, a in enumerate(data.get("animations") or []):
        emit("clips", {"kind": "godot", "name": a.get("name"), "owner": a.get("file"), "length": _num(a.get("length")),
                       "fps": None, "loop": int(bool(a.get("loop_mode"))), "events": _calls(a.get("methods")),
                       "tracks": _int(a.get("track_count")), "keys": _int(a.get("keys")), "clip_kind": "scene",
                       "source": src, "ref": f"data.animations[{i}]"})
    for i, t in enumerate(data.get("trees") or []):
        st = t.get("stats") or {}
        emit("anim_graphs", {"kind": "godot-tree", "name": t.get("node"), "file": t.get("file"),
                             "params": None, "layers": None, "states": _int(st.get("states")),
                             "transitions": _int(st.get("transitions")),
                             "blend_trees": sum(v for k, v in (st.get("node_types") or {}).items() if "Blend" in k),
                             "clips": sum((st.get("animations") or {}).values()) if isinstance(st.get("animations"), dict) else None,
                             "source": src, "ref": f"data.trees[{i}]"})
    for i, r in enumerate(data.get("ragdolls") or []):
        types = r.get("joint_node_types") or {}
        builtin = {k: v for k, v in (r.get("builtin_joint_types") or {}).items() if k != "none"}
        emit("ragdolls", {"kind": "godot-physical-bones", "item": r.get("file"), "file": r.get("file"),
                          "bodies": _int(r.get("physical_bones")),
                          "joints": (_int(r.get("joint_nodes")) or 0) + (_int(r.get("builtin_joints")) or 0),
                          "joint_types": _j({**types, **builtin}), "mass_total": _num(r.get("mass_total")),
                          "classes": _j(sorted({b.get("script") for b in r.get("bones") or [] if b.get("script")})),
                          "source": src, "ref": f"data.ragdolls[{i}]"})
    for i, j in enumerate(data.get("joints") or []):
        emit("joints", {"kind": "godot", "family": None, "item": j.get("file"), "path": j.get("path"), "type": j.get("type"),
                        "connected": " -> ".join(str(x) for x in (j.get("node_a"), j.get("node_b")) if x),
                        "mass": None, "settings": _j(j.get("settings") or {}),
                        "source": src, "ref": f"data.joints[{i}]"})


def gltf_animation(rd: Reader, emit: Emit) -> None:
    src = rd.rel("gltf_summary")
    for i, f in enumerate(rd.data("gltf_summary").get("files") or []):
        for k, a in enumerate(f.get("anims") or []):
            dt = _num(a.get("dt_min"))
            emit("clips", {"kind": "gltf", "name": a.get("name"), "owner": f.get("file"), "length": _num(a.get("dur")),
                           "fps": round(1 / dt, 2) if dt else None, "loop": None, "events": 0,
                           "tracks": _int(a.get("channels")), "keys": _int(a.get("keys")), "clip_kind": "skeletal"
                           if f.get("skins") else "node", "source": src, "ref": f"data.files[{i}].anims[{k}]"})


def unity_joints(rd: Reader, emit: Emit) -> None:
    src = rd.rel("joints")
    data = rd.data("joints")
    prefabs = data.get("prefabs") or []
    for i, p in enumerate(prefabs):
        for k, j in enumerate(p.get("joints") or []):
            settings = {key: j.get(key) for key in ("motion", "angular_x", "angular_y", "angular_z", "linear_limit", "drives",
                                                    "limits", "spring", "motor", "break_force") if key in j}
            emit("joints", {"kind": "unity", "family": p.get("family"), "item": p.get("root"), "path": j.get("path"),
                            "type": j.get("type"), "connected": j.get("connected"), "mass": _num((j.get("body") or {}).get("mass")),
                            "settings": _j(settings), "source": src, "ref": f"data.prefabs[{i}].joints[{k}]"})
    by_root = {(p.get("file"), p.get("root")): (i, p) for i, p in enumerate(prefabs)}
    for i, r in enumerate(data.get("ragdolls") or []):
        k, p = by_root.get((r.get("file"), r.get("prefab")), (None, {}))
        emit("ragdolls", {"kind": "unity-joints", "item": r.get("prefab"), "file": r.get("file"),
                          "bodies": len(p.get("bodies") or []) if p else None, "joints": len(p.get("joints") or []) if p else 0,
                          "joint_types": _j(p.get("counts") or {}), "mass_total": _num(p.get("total_mass")) if p else None,
                          "classes": _j(r.get("classes") or []), "detail": r.get("detail"), "source": src,
                          "ref": f"data.ragdolls[{i}]" + (f" + data.prefabs[{k}]" if k is not None else "")})


def audio_catalog(rd: Reader, emit: Emit) -> None:
    src = rd.rel("audio_catalog")
    for i, e in enumerate(rd.data("audio_catalog").get("events") or []):
        emit("audio", {"name": e.get("name"), "kind": e.get("kind"), "category": e.get("category"),
                       "clips": len(e.get("files") or []), "files": sum(1 for f in e.get("files") or [] if f.get("file")),
                       "users": len(e.get("used_by") or []),
                       "defined_at": e.get("defined_at"), "source": src, "ref": f"data.events[{i}]"})


def audio_files(rd: Reader, emit: Emit) -> None:
    """`assets/audio/files.jsonl` (`gst audio index`): formats, rates and durations, grouped."""
    path = rd.ws / "assets" / "audio" / "files.jsonl"
    if not path.is_file():
        return
    groups: dict[tuple, list] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            key = (row.get("ext"), row.get("fmt"), _int(row.get("channels")), _int(row.get("rate")))
            g = groups.setdefault(key, [0, 0.0, 0])
            g[0] += 1
            g[1] += _num(row.get("seconds")) or 0.0
            g[2] += _int(row.get("size")) or 0
    rd.sources["audio_files"] = {"path": "assets/audio/files.jsonl", "version": None, "generated_at": None,
                                 "bytes": path.stat().st_size}
    for (ext, fmt, ch, rate), (n, secs, size) in sorted(groups.items(), key=lambda kv: (-kv[1][0], str(kv[0]))):
        emit("audio_files", {"ext": ext, "format": fmt, "channels": ch, "rate": rate, "files": n, "seconds": round(secs, 3),
                             "bytes": size, "source": "assets/audio/files.jsonl", "ref": f"ext={ext} fmt={fmt} ch={ch} rate={rate}"})


def unity_shaders(rd: Reader, emit: Emit) -> None:
    src = rd.rel("shaders")
    for i, s in enumerate(rd.data("shaders").get("shaders") or []):
        emit("shaders", {"name": s.get("name"), "family": s.get("family"), "materials": _int(s.get("materials")),
                         "passes": _int(s.get("passes")), "keywords": len(s.get("keywords") or []),
                         "signatures": _j(s.get("signatures") or []), "source": src, "ref": f"data.shaders[{i}]"})


def unreal_rigs(rd: Reader, emit: Emit) -> None:
    src = rd.rel("unreal_animation")
    data = rd.data("unreal_animation")
    for i, s in enumerate(data.get("skeletons") or []):
        emit("rigs", {"kind": "unreal-skeleton", "item": s.get("package"), "node": s.get("name"), "bones": _int(s.get("bones")),
                      "root_bone": s.get("root"), "detail": s.get("detail"), "source": src, "ref": f"data.skeletons[{i}]"})
    for i, m in enumerate(data.get("meshes") or []):
        lod0 = (m.get("lods") or [{}])[0]
        emit("rigs", {"kind": "unreal-skelmesh", "item": m.get("package"), "node": m.get("name"),
                      "bones": _int(m.get("bones") or m.get("skeleton_bones")), "root_bone": m.get("root"),
                      "max_influences": _int(lod0.get("max_influences")), "verts": _int(lod0.get("verts")),
                      "tris": _int(lod0.get("tris")), "blendshapes": len(m.get("morph_targets") or []),
                      "mesh": m.get("skeleton"), "source": src, "ref": f"data.meshes[{i}]"})


def unreal_animation(rd: Reader, emit: Emit) -> None:
    src = rd.rel("unreal_animation")
    data = rd.data("unreal_animation")
    for i, s in enumerate(data.get("sequences") or []):
        emit("clips", {"kind": "unreal", "name": s.get("name"), "owner": s.get("skeleton"), "length": _num(s.get("length")),
                       "fps": _num(s.get("fps")), "loop": None, "events": len(s.get("notifies") or []),
                       "tracks": _int(s.get("tracks")), "keys": _int(s.get("keys")), "clip_kind": "sequence",
                       "root_motion": int(bool(s.get("root_motion"))), "source": src, "ref": f"data.sequences[{i}]"})
    for i, m in enumerate(data.get("montages") or []):
        emit("clips", {"kind": "unreal", "name": m.get("name"), "owner": m.get("skeleton"), "length": _num(m.get("length")),
                       "fps": None, "loop": None, "events": len(m.get("notifies") or []), "tracks": len(m.get("slots") or []),
                       "keys": None, "clip_kind": "montage", "root_motion": None, "source": src, "ref": f"data.montages[{i}]"})
    for i, a in enumerate(data.get("anim_blueprints") or []):
        machines = a.get("machines") or []
        nodes = a.get("nodes") or {}
        emit("anim_graphs", {"kind": "unreal-abp", "name": a.get("class"), "file": a.get("file"), "params": None,
                             "layers": len(machines), "states": sum(len(m.get("states") or []) for m in machines),
                             "transitions": sum(len(m.get("transitions") or []) for m in machines),
                             "blend_trees": sum(v for k, v in nodes.items() if "Blend" in k),
                             "clips": len(a.get("assets") or []), "source": src, "ref": f"data.anim_blueprints[{i}]"})
    for i, b in enumerate(data.get("blendspaces") or []):
        emit("anim_graphs", {"kind": "unreal-blendspace", "name": b.get("name"), "file": b.get("file"),
                             "params": len(b.get("axes") or []), "layers": None, "states": None, "transitions": None,
                             "blend_trees": 1, "clips": len(b.get("samples") or []), "source": src,
                             "ref": f"data.blendspaces[{i}]"})


def unreal_physics(rd: Reader, emit: Emit) -> None:
    src = rd.rel("unreal_animation")
    for i, p in enumerate(rd.data("unreal_animation").get("physics_assets") or []):
        cons = p.get("constraints") or []
        for k, c in enumerate(cons):
            settings = {key: c.get(key) for key in ("swing1", "swing2", "twist", "linear", "drives", "defaulted") if key in c}
            emit("joints", {"kind": "unreal", "family": "PhysicsAsset", "item": p.get("package"), "path": c.get("name"),
                            "type": c.get("type"), "connected": " -> ".join(str(x) for x in (c.get("bone1"), c.get("bone2")) if x),
                            "mass": None, "settings": _j(settings), "source": src,
                            "ref": f"data.physics_assets[{i}].constraints[{k}]"})
        emit("ragdolls", {"kind": "unreal-physics-asset", "item": p.get("name"), "file": p.get("package"),
                          "bodies": len(p.get("bodies") or []), "joints": len(cons), "joint_types": _j(p.get("joint_types") or {}),
                          "mass_total": _num(p.get("mass_override_total")), "classes": _j(["PhysicsAsset"]),
                          "detail": None, "source": src, "ref": f"data.physics_assets[{i}]"})


def godot_shaders(rd: Reader, emit: Emit) -> None:
    src = rd.rel("godot_shaders")
    for i, s in enumerate(rd.data("godot_shaders").get("shaders") or []):
        if s.get("kind") == "include":
            continue
        emit("shaders", {"name": s.get("name"), "family": s.get("shader_type") or s.get("kind"),
                         "materials": _int(s.get("materials")), "passes": None,
                         "keywords": len(s.get("render_modes") or []), "signatures": _j(s.get("features") or []),
                         "uniforms": len(s.get("uniforms") or []), "source": src, "ref": f"data.shaders[{i}]"})


def unreal_counts(rd: Reader, emit: Emit) -> None:
    src = rd.rel("unreal_assets")
    guessed = rd.data("unreal_assets").get("guessed") or {}
    for kind, count in sorted(guessed.items()):
        emit("asset_counts", {"type": kind, "count": _int(count), "source": src, "ref": f"data.guessed.{kind}"})


def middleware(rd: Reader, emit: Emit) -> None:
    recon = rd.get("recon") or {}
    src = rd.rel("recon")
    for i, name in enumerate(recon.get("middleware") or []):
        emit("middleware", {"name": name, "kind": "sdk", "evidence": None, "source": src, "ref": f"middleware[{i}]"})
    if recon.get("net_voice") is None:
        rd.notes.append({"facet": "middleware", "analysis": "recon", "kind": "partial",
                         "reason": "`recon/recon.json` sem `net_voice` (recon de uma versão antiga do gst): só a lista de SDKs das regras "
                                   "SteamDB; rode `gst recon` no workspace para rede/voz por nome de arquivo"})
    for i, m in enumerate(recon.get("net_voice") or []):
        emit("middleware", {"name": m.get("name"), "kind": m.get("kind"), "evidence": _j(m.get("evidence") or []),
                            "source": src, "ref": f"net_voice[{i}]"})


def systems(rd: Reader, emit: Emit) -> None:
    src = rd.rel("systems")
    for i, s in enumerate(rd.data("systems").get("systems") or []):
        top = [{"name": t.get("name"), "path": t.get("path"), "line": t.get("line"), "refs": t.get("refs")}
               for t in (s.get("top") or [])[:5]]
        emit("systems", {"label": s.get("label"), "types": _int(s.get("types")), "files": _int(s.get("files")),
                         "prefabs": _int(s.get("prefabs")), "score": _int(s.get("score")), "top": _j(top),
                         "source": src, "ref": f"data.systems[{i}]"})


def dcc(rd: Reader, emit: Emit) -> None:
    src = rd.rel("dcc")
    for family, f in sorted((rd.data("dcc").get("families") or {}).items()):
        if not isinstance(f, dict):
            continue
        emit("dcc", {"family": family, "verdict": f.get("verdict"), "strong": _int(f.get("strong")), "weak": _int(f.get("weak")),
                     "ambiguous": _int(f.get("ambiguous")), "rules": _j(f.get("rules") or {}), "source": src,
                     "ref": f"data.families.{family}"})


# analysis name -> extractors that read it (an extractor runs once per workspace when its analysis exists)
EXTRACTORS: dict[str, tuple[Callable[[Reader, Emit], None], ...]] = {
    "rig": (unity_rigs,), "avatar": (), "gltf_summary": (gltf_rigs, gltf_animation),
    "godot_animation": (godot_skeletons, godot_characters, godot_animation), "animation": (unity_animation,),
    "controller": (), "joints": (unity_joints,), "audio_catalog": (audio_catalog,), "shaders": (unity_shaders,),
    "godot_shaders": (godot_shaders,),
    "unreal_assets": (unreal_counts,), "unreal_animation": (unreal_rigs, unreal_animation, unreal_physics),
    "recon": (middleware,), "systems": (systems,), "dcc": (dcc,),
}
# extractors that also read a second analysis on their own (so `avatar`/`controller` alone still load)
COMPANIONS = {"avatar": "rig", "controller": "animation"}


def extract(ws: Path, emit: Emit) -> tuple[Reader, list[str]]:
    """Run every extractor whose analysis exists in ``ws``; returns the reader (``sources`` read, ``notes``) and the
    analyses present."""
    rd = Reader(ws)
    done: set = set()
    present = []
    for name, funcs in EXTRACTORS.items():
        if rd.get(name) is None:
            continue
        present.append(name)
        for func in funcs or EXTRACTORS.get(COMPANIONS.get(name, ""), ()):
            if func not in done:
                done.add(func)
                func(rd, emit)
    audio_files(rd, emit)
    return rd, present


def gaps(engine: str, present: set[str], key: str) -> list[dict]:
    """Rows of ``gaps`` for one workspace: missing analyses per facet, facets an engine has no analysis for, and
    what a present analysis does not tell (``partial``)."""
    out = []
    for facet in FACETS:
        names = facet_sources(facet, engine)
        if names is None:
            out.append({"facet": facet, "analysis": None, "kind": "no-analysis",
                        "reason": f"engine `{engine}` sem regra de análise para esta faceta"})
            continue
        if not names and (facet, engine) in PARTIAL:
            out.append({"facet": facet, "analysis": None, "kind": "no-analysis", "reason": PARTIAL[(facet, engine)]})
            continue
        for name in names:
            if name in present:
                continue
            hint = "`gst recon` no workspace" if name == "recon" else f"`gst analyze {name} --game {key}`"
            out.append({"facet": facet, "analysis": name, "kind": "missing",
                        "reason": f"`{'recon/recon.json' if name == 'recon' else f'analysis/{name}.json'}` ausente: rode {hint}"})
        if (facet, engine) in PARTIAL and any(n in present for n in names):
            out.append({"facet": facet, "analysis": None, "kind": "partial", "reason": PARTIAL[(facet, engine)]})
    return out
