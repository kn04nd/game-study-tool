"""Texture2D and Sprite -> PNG (+ 256 px thumbnail of big images)."""

from __future__ import annotations

from gst.assets import thumbnail


def _save_png(s, obj, image, folder: str, name: str, extra: dict) -> list:
    w = s.w
    path = w.target(folder, name or f"{folder}_{obj.path_id}", ".png", obj.path_id)
    w.commit(path, writer=lambda tmp: image.save(tmp, format="PNG"),
             meta=s.meta(obj, name, dimensions=list(image.size), **extra))
    if s.options.get("thumbs", True) and max(image.size) > 256:
        thumb = w.out_root / "_thumbs" / path.relative_to(w.out_root)
        try:
            thumbnail(image, thumb)
        except Exception:
            pass
    return [path]


def export_texture(s, obj) -> list:
    tex = obj.read(check_read=False)
    if not getattr(tex, "m_Width", 0) or not getattr(tex, "m_Height", 0):
        return []
    image = tex.image
    fmt = getattr(tex, "m_TextureFormat", None)
    return _save_png(s, obj, image, "Texture2D", tex.m_Name, {"format": getattr(fmt, "name", str(fmt))})


def export_sprite(s, obj) -> list:
    sprite = obj.read(check_read=False)
    image = sprite.image
    if not image.size[0] or not image.size[1]:
        return []
    return _save_png(s, obj, image, "Sprite", sprite.m_Name, {})
