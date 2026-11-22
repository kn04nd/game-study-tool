"""Build jobs/plan.json from the recon: the extractors of the game's engine (registry.for_engine) in study
order, every unit they discover upserted into jobs.db, and the estimate (units, input bytes, output,
free disk).

Order: loose files, code (decompile, then index), asset inventory, exports, analyze, heavy.
``--only``/``--skip`` take extractor names, name prefixes (``code`` = ``code.*``) or categories.
``options["assets"]`` (the asset level, ``pipeline.levels``) drops the asset extractors above it and, below
``full``, the units that are map containers; an extractor named exactly in ``--only`` is kept at any level.
Each entry carries its ``track`` (code | assets) and ``unit_ids`` = what this plan may run (the runner runs no
other pending job).
"""

from __future__ import annotations

import time
import traceback
from pathlib import Path

from gst import policy
from gst.common import human_size, write_json
from gst.extractors import registry
from gst.pipeline import budget, levels
from gst.pipeline.jobs import BLOCKED, FAILED, PENDING, JobsDB, fingerprint_of, stat_inputs
from gst.pipeline.worker import FINALIZE, context_for

DISCOVER = "<discover>"  # unit id of the job that records a discover() crash


def matches(cls, patterns: list[str]) -> bool:
    for p in patterns:
        p = p.strip()
        if p and (cls.name == p or cls.name.startswith(p + ".") or cls.category == p):
            return True
    return False


def policy_block(cls, game: dict) -> str | None:
    """Reason when the game's verdict does not allow what this extractor needs, else None."""
    need = cls.policy_need
    if need in ("none", "static"):
        return None
    if need == "decrypt-published-key":
        decision = policy.check(game, "decrypt")
        return None if decision.allowed else f"{decision.reason} (guardrails.md rule {decision.rule})"
    if need == "runtime-offline":
        decision = policy.check(game, "runtime")
        return None if decision.allowed else f"{decision.reason} (guardrails.md rule {decision.rule})"
    return None


def selected(engine_key: str | None, only: list[str] | None = None, skip: list[str] | None = None,
             level: str | None = None) -> list[type]:
    classes = registry.for_engine(engine_key)
    if only:
        classes = [c for c in classes if matches(c, only)]
    if skip:
        classes = [c for c in classes if not matches(c, skip)]
    if level is not None:
        named = {p.strip() for p in only or ()}
        classes = [c for c in classes if c.name in named or levels.included(c.name, level)]
    order = {c.name: i for i, c in enumerate(classes)}
    return sorted(classes, key=lambda c: (c.stage, order[c.name]))


