"""SkinnedMeshRenderer -> GLB with mesh, skin (joints + inverse bind matrices) and the bone hierarchy.

Unity is left-handed (Y up), glTF right-handed: X is mirrored (positions/normals ``-x``, quaternions
``(x, -y, -z, w)``, matrices ``S M S`` with ``S = diag(-1, 1, 1, 1)``) and the triangle winding is
reversed; UV ``v`` is flipped. Bones and their ancestors become nodes with their local TRS; the mesh's
bindposes are the inverse bind matrices. Pure Python (``array``), no numpy.
"""

from __future__ import annotations

import struct
from array import array

from gst.engines.unity import loader

FLOAT, USHORT, UINT, UBYTE = 5126, 5123, 5125, 5121
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


def _xyz(v) -> tuple[float, float, float]:
    if isinstance(v, dict):
        return float(v.get("x", 0)), float(v.get("y", 0)), float(v.get("z", 0))
    if hasattr(v, "x"):
        return float(v.x), float(v.y), float(v.z)
    return float(v[0]), float(v[1]), float(v[2])


def _quat(q) -> tuple[float, float, float, float]:
    if isinstance(q, dict):
        x, y, z, w = (float(q.get(k, 0)) for k in "xyzw")
    elif hasattr(q, "w"):
        x, y, z, w = float(q.x), float(q.y), float(q.z), float(q.w)
    else:
        x, y, z, w = (float(c) for c in q[:4])
    return x, -y, -z, w


def _matrix(m) -> list[float]:
    """Unity Matrix4x4 (eRC) -> glTF column-major with the X mirror applied."""
    out = []
    for c in range(4):
        for r in range(4):
            v = float(getattr(m, f"e{r}{c}", 0.0) if not isinstance(m, dict) else m.get(f"e{r}{c}", 0.0))
            if (r == 0) != (c == 0):
                v = -v
            out.append(v)
    return out


class _Blob:
    def __init__(self):
        self.data = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def add(self, arr: array, *, comp: int, count: int, typ: str, target: int | None = None,
            minmax: tuple[list, list] | None = None) -> int:
        while len(self.data) % 4:
            self.data.append(0)
        raw = arr.tobytes()
        view = {"buffer": 0, "byteOffset": len(self.data), "byteLength": len(raw)}
        if target:
            view["target"] = target
        self.data += raw
        self.views.append(view)
        acc = {"bufferView": len(self.views) - 1, "componentType": comp, "count": count, "type": typ}
        if minmax:
            acc["min"], acc["max"] = minmax
        self.accessors.append(acc)
        return len(self.accessors) - 1


