"""Unity player data for `gst assets list/extract`: thin wrappers over :mod:`gst.engines.unity`.

`inventory` lists every object of every ``*_Data`` folder (StreamingAssets and Addressables bundles
included) one file at a time, never the whole folder in RAM; `search` streams the same rows with
filters; `extract` converts the selected objects to plain formats with the engine exporters:
Texture2D/Sprite -> PNG (+ thumbnail), AudioClip -> WAV, TextAsset -> text or bytes, Font -> TTF/OTF,
Mesh -> OBJ, SkinnedMeshRenderer -> GLB, AnimationClip/Material -> JSON, VideoClip -> its container,
MonoBehaviour -> JSON (typetrees from the game's assemblies). Reads the install only; writes under
``assets/`` (``out`` overrides ``assets/unity``). No default limit: ``limit`` is explicit and stops
the export (``break``) once reached.
"""

from __future__ import annotations

import collections
import re
import sys
import time
from pathlib import Path

from gst.assets import assets_dir
from gst.common import GsError

DEFAULT_TYPES = ("Texture2D", "Sprite", "AudioClip", "TextAsset", "Font", "Mesh")
EXPORTABLE = DEFAULT_TYPES + ("MonoBehaviour", "SkinnedMeshRenderer", "AnimationClip", "Material", "VideoClip")
_EXPORTERS = {"Texture2D": ("texture", "export_texture"), "Sprite": ("texture", "export_sprite"),
              "AudioClip": ("audio", "export_audio"), "TextAsset": ("misc", "export_text"),
              "Font": ("misc", "export_font"), "Mesh": ("mesh", "export_mesh"),
              "SkinnedMeshRenderer": ("gltf", "export_skinned"), "AnimationClip": ("anim", "export_clip"),
              "Material": ("material", "export_material"), "VideoClip": ("misc", "export_video")}


def data_dirs(install: Path) -> list[Path]:
    from gst.engines.unity import loader

    return loader.data_dirs(install)


def data_dir(install: Path) -> Path:
    """First ``*_Data`` folder (kept for callers that show one name; use :func:`data_dirs`)."""
    return data_dirs(install)[0]


def _unitypy():
    from gst.engines.unity import loader

    return loader.unitypy()


def tool_label() -> str:
    return f"UnityPy {_unitypy().__version__}"


def describe(obj, relpath: str = "") -> dict:
    from gst.engines.unity import loader

    return {"type": loader.type_name(obj), "name": loader.peek_name(obj), "file": relpath or obj.assets_file.name,
            "path_id": obj.path_id, "size": obj.byte_size, "container": ""}


# --- inventory --------------------------------------------------------------------------------------

def inventory(ws: Path, install: Path, game_name: str, *, out: Path | None = None) -> dict:
    from gst.engines.unity import catalog, inventory as inv, loader

    t0 = time.time()
    root = Path(out) if out else assets_dir(ws) / "unity"
    inv_dir = root / inv.INVENTORY_DIR
    cat = catalog.Catalog(install)
    failed = []
    try:
        for rel, _kind, _size in loader.iter_unity_files(install):
            try:
                inv.inventory_file(install, rel, inv_dir, catalog=cat)
            except Exception as exc:  # one unreadable file must not stop the inventory
                failed.append(f"{rel}: {exc.__class__.__name__}: {exc}")
    finally:
        cat.close()
    merged = inv.merge(inv_dir, inv_dir / inv.INDEX, assets_dir(ws) / "INVENTORY.md", game_name)
    counts, sizes = collections.Counter(), collections.Counter()
    for s in inv.summaries(inv_dir):
        counts.update(s.get("types", {}))
        sizes.update(s.get("bytes", {}))
    return {"unity_version": merged["unity_version"], "objects": merged["objects"], "files": merged["files"],
            "types": dict(counts.most_common()), "bytes": dict(sizes), "failed": failed,
            "seconds": round(time.time() - t0, 1)}


