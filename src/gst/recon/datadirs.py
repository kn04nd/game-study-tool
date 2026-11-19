"""Where a game keeps saves, config and logs on Windows (existing folders only).

Adapted from universal-modder's save_hints (MIT); Documents is read from the registry
(handles OneDrive redirection) instead of spawning PowerShell; engine-specific locations added.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

KNOWN = {
    "project zomboid": ["{profile}/Zomboid"],
    "oxygen not included": ["{documents}/Klei/OxygenNotIncluded", "{profile}/AppData/LocalLow/Klei/Oxygen Not Included"],
    "the elder scrolls v: skyrim special edition": ["{documents}/My Games/Skyrim Special Edition"],
    "fallout 4": ["{documents}/My Games/Fallout4"],
    "no man's sky": ["{appdata}/HelloGames/NMS"],
    "dying light": ["{documents}/DyingLight"],
    "grand theft auto iv": ["{localappdata}/Rockstar Games/GTA IV"],
    "deep rock galactic": ["{localappdata}/FSD/Saved"],
}


def shell_folders() -> dict[str, str]:
    out = {"profile": str(Path.home())}
    for env, key in (("APPDATA", "appdata"), ("LOCALAPPDATA", "localappdata")):
        if os.environ.get(env):
            out[key] = os.environ[env]
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            docs = winreg.QueryValueEx(k, "Personal")[0]
            out["documents"] = os.path.expandvars(docs)
    except (ImportError, OSError):
        out["documents"] = str(Path.home() / "Documents")
    return out


def hints(name: str, engine_hits: list[dict]) -> list[str]:
    folders = shell_folders()
    prof = folders.get("profile")
    base = re.sub(r"[:®™]", "", name or "")
    names = {n for n in (name, base, base.split(" - ")[0]) if n}
    det: dict = {}
    for hit in engine_hits:
        det.update(hit.get("details", {}))
    for key in ("product", "project"):
        if det.get(key):
            names.add(det[key])
    cands: list[Path] = []
    for template in KNOWN.get((name or "").lower(), []):
        try:
            cands.append(Path(template.format(**folders)))
        except KeyError:
            pass
    if det.get("company") and det.get("product") and prof:
        cands.append(Path(prof) / "AppData" / "LocalLow" / det["company"] / det["product"])
    if det.get("project") and folders.get("localappdata"):
        cands.append(Path(folders["localappdata"]) / det["project"] / "Saved")
    if folders.get("appdata"):
        cands += [Path(folders["appdata"]) / "Godot" / "app_userdata" / n for n in names]
    for n in names:
        if folders.get("documents"):
            cands += [Path(folders["documents"]) / "My Games" / n, Path(folders["documents"]) / n]
        if prof:
            cands += [Path(prof) / "Saved Games" / n, Path(prof) / "Games" / n]
        for key in ("appdata", "localappdata"):
            if folders.get(key):
                cands.append(Path(folders[key]) / n)
    out: list[str] = []
    for c in cands:
        try:
            if c.is_dir() and str(c) not in out:
                out.append(str(c))
        except OSError:
            continue
    return out
