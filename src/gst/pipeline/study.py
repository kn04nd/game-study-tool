"""``gst study <folder|slug>``: resolve or create the workspace, recon when missing or when the build
changed (a new build gets a sibling workspace ``<slug>@<buildid>``), plan, print the estimate, run,
write COVERAGE.md.

Asset level (``pipeline.levels``): ``options["assets"]`` or the workspace's ``assets_level`` (game.json, saved by
the first run that chose one). A real run whose plan has asset extractors and no level raises
:class:`AssetLevelNeeded` with the estimate of each level: the user picks one (``--assets``). ``--dry-run``
always returns that estimate (``levels``). Unity: the whole-project export (AssetRipper) is a separate yes/no
choice (``options["unity_project"]`` or game.json ``unity_project``), asked the same way."""

from __future__ import annotations

import time
from pathlib import Path

from gst import config, workspace
from gst.common import GsError, norm_path, read_json, slugify


def _workspaces(home: Path):
    if not home.is_dir():
        return
    for child in sorted(home.iterdir()):
        game = read_json(child / config.WORKSPACE_MARKER, None) if child.is_dir() else None
        if isinstance(game, dict) and (game.get("gs_workspace") or game.get("gst_workspace")):
            yield child, game


def find_for_install(install: Path, home: Path) -> list[tuple[Path, dict]]:
    key = norm_path(install)
    return [(ws, g) for ws, g in _workspaces(home) if norm_path(g.get("install_dir") or "") == key]


def _current_build(game_entry: dict) -> str | None:
    from gst.recon.stores import build_id  # names the sibling workspace: no path separators from a store string

    return build_id(game_entry.get("buildid"))