def _iter_rows(install: Path, file=None):
    """Rows of every object, file by file (streams; the env of a file is closed before the next)."""
    from gst.engines.unity import loader

    for rel, _kind, _size in loader.iter_unity_files(install):
        if file and not re.search(file, rel, re.I):
            continue
        env = loader.open_env(Path(install) / rel)
        try:
            for obj in loader.iter_objects(env, release=True):
                yield obj, rel, describe(obj, rel)
        finally:
            loader.close_env(env)


def inventory_dir(ws: Path) -> Path:
    from gst.engines.unity import inventory as inv

    return assets_dir(ws) / "unity" / inv.INVENTORY_DIR


def search(install: Path, *, types=(), name=None, container=None, file=None, limit: int | None = None,
           ws: Path | None = None) -> list[dict]:
    """Filtered rows. With a workspace whose inventory exists (``_inventory/INDEX.json``), streams the
    per-file gz tables (no game file opened); otherwise streams the install file by file."""
    from gst.engines.unity import inventory as inv

    if ws is not None and (inventory_dir(ws) / inv.INDEX).is_file():
        source = inv.iter_rows(inventory_dir(ws), files=file, types=types or None, name=name)
    else:
        source = (row for _obj, _rel, row in _iter_rows(install, file))
    rows = []
    for row in source:
        if _matches(row, types, name, container, None):
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
    return rows


def _matches(row: dict, types, name, container, file) -> bool:
    return ((not types or row["type"] in types)
            and (not name or re.search(name, row["name"], re.I))
            and (not container or re.search(container, row["container"], re.I))
            and (not file or re.search(file, row["file"], re.I)))


# --- export -----------------------------------------------------------------------------------------

def extract(ws: Path, install: Path, *, types=DEFAULT_TYPES, name=None, container=None, file=None,
            limit: int | None = None, thumbs: bool = True, out: Path | None = None) -> dict:
    unknown = set(types) - set(EXPORTABLE)
    if unknown:
        raise GsError(f"cannot export {', '.join(sorted(unknown))} yet; exportable: {', '.join(EXPORTABLE)}")
    import importlib

    from gst.engines.unity import loader
    from gst.engines.unity.export import Session, Writer

    t0 = time.time()
    root = Path(out) if out else assets_dir(ws) / "unity"
    version = loader.unitypy_version()
    matched, exported, failed = 0, collections.Counter(), []
    stop = False
    for rel, _kind, _size in loader.iter_unity_files(install):
        if stop:
            break
        if file and not re.search(file, rel, re.I):
            continue
        env = loader.open_env(Path(install) / rel)
        w = Writer(root, ws, source=rel, build="?", tool="UnityPy", tool_version=version, extractor="gst assets extract",
                   unit=rel)
        w.src_folder = ""  # `gst assets extract` keeps the flat assets/unity/<Type>/ layout
        s = Session(install, rel, env, w, workspace=ws, build="?", options={"thumbs": thumbs})
        collector = None
        if "MonoBehaviour" in types:
            from gst.engines.unity.export.mono import MonoCollector

            collector = MonoCollector(s)
        try:
            for obj in loader.iter_objects(env, release=True):
                row = describe(obj, rel)
                if not _matches(row, types, name, container, None):
                    continue
                matched += 1
                if limit is not None and sum(exported.values()) >= limit:
                    stop = True
                    break
                try:
                    if row["type"] == "MonoBehaviour":
                        before = collector.done
                        collector.add(obj)
                        paths = [1] if collector.done > before else []
                    else:
                        module, func = _EXPORTERS[row["type"]]
                        paths = getattr(importlib.import_module(f"gst.engines.unity.export.{module}"), func)(s, obj)
                except Exception as exc:  # one broken object must not stop the batch
                    failed.append(f"{row['type']} {row['name']} ({rel}#{obj.path_id}): {exc.__class__.__name__}: {exc}")
                    continue
                if paths:
                    exported[row["type"]] += 1
                    if sum(exported.values()) % 100 == 0:
                        sys.stderr.write(f"gst: exported {sum(exported.values())} objects...\n")
            if collector is not None:
                collector.finish()
        finally:
            s.close()
            loader.close_env(env)
    return {"matched": matched, "exported": dict(exported), "failed": failed, "failures": len(failed),
            "limited": stop, "out": str(root), "seconds": round(time.time() - t0, 1)}
