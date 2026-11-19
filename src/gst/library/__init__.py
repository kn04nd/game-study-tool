"""Cross-game library GS_HOME/_library/library.db: build, facets, compare, search.

`gst lib build` reads every workspace of the registry (plus the extra workspaces and legacy gs homes saved in
`_library/library.json`) and writes one SQLite file: a table per facet (rigs, clips, joints, audio...) whose rows
carry the workspace-relative JSON they came from (`source`) and where in it (`ref`), the gaps (analyses a facet
needs that a workspace lacks) and an FTS5 index over the notes and the analysis Markdown. Read-only on the
workspaces: the only writes are under GS_HOME/_library/. Contract: docs/INTERFACES.md section 11.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from gst.common import GsError, norm_path, read_json, write_json

LIBRARY_DIR = "_library"
DB_NAME = "library.db"
SETTINGS = "library.json"
SCHEMA_VERSION = 1


def library_dir(home: Path | None = None) -> Path:
    from gst import config

    return (home or config.gs_home()) / LIBRARY_DIR


def db_path(home: Path | None = None) -> Path:
    return library_dir(home) / DB_NAME


def load_settings(home: Path) -> dict:
    """``_library/library.json``: extra workspaces and legacy homes added with `gst lib build --workspace/--legacy`."""
    data = read_json(library_dir(home) / SETTINGS, {}) or {}
    return {"workspaces": list(data.get("workspaces") or []), "legacy": list(data.get("legacy") or [])}


def save_settings(home: Path, settings: dict) -> None:
    write_json(library_dir(home) / SETTINGS, {"workspaces": sorted(set(settings.get("workspaces") or [])),
                                              "legacy": sorted(set(settings.get("legacy") or []))})


def connect(home: Path | None = None) -> sqlite3.Connection:
    path = db_path(home)
    if not path.is_file():
        raise GsError(f"no library at {path}: run `gst lib build` first")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def engine_family(key: str | None) -> str:
    """``unity-mono``/``unity-il2cpp`` -> ``unity``; ``godot``, ``unreal`` and the rest unchanged."""
    key = (key or "").lower()
    return "unity" if key.startswith("unity") else key or "?"


@dataclass
class Workspace:
    key: str                 # unique in the library: the slug, `<slug>@<folder>` on a clash, `legacy:<slug>`
    slug: str
    path: Path
    game: dict
    origin: str              # registry | extra | legacy
    home: Path
    extra: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.game.get("name") or self.slug

    @property
    def engine(self) -> str:
        return engine_family((self.game.get("engine") or {}).get("key"))


def _game_json(ws: Path) -> dict | None:
    data = read_json(ws / "game.json", None)
    return data if isinstance(data, dict) and (data.get("gs_workspace") or data.get("gst_workspace")) else None


def _home_workspaces(home: Path) -> list[tuple[str, Path]]:
    """``(slug, folder)`` of a GS_HOME: its registry entries whose folder holds a workspace; with no registry,
    every ``<home>/<slug>/game.json`` (``_name`` folders are internal and skipped)."""
    registry = read_json(home / "registry.json", {}) or {}
    games = registry.get("games") if isinstance(registry, dict) else None
    out = []
    if isinstance(games, dict) and games:
        for slug, entry in sorted(games.items()):
            folder = Path((entry or {}).get("workspace") or home / slug)
            if _game_json(folder) is not None:
                out.append((slug, folder))
        return out
    for game_json in sorted(home.glob("*/game.json")):
        if not game_json.parent.name.startswith("_") and _game_json(game_json.parent) is not None:
            out.append((game_json.parent.name, game_json.parent))
    return out


def norm_name(text: str) -> str:
    """Loose game name for matching (`repo` = `r-e-p-o` = `R.E.P.O.`)."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def discover(home: Path, *, games: list[str] | None = None, extra: list[str] | None = None,
             legacy: list[str] | None = None) -> tuple[list[Workspace], list[str]]:
    """Workspaces of the library and warnings. ``extra``: workspace folders or other GS_HOMEs (their registry);
    ``legacy``: gs v1 homes read only for search (their notes). ``games`` filters by slug/name."""
    found: list[Workspace] = []
    warnings: list[str] = []
    seen: set[str] = set()
    keys: set[str] = set()

    def add(slug: str, folder: Path, origin: str, root: Path) -> None:
        ident = norm_path(folder)
        if ident in seen:
            return
        game = _game_json(folder)
        if game is None:
            warnings.append(f"{folder}: not a workspace (no game.json)")
            return
        seen.add(ident)
        key = f"legacy:{slug}" if origin == "legacy" else slug
        if key in keys:
            key = f"{key}@{folder.parent.name}"
        keys.add(key)
        found.append(Workspace(key, slug, folder, game, origin, root))

    for slug, folder in _home_workspaces(home):
        add(slug, folder, "registry", home)
    for item in extra or []:
        path = Path(item)
        if _game_json(path) is not None:
            add(_game_json(path).get("slug") or path.name, path, "extra", path.parent)
        elif path.is_dir():
            pairs = _home_workspaces(path)
            if not pairs:
                warnings.append(f"{path}: no workspace in it")
            for slug, folder in pairs:
                add(slug, folder, "extra", path)
        else:
            warnings.append(f"{path}: not found")
    for item in legacy or []:
        root = Path(item)
        if not root.is_dir():
            warnings.append(f"{root}: legacy home not found")
            continue
        if norm_path(root) == norm_path(home):
            continue
        for game_json in sorted(root.glob("*/game.json")):
            folder = game_json.parent
            if not folder.name.startswith("_"):
                add((_game_json(folder) or {}).get("slug") or folder.name, folder, "legacy", root)
    if games:
        wanted = {norm_name(g) for g in games}
        found = [w for w in found if {norm_name(w.slug), norm_name(w.key), norm_name(w.name)} & wanted]
    return found, warnings


def resolve_games(conn: sqlite3.Connection, names: list[str], *, analysis_only: bool = False) -> list[sqlite3.Row]:
    """Rows of ``games`` for user-given names (key, slug or loose name: `repo` finds `r-e-p-o`); all when empty.
    Unknown names raise with the list of what the library has."""
    rows = conn.execute("SELECT * FROM games ORDER BY key").fetchall()
    if analysis_only:
        rows = [r for r in rows if r["origin"] != "legacy"]
    if not names:
        return rows
    out, missing = [], []
    for name in names:
        exact = [r for r in rows if name in (r["key"], r["slug"])]
        loose = exact or [r for r in rows if norm_name(name) in (norm_name(r["key"]), norm_name(r["slug"]), norm_name(r["name"]))]
        if not loose:
            missing.append(name)
        for r in loose:
            if r["key"] not in {o["key"] for o in out}:
                out.append(r)
    if missing:
        known = ", ".join(r["key"] for r in rows) or "(empty)"
        raise GsError(f"not in the library: {', '.join(missing)} (games: {known}; `gst lib build` reads the registry, "
                      "`--workspace <dir>` adds another)")
    return out
