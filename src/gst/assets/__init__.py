"""Asset extraction into ``<workspace>/assets/``.

Every exported file gets a sidecar ``<file>.gs.json`` (source file, object id, tool, sha256), or a
folder gets ``_folder.gs.json`` when one source yields many files (an audio bank). ``assets/INDEX.md``
is rebuilt from the sidecars, so it always matches what is on disk.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from gst.common import human_size, write_json, write_text

SIDECAR = ".gs.json"
FOLDER_SIDECAR = "_folder.gs.json"
THUMB_SIZE = 256


def backend_for(engine_key: str) -> str | None:
    """The `gst assets list/extract` backend of a recon engine key (`unity`, `godot`, `unreal`), or None."""
    key = str(engine_key or "")
    return ("unity" if key.startswith("unity") else "godot" if key.startswith("godot")
            else "unreal" if key == "unreal" else None)


def assets_dir(ws: Path) -> Path:
    return ws / "assets"


def safe_name(name: str, max_len: int = 80) -> str:
    """File-system safe and short (MAX_PATH matters while LongPathsEnabled=0)."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .") or "unnamed"
    cleaned = cleaned[:max_len].rstrip(" .") or "unnamed"
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED:  # CON, NUL, COM1...: not creatable as files
        cleaned = "_" + cleaned[:max_len - 1]
    return cleaned


WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                              *(f"LPT{i}" for i in range(1, 10))})


def unique_path(folder: Path, stem: str, ext: str, key: str) -> Path:
    path = folder / f"{safe_name(stem)}{ext}"
    if path.exists() or Path(str(path) + SIDECAR).exists():
        path = folder / f"{safe_name(stem, 60)}__{safe_name(str(key), 80)}{ext}"
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


REQUIRED_KEYS = ("file", "size", "sha256", "source", "build", "tool", "tool_version", "exported_at")
"""Every sidecar has these, plus ``path_id`` (Unity) or ``res_path`` (Godot/Unreal/others): docs/INTERFACES.md."""


def sidecar_meta(meta: dict, *, file: str, size: int, sha256: str | None) -> dict:
    """The unified sidecar schema: required keys filled or checked, ``size`` always an int (bytes of
    the export; image sizes go to ``dimensions``)."""
    out = dict(meta)
    old_size = out.pop("size", None)
    if isinstance(old_size, (list, tuple)) and "dimensions" not in out:
        out["dimensions"] = [int(v) for v in old_size]
    for key in ("source", "tool"):
        if not out.get(key):
            raise ValueError(f"sidecar of {file} without '{key}'")
    if "path_id" not in out and "res_path" not in out:
        raise ValueError(f"sidecar of {file} needs path_id (Unity) or res_path (other engines)")
    out["source"] = str(out["source"]).replace("\\", "/")
    out["build"] = str(out.get("build") or "?")
    out.setdefault("tool_version", "")
    out.update(file=file, size=int(size), exported_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    if sha256 is not None or file != ".":
        out["sha256"] = sha256 or ""
    return out


def write_sidecar(path: Path, meta: dict) -> Path:
    """``<file>.gs.json`` next to an exported file, written last. The only sidecar writer."""
    from gst.common import long_path

    real = Path(long_path(path))
    side = Path(long_path(str(path) + SIDECAR))
    write_json(side, sidecar_meta(meta, file=Path(path).name, size=real.stat().st_size, sha256=sha256_file(real)))
    return side


def write_folder_sidecar(folder: Path, meta: dict, *, files: list[Path] | None = None) -> Path:
    """``_folder.gs.json`` for a folder one tool generated (GDRE project, uas export, audio bank):
    ``file = "."``, ``size`` = bytes of the folder, plus ``count``/``bytes``/``examples`` for INDEX.md."""
    if files is None:
        files = [p for p in folder.rglob("*") if p.is_file() and p.name != FOLDER_SIDECAR]
    total = sum(p.stat().st_size for p in files)
    names = sorted(p.relative_to(folder).as_posix() for p in files)
    meta = {"res_path": ".", **{k: v for k, v in meta.items() if k != "size"}}
    meta.setdefault("count", len(files))
    meta.setdefault("bytes", total)
    meta.setdefault("examples", names[:6])
    side = folder / FOLDER_SIDECAR
    write_json(side, sidecar_meta(meta, file=".", size=total, sha256=None))
    return side


def _size_of(meta: dict) -> int:
    """Bytes recorded by a sidecar; old (gs v1) Unity sidecars stored the image [w, h] in ``size``."""
    size = meta.get("size", 0)
    return size if isinstance(size, int) else 0


def thumbnail(image, dest: Path, size: int = THUMB_SIZE) -> Path:
    """A small PNG for quick looks (big textures are expensive to read whole)."""
    thumb = image.copy()
    thumb.thumbnail((size, size))
    dest.parent.mkdir(parents=True, exist_ok=True)
    thumb.save(dest)
    return dest


def build_index(ws: Path, *, examples: int = 6) -> dict:
    """Rewrite assets/INDEX.md from the sidecars: what was exported, from where, with what."""
    root = assets_dir(ws)
    groups: dict[str, dict] = {}
    for side in sorted(root.rglob("*.gs.json")) if root.exists() else []:
        if side.name != FOLDER_SIDECAR and not Path(str(side)[: -len(SIDECAR)]).exists():
            continue  # an exported file that merely ends in .gs.json, not a sidecar
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rel_folder = side.parent.relative_to(root).as_posix()
        if side.name == FOLDER_SIDECAR:
            g = groups.setdefault(rel_folder, {"files": 0, "bytes": 0, "examples": [], "sources": set(), "tools": set()})
            g["files"] += meta.get("count", 0)
            g["bytes"] += meta.get("bytes", 0)
            g["examples"] += [f"{rel_folder}/{n}" for n in meta.get("examples", [])][: examples - len(g["examples"])]
        else:
            g = groups.setdefault(rel_folder, {"files": 0, "bytes": 0, "examples": [], "sources": set(), "tools": set()})
            g["files"] += 1
            g["bytes"] += _size_of(meta)
            if len(g["examples"]) < examples:
                g["examples"].append(f"{rel_folder}/{meta.get('file')}")
        g["sources"].add(Path(str(meta.get("source", "?"))).name)
        g["tools"].add(str(meta.get("tool", "?")))
    lines = ["# Assets index", "",
             "Generated by gst from the `*.gs.json` sidecars (source, object id, tool, sha256 of each export). "
             "Thumbnails of textures live in `_thumbs/`; the full Unity object list is `INVENTORY.md` / `inventory.jsonl`.", ""]
    if not groups:
        lines.append("Nothing exported yet (gst assets list, gst assets extract, gst assets audio).")
    else:
        lines += ["| Folder | Files | Size | From | Tool |", "| --- | --- | --- | --- | --- |"]
        for folder, g in sorted(groups.items()):
            src = ", ".join(sorted(g["sources"])[:3]) + (" ..." if len(g["sources"]) > 3 else "")
            lines.append(f"| `{folder}` | {g['files']} | {human_size(g['bytes'])} | {src} | {', '.join(sorted(g['tools']))} |")
        lines += ["", "## Examples", ""]
        for folder, g in sorted(groups.items()):
            lines.append(f"- `{folder}`: " + ", ".join(f"`{e.split('/')[-1]}`" for e in g["examples"]))
    write_text(root / "INDEX.md", "\n".join(lines) + "\n")
    return {"folders": len(groups), "files": sum(g["files"] for g in groups.values()),
            "bytes": sum(g["bytes"] for g in groups.values())}
