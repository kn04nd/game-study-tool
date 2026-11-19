"""``.usmap`` mappings (UE5 unversioned properties): only from a published source or a file the user
provides. gst never dumps one at runtime: that needs injecting into the running game, which is out of
scope here even for ``offline-ok`` games (a sandbox dump stays a manual, documented step). An export
that needs mappings and has none is ``blocked-policy`` with this reason.

Where gst looks, in order:

- ``game.json`` ``usmap``: ``{"path": "<file>", "source": "https://... or user"}``;
- ``notes/*.usmap`` in the workspace (the user's file; provenance in ``notes/usmap.json`` when present).
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

USMAP_MAGIC = 0x30C4
NEEDS_REASON = ("these assets use unversioned properties and need a .usmap; gst does not dump mappings at runtime "
                "(injection, out of scope): put a published .usmap in notes/ (or game.json usmap.path with its source)")
# CUE4Parse / uas messages when a package needs mappings it does not have
NEEDS_RX = re.compile(r"usmap|mappings? (?:file )?(?:is )?(?:required|missing|needed)|unversioned propert", re.I)


def header(path: Path) -> dict | None:
    """Magic 0x30C4, version, compression method (0 none, 1 Oodle, 2 Brotli, 3 ZStandard)."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if len(head) < 4 or struct.unpack_from("<H", head, 0)[0] != USMAP_MAGIC:
        return None
    return {"version": head[2], "compression": {0: "none", 1: "oodle", 2: "brotli", 3: "zstd"}.get(head[3], head[3])}


def find(ws: Path, game: dict) -> tuple[Path | None, str]:
    """(path of a valid .usmap, its source) or (None, "")."""
    entry = game.get("usmap") or {}
    if isinstance(entry, dict) and entry.get("path"):
        p = Path(entry["path"])
        if p.is_file() and header(p):
            return p, str(entry.get("source") or "game.json")
    notes = Path(ws) / "notes"
    for p in sorted(notes.glob("*.usmap")) if notes.is_dir() else []:
        if header(p):
            return p, "user (notes/)"
    return None, ""


def needs_mappings(text: str) -> bool:
    """Does uas output say the packages need a .usmap?"""
    return bool(NEEDS_RX.search(text or ""))
