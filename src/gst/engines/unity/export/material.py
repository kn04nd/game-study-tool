"""Material -> JSON (shader name, keywords, textures/floats/colors with texture names resolved);
Shader -> parsed form JSON (+ UnityPy's ShaderLab text when it converts)."""

from __future__ import annotations

from gst.engines.unity import loader, typetree


def _pairs(seq):
    for item in seq or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            yield item[0], item[1]
        elif isinstance(item, dict) and "first" in item:
            yield item["first"], item["second"]


def export_material(s, obj) -> list:
    tree = typetree.read_builtin(obj)
    if tree is None:
        raise RuntimeError("typetree read failed")
    sf = obj.assets_file
    shader = s.resolve(sf, tree.get("m_Shader"))
    shader_name = ""
    if shader is not None:
        try:
            parsed = loader.unwrap_offsetptr(shader.read_typetree(check_read=False).get("m_ParsedForm", {}))
            shader_name = parsed.get("m_Name", "") if isinstance(parsed, dict) else ""
        except Exception:
            shader_name = loader.peek_name(shader)
    props = tree.get("m_SavedProperties", {}) or {}
    textures = {}
    for key, env in _pairs(props.get("m_TexEnvs")):
        tex = env.get("m_Texture", {}) if isinstance(env, dict) else {}
        ref = s.resolve(sf, tex)
        textures[key] = {"texture": loader.peek_name(ref) if ref is not None else None,
                         "texture_type": loader.type_name(ref) if ref is not None else None,
                         "path_id": tex.get("m_PathID"), "file_id": tex.get("m_FileID"),
                         "scale": env.get("m_Scale"), "offset": env.get("m_Offset")}
    out = {"name": tree.get("m_Name"), "shader": shader_name, "shader_path_id": (tree.get("m_Shader") or {}).get("m_PathID"),
           "keywords": tree.get("m_ValidKeywords") or tree.get("m_ShaderKeywords"),
           "invalid_keywords": tree.get("m_InvalidKeywords"), "render_queue": tree.get("m_CustomRenderQueue"),
           "textures": textures, "floats": dict(_pairs(props.get("m_Floats"))),
           "ints": dict(_pairs(props.get("m_Ints"))), "colors": dict(_pairs(props.get("m_Colors"))),
           "tags": dict(_pairs(tree.get("stringTagMap")))}
    path = s.w.target("Material", tree.get("m_Name") or f"Material_{obj.path_id}", ".json", obj.path_id)
    s.w.json(path, out, meta=s.meta(obj, tree.get("m_Name", ""), shader=shader_name))
    return [path]


_BLOB_KEYS = ("compressedBlob", "decompressedLengths", "compressedLengths", "offsets", "decompressedSize")


def export_shader(s, obj) -> list:
    tree = typetree.read_builtin(obj)
    if tree is None:
        raise RuntimeError("typetree read failed")
    parsed = loader.unwrap_offsetptr(tree.get("m_ParsedForm", {})) or {}
    name = (parsed.get("m_Name") if isinstance(parsed, dict) else "") or tree.get("m_Name") or f"Shader_{obj.path_id}"
    blobs = {k: (len(tree[k]) if isinstance(tree.get(k), (list, bytes)) else tree.get(k)) for k in _BLOB_KEYS if k in tree}
    out = {"name": name, "platforms": tree.get("platforms"), "parsed_form": parsed,
           "dependencies": tree.get("m_Dependencies"), "blob_sizes": blobs,
           "note": "compiled programs (compressedBlob) are not included; see the .shader text when present"}
    path = s.w.target("Shader", name, ".json", obj.path_id)
    s.w.json(path, out, meta=s.meta(obj, name))
    paths = [path]
    try:
        text = obj.read(check_read=False).export()
        if text:
            tpath = s.w.target("Shader", name, ".shader", obj.path_id)
            s.w.commit(tpath, text, meta=s.meta(obj, name, note="UnityPy ShaderConverter"))
            paths.append(tpath)
    except Exception as exc:
        s.notes.append(f"Shader {name}: ShaderLab text not converted ({exc.__class__.__name__})")
    return paths