def ensure_v2(ws: Path, game: dict) -> dict:
    """Additive v2 markers: ``gst_workspace: 2`` plus jobs/ and analysis/ (the v1 layout stays)."""
    for sub in ("jobs", "analysis", "logs"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    if game.get("gst_workspace") != 2:
        game["gst_workspace"] = 2
        workspace.save(ws, game)
    return game


def recon_into(ws: Path, game_entry: dict, *, deep: bool = False) -> dict:
    from gst import guard
    from gst.recon import scan

    report = scan.run_recon(game_entry, deep=deep)
    scan.write_outputs(report, ws / "recon")
    meta = workspace.load(ws)
    meta.update(engine=workspace.engine_meta(report), verdict=report["verdict"], protections=report["protections"],
                protected_modules=report["protected_modules"], recon_at=report["scanned_at"],
                buildid=game_entry.get("buildid") or meta.get("buildid"))
    workspace.save(ws, meta)
    if not meta["slug"].startswith("_"):
        guard.save_registry_entry(meta["slug"], workspace.registry_entry(meta, report, ws))
    return meta


def _recon_meta(game: dict, entry: dict, report: dict) -> dict:
    """game.json after a recon, in memory (what ``recon_into`` saves)."""
    return {**game, "engine": workspace.engine_meta(report), "verdict": report["verdict"],
            "protections": report["protections"], "protected_modules": report["protected_modules"],
            "recon_at": report["scanned_at"], "buildid": entry.get("buildid") or game.get("buildid")}


def resolve(target: str, *, deep: bool = False, say=lambda text: None, dry_run: bool = False) -> tuple[Path, dict]:
    """Workspace for an install folder or a slug, created/reconned as needed.

    ``dry_run``: nothing is written anywhere (no workspace, sibling ``<slug>@<buildid>``, folder, recon file or
    registry entry). A missing workspace or recon is computed in memory; the returned game carries
    ``_dry_run`` = what a real run would create (``{"create": <path>, "sibling_of": <slug>|None, "recon": bool}``)."""
    from gst.recon import scan, stores

    home = config.gs_home()
    p = Path(target.strip().strip('"'))
    if p.is_dir():
        entry = stores.resolve_game(str(p))
        existing = find_for_install(p, home)
    else:
        ws = workspace.resolve(target)
        game = workspace.load(ws)
        entry = {"name": game.get("name"), "store": game.get("store"), "appid": game.get("appid"),
                 "buildid": game.get("buildid"), "path": game["install_dir"]}
        try:  # the store knows the installed build now
            fresh = stores.resolve_game(game["install_dir"])
            entry["buildid"] = fresh.get("buildid") or entry["buildid"]
        except GsError:
            pass
        existing = find_for_install(Path(game["install_dir"]), home)
    build = _current_build(entry)
    base_slug = slugify(entry.get("name") or Path(entry["path"]).name)
    same = [(ws, g) for ws, g in existing if build is None or str(g.get("buildid") or "") in ("", build)]
    if same:
        ws, game = same[0]
        if not (ws / "recon" / "recon.json").exists():
            say(f"recon of {entry.get('name')} (missing in {ws.name})" + (" (dry run: in memory)" if dry_run else ""))
            if dry_run:
                return ws, {**_recon_meta(game, entry, scan.run_recon(entry, deep=deep)),
                            "_dry_run": {"create": None, "sibling_of": None, "recon": True}}
            game = recon_into(ws, entry, deep=deep)
        if dry_run:
            return ws, game
        return ws, ensure_v2(ws, game)
    slug = base_slug if not existing else f"{base_slug}@{build}"
    if existing:
        say(f"new build {build} (workspace {existing[0][0].name} has {existing[0][1].get('buildid')}): "
            + (f"a real run creates the sibling workspace {slug}" if dry_run else f"sibling workspace {slug}"))
    report_target = home / slug
    say(f"recon of {entry.get('name')} -> {report_target}" + (" (dry run: in memory, nothing created)" if dry_run else ""))
    report = scan.run_recon(entry, deep=deep)
    if dry_run:
        meta = workspace.new_meta(entry, report, slug, report_target)
        meta["_dry_run"] = {"create": str(report_target), "sibling_of": existing[0][0].name if existing else None,
                            "recon": True}
        return report_target, meta
    ws = workspace.create(entry, report, slug, home=home)
    scan.write_outputs(report, ws / "recon")
    return ws, ensure_v2(ws, workspace.load(ws))


class AssetLevelNeeded(GsError):
    """A choice is missing for this workspace: the asset level and/or (Unity) the whole project export.
    ``missing`` = ["assets", "unity_project"] subset; ``estimates`` = ``capacity.level_estimates``."""

    def __init__(self, ws: Path, estimates: dict, missing: tuple[str, ...] = ("assets",)):
        from gst.pipeline import capacity

        self.workspace = ws
        self.estimates = estimates
        self.missing = list(missing)
        asks = []
        if "assets" in missing:
            asks.append("choose how much of the assets to study: gst study <game> --assets none|basic|normal|full")
        if "unity_project" in missing:
            asks.append("choose whether to export the whole Unity project (AssetRipper): --unity-project yes|no")
        super().__init__("\n".join(asks) + "\n" + capacity.render_levels(estimates))


def needs_level(game: dict, options: dict) -> bool:
    from gst.pipeline import levels, plan

    classes = plan.selected((game.get("engine") or {}).get("key"), options.get("only"), options.get("skip"))
    return any(levels.track_of(c.name) == "assets" for c in classes)


def needs_project(game: dict, options: dict) -> bool:
    """Does the plan have the whole-project export (Unity) to say yes or no to?"""
    from gst.pipeline import levels, plan

    classes = plan.selected((game.get("engine") or {}).get("key"), options.get("only"), options.get("skip"))
    return any(c.name in levels.PROJECT for c in classes)


def run(target: str, *, options: dict, dry_run: bool = False, jobs: int | None = None, timeout: float | None = None,
        min_free: int | None = None, quiet: bool = False) -> dict:
    from gst.pipeline import capacity, coverage, levels, plan
    from gst.pipeline.jobs import JobsDB, StudyLock
    from gst.pipeline.progress import Progress
    from gst.pipeline.runner import Runner
    from gst.pipeline.worker import write_options

    t0 = time.time()
    progress = None

    def say(text):
        if not quiet:
            import sys

            sys.stderr.write(f"gst: {text}\n")

    ws, game = resolve(target, say=say, dry_run=dry_run)
    level = levels.check(options.get("assets")) or levels.check(game.get("assets_level"))
    project = levels.check_project(options.get("unity_project"))
    if project is None:
        project = levels.check_project(game.get("unity_project"))
    options = {**options, "assets": level, "unity_project": project, "min_free": min_free, "timeout": timeout}
    home = config.gs_home()
    if dry_run:  # writes nothing: no workspace, folder, plan.json, jobs.db or registry entry
        would = game.pop("_dry_run", None)
        full = plan.build(ws, game, options={**options, "assets": "full", "unity_project": True, "_todo": True},
                          write=False)
        p = (full if level in (None, "full") and project in (None, True)
             else plan.build(ws, game, options=options, write=False))
        est = capacity.level_estimates(ws, game, options=options, home=home, jobs=jobs, plan_full=full)
        for e in p["extractors"]:
            e.pop("todo", None)
        return {"workspace": str(ws), "dry_run": True, "would_create": would, "plan": p, "assets": level,
                "unity_project": project, "levels": est, "seconds": round(time.time() - t0, 1)}
    missing = tuple(m for m, need in (("assets", level is None and needs_level(game, options)),
                                      ("unity_project", project is None and needs_project(game, options))) if need)
    if missing:
        say(f"choice needed ({', '.join(missing)}): estimating each option")
        raise AssetLevelNeeded(ws, capacity.level_estimates(ws, game, options=options, home=home, jobs=jobs), missing)
    changed = False
    if level is not None and game.get("assets_level") != level:
        game["assets_level"] = level
        changed = True
    if project is not None and game.get("unity_project") is not project:
        game["unity_project"] = project
        changed = True
    if changed:
        workspace.save(ws, game)
    lock = StudyLock(ws)
    lock.acquire()
    try:
        progress = Progress(ws, quiet=quiet)
        with JobsDB(ws / "jobs" / "jobs.db") as db:
            stale = db.recover_stale()
            paused = db.resume_paused()
        if stale or paused:
            progress.event("resume", stale_running=stale, paused_disk=paused)
            say(f"resume: {stale} interrupted jobs and {paused} paused jobs back to pending")
        write_options(ws, options)
        p = plan.build(ws, game, options=options, write=True)
        progress.event("plan", totals=p["totals"], seconds=p["seconds"])
        say(plan.render(p).replace("\n", "\ngst: "))
        runner = Runner(ws, jobs=jobs, timeout=timeout, min_free=min_free, progress=progress,
                        planned=plan.planned_units(p))
        summary = runner.run()
        with JobsDB(ws / "jobs" / "jobs.db") as db:
            cov = coverage.build(ws, db)
        progress.event("end", **summary)
        workspace.journal(ws, f"gst study: {summary['started']} jobs run in {summary['seconds']} s; "
                              + ", ".join(f"{k} {v}" for k, v in sorted(summary["totals"].items())))
        return {"workspace": str(ws), "plan": p["totals"], "run": summary, "coverage": cov["file"],
                "log": str(progress.log_path), "seconds": round(time.time() - t0, 1)}
    finally:
        if progress:
            progress.close()
        lock.release()
