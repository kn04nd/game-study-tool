"""Read workspaces made by the legacy gs (game-study-toolkit): ``game.json`` with ``"gs_workspace": 1``.

Workspace v1 is what gs creates: ``recon/ decomp/ assets/ dumps/ traces/ notes/ index/ logs/`` plus
STUDY/FACTS/ARCHITECTURE.md. Workspace v2 (gst) is additive: the same layout plus ``jobs/`` and
``analysis/`` (see docs/INTERFACES.md), so the legacy gs keeps reading a migrated workspace.

Everything here is read-only: ``scan_home`` sorts the entries of a study root (workspaces, archives, stray
folders), ``plan_migration`` lists what ``gst migrate`` would add to one workspace (the units it would import
as ``done`` jobs with ``tool = "legacy"``, the folders it would create, the problems it sees). The writer is
``gst.compat.migrate``; nothing in this module writes into GS_HOME (an existing jobs.db is opened immutable).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from gst.common import long_path, norm_path, read_json

MARKER = "game.json"
V1_SUBDIRS = ("recon", "decomp", "assets", "dumps", "traces", "notes", "index", "logs")
V2_SUBDIRS = ("jobs", "analysis")
# v1 folders whose contents are extraction outputs, in study order; each top-level entry (recon/ as a whole)
# becomes one done(legacy) job. notes/, logs/, sandbox/ and the documents are the study itself: never imported.
IMPORT_SUBDIRS = ("recon", "decomp", "index", "assets", "dumps", "traces")
STAGE = {"recon": 0, "decomp": 20, "index": 25, "assets": 40, "dumps": 50, "traces": 70}
LEGACY_PREFIX = "legacy."
LEGACY_TOOL = "legacy"
SKIP_NAMES = {".partial", "__pycache__"}
MAX_PATH = 260  # Win32 limit without LongPathsEnabled (gst itself uses the \\?\ prefix)
# Folder/file name words -> COVERAGE.md category of an imported legacy output: a word starts a token of the name
# (tokens split on non-letters/digits: "unreal" is not "rea", "audio_scan.json" is audio); first match wins.
CATEGORY_WORDS = (
    (("audio", "sound", "sfx", "music", "voice", "wwise", "fmod", "bank"), "audio"),
    (("texture", "sprite", "image", "icon"), "texture"),
    (("anim", "clip"), "animation"),
    (("model", "mesh", "character", "glb", "gltf", "fbx"), "mesh"),
    (("map", "level", "terrain"), "map"),
    (("prefab", "scene"), "prefab_scene"),
    (("shader", "material", "particle"), "material_shader"),
    (("font",), "font"),
    (("video", "movie"), "video"),
    (("local", "lang", "i18n", "l10n"), "localization"),
    (("rea", "il2cpp", "decomp"), "code"),
    (("inventory", "data", "config", "ini", "save", "sgs"), "data"),
)


@dataclass
class LegacyWorkspace:
    path: Path
    game: dict
    version: int  # 1 = legacy gs layout, 2 = gst (has jobs/ and analysis/)

    @property
    def slug(self) -> str:
        return self.game.get("slug") or self.path.name

    @property
    def missing_v2(self) -> list[str]:
        return [d for d in V2_SUBDIRS if not (self.path / d).is_dir()]


@dataclass
class LegacyUnit:
    """One top-level output of a v1 workspace (``decomp/cs``, ``assets/inventory.jsonl``, ``recon``)."""
    relpath: str
    sub: str
    files: int
    bytes: int
    mtime_ns: int
    action: str = "new"  # new | changed | kept

    @property
    def extractor(self) -> str:
        return LEGACY_PREFIX + self.sub

    @property
    def category(self) -> str:
        return category_of(self.sub, self.relpath.rsplit("/", 1)[-1])

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps([self.relpath, self.files, self.bytes, self.mtime_ns]).encode()).hexdigest()

    def as_dict(self) -> dict:
        return {"relpath": self.relpath, "extractor": self.extractor, "category": self.category, "files": self.files,
                "bytes": self.bytes, "action": self.action}


@dataclass
class MigrationPlan:
    workspace: Path
    version: int
    slug: str = ""
    engine: str | None = None
    status: str = "up-to-date"  # would-migrate | up-to-date | stray | error (migrate.apply: migrated)
    create_dirs: list[str] = field(default_factory=list)
    units: list[LegacyUnit] = field(default_factory=list)
    mark_game_json: bool = False  # game.json gets "gst_workspace": 2 and "gst_migrated"
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def import_as_legacy_jobs(self) -> dict[str, int]:
        """output folder -> file count of the units a run would import (new or changed)."""
        return {u.relpath: u.files for u in self.to_import}

    @property
    def to_import(self) -> list[LegacyUnit]:
        return [u for u in self.units if u.action != "kept"]

    @property
    def needs_work(self) -> bool:
        return bool(self.create_dirs or self.to_import or self.mark_game_json)

    def as_dict(self) -> dict:
        imp = self.to_import
        return {"workspace": str(self.workspace), "slug": self.slug, "version": self.version, "engine": self.engine,
                "status": self.status, "create_dirs": self.create_dirs, "mark_game_json": self.mark_game_json,
                "import_as_legacy_jobs": self.import_as_legacy_jobs,
                "import_files": sum(u.files for u in imp), "import_bytes": sum(u.bytes for u in imp),
                "units": [u.as_dict() for u in self.units], "problems": self.problems, "notes": self.notes}


def category_of(sub: str, name: str) -> str:
    if sub in ("decomp", "index"):
        return "code"
    if sub == "recon":
        return "other"
    tokens = [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]
    for words, category in CATEGORY_WORDS:
        if any(t.startswith(w) for t in tokens for w in words):
            return category
    return "other"


def workspace_version(game: dict, ws: Path) -> int:
    """2 when the workspace already has the gst additions, else the game.json version (1 for gs)."""
    if isinstance(game.get("gst_workspace"), int):
        return int(game["gst_workspace"])
    if all((ws / d).is_dir() for d in V2_SUBDIRS):
        return 2
    return int(game.get("gs_workspace") or 1)


def is_workspace(ws: Path) -> bool:
    data = read_json(ws / MARKER, None)
    return isinstance(data, dict) and bool(data.get("gs_workspace") or data.get("gst_workspace"))


def open_workspace(ws: Path) -> LegacyWorkspace | None:
    """A v1 (or v2) workspace at ``ws``, or None when ``ws`` holds no gs/gst game.json."""
    data = read_json(ws / MARKER, None)
    if not isinstance(data, dict) or not (data.get("gs_workspace") or data.get("gst_workspace")):
        return None
    return LegacyWorkspace(ws, data, workspace_version(data, ws))


def find_by_slug(slug: str, home: Path) -> Path | None:
    """``home/<slug>`` when it is a workspace, else a registry/game.json match by slug or name."""
    direct = home / slug
    if is_workspace(direct):
        return direct
    wanted = slug.lower()
    try:
        candidates = sorted(p for p in home.iterdir() if p.is_dir())
    except OSError:
        return None
    for folder in candidates:
        data = read_json(folder / MARKER, None)
        if isinstance(data, dict) and (data.get("gs_workspace") or data.get("gst_workspace")):
            if wanted in {str(data.get("slug") or "").lower(), str(data.get("name") or "").lower()}:
                return folder
    return None


# --- the study root ---------------------------------------------------------------------------------

HOME_FILES = {"registry.json": "study registry (gs/gst)", "INDEX.md": "index of the studies (gst notes index)"}


def scan_home(home: Path) -> tuple[list[Path], list[dict]]:
    """Workspaces under ``home`` (sorted) and every other entry with why it is not one (archives, files,
    folders without game.json, ``_library``). Read-only."""
    targets, ignored = [], []
    try:
        entries = sorted(home.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return [], []
    for p in entries:
        if p.is_file():
            if p.suffix.lower() in (".zip", ".7z", ".rar", ".tar", ".gz"):
                reason = "archive (a backup, not a workspace)"
            else:
                reason = HOME_FILES.get(p.name, "file (not a workspace)")
            ignored.append({"name": p.name, "reason": reason})
        elif p.is_dir():
            if (p / MARKER).is_file():
                targets.append(p)  # a broken game.json is reported by plan_migration, not skipped here
            elif p.name == "_library":
                ignored.append({"name": p.name, "reason": "cross-game library (gst lib build)"})
            else:
                ignored.append({"name": p.name, "reason": "folder without game.json (not a workspace)"})
    return targets, ignored


def registry_slugs(home: Path) -> dict[str, str]:
    """slug -> workspace path of ``home/registry.json`` (read-only; empty when there is none)."""
    data = read_json(home / "registry.json", {}) or {}
    games = data.get("games", {}) if isinstance(data, dict) else {}
    return {k: str(v.get("workspace") or "") for k, v in games.items() if isinstance(v, dict)} if isinstance(games, dict) else {}


# --- one workspace ----------------------------------------------------------------------------------

def _scan_path(folder: str) -> str:
    """``folder`` (absolute) with the ``\\\\?\\`` prefix on Windows, so trees deeper than MAX_PATH are listed too."""
    if os.name != "nt" or folder.startswith("\\\\?\\"):
        return folder
    return "\\\\?\\UNC\\" + folder[2:] if folder.startswith("\\\\") else "\\\\?\\" + folder


def tree_stats(path: Path) ->tuple[int, int, int, int, list[str]]:
    """(files, bytes, newest mtime_ns, paths >= MAX_PATH, errors) of a file or a folder tree, without limits."""
    files = size = newest = longp = 0
    errors: list[str] = []
    try:
        st = os.stat(long_path(path))
    except OSError as exc:
        return 0, 0, 0, 0, [f"{path}: {exc}"]
    if not os.path.isdir(long_path(path)):
        return 1, st.st_size, st.st_mtime_ns, int(len(os.path.abspath(path)) >= MAX_PATH), []
    stack = [os.path.abspath(path)]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(_scan_path(folder)) as it:
                for e in it:
                    full = os.path.join(folder, e.name)
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(full)
                            continue
                        est = e.stat(follow_symlinks=False)
                    except OSError as exc:
                        errors.append(f"{full}: {exc}")
                        continue
                    files += 1
                    size += est.st_size
                    newest = max(newest, est.st_mtime_ns)
                    if len(full) >= MAX_PATH:
                        longp += 1
        except OSError as exc:
            errors.append(f"{folder}: {exc}")
    return files, size, newest, longp, errors


def _legacy_rows(ws: Path) -> tuple[dict[str, str], int]:
    """(unit relpath -> fingerprint of the legacy jobs already imported, count of gst-made jobs) from jobs.db,
    opened read-only."""
    db = ws / "jobs" / "jobs.db"
    if not db.is_file():
        return {}, 0
    from gst.pipeline.jobs import JobsDB

    con = JobsDB.open_readonly(db)
    try:
        rows = con.con.execute("SELECT extractor, unit_id, fingerprint FROM jobs").fetchall()
    finally:
        con.close()
    legacy = {r["unit_id"]: r["fingerprint"] for r in rows if str(r["extractor"]).startswith(LEGACY_PREFIX)}
    return legacy, sum(1 for r in rows if not str(r["extractor"]).startswith(LEGACY_PREFIX))


def stray_of(ws: Path, game: dict, home: Path | None) -> str | None:
    """The base workspace when ``ws`` is an empty ``<slug>@<buildid>`` sibling (made by an older gst study
    --dry-run: recon only, no jobs, nothing studied), else None."""
    if "@" not in ws.name:
        return None
    base = (home or ws.parent) / ws.name.split("@", 1)[0]
    base_game = read_json(base / MARKER, None)
    if not isinstance(base_game, dict) or norm_path(base_game.get("install_dir") or "") != norm_path(game.get("install_dir") or "-"):
        return None
    if (ws / "jobs" / "jobs.db").exists():
        return None
    for sub in ("decomp", "assets", "dumps", "traces", "index", "notes", "analysis"):
        if (ws / sub).is_dir() and any((ws / sub).iterdir()):
            return None
    return base.name


def plan_migration(ws: Path, home: Path | None = None) -> MigrationPlan:
    """What ``gst migrate`` would add to a workspace (read-only).

    Each top-level output of the v1 folders becomes a job ``legacy.<folder>`` with status ``done`` and
    ``tool = "legacy"``; nothing is moved or rewritten. Already-imported units with the same fingerprint are
    ``kept``; a workspace where ``gst study`` already ran keeps its jobs (nothing is imported)."""
    found = open_workspace(ws)
    if found is None:
        plan = MigrationPlan(ws, 0, slug=ws.name, status="error")
        why = "game.json is not valid JSON" if (ws / MARKER).is_file() else "no game.json"
        plan.problems.append(f"not a gs/gst workspace: {why}")
        return plan
    game = found.game
    plan = MigrationPlan(ws, found.version, slug=found.slug, engine=(game.get("engine") or {}).get("key"))
    stray = stray_of(ws, game, home)
    if stray:
        plan.status = "stray"
        plan.problems.append(f"empty sibling of {stray} (build {game.get('buildid')}): recon only, no jobs, nothing "
                             f"studied - made by an older gst study --dry-run. migrate leaves it alone; delete the "
                             f"folder (and its registry.json entry) by hand if you don't need it")
        return plan
    if found.slug != ws.name:
        plan.problems.append(f"game.json slug '{found.slug}' differs from the folder name '{ws.name}' "
                             f"(gst status {ws.name} still finds it by folder)")
    install = game.get("install_dir")
    if not install:
        plan.problems.append("game.json has no install_dir: gst study cannot run on this workspace")
    elif not Path(install).is_dir():
        plan.problems.append(f"install folder not found: {install} (the study stays readable; gst study needs the game)")
    if not (game.get("engine") or {}).get("key"):
        plan.problems.append("game.json has no engine key (gst recon <install> --refresh fills it)")
    plan.create_dirs = found.missing_v2
    plan.mark_game_json = game.get("gst_workspace") != 2 or "gst_migrated" not in game
    legacy, gst_jobs = _legacy_rows(ws)
    if gst_jobs:
        plan.mark_game_json = game.get("gst_workspace") != 2
        plan.notes.append(f"gst study already ran here ({gst_jobs} jobs): its jobs stand, nothing is imported")
        plan.status = "would-migrate" if plan.needs_work else "up-to-date"
        return plan
    longp = 0
    for sub in IMPORT_SUBDIRS:
        folder = ws / sub
        if not folder.is_dir():
            continue
        children = [folder] if sub == "recon" else sorted((c for c in folder.iterdir() if c.name not in SKIP_NAMES),
                                                           key=lambda c: c.name.lower())
        for child in children:
            files, size, newest, lp, errors = tree_stats(child)
            longp += lp
            plan.problems += [f"unreadable: {e}" for e in errors]
            if not files:
                continue
            rel = child.relative_to(ws).as_posix()
            unit = LegacyUnit(rel, sub, files, size, newest)
            old = legacy.get(rel)
            unit.action = "new" if old is None else ("kept" if old == unit.fingerprint() else "changed")
            plan.units.append(unit)
    if longp:
        from gst.doctor import long_paths_enabled

        if long_paths_enabled() is not True:
            plan.problems.append(f"{longp} files have paths of {MAX_PATH}+ characters and LongPathsEnabled is off: "
                                 "gst reads them (\\\\?\\ prefix), some third-party tools may not")
    if not plan.units:
        plan.notes.append("no extraction outputs to import (recon/decomp/index/assets/dumps/traces are empty)")
    plan.status = "would-migrate" if plan.needs_work else "up-to-date"
    return plan
