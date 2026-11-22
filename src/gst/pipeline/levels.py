"""Asset levels and study tracks of ``gst study``.

Two tracks run side by side in one scheduler:

- ``code``: loose files, decompilers, the code index, REA and the analyses that read only code
  (``CODE_ANALYSES``). Always planned.
- ``assets``: asset inventories, exports, the asset analyses and the heavy project export. Planned by level.

``after_earlier_stages`` waits only for earlier stages of the same track, so the code analyses never wait for a
13 GB asset export; explicit ``after`` dependencies still cross tracks (an asset analysis that reads the code
index waits for ``code.index``).

Levels (``gst study --assets <level>``; chosen once per workspace, kept in game.json ``assets_level``):

- ``none``: no asset job.
- ``basic``: inventories, the map listing and the overview analyses (``BASIC``): enough for a note on how the
  assets are organized.
- ``normal``: plus every export and asset analysis, except the map/level containers (Unity scene bundles,
  ``is_map_container``) and the ``FULL`` extractors: a detailed note on prefabs, meshes, rigs, animations, audio.
- ``full``: everything, map containers and the whole-project export included.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

LEVELS = ("none", "basic", "normal", "full")
TRACKS = ("code", "assets")
CODE_PREFIXES = ("files.", "code.", "rea.")
CODE_ANALYSES = frozenset({"analysis.code_architecture", "analysis.systems"})
BASIC = frozenset({
    "unity.inventory", "godot.inventory", "unreal.inventory", "maps.list",
    "analysis.catalogs", "analysis.dcc", "analysis.audio_refs", "analysis.audio_catalog",
    "analysis.unreal_assets", "analysis.godot_pck",
})
FULL = frozenset({"unity.assetripper_project", "unity.terrain", "unity.navmesh", "unity.video"})
# extractors that read map containers even below ``full`` (they only list them)
LIST_ONLY = frozenset({"maps.list"})


def track_of(name: str) -> str:
    return "code" if name.startswith(CODE_PREFIXES) or name in CODE_ANALYSES else "assets"


def min_level(name: str) -> str:
    """The lowest level that plans this extractor (``none`` for the code track)."""
    if track_of(name) == "code":
        return "none"
    if name in BASIC:
        return "basic"
    if name in FULL:
        return "full"
    return "normal"


def rank(level: str | None) -> int:
    return LEVELS.index(level) if level in LEVELS else len(LEVELS) - 1


def included(name: str, level: str | None) -> bool:
    """Is the extractor planned at ``level``? ``None`` = no level chosen: everything (``full``)."""
    return rank(min_level(name)) <= rank(level)


@functools.lru_cache(maxsize=4096)
def _scene_bundle(path: str, size: int, mtime_ns: int) -> bool:
    from gst.engines.unity.bundle import read_header

    try:
        nodes = read_header(path).nodes
    except Exception:
        return False
    return any(n[3].startswith("BuildPlayer-") and not n[3].endswith(".sharedAssets") for n in nodes)


def is_map_container(install: Path | str | None, relpath: str) -> bool:
    """A Unity asset bundle that holds built scenes (``BuildPlayer-<scene>`` CABs): the streamed levels of the
    game. ``levelN`` files of ``*_Data`` are not map containers (the game's own scenes, usually small)."""
    if not install or not relpath:
        return False
    p = Path(install) / relpath
    try:
        st = os.stat(p)
        with open(p, "rb") as fh:
            if not fh.read(8).startswith(b"UnityFS"):
                return False
    except OSError:
        return False
    return _scene_bundle(str(p), st.st_size, st.st_mtime_ns)


def unit_left_out(name: str, relpaths: list[str], level: str | None, install) -> bool:
    """Is this unit left out at ``level``? Below ``full``, Unity extractors skip map containers (except the
    ones that only list them)."""
    if rank(level) >= rank("full") or name in LIST_ONLY or not name.startswith("unity.") or not relpaths:
        return False
    return is_map_container(install, relpaths[0])


def check(level: str | None) -> str | None:
    if level is None:
        return None
    level = str(level).strip().lower()
    if level not in LEVELS:
        from gst.common import GsError

        raise GsError(f"--assets takes one of {', '.join(LEVELS)}, got {level!r}")
    return level
