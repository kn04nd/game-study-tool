"""The sandbox: a copy of the install inside the workspace (`sandbox/`). Tools that inject into the game or
drop files next to it (Frida, BepInEx, UE4SS, ReShade...) run only there, offline; the original install is
never touched (guardrails rule 5).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from gst import policy
from gst.common import GsError


def path(ws: Path) -> Path:
    return ws / "sandbox"


def _size(folder: Path) -> tuple[int, int]:
    total = files = 0
    for p in folder.rglob("*"):
        if p.is_file():
            files += 1
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total, files


def create(ws: Path, game: dict) -> dict:
    decision = policy.check(game, "runtime")
    if not decision.allowed:
        raise GsError(f"{decision.reason} (guardrails.md rule {decision.rule}): no sandbox for this game", 3)
    install = Path(game["install_dir"])
    target = path(ws)
    if target.exists() and any(target.iterdir()):
        raise GsError(f"the sandbox already exists ({target}); gst sandbox remove first to refresh it")
    need, files = _size(install)
    free = shutil.disk_usage(ws).free
    if need * 1.1 > free:
        raise GsError(f"not enough space for the sandbox: {need >> 20} MB needed, {free >> 20} MB free on {ws.anchor}")
    target.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    proc = subprocess.run(["robocopy", str(install), str(target), "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1", "/MT:8",
                           "/NFL", "/NDL", "/NJH", "/NJS", "/NP"], capture_output=True, text=True, errors="replace",
                          timeout=7200, stdin=subprocess.DEVNULL)
    if proc.returncode >= 8:
        raise GsError(f"robocopy failed (exit {proc.returncode}): {(proc.stdout or proc.stderr)[-400:]}")
    return {"sandbox": str(target), "files": files, "bytes": need, "seconds": round(time.time() - t0, 1)}


def remove(ws: Path) -> dict:
    target = path(ws)
    if not target.exists():
        return {"sandbox": str(target), "removed": False}
    shutil.rmtree(target)
    target.mkdir()
    return {"sandbox": str(target), "removed": True}


def status(ws: Path) -> dict:
    target = path(ws)
    if not target.exists() or not any(target.iterdir()):
        return {"sandbox": str(target), "exists": False}
    size, files = _size(target)
    return {"sandbox": str(target), "exists": True, "files": files, "bytes": size}
