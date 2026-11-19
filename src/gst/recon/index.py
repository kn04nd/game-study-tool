"""Bounded file index of a game folder: lower-cased relative paths, dirs, sizes.

Adapted from universal-modder's um/scan.py Index (MIT). Changes: compiled regex per pattern
(cheaper on 70k+ files), \\\\?\\ walk root (long paths), sizes and extension histogram.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

MAX_ENTRIES: int | None = None  # no ceiling: every file of the install (an explicit limit only)
MAX_DEPTH: int | None = None
SKIP_DIRS = {"__pycache__", ".git", "shadercache", "$recycle.bin"}


@lru_cache(maxsize=4096)
def _glob_rx(pattern: str) -> re.Pattern:
    return re.compile(fnmatch.translate(pattern.lower()))


def _walk_root(root: Path) -> str:
    s = str(root.resolve())
    if os.name == "nt" and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


class FileIndex:
    def __init__(self, root: Path | str, max_entries: int | None = MAX_ENTRIES, max_depth: int | None = MAX_DEPTH):
        self.root = Path(root)
        self.files: list[str] = []
        self.sizes: dict[str, int] = {}
        self.dirs: set[str] = set()
        self.truncated = False
        self.errors = 0
        walk = _walk_root(self.root)
        base = len(walk) + 1
        for dirpath, dirnames, filenames in os.walk(walk, onerror=self._onerror):
            rel = dirpath[base:].replace("\\", "/")
            depth = rel.count("/") + 1 if rel else 0
            if max_depth is not None and depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
            for d in dirnames:
                self.dirs.add((f"{rel}/{d}" if rel else d).lower())
            for f in filenames:
                rp = (f"{rel}/{f}" if rel else f).lower()
                self.files.append(rp)
                try:
                    self.sizes[rp] = os.stat(os.path.join(dirpath, f)).st_size
                except OSError:
                    self.errors += 1
            if max_entries is not None and len(self.files) > max_entries:
                self.truncated = True
                break
        self._fileset = set(self.files)

    def _onerror(self, _exc) -> None:
        self.errors += 1

    def __contains__(self, rel: str) -> bool:
        return rel.lower() in self._fileset

    def find(self, *patterns: str) -> list[str]:
        rx = [_glob_rx(p) for p in patterns]
        return [f for f in self.files if any(r.match(f) for r in rx)]

    def has(self, *patterns: str) -> bool:
        rx = [_glob_rx(p) for p in patterns]
        return any(r.match(f) for f in self.files for r in rx)

    def has_dir(self, *patterns: str) -> bool:
        rx = [_glob_rx(p) for p in patterns]
        return any(r.match(d) for d in self.dirs for r in rx)

    def path(self, rel_lower: str) -> Path:
        """Case-insensitive relative path -> real path."""
        cur = self.root
        for part in rel_lower.split("/"):
            try:
                cur = next(c for c in cur.iterdir() if c.name.lower() == part)
            except (StopIteration, OSError):
                return self.root / rel_lower
        return cur

    def total_size(self) -> int:
        return sum(self.sizes.values())

    def ext_histogram(self, top: int = 25) -> list[tuple[str, int, int]]:
        """[(extension, count, bytes)] by bytes."""
        count: Counter = Counter()
        size: Counter = Counter()
        for f, s in self.sizes.items():
            ext = Path(f).suffix or "(none)"
            count[ext] += 1
            size[ext] += s
        return [(ext, count[ext], size[ext]) for ext, _ in size.most_common(top)]

    def largest(self, top: int = 15) -> list[tuple[str, int]]:
        return sorted(self.sizes.items(), key=lambda kv: -kv[1])[:top]
