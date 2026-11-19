"""Unreal 4/5: containers read with the stdlib, then the catalog tools, always into the workspace.

- ``paks``: find containers; pak footer/index header (v1-v12) and v10+ directory listing; utoc header (stdlib).
- ``repak``: list legacy paks (v1-v11; v12 goes to the stdlib lister, repak 0.2.3 stops at v11).
- ``retoc``: IoStore info/list, ``to-legacy`` into ``dumps/unreal/legacy/`` (filtered), script objects of ``global.utoc``.
- ``uas``: UnrealAssetScout list/export, arguments through a response file (no command-line ceiling).
- ``usmap``, ``keys``: mappings and AES keys only from published sources or the user's files (rule 4).
- ``inventory``/``export``: what ``gst assets`` and the ``unreal.*`` jobs call (``gst.assets.unreal`` re-exports them).
- ``extractors``: ``unreal.inventory``, ``unreal.iostore``, ``unreal.export`` (registered for ``gst study``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def tool_bin(tool_id: str) -> Path:
    from gst import tools
    from gst.common import GsError

    exe = tools.bin_path(tool_id)
    if exe is None:
        raise GsError(f"{tool_id} is not installed (gst tools install unreal)")
    return exe


def run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    """No time limit by default (timeouts are explicit options only)."""
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          stdin=subprocess.DEVNULL)


def tool_version(tool_id: str) -> str | None:
    from gst import tools

    try:
        return tools.installed_version(tools.get(tool_id))
    except Exception:  # noqa: BLE001 - a missing catalog entry only loses provenance
        return None
