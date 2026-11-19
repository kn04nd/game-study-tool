"""Whole Unity project export with AssetRipper's headless web API (bound to 127.0.0.1 only).

`project` reconstructs a Unity project (scenes, prefabs and ScriptableObjects as YAML, plus assets);
`primary` exports content only (models, textures, audio, text). Both can be large: they go to
`assets/assetripper/<mode>/` (or an explicit fresh ``out`` folder) and are summarized by one
`_folder.gs.json` sidecar. No time limit unless the caller passes one (``timeout``); the wait for the
headless server ends when it answers or its process exits.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from gst import tools
from gst.assets import FOLDER_SIDECAR, assets_dir
from gst.common import GsError, write_json

POLL_SECONDS = 2
ROUTES = {"project": "/Export/UnityProject", "primary": "/Export/PrimaryContent"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fresh_dir(base: Path, name: str) -> Path:
    """``base/name`` if absent or empty, else ``base/name-2``, ``-3``... (AssetRipper needs an empty folder)."""
    cand, n = base / name, 1
    while cand.exists() and any(cand.iterdir()):
        n += 1
        cand = base / f"{name}-{n}"
    return cand


def _post(base: str, route: str, timeout: float | None, **form) -> int:
    req = urllib.request.Request(base + route, data=urllib.parse.urlencode(form).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303):  # the web UI redirects after an action
            return exc.code
        raise GsError(f"AssetRipper {route} failed: HTTP {exc.code} {exc.read()[:200]!r}") from exc


def _tree_size(folder: Path) -> tuple[int, int]:
    files = size = 0
    for dirpath, _dirs, names in os.walk(folder):
        for name in names:
            files += 1
            try:
                size += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return files, size


def export(ws: Path, install: Path, *, mode: str = "project", out: Path | None = None, timeout: float | None = None,
           settle: int = 10) -> dict:
    if mode not in ROUTES:
        raise GsError(f"mode must be one of {', '.join(ROUTES)}")
    exe = tools.bin_path("assetripper")
    if exe is None:
        raise GsError("AssetRipper is not installed (gst tools install assetripper)")
    out = Path(out) if out is not None else assets_dir(ws) / "assetripper" / mode
    if out.exists() and any(out.iterdir()):
        raise GsError(f"{out} is not empty: remove it to export again")
    out.mkdir(parents=True, exist_ok=True)
    logs = ws / "logs"
    logs.mkdir(exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    t0 = time.time()
    proc = subprocess.Popen([str(exe), "--headless", "--port", str(port), "--log-path", str(logs / "assetripper.log")],
                            cwd=str(logs), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        while True:
            try:
                urllib.request.urlopen(base + "/", timeout=POLL_SECONDS).read()  # one poll, not a deadline
                break
            except (urllib.error.URLError, OSError):
                if proc.poll() is not None or (timeout is not None and time.time() - t0 > timeout):
                    raise GsError("AssetRipper did not start (see logs/assetripper.log)") from None
                time.sleep(0.5)
        _post(base, "/LoadFolder", timeout, path=str(install))
        _post(base, ROUTES[mode], timeout, path=str(out))  # blocks until AssetRipper finished exporting
        # Safety net in case a version answers before the last files land: wait until the output is stable.
        last, still = (-1, -1), 0
        while (timeout is None or time.time() - t0 < timeout) and still < settle:
            time.sleep(5)
            now = _tree_size(out)
            still = still + 5 if now == last else 0
            last = now
    finally:
        proc.terminate()  # our own child, by PID
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
    files, size = _tree_size(out)
    if not files:
        raise GsError("AssetRipper exported nothing (see logs/assetripper.log)")
    version = tools.installed_version(tools.get("assetripper")) or ""
    label = f"AssetRipper {version}".strip()
    examples = sorted(p.relative_to(out).as_posix() for p in list(out.rglob("*"))[:200] if p.is_file())[:6]
    write_json(out / FOLDER_SIDECAR, {"file": ".", "size": size, "source": ".", "res_path": str(install), "tool": label,
                                      "tool_version": version, "label": label, "mode": mode, "count": files, "bytes": size,
                                      "examples": examples, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return {"out": str(out), "files": files, "bytes": size, "seconds": round(time.time() - t0, 1), "tool_version": version}
