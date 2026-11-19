"""repak: list legacy ``.pak`` files (v1-v11). v12 (UE 5.4+ ``Utf8PakDirectory``) is beyond repak 0.2.3, the
latest release: those, and any plain v10+ pak repak fails on, are listed by the stdlib reader
(:func:`gst.engines.unreal.paks.list_pak`). An encrypted index needs a published key (``keys``)."""

from __future__ import annotations

from pathlib import Path

from gst.common import GsError
from gst.engines.unreal import paks as paks_mod


def list_paths(pak: Path, *, key: dict | None = None) -> tuple[list[str], str]:
    """(packed paths, how they were listed: ``repak``, ``stdlib`` or ``repak+aes``). Raises GsError
    (code 3) when the index is encrypted and no published key was given."""
    from gst.engines.unreal import run, tool_bin

    info = paks_mod.pak_info(pak) or {}
    version = info.get("version") or 0
    if info.get("encrypted_index") and key is None:
        raise GsError(f"{pak.name}: the pak index is encrypted and no published key is recorded (rule 4)", 3)
    if not info.get("encrypted_index") and (version > paks_mod.REPAK_MAX_VERSION):
        return paks_mod.list_pak(pak), "stdlib"
    cmd = [str(tool_bin("repak"))]
    if key is not None:
        cmd += ["--aes-key", key["key"]]
    cmd += ["list", str(pak)]
    proc = run(cmd)
    rows = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode == 0 and rows:
        return rows, "repak+aes" if key is not None else "repak"
    if not info.get("encrypted_index") and version >= 10:
        return paks_mod.list_pak(pak), "stdlib"
    raise GsError(f"repak list {pak.name} failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()}")