def build(ws: Path, game: dict, *, options: dict | None = None, write: bool = True, log=None) -> dict:
    """Discover every unit and (unless ``write`` is False: --dry-run) upsert them into jobs.db and write
    jobs/plan.json. ``write=False`` writes nothing (an existing jobs.db is only read)."""
    options = dict(options or {})
    t0 = time.time()
    engine_key = (game.get("engine") or {}).get("key")
    buildid = str(game.get("buildid") or "?")
    install = Path(game["install_dir"])
    force = set(options.get("force") or [])
    retry = bool(options.get("retry_failed"))
    level = levels.check(options.get("assets"))
    collect = bool(options.get("_todo"))  # estimate mode: list the units that would run (with a map flag)
    plan = {"workspace": str(ws), "game": game.get("name"), "engine": engine_key, "buildid": buildid,
            "verdict": game.get("verdict"), "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "options": options,
            "extractors": []}
    db = JobsDB(ws / "jobs" / "jobs.db") if write else None
    readonly = None
    if not write and (ws / "jobs" / "jobs.db").exists():
        readonly = JobsDB.open_readonly(ws / "jobs" / "jobs.db")
    seq = 0
    try:
        if db is not None:
            db.con.execute("BEGIN")
        for cls in selected(engine_key, options.get("only"), options.get("skip"), level):
            ext = cls()
            ctx = context_for(ws, game, options, out_subdir=getattr(cls, "out_subdir", ""))
            unit_fps: list[str] = []
            track = levels.track_of(cls.name)
            upstream = getattr(cls, "after_earlier_stages", False) and any(
                e["to_run"] for e in plan["extractors"] if e["stage"] < cls.stage and e["track"] == track)
            entry = {"name": cls.name, "category": cls.category, "resource": cls.resource, "stage": cls.stage,
                     "policy_need": cls.policy_need, "after": list(cls.after), "track": track,
                     "level": levels.min_level(cls.name), "units": 0, "in_bytes": 0, "est_out": 0,
                     "to_run": 0, "already_done": 0, "blocked": 0, "left_out": 0, "left_out_bytes": 0, "unit_ids": []}
            if collect:
                entry["todo"] = []
            plan["extractors"].append(entry)
            try:
                if not ext.applies(ctx):
                    entry["applies"] = False
                    continue
            except Exception:
                entry["applies"] = False
                entry["error"] = traceback.format_exc()
                continue
            entry["applies"] = True
            blocked = policy_block(cls, game)
            try:
                for unit in ext.discover(ctx):
                    if levels.unit_left_out(cls.name, unit.relpaths, level, install):
                        entry["left_out"] += 1
                        entry["left_out_bytes"] += unit.size
                        continue
                    seq += 1
                    inputs = stat_inputs(install, unit.relpaths)
                    fp = unit.fingerprint or fingerprint_of(inputs, buildid)
                    unit_fps.append(fp)
                    size = unit.size  # the contract: input bytes as the extractor counts them
                    entry["units"] += 1
                    entry["in_bytes"] += size
                    entry["unit_ids"].append(unit.id)
                    status = BLOCKED if blocked else PENDING
                    if db is not None:
                        action = db.upsert(extractor=cls.name, unit_id=unit.id, relpaths=unit.relpaths, inputs=inputs,
                                           fingerprint=fp, buildid=buildid, category=cls.category, resource=cls.resource,
                                           stage=cls.stage, seq=seq, size=size, meta=unit.meta, retry_failed=retry,
                                           force=upstream or cls.name in force or any(cls.name.startswith(f + ".") for f in force),
                                           status=status, error=blocked)
                    else:
                        prev = readonly.find(cls.name, unit.id) if readonly else None
                        action = ("kept-done" if prev and prev["status"] in ("done", "skipped") and prev["fingerprint"] == fp
                                  and cls.name not in force and not upstream else "would-run")
                    if blocked:
                        entry["blocked"] += 1
                    elif action.startswith("kept-done") or action.startswith("kept-skipped"):
                        entry["already_done"] += 1
                    elif action in ("new", "changed", "forced", "retry", "resumed", "would-run"):
                        entry["to_run"] += 1
                        entry["est_out"] += budget.estimate_out(cls.category, size, cls.name)
                        if collect:
                            entry["todo"].append([unit.id, size, levels.unit_left_out(cls.name, unit.relpaths, "normal",
                                                                                      install)])
            except Exception:
                err = traceback.format_exc()
                entry["error"] = err
                if db is not None:
                    seq += 1
                    db.upsert(extractor=cls.name, unit_id=DISCOVER, relpaths=[], inputs=[], fingerprint=f"discover-failed-{time.time()}",
                              buildid=buildid, category=cls.category, resource=cls.resource, stage=cls.stage, seq=seq, size=0,
                              meta={}, status=FAILED, error=f"discover() failed:\n{err}")
            if blocked:
                entry["blocked_reason"] = blocked
            elif unit_fps and getattr(cls, "has_finalize", lambda: False)():
                done_before = entry["to_run"]
                seq = _plan_finalize(db, readonly, cls, entry, unit_fps, buildid, seq, force)
                if collect and entry["to_run"] > done_before:
                    entry["todo"].append([FINALIZE, 0, False])
        if db is not None:
            db.con.execute("COMMIT")
    finally:
        if db is not None:
            db.close()
        if readonly is not None:
            readonly.close()
    ex = plan["extractors"]
    plan["totals"] = {"units": sum(e["units"] for e in ex), "in_bytes": sum(e["in_bytes"] for e in ex),
                      "to_run": sum(e["to_run"] for e in ex), "already_done": sum(e["already_done"] for e in ex),
                      "blocked": sum(e["blocked"] for e in ex), "est_out": sum(e["est_out"] for e in ex),
                      "left_out": sum(e["left_out"] for e in ex), "left_out_bytes": sum(e["left_out_bytes"] for e in ex),
                      "assets": level,
                      "disk_free": budget.disk_free(ws), "min_free": options.get("min_free")}
    plan["seconds"] = round(time.time() - t0, 1)
    if write:
        write_json(ws / "jobs" / "plan.json", plan)
    return plan


