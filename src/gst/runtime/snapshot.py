"""Folder snapshots (saves, configs, logs) and their diff: what one action in the game changed on disk.

A snapshot is a manifest (path, size, mtime, sha256) plus copies of the small files under
`traces/snapshots/<name>/`, so `diff` can also show which bytes changed (gst fmt diff).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

from gst.common import GsError, read_json, write_json

MAX_COPY_FILE: int | None = None   # no ceiling: every file is copied (hash streamed); a limit only when asked
MAX_COPY_TOTAL: int | None = None
MAX_FILES: int | None = None


def default_paths(ws: Path) -> list[Path]:
    """The user-data folders recon found (saves, configs)."""
    recon = read_json(ws / "recon" / "recon.json", {}) or {}
    found = recon.get("user_data") or []
    return [Path(p) for p in found if isinstance(p, str) and Path(p).is_dir()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(ws: Path, name: str, paths: list[Path], *, force: bool = False) -> dict:
    root = ws / "traces" / "snapshots" / name
    if root.exists():
        if not force:
            raise GsError(f"snapshot {name} exists (pick another name or --force)")
        shutil.rmtree(root)
    if not paths:
        raise GsError("nothing to snapshot: pass --path <folder> (recon found no user-data folder)")
    t0 = time.time()
    files, copied_bytes, count = {}, 0, 0
    for index, folder in enumerate(paths):
        for dirpath, _dirs, names in os.walk(folder):
            for fname in names:
                if MAX_FILES is not None and count >= MAX_FILES:
                    break
                path = Path(dirpath) / fname
                try:
                    st = path.stat()
                    digest = _sha256(path)
                except OSError:
                    continue  # locked by the game: skip, the manifest says what was seen
                key = f"{index}/{path.relative_to(folder).as_posix()}"
                entry = {"size": st.st_size, "mtime": round(st.st_mtime, 3), "sha256": digest, "copied": False}
                if ((MAX_COPY_FILE is None or st.st_size <= MAX_COPY_FILE)
                        and (MAX_COPY_TOTAL is None or copied_bytes + st.st_size <= MAX_COPY_TOTAL)):
                    target = root / "files" / key
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    entry["copied"] = True
                    copied_bytes += st.st_size
                files[key] = entry
                count += 1
    manifest = {"name": name, "roots": [str(p) for p in paths], "files": files, "copied_bytes": copied_bytes,
                "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t0, 1)}
    write_json(root / "manifest.json", manifest)
    return {"name": name, "files": len(files), "copied_bytes": copied_bytes, "roots": manifest["roots"],
            "seconds": manifest["seconds"]}


def diff(ws: Path, a: str, b: str, *, ranges: int = 5) -> dict:
    from gst import formats

    base = ws / "traces" / "snapshots"
    ma, mb = (read_json(base / n / "manifest.json", None) for n in (a, b))
    if ma is None or mb is None:
        raise GsError(f"unknown snapshot: {a if ma is None else b} (gst trace snapshot <name> first)")
    fa, fb = ma["files"], mb["files"]
    changed = []
    for key in sorted(set(fa) & set(fb)):
        if fa[key]["sha256"] == fb[key]["sha256"]:
            continue
        row = {"file": key, "size_a": fa[key]["size"], "size_b": fb[key]["size"]}
        if fa[key]["copied"] and fb[key]["copied"]:
            d = formats.diff(base / a / "files" / key, base / b / "files" / key, limit=ranges)
            row.update(same_size=d["same_size"], common_prefix=d["common_prefix"], ranges=d["ranges"])
        changed.append(row)
    return {"a": a, "b": b, "added": sorted(set(fb) - set(fa)), "removed": sorted(set(fa) - set(fb)), "changed": changed,
            "roots": mb["roots"]}
