"""Mesh -> OBJ (mesh space, X mirrored like UnityPy/AssetStudio) with a stats sidecar."""

from __future__ import annotations

from gst.engines.unity import loader


def obj_text(mesh, h) -> str:
    """OBJ from a processed MeshHandler (works when ``Mesh.export`` trips on ``m_Skin`` None)."""
    name = mesh.m_Name or "mesh"
    sb = [f"g {name}\n"]
    sb.extend("v {0:.9G} {1:.9G} {2:.9G}\n".format(-p[0], p[1], p[2]).replace("nan", "0") for p in h.m_Vertices)
    if h.m_UV0:
        sb.extend("vt {0:.9G} {1:.9G}\n".format(uv[0], uv[1]).replace("nan", "0") for uv in h.m_UV0)
    if h.m_Normals:
        sb.extend("vn {0:.9G} {1:.9G} {2:.9G}\n".format(-n[0], n[1], n[2]).replace("nan", "0") for n in h.m_Normals)
    uv, nrm = bool(h.m_UV0), bool(h.m_Normals)
    for i, tris in enumerate(h.get_triangles()):
        sb.append(f"g {name}_{i}\n")
        for a, b, c in tris:
            idx = [c + 1, b + 1, a + 1]
            if uv and nrm:
                sb.append("f {0}/{0}/{0} {1}/{1}/{1} {2}/{2}/{2}\n".format(*idx))
            elif uv:
                sb.append("f {0}/{0} {1}/{1} {2}/{2}\n".format(*idx))
            elif nrm:
                sb.append("f {0}//{0} {1}//{1} {2}//{2}\n".format(*idx))
            else:
                sb.append("f {0} {1} {2}\n".format(*idx))
    return "".join(sb)


def mesh_stats(mesh, h) -> dict:
    tris = sum(len(t) for t in h.get_triangles())
    return {"vertices": h.m_VertexCount, "triangles": tris, "submeshes": len(getattr(mesh, "m_SubMeshes", []) or []),
            "bindposes": len(getattr(mesh, "m_BindPose", []) or []), "skinned": bool(h.m_BoneWeights),
            "blendshapes": len(getattr(getattr(mesh, "m_Shapes", None), "shapes", []) or [])}


def export_mesh(s, obj) -> list:
    mesh = obj.read(check_read=False)
    h = loader.mesh_handler(mesh)
    if not h.m_VertexCount or not h.m_Vertices:
        return []
    path = s.w.target("Mesh", mesh.m_Name or f"Mesh_{obj.path_id}", ".obj", obj.path_id)
    s.w.commit(path, obj_text(mesh, h), meta=s.meta(obj, mesh.m_Name, **mesh_stats(mesh, h)))
    return [path]