def _plan_finalize(db, readonly, cls, entry: dict, unit_fps: list[str], buildid: str, seq: int, force: set) -> int:
    """The ``<finalize>`` job of an extractor with ``finalize``: last in its seq, fingerprint = all its units'
    fingerprints, and rerun whenever any of its units runs in this plan (a retry keeps the fingerprint)."""
    seq += 1
    fp = fingerprint_of([[f] for f in unit_fps], buildid, "finalize")
    rerun = entry["to_run"] > 0 or cls.name in force or any(cls.name.startswith(f + ".") for f in force)
    entry["finalize"] = True
    if db is not None:
        action = db.upsert(extractor=cls.name, unit_id=FINALIZE, relpaths=[], inputs=[], fingerprint=fp, buildid=buildid,
                           category=cls.category, resource=cls.resource, stage=cls.stage, seq=seq, size=0,
                           meta={"finalize": True}, force=rerun)
    else:
        prev = readonly.find(cls.name, FINALIZE) if readonly else None
        action = "kept-done" if prev and prev["status"] in ("done", "skipped") and prev["fingerprint"] == fp and not rerun             else "would-run"
    entry["units"] += 1
    entry["unit_ids"].append(FINALIZE)
    if action.startswith("kept-done") or action.startswith("kept-skipped"):
        entry["already_done"] += 1
    elif action in ("new", "changed", "forced", "retry", "resumed", "would-run"):
        entry["to_run"] += 1
    return seq


def planned_units(plan: dict) -> set[tuple[str, str]]:
    """``(extractor, unit_id)`` of every unit this plan covers: the runner runs no other pending job."""
    return {(e["name"], u) for e in plan.get("extractors") or [] for u in e.get("unit_ids") or []}


def render(plan: dict) -> str:
    t = plan["totals"]
    lines = [f"plan for {plan['game']} ({plan['engine']}, build {plan['buildid']}, verdict {plan['verdict']})"
             + (f", assets {t['assets']}" if t.get("assets") else "") + ":"]
    for e in plan["extractors"]:
        if not e.get("applies"):
            lines.append(f"  {e['name']:<20} -  (does not apply{': ' + e['error'].strip().splitlines()[-1] if e.get('error') else ''})")
            continue
        extra = f", {e['already_done']} done" if e["already_done"] else ""
        extra += f", {e['blocked']} blocked-policy" if e["blocked"] else ""
        extra += f", {e['left_out']} map containers left out" if e.get("left_out") else ""
        extra += "  DISCOVER FAILED" if e.get("error") else ""
        lines.append(f"  {e['name']:<20} {e['units']:>6} units  {human_size(e['in_bytes']):>9} in  "
                     f"{e['to_run']:>6} to run{extra}  (~{human_size(e['est_out'])} out)")
    lines.append(f"total: {t['units']} units, {human_size(t['in_bytes'])} input, {t['to_run']} to run, {t['already_done']} already done; "
                 f"estimated output ~{human_size(t['est_out'])}; free disk {human_size(t['disk_free'])}"
                 + (f" (pauses below {human_size(t['min_free'])})" if t.get("min_free") else ""))
    if t.get("left_out"):
        lines.append(f"left out at assets {t.get('assets')}: {t['left_out']} map-container units "
                     f"({human_size(t['left_out_bytes'])}); --assets full includes them")
    return "\n".join(lines)