def build_glb(name: str, h, bones: list[dict], bindposes: list, path) -> dict:
    """Write a GLB. ``bones``: list of {"name", "t", "r", "s", "parent": index|None} in joint order
    first, ancestors after. Returns stats."""
    import pygltflib as g

    blob = _Blob()
    verts = [(-v[0], v[1], v[2]) for v in (tuple(x[:3]) for x in h.m_Vertices)]
    pos = array("f", [c for v in verts for c in v])
    mins = [min(v[i] for v in verts) for i in range(3)]
    maxs = [max(v[i] for v in verts) for i in range(3)]
    attrs = {"POSITION": blob.add(pos, comp=FLOAT, count=len(verts), typ="VEC3", target=ARRAY_BUFFER,
                                  minmax=(mins, maxs))}
    n = len(verts)
    if h.m_Normals and len(h.m_Normals) == n:
        attrs["NORMAL"] = blob.add(array("f", [c for x in h.m_Normals for c in (-x[0], x[1], x[2])]),
                                   comp=FLOAT, count=n, typ="VEC3", target=ARRAY_BUFFER)
    if h.m_UV0 and len(h.m_UV0) == n:
        attrs["TEXCOORD_0"] = blob.add(array("f", [c for x in h.m_UV0 for c in (x[0], 1.0 - x[1])]),
                                       comp=FLOAT, count=n, typ="VEC2", target=ARRAY_BUFFER)
    skinned = bool(bones) and h.m_BoneWeights and h.m_BoneIndices and len(h.m_BoneWeights) == n
    if skinned:
        nj = len([b for b in bones if b.get("joint")])
        idx = array("H")
        wts = array("f")
        for bi, bw in zip(h.m_BoneIndices, h.m_BoneWeights):
            bi4 = list(bi[:4]) + [0] * (4 - len(bi[:4]))
            bw4 = [float(x) for x in bw[:4]] + [0.0] * (4 - len(bw[:4]))
            bi4 = [int(i) if 0 <= int(i) < nj else 0 for i in bi4]
            total = sum(bw4) or 1.0
            idx.extend(bi4)
            wts.extend(w / total for w in bw4)
        attrs["JOINTS_0"] = blob.add(idx, comp=USHORT, count=n, typ="VEC4", target=ARRAY_BUFFER)
        attrs["WEIGHTS_0"] = blob.add(wts, comp=FLOAT, count=n, typ="VEC4", target=ARRAY_BUFFER)
    prims = []
    tris_total = 0
    for tris in h.get_triangles():
        if not tris:
            continue
        flat = array("I", [i for a, b, c in tris for i in (a, c, b)])
        tris_total += len(tris)
        acc = blob.add(flat, comp=UINT, count=len(flat), typ="SCALAR", target=ELEMENT_ARRAY_BUFFER)
        prims.append(g.Primitive(attributes=g.Attributes(**attrs), indices=acc))
    if not prims:
        raise ValueError("mesh has no triangles")
    nodes = [g.Node(name=name, mesh=0)]
    skins = []
    if skinned:
        first = len(nodes)
        for b in bones:
            nodes.append(g.Node(name=b["name"], translation=list(b["t"]), rotation=list(b["r"]), scale=list(b["s"])))
        for i, b in enumerate(bones):
            if b["parent"] is not None:
                nodes[first + b["parent"]].children.append(first + i)
        nodes[0].skin = 0
        joints = [first + i for i, b in enumerate(bones) if b.get("joint")]
        ibm = array("f")
        for i in range(len(joints)):
            ibm.extend(_matrix(bindposes[i]) if i < len(bindposes) else
                       [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
        acc = blob.add(ibm, comp=FLOAT, count=len(joints), typ="MAT4")
        skins.append(g.Skin(joints=joints, inverseBindMatrices=acc))
    roots = [0] + ([len([0]) + i for i, b in enumerate(bones) if b["parent"] is None] if skinned else [])
    gltf = g.GLTF2(
        asset=g.Asset(generator="gst (UnityPy)", version="2.0"),
        scene=0, scenes=[g.Scene(nodes=roots)], nodes=nodes,
        meshes=[g.Mesh(name=name, primitives=prims)], skins=skins,
        buffers=[g.Buffer(byteLength=len(blob.data))],
        bufferViews=[g.BufferView(**v) for v in blob.views],
        accessors=[g.Accessor(**a) for a in blob.accessors],
    )
    gltf.set_binary_blob(bytes(blob.data))
    gltf.save_binary(str(path))
    return {"vertices": n, "triangles": tris_total, "joints": len([b for b in bones if b.get("joint")]) if skinned else 0,
            "nodes": len(nodes)}


def _bone_table(s, sf, smr) -> list[dict]:
    """Joints in m_Bones order, then their ancestors; each with local TRS, name and parent index."""
    entries: list[dict] = []
    index: dict[tuple, int] = {}

    def add(reader, joint: bool) -> int | None:
        if reader is None:
            return None
        key = (id(reader.assets_file), reader.path_id)
        if key in index:
            if joint:
                entries[index[key]]["joint"] = True
            return index[key]
        tf = reader.read(check_read=False)
        go = loader.deref(getattr(tf, "m_GameObject", None))
        e = {"name": getattr(go, "m_Name", "") or f"bone_{reader.path_id}",
             "t": [-_xyz(tf.m_LocalPosition)[0], *_xyz(tf.m_LocalPosition)[1:]],
             "r": list(_quat(tf.m_LocalRotation)), "s": list(_xyz(tf.m_LocalScale)),
             "parent": None, "joint": joint, "_tf": tf, "_sf": reader.assets_file}
        index[key] = len(entries)
        entries.append(e)
        return index[key]

    joint_ids = []
    for ptr in getattr(smr, "m_Bones", []) or []:
        joint_ids.append(add(s.resolve(sf, ptr), True))
    if any(j is None for j in joint_ids):
        return []
    # ancestors (bounded by the hierarchy itself)
    i = 0
    while i < len(entries):
        e = entries[i]
        father = getattr(e["_tf"], "m_Father", None)
        if father is not None and getattr(father, "m_PathID", 0):
            p = add(s.resolve(e["_sf"], father), False)
            e["parent"] = p
        i += 1
    # joints must be the first len(m_Bones) entries in order: they are (added first, unique)
    if len(set(joint_ids)) != len(joint_ids):  # the same transform listed twice: keep order by duplicating
        return []
    for e in entries:
        e.pop("_tf", None)
        e.pop("_sf", None)
    return entries


def export_skinned(s, obj) -> list:
    sf = obj.assets_file
    smr = obj.read(check_read=False)
    mesh_reader = s.resolve(sf, smr.m_Mesh)
    if mesh_reader is None:
        go = loader.deref(getattr(smr, "m_GameObject", None))
        why = "no mesh (m_Mesh = 0, assigned at runtime)" if not getattr(smr.m_Mesh, "m_PathID", 0) else "mesh not found"
        s.notes.append(f"SkinnedMeshRenderer {getattr(go, 'm_Name', '')!r} #{obj.path_id}: {why}")
        return []
    mesh = mesh_reader.read(check_read=False)
    h = loader.mesh_handler(mesh)
    if not h.m_VertexCount or not h.m_Vertices:
        return []
    go = loader.deref(getattr(smr, "m_GameObject", None))
    go_name = getattr(go, "m_Name", "") or ""
    bones = _bone_table(s, sf, smr)
    bindposes = list(getattr(mesh, "m_BindPose", []) or [])
    label = f"{go_name}__{mesh.m_Name}" if go_name and go_name != mesh.m_Name else (mesh.m_Name or go_name)
    path = s.w.target("SkinnedMesh", label or f"SMR_{obj.path_id}", ".glb", obj.path_id)
    stats: dict = {}

    def write(tmp):
        stats.update(build_glb(mesh.m_Name or "mesh", h, bones, bindposes, tmp))

    s.w.commit(path, writer=write, meta=s.meta(obj, label, mesh=mesh.m_Name, mesh_path_id=mesh_reader.path_id,
                                               gameobject=go_name, bones=len(getattr(smr, "m_Bones", []) or [])))
    if not bones and getattr(smr, "m_Bones", None):
        s.notes.append(f"{label}: bones not resolvable, exported without skin")
    return [path]


def read_glb_summary(path) -> dict:
    """Small reader used by tests: counts of the GLB we wrote."""
    import pygltflib as g

    doc = g.GLTF2().load_binary(str(path))
    return {"nodes": len(doc.nodes), "skins": len(doc.skins), "joints": len(doc.skins[0].joints) if doc.skins else 0,
            "primitives": sum(len(m.primitives) for m in doc.meshes),
            "magic": struct.unpack("<I", open(path, "rb").read(4))[0]}
