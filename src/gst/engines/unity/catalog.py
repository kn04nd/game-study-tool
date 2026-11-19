"""Cross-file PPtr resolution with a bounded LRU of opened files.

A PPtr ``(m_FileID, m_PathID)`` with ``m_FileID != 0`` points into the file's ``externals[m_FileID-1]``
(``globalgamemanagers.assets``, ``resources.assets``, ``archive:/CAB-.../CAB-...`` of another bundle,
``Library/unity default resources``). The catalog maps those names to install files (CAB names to the
bundle that holds them, from the bundle headers only) and opens them on demand, keeping at most
``max_envs`` of them open (least recently used are closed).
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

from gst.engines.unity import loader


class Catalog:
    def __init__(self, install: Path, *, max_envs: int = 4):
        self.install = Path(install)
        self.max_envs = max(1, max_envs)
        self.envs: OrderedDict[str, object] = OrderedDict()
        self._by_name: dict[str, str] | None = None
        self._cab_to_bundle: dict[str, str] | None = None
        self.pinned: dict[str, object] = {}

    # --- index of install files ---
    def _index(self) -> dict[str, str]:
        if self._by_name is None:
            self._by_name, self._cab_to_bundle = {}, {}
            from gst.engines.unity import bundle

            for rel, kind, _size in loader.iter_unity_files(self.install):
                self._by_name.setdefault(os.path.basename(rel).lower(), rel)
                if kind == "bundle":
                    try:
                        for _off, _size2, _f, node in bundle.read_header(self.install / rel).nodes:
                            self._cab_to_bundle.setdefault(node.lower(), rel)
                    except Exception:
                        continue
        return self._by_name

    def locate(self, name: str) -> str | None:
        """Install relpath of an external file name (``archive:/CAB-x/CAB-x`` -> its bundle)."""
        base = os.path.basename(str(name).replace("\\", "/")).lower()
        index = self._index()
        if base in index:
            return index[base]
        return (self._cab_to_bundle or {}).get(base)

    # --- opened files ---
    def pin(self, relpath: str, env) -> None:
        """Register an env opened by the caller (the unit's own file): never evicted, never closed here."""
        self.pinned[relpath] = env

    def unpin(self, relpath: str) -> None:
        self.pinned.pop(relpath, None)

    def env_for(self, relpath: str):
        if relpath in self.pinned:
            return self.pinned[relpath]
        if relpath in self.envs:
            self.envs.move_to_end(relpath)
            return self.envs[relpath]
        env = loader.open_env(self.install / relpath)
        self.envs[relpath] = env
        while len(self.envs) > self.max_envs:
            _k, old = self.envs.popitem(last=False)
            loader.close_env(old)
        return env

    def close(self) -> None:
        for env in self.envs.values():
            loader.close_env(env)
        self.envs.clear()

    def resolve(self, sf, file_id: int, path_id: int):
        """ObjectReader for a PPtr read in SerializedFile ``sf``, or None."""
        if not path_id:
            return None
        file_id, path_id = loader.fix_pptr(file_id, path_id)
        if file_id == 0:
            return sf.objects.get(path_id)
        name = loader.external_name(sf, file_id)
        if not name:
            return None
        env = getattr(sf, "environment", None)
        cab = env.get_cab(name) if env is not None else None
        if cab is not None and hasattr(cab, "objects"):
            return cab.objects.get(path_id)
        rel = self.locate(name)
        if rel is None:
            return None
        try:
            other = self.env_for(rel)
        except Exception:
            return None
        target = os.path.basename(name).lower()
        sfs = list(loader.serialized_files(other))
        for osf in sfs:
            if str(osf.name).lower() == target:
                return osf.objects.get(path_id)
        return sfs[0].objects.get(path_id) if len(sfs) == 1 else None

    def resolve_pptr(self, sf, ptr):
        """Same as :meth:`resolve` for a UnityPy PPtr object or a ``{"m_FileID", "m_PathID"}`` dict."""
        if ptr is None:
            return None
        if isinstance(ptr, dict):
            return self.resolve(sf, int(ptr.get("m_FileID", 0)), int(ptr.get("m_PathID", 0)))
        return self.resolve(sf, int(getattr(ptr, "m_FileID", 0)), int(getattr(ptr, "m_PathID", 0)))
