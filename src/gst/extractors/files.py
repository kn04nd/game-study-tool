"""Loose files of the install and the user data recon found (saves, configs, logs), any engine.

- ``files.loose``: a catalog of every file of the install with its format (``gst fmt id``: signature,
  entropy), one unit per folder (batches of ``BATCH`` files), written to ``dumps/loose/catalog/*.jsonl``.
  The install stays read-only; plain files (ini/json/xml/csv/cfg/lua/video) are read in place.
- ``files.userdata``: a read-only copy of each user-data folder into ``dumps/userdata/`` with a manifest
  (path, size, mtime, sha256 streamed).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from pathlib import Path

from gst.common import long_path, read_json, write_json
from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.registry import register

BATCH = 5000  # files per catalog unit (a batch, never a ceiling)
SAMPLE = 64 << 10

KIND_CATEGORY = {
    ".ini": "config_save", ".cfg": "config_save", ".json": "data", ".xml": "data", ".csv": "data", ".yaml": "data",
    ".yml": "data", ".toml": "config_save", ".txt": "data", ".lua": "code", ".py": "code", ".js": "code",
    ".mp4": "video", ".webm": "video", ".bik": "video", ".bk2": "video", ".usm": "video", ".ogv": "video", ".avi": "video",
    ".wav": "audio", ".ogg": "audio", ".mp3": "audio", ".bank": "audio", ".bnk": "audio", ".wem": "audio", ".fsb": "audio",
    ".png": "texture", ".jpg": "texture", ".dds": "texture", ".tga": "texture", ".ttf": "font", ".otf": "font",
    ".po": "localization", ".mo": "localization", ".locres": "localization", ".translation": "localization",
    ".dll": "code", ".exe": "code", ".pdb": "code", ".so": "code",
}


def walk_files(root: Path):
    """(relpath, size) of every file under ``root``, any depth or count (scandir; long paths)."""
    base = long_path(root)
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        try:
            with os.scandir(os.path.join(base, rel_dir) if rel_dir else base) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            continue
        subdirs = []
        for e in entries:
            rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
            try:
                if e.is_dir(follow_symlinks=False):
                    subdirs.append(rel)
                elif e.is_file(follow_symlinks=False):
                    yield rel, e.stat().st_size
            except OSError:
                continue
        stack.extend(reversed(subdirs))


def folders(root: Path):
    """(folder relpath, [(file relpath, size)]) per folder, files directly inside it."""
    current, files = None, []
    for rel, size in walk_files(root):
        folder = rel.rsplit("/", 1)[0] if "/" in rel else "."
        if folder != current:
            if files:
                yield current, files
            current, files = folder, []
        files.append((rel, size))
    if files:
        yield current, files


def safe_id(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:60] or "root"
    return f"{slug}-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"


@register()
class LooseFiles(Extractor):
    name = "files.loose"
    category = "config_save"
    resource = "io"
    stage = 10

    def discover(self, ctx: Context):
        for folder, files in folders(ctx.install_dir):
            for i in range(0, len(files), BATCH):
                part = files[i:i + BATCH]
                uid = folder if len(files) <= BATCH else f"{folder}#{i // BATCH + 1}"
                yield Unit(id=uid, extractor=self.name, relpaths=[r for r, _ in part], size=sum(s for _, s in part))

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst import formats
        from gst.pipeline.worker import commit, partial_dir

        staging = partial_dir(ctx, unit)
        tmp = staging / "catalog.jsonl"
        cats: dict[str, int] = {}
        errors = []
        with open(long_path(tmp), "w", encoding="utf-8", newline="\n") as fh:
            for rel in unit.relpaths:
                path = ctx.install_dir / rel
                ext = Path(rel).suffix.lower()
                row = {"path": rel, "ext": ext, "category": KIND_CATEGORY.get(ext, "other")}
                try:
                    info = formats.identify(Path(long_path(path)), sample=SAMPLE)
                    row.update(size=info.get("size"), kind=info.get("kind"), detail=info.get("detail"), entropy=info.get("entropy"))
                except OSError as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    errors.append(f"{rel}: {row['error']}")
                cats[row["category"]] = cats.get(row["category"], 0) + 1
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        final = ctx.workspace / "dumps" / "loose" / "catalog" / f"{safe_id(unit.id)}.jsonl"
        commit(tmp, final)
        shutil.rmtree(long_path(staging), ignore_errors=True)
        notes = [f"cataloged {len(unit.relpaths)} files: " + ", ".join(f"{k} {v}" for k, v in sorted(cats.items()))]
        notes += [f"unreadable: {e}" for e in errors]
        return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=final.stat().st_size,
                      tool="gst fmt id", tool_version="", notes=notes)


@register()
class UserData(Extractor):
    name = "files.userdata"
    category = "config_save"
    resource = "io"
    stage = 10

    def folders(self, ctx: Context) -> list[Path]:
        recon = read_json(ctx.workspace / "recon" / "recon.json", {}) or {}
        return [Path(p) for p in recon.get("user_data") or [] if isinstance(p, str) and Path(p).is_dir()]

    def applies(self, ctx: Context) -> bool:
        return bool(self.folders(ctx))

    def discover(self, ctx: Context):
        from gst.pipeline.jobs import fingerprint_of

        for i, folder in enumerate(self.folders(ctx)):
            entries = []
            for rel, _size in walk_files(folder):
                try:
                    st = os.stat(long_path(folder / rel))
                    entries.append([rel, st.st_size, st.st_mtime_ns])
                except OSError:
                    entries.append([rel, -1, 0])
            yield Unit(id=f"{i}-{folder.name}", extractor=self.name, relpaths=[], size=sum(max(0, e[1]) for e in entries),
                       fingerprint=fingerprint_of(entries, str(ctx.game.get("buildid") or "?"), extra=str(folder)),
                       meta={"path": str(folder)})

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.assets import sha256_file
        from gst.pipeline.worker import commit, partial_dir

        source = Path(unit.meta["path"])
        staging = partial_dir(ctx, unit)
        files, skipped = {}, []
        for rel, _size in walk_files(source):
            src, dst = source / rel, staging / "files" / rel
            try:
                os.makedirs(os.path.dirname(long_path(dst)), exist_ok=True)
                shutil.copy2(long_path(src), long_path(dst))  # read the game's files, never write them
                st = os.stat(long_path(dst))
                files[rel] = {"size": st.st_size, "mtime": round(st.st_mtime, 3), "sha256": sha256_file(Path(long_path(dst)))}
                os.chmod(long_path(dst), stat.S_IREAD)  # a study copy: read-only
            except OSError as exc:  # a file locked by the running game
                skipped.append(f"{rel}: {type(exc).__name__}: {exc}")
        write_json(staging / "manifest.json", {"source": str(source), "files": files, "skipped": skipped,
                                               "copied_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        final = ctx.workspace / "dumps" / "userdata" / unit.id
        commit(staging, final)
        total = sum(f["size"] for f in files.values())
        return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=total, tool="copy",
                      notes=[f"{len(files)} files copied read-only from {source}"] + [f"not copied: {s}" for s in skipped])
