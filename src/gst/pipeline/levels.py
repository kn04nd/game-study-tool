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
- ``full``: everything, map containers included.

The whole Unity project (``unity.assetripper_project``, AssetRipper) is a separate yes/no choice, not a level
(``gst study --unity-project yes|no``, kept in game.json ``unity_project``): below ``full`` it leaves the map
containers out (``map_containers``), which is what keeps it in minutes instead of hours.
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
FULL = frozenset({"unity.terrain", "unity.navmesh", "unity.video"})
PROJECT = frozenset({"unity.assetripper_project"})  # planned by the unity_project choice, not by the level
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
    if name in FULL or name in PROJECT:
        return "full"
    return "normal"


def rank(level: str | None) -> int:
    return LEVELS.index(level) if level in LEVELS else len(LEVELS) - 1


def included(name: str, level: str | None, project: bool | None = None) -> bool:
    """Is the extractor planned at ``level``? ``None`` = no level chosen: everything (``full``). The project
    export follows ``project`` (the unity_project choice); with no choice, it follows ``full`` as before."""
    if name in PROJECT and project is not None:
        return bool(project)
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


def map_containers(install: Path | str | None) -> list[str]:
    """Relative paths of the Unity map containers of an install (``is_map_container``)."""
    from gst.engines.unity import loader

    if not install:
        return []
    return [rel for rel, _kind, _size in loader.iter_unity_files(Path(install)) if is_map_container(install, rel)]


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


def check_project(value) -> bool | None:
    """The unity_project choice: ``None`` (not chosen), or yes/no as a bool."""
    if value is None or isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("yes", "y", "true", "1", "sim", "s"):
        return True
    if text in ("no", "n", "false", "0", "nao", "não"):
        return False
    from gst.common import GsError

    raise GsError(f"--unity-project takes yes or no, got {value!r}")
