"""Maps and levels, any engine: find the level files a game ships (in the install and in the asset
inventories the study already made) and turn raw grids found inside them (heightmaps, tile/collision
layers, splat maps) into PNG images. Understanding the format itself is study work: see the
study-maps skill (container -> chunks -> terrain, placed objects, scripts/triggers, lighting, spawns)."""

from __future__ import annotations

import collections
import json
import re
import struct
import time
import zlib
from pathlib import Path

from gst.common import GsError, write_json, write_text

# extension -> what it usually is. Generic names (.map, .lvl) need the study to confirm.
LEVEL_EXT = {
    ".umap": "Unreal map", ".unity": "Unity scene (project)", ".tscn": "Godot scene (text)", ".scn": "Godot scene (binary)",
    ".bsp": "BSP level (Source/GoldSrc/idTech)", ".vmf": "Hammer source map", ".map": "map (custom, idTech/Radiant, SAGE...)",
    ".lvl": "level (custom)", ".level": "level (custom)", ".lev": "level (custom)", ".wld": "world (custom)",
    ".world": "world (custom)", ".ldtk": "LDtk project", ".tmx": "Tiled map", ".tmj": "Tiled map (JSON)",
    ".w3x": "Warcraft III map", ".w3m": "Warcraft III map", ".scx": "StarCraft map", ".scm": "StarCraft map",
    ".sc2map": "StarCraft II map", ".rmp": "map (custom)", ".mis": "mission (custom)", ".scenario": "scenario (custom)",
    ".btr": "terrain (custom)", ".ter": "terrain heightmap (custom)", ".hmap": "heightmap (custom)",
}
UNITY_LEVEL = re.compile(r"^level\d+$", re.I)  # <Game>_Data/level0, level1... = built scenes
FOLDER_HINT = re.compile(r"(^|[/\\])(maps?|levels?|worlds?|scenes?|missions?|stages?|zones?|areas?)([/\\]|$)", re.I)
DTYPES = {"u8": ("B", 1), "i8": ("b", 1), "u16le": ("<H", 2), "u16be": (">H", 2), "i16le": ("<h", 2), "i16be": (">h", 2),
          "u32le": ("<I", 4), "f32le": ("<f", 4), "f32be": (">f", 4)}


def _kind(path: str) -> str | None:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext in LEVEL_EXT:
        return LEVEL_EXT[ext]
    if not ext and UNITY_LEVEL.match(name):
        return "Unity built scene"
    return None


def find(ws: Path, install: Path | None) -> dict:
    """Level files in the install (read-only walk) and in assets/**/inventory*.jsonl; writes dumps/maps/levels.json."""
    t0 = time.time()
    rows = []
    if install and install.is_dir():
        for p in install.rglob("*"):
            if p.is_file():
                rel = p.relative_to(install).as_posix()
                kind = _kind(rel)
                if kind:
                    rows.append({"source": "install", "path": rel, "size": p.stat().st_size, "kind": kind})
    assets = ws / "assets"
    if assets.is_dir():
        for inv in sorted(assets.rglob("inventory*.jsonl")):
            for line in inv.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                path = str(item.get("path") or item.get("name") or "")
                kind = _kind(path) or ("Unity Scene object" if item.get("type") == "Scene" else None)
                if kind:
                    rows.append({"source": inv.relative_to(ws).as_posix(), "container": item.get("archive") or item.get("container")
                                 or item.get("file"), "path": path, "size": item.get("size"), "kind": kind})
    for r in rows:
        r["in_level_folder"] = bool(FOLDER_HINT.search(r["path"]))
    by_kind = collections.Counter(r["kind"] for r in rows)
    folders = collections.Counter(r["path"].replace("\\", "/").rsplit("/", 1)[0] if "/" in r["path"].replace("\\", "/") else "."
                                  for r in rows)
    out = write_json(ws / "dumps" / "maps" / "levels.json", rows)
    return {"levels": len(rows), "by_kind": dict(by_kind.most_common()), "folders": dict(folders.most_common(10)),
            "examples": [r["path"] for r in rows[:8]], "out": out.relative_to(ws).as_posix(), "seconds": round(time.time() - t0, 1)}


def png_gray(width: int, height: int, pixels: bytes) -> bytes:
    """8-bit grayscale PNG (stdlib only)."""
    if len(pixels) != width * height:
        raise GsError(f"{len(pixels)} pixels for a {width}x{height} image")
    raw = b"".join(b"\0" + pixels[y * width:(y + 1) * width] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def raster(ws: Path, src: Path, *, width: int, height: int, offset: int = 0, dtype: str = "u8", flip_y: bool = False,
           name: str | None = None) -> dict:
    """Read width*height values of `dtype` at `offset` and write assets/maps/<name>.png (min..max -> 0..255)
    plus a .gs.json sidecar with the source, the layout and the value range."""
    if dtype not in DTYPES:
        raise GsError(f"--dtype must be one of {', '.join(DTYPES)}")
    if width <= 0 or height <= 0:
        raise GsError("--width and --height must be positive")
    fmt, size = DTYPES[dtype]
    need = width * height * size
    with src.open("rb") as fh:
        fh.seek(offset)
        data = fh.read(need)
    if len(data) < need:
        raise GsError(f"{src.name}: {len(data)} bytes after offset {offset}, {need} needed for {width}x{height} {dtype}")
    if size == 1 and fmt == "B":
        values = list(data)
    else:
        values = [v[0] for v in struct.iter_unpack(fmt, data)]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pixels = bytes(int((v - lo) * 255 / span) for v in values)
    if flip_y:
        pixels = b"".join(pixels[y * width:(y + 1) * width] for y in range(height - 1, -1, -1))
    stem = re.sub(r"[^\w.-]+", "_", name or f"{src.stem}_{offset:x}")
    out = ws / "assets" / "maps" / f"{stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png_gray(width, height, pixels))
    meta = {"source": str(src), "offset": offset, "width": width, "height": height, "dtype": dtype, "flip_y": flip_y,
            "min": lo, "max": hi, "tool": "gst maps raster", "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    write_text(out.with_name(out.name + ".gs.json"), json.dumps(meta, indent=2) + "\n")
    return {**meta, "out": out.relative_to(ws).as_posix()}
