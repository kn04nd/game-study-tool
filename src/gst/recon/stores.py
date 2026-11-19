"""Installed games from Steam, Epic, GOG and Xbox (read-only).

Steam/Epic/Xbox discovery and the KeyValues parser are adapted from universal-modder's
um/scan.py (MIT); registry-based Steam roots and GOG are new.
"""

from __future__ import annotations

import json
import os
import re
import string
from pathlib import Path

from gst.common import GsError

try:  # Windows only
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]


def vdf(text: str) -> dict:
    """Minimal Valve KeyValues parser (libraryfolders.vdf, appmanifest_*.acf)."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    stack: list[dict] = []
    cur: dict = {}
    key = None
    for s, brace in tokens:
        if brace == "{":
            new: dict = {}
            cur[key] = new
            stack.append(cur)
            cur, key = new, None
        elif brace == "}":
            cur = stack.pop() if stack else cur
        elif key is None:
            key = s
        else:
            cur[key] = s.replace("\\\\", "\\")
            key = None
    return cur


def _reg(hive, path: str, name: str) -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(hive, path) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def steam_roots() -> list[Path]:
    candidates = []
    if winreg is not None:
        for hive, path, name in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            value = _reg(hive, path, name)
            if value:
                candidates.append(Path(value))
    candidates += [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam", Path(r"C:\Program Files\Steam")]
    out: list[Path] = []
    for c in candidates:
        try:
            r = c.resolve()
        except OSError:
            continue
        if (r / "steamapps").is_dir() and r not in out:
            out.append(r)
    return out


def steam_libraries() -> list[Path]:
    libs: list[Path] = []
    for root in steam_roots():
        libs.append(root)
        lf = root / "steamapps" / "libraryfolders.vdf"
        if lf.exists():
            data = vdf(lf.read_text(encoding="utf-8", errors="replace"))
            for v in (data.get("libraryfolders") or {}).values():
                if isinstance(v, dict) and v.get("path"):
                    libs.append(Path(v["path"]))
    out: list[Path] = []
    for lib in libs:
        try:
            r = lib.resolve()
        except OSError:
            continue
        if (r / "steamapps").is_dir() and r not in out:
            out.append(r)
    return out


NOT_GAMES_APPIDS = {"228980", "1070560", "1391110", "1628350", "2180100"}  # redistributables, Linux runtimes
NOT_GAMES_NAME = re.compile(r"redistributable|steam linux runtime|^proton\b|steamworks shared|steamvr", re.I)


def steam_games() -> list[dict]:
    games = []
    for lib in steam_libraries():
        apps = lib / "steamapps"
        for acf in apps.glob("appmanifest_*.acf"):
            st = vdf(acf.read_text(encoding="utf-8", errors="replace")).get("AppState", {})
            installdir = st.get("installdir")
            path = apps / "common" / (installdir or "")
            if st.get("appid") in NOT_GAMES_APPIDS or NOT_GAMES_NAME.search(st.get("name") or ""):
                continue
            if installdir and path.is_dir():
                appid = st.get("appid")
                workshop = apps / "workshop" / "content" / (appid or "")
                games.append({
                    "store": "steam", "appid": appid, "name": st.get("name"), "path": str(path),
                    "buildid": st.get("buildid"), "last_updated": st.get("LastUpdated"),
                    "size_on_disk": int(st.get("SizeOnDisk") or 0),
                    "workshop": str(workshop) if appid and workshop.is_dir() else None,
                })
    return games


def epic_games() -> list[dict]:
    out = []
    manifests = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    for item in manifests.glob("*.item"):
        try:
            d = json.loads(item.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        p = d.get("InstallLocation", "")
        if p and Path(p).is_dir():
            out.append({"store": "epic", "appid": d.get("AppName"), "name": d.get("DisplayName"), "path": p,
                        "buildid": d.get("AppVersionString")})
    return out


def gog_games() -> list[dict]:
    out = []
    if winreg is None:
        return out
    base = r"SOFTWARE\WOW6432Node\GOG.com\Games"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as key:
            count = winreg.QueryInfoKey(key)[0]
            for i in range(count):
                sub = winreg.EnumKey(key, i)
                path = _reg(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", "path")
                name = _reg(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", "gameName")
                if path and Path(path).is_dir():
                    out.append({"store": "gog", "appid": sub, "name": name or Path(path).name, "path": path,
                                "buildid": _reg(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{sub}", "ver")})
    except OSError:
        pass
    return out


def xbox_games() -> list[dict]:
    out = []
    for letter in string.ascii_uppercase[2:]:
        root = Path(f"{letter}:/XboxGames")
        try:
            if not root.is_dir():
                continue
            for g in root.iterdir():
                content = g / "Content"
                # real game folders have Content/ (system folders such as GameSave do not)
                if g.is_dir() and (content.is_dir() or (g / "MicrosoftGame.Config").is_file()):
                    out.append({"store": "xbox", "appid": None, "name": g.name,
                                "path": str(content if content.is_dir() else g)})
        except OSError:
            continue
    return out


def all_games() -> list[dict]:
    return steam_games() + epic_games() + gog_games() + xbox_games()


def _key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


GENERIC_FOLDERS = {"build", "builds", "bin", "binaries", "release", "debug", "x64", "x86", "win64", "win32", "dist",
                   "out", "output", "app", "game", "files"}


def resolve_game(query: str, games: list[dict] | None = None) -> dict:
    """A folder path, a Steam AppID, or a fuzzy name -> game dict (store, appid, name, path...)."""
    p = Path(query.strip().strip('"')).expanduser()
    games = games if games is not None else all_games()
    if p.is_dir():
        for g in games:
            try:
                if Path(g["path"]).resolve() == p.resolve():
                    return g
            except OSError:
                continue
        named = p.resolve()
        while named.name.lower() in GENERIC_FOLDERS and named.parent != named:
            named = named.parent  # build/, bin/, Release/ say nothing: name the game after the project folder
        return {"store": None, "appid": None, "name": named.name or p.name, "path": str(p)}
    q = _key(query)
    exact = [g for g in games if _key(g["name"]) == q or g.get("appid") == query.strip()]
    hits = exact or [g for g in games if q and (q in _key(g["name"]) or q in _key(Path(g["path"]).name))]
    if not hits:
        raise GsError(f"no installed game matches {query!r}; pass the install folder instead (gst recon --list shows what was found)")
    hits.sort(key=lambda g: len(g["name"] or ""))
    return hits[0]
