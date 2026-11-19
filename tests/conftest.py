"""Shared fixtures. Tests never touch the real GS_HOME, tools dir, Steam library or games."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from gst import guard

REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")  # captured before any fixture isolates it


@pytest.fixture
def real_tool(monkeypatch):
    """Path of an installed third-party tool (read-only lookup in the real tools dir), or None."""

    def find(tool_id: str):
        from gst import tools

        with monkeypatch.context() as m:
            m.setenv("LOCALAPPDATA", REAL_LOCALAPPDATA)
            m.setattr(tools, "WINGET_PACKAGES", Path(REAL_LOCALAPPDATA) / "Microsoft" / "WinGet" / "Packages")
            return tools.bin_path(tool_id)

    return find


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    home = tmp_path / "gs-home"
    state = tmp_path / "localappdata"
    home.mkdir()
    state.mkdir()
    monkeypatch.setenv("GS_HOME", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(state))
    for name in ("GST_HOME", "GST_TOOLS", "GST_ONLINE", "GST_GAME", "GS_GAME", "GS_TOOLS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("GS_ONLINE", raising=False)
    monkeypatch.setattr(guard, "_steam_path", lambda: None)
    for cached in (guard.load_registry, guard.steam_library_roots, guard._git_root_of):
        cached.cache_clear()
    yield {"home": home, "state": state}
    for cached in (guard.load_registry, guard.steam_library_roots, guard._git_root_of):
        cached.cache_clear()


@pytest.fixture
def fake_steam(tmp_path, monkeypatch):
    """A fake Steam install with one extra library, wired into guard._steam_path."""
    steam = tmp_path / "Steam"
    lib = tmp_path / "SteamLibrary"
    for root in (steam, lib):
        (root / "steamapps" / "common").mkdir(parents=True)
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    vdf.write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
        % (str(steam).replace("\\", "\\\\"), str(lib).replace("\\", "\\\\")),
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "_steam_path", lambda: str(steam))
    guard.steam_library_roots.cache_clear()
    return {"steam": steam, "lib": lib, "common": lib / "steamapps" / "common"}


@pytest.fixture
def registry(isolated_env):
    """Write registry.json entries: registry({'slug': {...}})."""

    def _write(games: dict) -> Path:
        path = isolated_env["home"] / "registry.json"
        path.write_text(json.dumps({"games": games}), encoding="utf-8")
        guard.load_registry.cache_clear()
        return path

    return _write


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "some-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    guard._git_root_of.cache_clear()
    return repo
