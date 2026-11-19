"""Unity analyses (prefabs, mono_values, catalogs, animation, rig, shaders...) and what they share.

:class:`UnityFile` opens one install file read-only (streaming, one at a time) with a PPtr catalog, the
MonoScript index (script names from raw bytes) and the typetree generator; :func:`ptr` normalises PPtrs;
:class:`Resolver` turns PPtrs into small cached facts (names, mesh stats, materials) without keeping objects.
Analyses keep their logic on plain dicts so tests can feed synthetic objects (``tests/unit/test_analysis_*.py``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

TRANSFORMS = ("Transform", "RectTransform")
COLLIDERS = ("BoxCollider", "SphereCollider", "CapsuleCollider", "MeshCollider", "WheelCollider", "TerrainCollider",
             "BoxCollider2D", "CircleCollider2D", "PolygonCollider2D", "CapsuleCollider2D", "EdgeCollider2D")
JOINTS = ("HingeJoint", "FixedJoint", "ConfigurableJoint", "SpringJoint", "CharacterJoint", "HingeJoint2D",
          "FixedJoint2D", "SpringJoint2D", "DistanceJoint2D")


def ptr(value: Any) -> tuple[int, int]:
    """``(m_FileID, m_PathID)`` of a PPtr dict/object (``(0, 0)`` for null), with the 3-byte shift fixed."""
    if value is None:
        return 0, 0
    if isinstance(value, dict):
        fid, pid = int(value.get("m_FileID", 0) or 0), int(value.get("m_PathID", 0) or 0)
    else:
        fid, pid = int(getattr(value, "m_FileID", 0) or 0), int(getattr(value, "m_PathID", 0) or 0)
    if fid > 0 and (fid & 0xFFFFFF) == 0:
        return fid >> 24, pid >> 24
    return fid, pid


def is_scene(sf_name: str) -> bool:
    """Scene files (``levelN``, bundle scene CABs ``BuildPlayer-*``) vs prefab/asset files."""
    base = os.path.basename(str(sf_name))
    return (base.startswith("level") and base[5:].isdigit()) or (base.startswith("BuildPlayer-")
                                                                  and not base.endswith(".sharedAssets"))


class UnityFile:
    """One install file opened for an analysis: ``with UnityFile(ctx, relpath) as uf: for sf in uf.files(): ...``."""

    def __init__(self, ctx, relpath: str):
        from gst.engines.unity.catalog import Catalog
        from gst.engines.unity.scripts import ScriptIndex

        self.ctx = ctx
        self.relpath = relpath
        self.env = ctx.open_unity(relpath)
        ctx.use(f"install:{relpath}", Path(ctx.install_dir) / relpath)
        self.catalog = Catalog(ctx.install_dir, max_envs=int(ctx.opt("catalog_envs") or 4))
        self.catalog.pin(relpath, self.env)
        self.scripts = ScriptIndex(self.catalog)
        self._gen = None

    def __enter__(self) -> "UnityFile":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        from gst.engines.unity import loader

        self.catalog.unpin(self.relpath)
        self.catalog.close()
        loader.close_env(self.env)

    def files(self):
        """SerializedFiles of this file (one CAB of a bundle at a time)."""
        from gst.engines.unity import loader

        return loader.serialized_files(self.env, release=True)

    def generator(self):
        if self._gen is None:
            from gst.engines.unity import loader, typetree

            version = loader.unity_version(self.env) or ""
            self._gen = typetree.generator(self.ctx.install_dir, version, self.ctx.workspace,
                                           str(self.ctx.game.get("buildid") or "?"))
            if self._gen[0] is not None:
                typetree.attach(self.env, self._gen[0])
            self.ctx.tool("typetree", self._gen[1])
        return self._gen

    def script(self, obj):
        """ScriptInfo of a MonoBehaviour from its raw ``m_Script`` (no typetree read)."""
        return self.scripts.script_of(obj)

    def mono(self, obj, info=None) -> dict | None:
        """Typetree of a MonoBehaviour through the generator chain (raw prefilter; None when unknown)."""
        from gst.engines.unity import typetree

        gen, _src = self.generator()
        return typetree.read_mono(obj, gen, info if info is not None else self.script(obj))

    def resolve(self, sf, value):
        fid, pid = ptr(value)
        return self.catalog.resolve(sf, fid, pid) if pid else None


def builtin(obj) -> dict | None:
    from gst.engines.unity import typetree

    return typetree.read_builtin(obj)


class Resolver:
    """Facts about PPtr targets, cached per (file, path id): names, mesh stats, material summaries."""

    def __init__(self, uf: UnityFile | None):
        self.uf = uf
        self.meshes: dict[tuple, dict] = {}
        self.materials: dict[tuple, dict] = {}
        self.names: dict[tuple, str] = {}

    def _key(self, reader) -> tuple:
        return (str(getattr(reader.assets_file, "name", "")), reader.path_id)

    def reader(self, sf, value):
        return self.uf.resolve(sf, value) if self.uf is not None else None

    def name(self, sf, value) -> str | None:
        from gst.engines.unity import loader

        r = self.reader(sf, value)
        if r is None:
            return None
        k = self._key(r)
        if k not in self.names:
            self.names[k] = loader.peek_name(r)
        return self.names[k]

    def mesh(self, sf, value, *, skin: bool = False) -> dict | None:
        """``{"mesh", "file", "path_id", "verts", "tris", "submeshes", "bindposes", "blendshapes"}`` (+ with
        ``skin``: ``max_influences``, ``influences_hist``, ``bones_used``)."""
        r = self.reader(sf, value)
        if r is None:
            fid, pid = ptr(value)
            return {"missing": True, "file_id": fid, "path_id": pid} if pid else None
        k = self._key(r)
        st = self.meshes.get(k)
        if st is None or (skin and "max_influences" not in st and "skin_error" not in st):
            st = mesh_stats(r, skin=skin)
            self.meshes[k] = st
        return st

    def material(self, sf, value) -> dict | None:
        """``{"name", "shader", "textures": {slot: name}}`` of a Material."""
        from gst.engines.unity import loader

        r = self.reader(sf, value)
        if r is None:
            return None
        k = self._key(r)
        if k in self.materials:
            return self.materials[k]
        tree = builtin(r) or {}
        out: dict[str, Any] = {"name": tree.get("m_Name") or loader.peek_name(r), "path_id": r.path_id,
                               "file": os.path.basename(str(r.assets_file.name))}
        shader = self.uf.resolve(r.assets_file, tree.get("m_Shader")) if self.uf is not None else None
        out["shader"] = loader.peek_name(shader) if shader is not None else None
        tex = {}
        envs = ((tree.get("m_SavedProperties") or {}).get("m_TexEnvs")) or []
        for te in envs:
            slot, val = (te[0], te[1]) if isinstance(te, (list, tuple)) else (te.get("first"), te.get("second"))
            t = (val or {}).get("m_Texture")
            if ptr(t)[1]:
                tr = self.uf.resolve(r.assets_file, t)
                tex[str(slot)] = loader.peek_name(tr) if tr is not None else f"?{ptr(t)[1]}"
        out["textures"] = dict(sorted(tex.items()))
        self.materials[k] = out
        return out


def mesh_stats(reader, *, skin: bool = False) -> dict:
    """Vertex/triangle counts of a Mesh reader; bone influences only with ``skin`` (decodes the vertex data)."""
    import collections

    from gst.engines.unity import loader

    out: dict[str, Any] = {"mesh": loader.peek_name(reader), "file": os.path.basename(str(reader.assets_file.name)),
                           "path_id": reader.path_id}
    try:
        m = reader.read(check_read=False)
    except Exception as exc:
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        return out
    vd = getattr(m, "m_VertexData", None)
    out["verts"] = int(getattr(vd, "m_VertexCount", 0) or 0) if vd is not None else 0
    subs = getattr(m, "m_SubMeshes", None) or []
    tris = 0
    for s in subs:
        topo = int(getattr(s, "topology", 0) or 0)
        if topo == 0:  # triangles
            tris += int(getattr(s, "indexCount", 0) or 0) // 3
    out["tris"] = tris
    out["submeshes"] = len(subs)
    out["bindposes"] = len(getattr(m, "m_BindPose", None) or [])
    shapes = getattr(m, "m_Shapes", None)
    out["blendshapes"] = len(getattr(shapes, "channels", None) or []) if shapes is not None else 0
    if skin and out["bindposes"]:
        try:
            h = loader.mesh_handler(m)
            hist: collections.Counter = collections.Counter()
            used = set()
            for ws, bs in zip(h.m_BoneWeights or [], h.m_BoneIndices or []):
                n = 0
                for w, b in zip(ws, bs):
                    if w > 1e-4:
                        n += 1
                        used.add(b)
                hist[n] += 1
            out["influences_hist"] = {str(k): v for k, v in sorted(hist.items())}
            out["max_influences"] = max(hist) if hist else 0
            out["bones_used"] = len(used)
        except Exception as exc:
            out["skin_error"] = f"{exc.__class__.__name__}: {exc}"
    return out


def basename(path: str | Path) -> str:
    return os.path.basename(str(path).replace("\\", "/"))
