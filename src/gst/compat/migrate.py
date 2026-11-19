"""``gst migrate``: the additive upgrade of gs (v1) workspaces to gst v2, from a ``legacy.plan_migration`` plan.

Writes only inside the workspace and only what is missing: ``jobs/`` and ``analysis/``, one ``done`` job per
legacy output in ``jobs/jobs.db`` (extractor ``legacy.<folder>``, ``tool = "legacy"``, category and file
count for COVERAGE.md), ``jobs/COVERAGE.md``, and ``"gst_workspace": 2`` + ``"gst_migrated"`` in game.json
(the gs keys stay, so the legacy gs keeps reading it). Nothing is moved, renamed or deleted. A second run with
nothing new writes nothing (idempotent). The registry is never touched.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from gst import __version__
from gst.common import human_size, read_json, write_text
from gst.compat.legacy import LEGACY_TOOL, MARKER, STAGE, MigrationPlan

NOTE = "imported by gst migrate from the gs (v1) workspace: produced by the legacy gs, not re-run by gst"


def _save_game_json(ws: Path, game: dict) -> None:
    """game.json through a temporary file + rename: the legacy gs never sees a half-written file."""
    target = ws / MARKER
    tmp = ws / f"{MARKER}.gst-migrate-{os.getpid()}"
    write_text(tmp, json.dumps(game, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, target)


def apply(plan: MigrationPlan) -> dict:
    """Carry out ``plan`` (status would-migrate). Returns what was written."""
    from gst import workspace
    from gst.pipeline import coverage
    from gst.pipeline.jobs import DONE, JobsDB

    ws = plan.workspace
    done: dict = {"created": [], "imported": 0, "updated": 0, "game_json": False, "coverage": None}
    if plan.status != "would-migrate":
        return done
    for sub in plan.create_dirs:
        (ws / sub).mkdir(parents=True, exist_ok=True)
        done["created"].append(sub)
    game = read_json(ws / MARKER, {})
    units = plan.to_import
    if units:
        buildid = str(game.get("buildid")) if game.get("buildid") not in (None, "") else None
        with JobsDB(ws / "jobs" / "jobs.db") as db:
            db.con.execute("BEGIN")
            for seq, unit in enumerate(plan.units, 1):
                if unit.action == "kept":
                    continue
                inputs = [[unit.relpath, unit.bytes, unit.mtime_ns]]
                action = db.upsert(extractor=unit.extractor, unit_id=unit.relpath, relpaths=[unit.relpath], inputs=inputs,
                                   fingerprint=unit.fingerprint(), buildid=buildid, category=unit.category, resource="io",
                                   stage=STAGE[unit.sub], seq=seq, size=unit.bytes,
                                   meta={"legacy": True, "files": unit.files}, status=DONE)
                row = db.find(unit.extractor, unit.relpath)
                db.finish(row["id"], status=DONE, outputs=[unit.relpath], out_bytes=unit.bytes, tool=LEGACY_TOOL,
                          tool_version="gs v1", notes=[NOTE, f"{unit.files} files, {human_size(unit.bytes)}"])
                done["imported" if action == "new" else "updated"] += 1
            db.con.execute("COMMIT")
            done["coverage"] = coverage.build(ws, db)["file"]
    if plan.mark_game_json:
        game["gst_workspace"] = 2
        game.setdefault("gst_migrated", {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                         "from_version": int(game.get("gs_workspace") or 1), "gst": __version__})
        _save_game_json(ws, game)
        done["game_json"] = True
    if units or done["created"]:
        workspace.journal(ws, f"gst migrate: {len(units)} legacy outputs as done(legacy) jobs"
                              + (f", created {', '.join(done['created'])}/" if done["created"] else ""))
    plan.status = "migrated"
    return done


def report_markdown(result: dict) -> str:
    """The --report file: one section per workspace (what was or would be imported, problems, status)."""
    mode = "dry run (nothing written)" if result["dry_run"] else "migration"
    lines = [f"# gst migrate: {mode}", "", f"- GS_HOME: `{result['home']}`", f"- When: {result['at']}",
             f"- Workspaces: {len(result['plans'])} ({', '.join(f'{v} {k}' for k, v in sorted(result['totals'].items()))})",
             ""]
    if result["ignored"]:
        lines += ["## Ignored", ""] + [f"- `{i['name']}`: {i['reason']}" for i in result["ignored"]] + [""]
    for p in result["plans"]:
        lines += [f"## {p['slug']} ({p['status']})", "", f"- Folder: `{p['workspace']}`",
                  f"- Version: v{p['version']}, engine {p['engine'] or '?'}"]
        if p["create_dirs"]:
            lines.append(f"- Creates: {', '.join(d + '/' for d in p['create_dirs'])}")
        if p["mark_game_json"]:
            lines.append('- game.json: adds "gst_workspace": 2 and "gst_migrated" (gs keys unchanged)')
        imp = [u for u in p["units"] if u["action"] != "kept"]
        kept = len(p["units"]) - len(imp)
        lines.append(f"- Imports {len(imp)} outputs as done(legacy): {p['import_files']} files, "
                     f"{human_size(p['import_bytes'])}" + (f"; {kept} already imported" if kept else ""))
        if imp:
            lines += ["", "| output | job | category | files | size | action |", "| --- | --- | --- | --- | --- | --- |"]
            lines += [f"| `{u['relpath']}` | {u['extractor']} | {u['category']} | {u['files']} | {human_size(u['bytes'])} | "
                      f"{u['action']} |" for u in imp]
        if p.get("written"):
            w = p["written"]
            lines.append(f"- Written: {w['imported']} new jobs, {w['updated']} updated, dirs {w['created'] or '-'}, "
                         f"game.json {'updated' if w['game_json'] else 'unchanged'}")
        lines += [f"- Note: {n}" for n in p["notes"]]
        lines += [f"- **Problem**: {pr}" for pr in p["problems"]]
        lines.append("")
    return "\n".join(lines)
