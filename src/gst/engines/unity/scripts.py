"""MonoScript lookup for MonoBehaviours, from raw bytes (no typetree, no generator).

``m_Script`` is read from the MonoBehaviour's first 28 bytes (:func:`loader.raw_script_pptr`), then
resolved through the :class:`~gst.engines.unity.catalog.Catalog` (MonoScripts of a player build live in
``globalgamemanagers.assets``; a bundle carries its own). Results are cached per (file, fileID, pathID).
"""

from __future__ import annotations

from dataclasses import dataclass

from gst.engines.unity import loader


@dataclass(frozen=True)
class ScriptInfo:
    class_name: str
    namespace: str
    assembly: str

    @property
    def fullname(self) -> str:
        return f"{self.namespace}.{self.class_name}" if self.namespace else self.class_name


class ScriptIndex:
    def __init__(self, catalog):
        self.catalog = catalog
        self.cache: dict[tuple, ScriptInfo | None] = {}

    def script_of(self, obj) -> ScriptInfo | None:
        ref = loader.raw_script_pptr(obj)
        if ref is None:
            return None
        sf = obj.assets_file
        key = (id(sf), ref)
        if key in self.cache:
            return self.cache[key]
        info = None
        reader = self.catalog.resolve(sf, *ref)
        if reader is not None and loader.type_name(reader) == "MonoScript":
            try:
                ms = reader.read(check_read=False)
                info = ScriptInfo(str(getattr(ms, "m_ClassName", "") or ""), str(getattr(ms, "m_Namespace", "") or ""),
                                  str(getattr(ms, "m_AssemblyName", "") or ""))
            except Exception:
                info = None
        self.cache[key] = info
        return info


def script_name(obj, index: ScriptIndex | None) -> str:
    if index is None or loader.type_name(obj) != "MonoBehaviour":
        return ""
    info = index.script_of(obj)
    return info.class_name if info else ""
