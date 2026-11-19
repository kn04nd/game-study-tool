"""Godot packs with GDRE Tools: inventory (`--list-files`), selective recovery (`--include` per file) and
the whole project (`--recover`, C# sources left to `gst decompile dotnet`). GDRE converts while it
recovers: binary scenes/resources to .tscn/.tres text, .ctex to PNG, .sample to WAV, streams to OGG/MP3.
"""

from __future__ import annotations

import collections
import json
import re
import time
from pathlib import Path

from gst import tools
from gst.assets import FOLDER_SIDECAR, assets_dir
from gst.common import GsError, human_size, write_json, write_text
from gst.decompile import godot as gdre_backend

TYPES = {
    "scene": (".scn", ".tscn"), "resource": (".res", ".tres"),
    "texture": (".ctex", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ctexarray", ".dpitex", ".image"),
    "audio": (".sample", ".oggvorbisstr", ".mp3str", ".wav", ".ogg", ".mp3"),
    "shader": (".gdshader", ".shader"), "font": (".fontdata", ".ttf", ".otf", ".woff", ".woff2"),
    "text": (".json", ".cfg", ".csv", ".po", ".txt", ".translation", ".xml", ".ini"),
    "script": (".gd", ".gdc"), "model": (".mesh", ".glb", ".gltf", ".obj", ".fbx"),
}
DEFAULT_TYPES = ("scene", "texture", "audio", "text")


def list_files(pack: Path) -> list[str]:
    import subprocess

    proc = subprocess.run([str(gdre_backend.gdre()), "--headless", f"--list-files={pack}"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    paths = [line.strip() for line in (proc.stdout + proc.stderr).splitlines() if line.strip().startswith("res://")]
    if not paths:
        raise GsError(f"GDRE listed no files in {pack.name}: {(proc.stderr or proc.stdout).strip()}")
    return paths


def kind_of(path: str) -> str:
    ext = Path(path).suffix.lower()
    return next((k for k, exts in TYPES.items() if ext in exts), ext.lstrip(".") or "other")


def inventory(ws: Path, install: Path, game_name: str) -> dict:
    t0 = time.time()
    pack = gdre_backend.find_pack(install)
    info = gdre_backend.check_pack(pack)
    paths = list_files(pack)
    out = assets_dir(ws)
    out.mkdir(parents=True, exist_ok=True)
    kinds, exts, folders = collections.Counter(), collections.Counter(), collections.Counter()
    with open(out / "inventory.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for p in paths:
            rel = p[len("res://"):]
            row = {"path": p, "type": kind_of(p), "ext": Path(rel).suffix.lower(), "folder": rel.split("/", 1)[0] if "/" in rel else ""}
            kinds[row["type"]] += 1
            exts[row["ext"]] += 1
            folders[row["folder"]] += 1
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    lines = [f"# Godot asset inventory: {game_name}", "",
             f"- Godot {info.get('version')} pack `{pack.name}` ({human_size(pack.stat().st_size)}): {len(paths)} files.",
             "- Full list: `inventory.jsonl` (res:// path, type, extension, top folder).",
             "- Export: `gst assets extract --types scene,texture --name <regex on the path>`; whole project: "
             "`gst assets project`.", "",
             "| Type | Files |", "| --- | --- |"] + [f"| {k} | {n} |" for k, n in kinds.most_common()]
    lines += ["", "## Top folders", "", "| Folder | Files |", "| --- | --- |"]
    lines += [f"| `{f or '(root)'}` | {n} |" for f, n in folders.most_common(15)]
    lines += ["", "## Extensions", "", ", ".join(f"`{e or '-'}` {n}" for e, n in exts.most_common(30))]
    write_text(out / "INVENTORY.md", "\n".join(lines) + "\n")
    return {"godot_version": info.get("version"), "files": len(paths), "types": dict(kinds.most_common()),
            "pack": str(pack), "seconds": round(time.time() - t0, 1)}


def _record(folder: Path, pack: Path, cmd: list[str], label: str, extra: dict) -> dict:
    from gst.assets import write_folder_sidecar

    files = [p for p in folder.rglob("*") if p.is_file() and p.name != FOLDER_SIDECAR]
    size = sum(p.stat().st_size for p in files)
    name, _, version = label.partition(" ")
    write_folder_sidecar(folder, {"source": str(pack), "res_path": "res://", "tool": name, "tool_version": version,
                                  "build": extra.pop("build", None), "command": cmd[:12] + (["..."] if len(cmd) > 12 else []),
                                  **extra}, files=files)
    return {"files": len(files), "bytes": size}


def extract(ws: Path, install: Path, *, types=DEFAULT_TYPES, name: str | None = None, limit: int | None = None) -> dict:
    unknown = set(types) - set(TYPES)
    if unknown:
        raise GsError(f"unknown Godot types: {', '.join(sorted(unknown))}; use {', '.join(TYPES)}")
    t0 = time.time()
    pack = gdre_backend.find_pack(install)
    gdre_backend.check_pack(pack)
    rx = re.compile(name, re.I) if name else None
    wanted = tuple(ext for t in types for ext in TYPES[t])
    matched = [p for p in list_files(pack) if p.lower().endswith(wanted) and (not rx or rx.search(p))]
    chosen = matched if limit is None else matched[:limit]
    if not chosen:
        return {"matched": 0, "exported": {}, "out": None, "seconds": round(time.time() - t0, 1), "limited": False}
    out = assets_dir(ws) / "godot" / "extract"
    cmds = gdre_backend.batched_commands(gdre_backend.gdre(), pack, out, chosen)
    code, cmd = 0, cmds[0]
    for i, cmd in enumerate(cmds):  # several calls when the selection does not fit one command line
        rc, _text = gdre_backend.run(cmd, out, "gdre-extract.log" if len(cmds) == 1 else f"gdre-extract-{i + 1}.log")
        code = code or rc
    label = f"GDRE {tools.installed_version(tools.get('gdre')) or ''}".strip()
    stats = _record(out, pack, cmd, label, {"selected": len(chosen), "commands": len(cmds)})
    if code != 0 and not stats["files"]:
        raise GsError(f"GDRE recovery failed (exit {code}); see assets/godot/gdre-extract.log")
    return {"matched": len(matched), "exported": dict(collections.Counter(kind_of(p) for p in chosen)), "out": str(out),
            "files": stats["files"], "limited": limit is not None and len(matched) > len(chosen),
            "seconds": round(time.time() - t0, 1)}


def project(ws: Path, install: Path, *, include_csharp: bool = False) -> dict:
    t0 = time.time()
    pack = gdre_backend.find_pack(install)
    gdre_backend.check_pack(pack)
    out = assets_dir(ws) / "godot" / "project"
    if out.exists() and any(out.iterdir()):
        raise GsError(f"{out} is not empty: remove it to recover again")
    excludes = () if include_csharp else ("*.cs",)
    cmd = gdre_backend.command(gdre_backend.gdre(), pack, out, includes=(), excludes=excludes)
    code, _text = gdre_backend.run(cmd, out, "gdre-project.log")
    label = f"GDRE {tools.installed_version(tools.get('gdre')) or ''}".strip()
    stats = _record(out, pack, cmd, label, {"mode": "project"})
    if code != 0 and not stats["files"]:
        raise GsError(f"GDRE recovery failed (exit {code}); see assets/godot/gdre-project.log")
    return {"out": str(out), **stats, "seconds": round(time.time() - t0, 1)}
