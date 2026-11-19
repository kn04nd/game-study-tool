"""TextAsset, Font, VideoClip, TerrainData, NavMeshData exporters."""

from __future__ import annotations

import re
from pathlib import Path

from gst.engines.unity import typetree


def export_text(s, obj) -> list:
    ta = obj.read(check_read=False)
    script = ta.m_Script
    raw = script.encode("utf-8", "surrogateescape") if isinstance(script, str) else bytes(script or b"")
    name = ta.m_Name or f"TextAsset_{obj.path_id}"
    suffix = Path(name).suffix
    ext = suffix if re.fullmatch(r"\.\w{1,8}", suffix or "") else ""
    if not ext:
        try:
            text = raw.decode("utf-8")
            ext = ".txt" if not re.search(r"[\x00-\x08\x0e-\x1f]", text[:4096]) else ".bytes"
        except UnicodeDecodeError:
            ext = ".bytes"
    stem = Path(name).stem if suffix == ext else name
    path = s.w.target("TextAsset", stem, ext, obj.path_id)
    s.w.commit(path, raw, meta=s.meta(obj, ta.m_Name))
    return [path]


def export_font(s, obj) -> list:
    font = obj.read(check_read=False)
    data = bytes(getattr(font, "m_FontData", b"") or b"")
    if not data:
        return []
    ext = ".otf" if data[:4] == b"OTTO" else ".ttf"
    path = s.w.target("Font", font.m_Name or f"Font_{obj.path_id}", ext, obj.path_id)
    s.w.commit(path, data, meta=s.meta(obj, font.m_Name))
    return [path]


def export_video(s, obj) -> list:
    from UnityPy.helpers.ResourceReader import get_resource_data

    tree = typetree.read_builtin(obj) or {}
    res = tree.get("m_ExternalResources") or {}
    size = int(res.get("m_Size", 0) or 0)
    if not size:
        return []
    data = get_resource_data(res.get("m_Source", ""), obj.assets_file, int(res.get("m_Offset", 0)), size)
    original = tree.get("m_OriginalPath", "") or ""
    ext = Path(original).suffix or ".bin"
    name = tree.get("m_Name") or Path(original).stem or f"VideoClip_{obj.path_id}"
    path = s.w.target("VideoClip", name, ext, obj.path_id)
    s.w.commit(path, bytes(data), meta=s.meta(obj, tree.get("m_Name", ""), original_path=original,
                                              dimensions=[tree.get("Width"), tree.get("Height")],
                                              frame_rate=tree.get("m_FrameRate"), frames=tree.get("m_FrameCount")))
    return [path]


def export_terrain(s, obj) -> list:
    from PIL import Image

    tree = typetree.read_builtin(obj)
    if tree is None:
        raise RuntimeError("typetree read failed")
    name = tree.get("m_Name") or f"TerrainData_{obj.path_id}"
    hm = tree.get("m_Heightmap") or {}
    heights = hm.get("m_Heights") or []
    res = int(hm.get("m_Resolution") or 0) or int(round(len(heights) ** 0.5))
    paths = []
    if heights and res * res == len(heights):
        import array

        arr = array.array("H", (int(h) & 0xFFFF for h in heights))
        img = Image.frombytes("I;16", (res, res), arr.tobytes())
        path = s.w.target("TerrainData", name + "_height", ".png", obj.path_id)
        s.w.commit(path, writer=lambda tmp: img.save(tmp, format="PNG"),
                   meta=s.meta(obj, name, dimensions=[res, res], scale=hm.get("m_Scale")))
        paths.append(path)
    meta = {k: v for k, v in tree.items() if k not in ("m_Heightmap",)}
    meta["m_Heightmap"] = {k: v for k, v in hm.items() if k != "m_Heights"}
    jpath = s.w.target("TerrainData", name, ".json", obj.path_id)
    s.w.json(jpath, meta, meta=s.meta(obj, name))
    return paths + [jpath]


def export_navmesh(s, obj) -> list:
    tree = typetree.read_builtin(obj)
    if tree is None:
        raise RuntimeError("typetree read failed")
    name = tree.get("m_Name") or f"NavMeshData_{obj.path_id}"
    path = s.w.target("NavMeshData", name, ".json", obj.path_id)
    tiles = tree.get("m_NavMeshTiles") or []
    s.w.json(path, tree, meta=s.meta(obj, name, tiles=len(tiles)))
    return [path]
