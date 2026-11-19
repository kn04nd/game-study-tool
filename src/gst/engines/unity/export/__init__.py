"""Exporters of Unity objects to plain files, and the per-unit :class:`Session` they share.

Every file goes ``<out_root>/.partial/`` -> atomic rename -> sidecar (``<file>.gs.json``) last. Output
layout: ``<out_root>/<Type>/<source>/<name>.<ext>`` where ``<source>`` is the serialized file or bundle
path below ``<Game>_Data`` with ``/`` -> ``__`` (so parallel units never write the same folder and a
re-run overwrites instead of piling up ``__<id>`` copies).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from gst.assets import safe_name, write_sidecar
from gst.engines.unity import loader


def source_folder(relpath: str) -> str:
    parts = relpath.replace("\\", "/").split("/")
    if parts and parts[0].endswith("_Data"):
        parts = parts[1:]
    return safe_name("__".join(parts) or relpath, 120)


def to_jsonable(value: Any) -> Any:
    """Typetree dicts -> JSON (bytes as hex-free summaries are NOT used: bytes become lists of ints)."""
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"_bytes_b64": __import__("base64").b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return {k: to_jsonable(v) for k, v in vars(value).items() if not k.startswith("_") and k != "object_reader"}
    return str(value)


class Writer:
    """Writes the exports of one unit (atomic, sidecar last) and records outputs/bytes."""

    def __init__(self, out_root: Path, workspace: Path, *, source: str, build: str, tool: str, tool_version: str,
                 extractor: str, unit: str):
        self.out_root = Path(out_root)
        self.workspace = Path(workspace)
        self.source = source
        self.src_folder = source_folder(source)
        self.base_meta = {"source": source, "build": build or "?", "tool": tool, "tool_version": tool_version,
                          "extractor": extractor, "unit": unit}
        self.outputs: list[str] = []
        self.out_bytes = 0
        self.used: set[str] = set()
        self.partial = self.out_root / ".partial"

    def target(self, type_folder: str, stem: str, ext: str, key: Any, *sub: str) -> Path:
        folder = self.out_root / type_folder / self.src_folder
        for s in sub:
            folder = folder / safe_name(s, 80)
        name = f"{safe_name(stem or 'unnamed')}{ext}"
        path = folder / name
        if str(path).lower() in self.used:
            path = folder / f"{safe_name(stem or 'unnamed', 60)}__{key}{ext}"
        self.used.add(str(path).lower())
        return path

    def commit(self, final: Path, data: bytes | str | None = None, *, writer=None, meta: dict | None = None) -> Path:
        """Write ``data`` (or call ``writer(tmp_path)``) into .partial, rename to ``final``, then sidecar."""
        self.partial.mkdir(parents=True, exist_ok=True)
        tmp = self.partial / f"{uuid.uuid4().hex}{final.suffix}"
        try:
            if writer is not None:
                writer(tmp)
            elif isinstance(data, str):
                with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(data)
            else:
                with open(tmp, "wb") as fh:
                    fh.write(data or b"")
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, final)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        side = {**self.base_meta, **{k: v for k, v in (meta or {}).items() if v not in (None, "")}}
        write_sidecar(final, side)
        self.outputs.append(final.relative_to(self.workspace).as_posix() if final.is_relative_to(self.workspace)
                            else str(final))
        self.out_bytes += final.stat().st_size
        return final

    def json(self, final: Path, value: Any, meta: dict | None = None) -> Path:
        return self.commit(final, json.dumps(to_jsonable(value), ensure_ascii=False, indent=1), meta=meta)


class Session:
    """What an exporter needs for one unit: the opened file, PPtr catalog, scripts, typetree generator."""

    def __init__(self, install: Path, relpath: str, env, writer: Writer, *, workspace: Path, build: str,
                 options: dict | None = None, log=None):
        from gst.engines.unity.catalog import Catalog
        from gst.engines.unity.scripts import ScriptIndex

        self.install = Path(install)
        self.relpath = relpath
        self.env = env
        self.w = writer
        self.workspace = Path(workspace)
        self.build = build
        self.options = options or {}
        self.log = log
        self.catalog = Catalog(self.install)
        self.catalog.pin(relpath, env)
        self.scripts = ScriptIndex(self.catalog)
        self._gen: tuple | None = None
        self.failures: list[str] = []
        self.notes: list[str] = []
        self.counts: dict[str, int] = {}

    def generator(self):
        if self._gen is None:
            from gst.engines.unity import typetree

            version = loader.unity_version(self.env) or ""
            self._gen = typetree.generator(self.install, version, self.workspace, self.build)
            if self._gen[0] is not None:
                typetree.attach(self.env, self._gen[0])
        return self._gen

    def resolve(self, sf, ptr):
        return self.catalog.resolve_pptr(sf, ptr)

    def name_of(self, sf, ptr) -> str:
        reader = self.resolve(sf, ptr)
        return loader.peek_name(reader) if reader is not None else ""

    def meta(self, obj, name: str = "", **extra) -> dict:
        sf = obj.assets_file
        m = {"path_id": obj.path_id, "type": loader.type_name(obj), "name": name}
        if sf.name != Path(self.relpath).name:
            m["container"] = sf.name
        m.update(extra)
        return m

    def fail(self, obj, exc: BaseException) -> None:
        self.failures.append(f"{loader.type_name(obj)} {loader.peek_name(obj)!r} "
                             f"({self.relpath}#{obj.path_id}): {exc.__class__.__name__}: {exc}")

    def close(self) -> None:
        self.catalog.unpin(self.relpath)
        self.catalog.close()


def matches(row_name: str, options: dict) -> bool:
    pat = options.get("name")
    return not pat or bool(re.search(pat, row_name or "", re.I))
