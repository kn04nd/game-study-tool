"""``gst jobs estimate``: how many jobs this machine can run at once and how long the rest of a study takes.

- Machine: logical CPUs, how busy they are right now (a short sample), physical RAM, free disk of the workspace.
- Per job: expected seconds from finished jobs. Model per extractor: ``overhead + rate * size`` fitted on the done
  jobs of this workspace, then of the other workspaces under GS_HOME, then of the same category; with no
  history, a guess (flagged). Running jobs count with what is left of their estimate.
- Parallel jobs: the CPU limit (idle cores minus one for the system) and the RAM limit under the scheduler's
  own rule (``budget.job_weight``: measured peak of the extractor x margin, else the default of its kind;
  ``budget.ram_fits``).
- ETA: a simulation of the scheduler (slots, one heavy job at a time, ``after`` / ``after_earlier_stages``,
  the RAM rule) for a few ``--jobs`` values.
- Decision: ``auto`` (take the recommendation) or ``ask`` (show the table to the user), with the reasons.
- Only the units of the last plan (``jobs/plan.json``) count: pending jobs of an earlier plan never run.
- ``level_estimates``: the time of each asset level (``pipeline.levels``) before the first run, from one
  read-only plan at ``full`` (map containers flagged), for ``gst study --dry-run`` and the asset-level question.

Read-only: opens jobs.db files read-only and writes nothing.
"""

from __future__ import annotations

import os
import sqlite3
import statistics
import time
from pathlib import Path

from gst.pipeline import budget, levels

BIG = 64 << 20            # samples at least this large fit the per-byte rate
GUESS_OVERHEAD_S = 5.0    # no history at all: a few seconds per job ...
GUESS_RATE_S_PER_GB = 120.0  # ... plus two minutes per GB of input
SAMPLE_S = 1.0            # CPU sample length
ASK_SAVING_S = 15 * 60    # going above the default --jobs saves at least this much: the user decides
ASK_OTHER_LOAD = 0.25     # other programs use more than this share of the CPUs
ASK_UNKNOWN_SHARE = 0.5   # more than half of the remaining time is a guess


# --- machine ------------------------------------------------------------------------------------------

def cpu_busy(sample_s: float = SAMPLE_S) -> float | None:
    """Share of all CPUs busy over ``sample_s`` seconds (0..1), None if unknown."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        def times():
            idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            as_int = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime  # noqa: E731
            return as_int(idle), as_int(kernel), as_int(user)

        a = times()
        time.sleep(sample_s)
        b = times()
        if not a or not b:
            return None
        idle, total = b[0] - a[0], (b[1] - a[1]) + (b[2] - a[2])  # kernel time includes idle time
        return max(0.0, min(1.0, 1 - idle / total)) if total > 0 else None
    try:
        return max(0.0, min(1.0, os.getloadavg()[0] / (os.cpu_count() or 1)))
    except (AttributeError, OSError):
        return None


def drive_kind(path: Path) -> str:
    """'fixed', 'removable', 'network', ... for the drive holding ``path`` (Windows), else 'unknown'."""
    if os.name != "nt":
        return "unknown"
    import ctypes

    root = Path(path).resolve().anchor or str(path)
    kinds = {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}
    return kinds.get(ctypes.windll.kernel32.GetDriveTypeW(root), "unknown")


def machine(ws: Path, *, sample_s: float = SAMPLE_S) -> dict:
    total, avail = budget.memory()
    return {"cpus": os.cpu_count() or 1, "cpu_busy": cpu_busy(sample_s), "ram_total": total, "ram_available": avail,
            "disk_free": budget.disk_free(ws), "drive": drive_kind(ws)}


# --- history ------------------------------------------------------------------------------------------

def _connect_ro(path: Path, *, immutable: bool = False) -> sqlite3.Connection | None:
    """Read-only connection that still sees a running study's WAL; falls back to ``immutable`` (may be stale).
    ``immutable``: only that one, which writes no -wal/-shm side file (``gst study --dry-run`` writes nothing)."""
    for query in (("immutable=1",) if immutable else ("mode=ro", "immutable=1")):
        try:
            con = sqlite3.connect(f"{Path(path).resolve().as_uri()}?{query}", uri=True)
            con.row_factory = sqlite3.Row
            con.execute("SELECT 1 FROM jobs LIMIT 1")
            return con
        except sqlite3.Error:
            continue
    return None


def _columns(con: sqlite3.Connection) -> set[str]:
    return {r[1] for r in con.execute("PRAGMA table_info(jobs)")}


def read_jobs(db_file: Path, *, immutable: bool = False) -> list[dict]:
    con = _connect_ro(db_file, immutable=immutable)
    if con is None:
        return []
    try:
        cols = ["id", "extractor", "unit_id", "category", "resource", "stage", "seq", "size", "status", "duration_s",
                "started_at", "pid"]
        cols += ["peak_ram"] if "peak_ram" in _columns(con) else []
        rows = [dict(r) for r in con.execute(f"SELECT {', '.join(cols)} FROM jobs ORDER BY stage, seq, id")]
    finally:
        con.close()
    for r in rows:
        r.setdefault("peak_ram", None)
        r["size"] = r.get("size") or 0
    return rows


def other_histories(ws: Path, home: Path | None, *, immutable: bool = False) -> list[dict]:
    """Done jobs of the other workspaces under GS_HOME (their timing teaches the estimate)."""
    out: list[dict] = []
    if home is None or not Path(home).is_dir():
        return out
    me = os.path.normcase(str(Path(ws).resolve()))
    for child in sorted(Path(home).iterdir()):
        db_file = child / "jobs" / "jobs.db"
        if not db_file.is_file() or os.path.normcase(str(child.resolve())) == me:
            continue
        out += [r for r in read_jobs(db_file, immutable=immutable)
                if r["status"] == "done" and r.get("duration_s") is not None]
    return out


class Model:
    """Expected seconds of one job: ``overhead + rate * size`` per extractor (fallback: category, guess)."""

    def __init__(self, own: list[dict], others: list[dict]):
        # a job planned again (pending, or running now) keeps the duration of its last finished run: still history
        timed = ("done", "pending", "running", "paused-disk")
        done = lambda rows: [r for r in rows if r["status"] in timed and r.get("duration_s") is not None]  # noqa: E731
        self.sources = (("this workspace", done(own)), ("other workspaces", done(others)))

    @staticmethod
    def _fit(samples: list[dict]) -> tuple[float, float] | None:
        if not samples:
            return None
        small = [r["duration_s"] for r in samples if r["size"] < BIG]
        overhead = statistics.median(small) if small else 0.0
        big = [r for r in samples if r["size"] >= BIG]
        if big:
            rate = statistics.median(max(0.0, r["duration_s"] - overhead) / r["size"] for r in big)
        else:  # only small samples: no per-byte signal, the median duration is the estimate
            rate = 0.0
            overhead = statistics.median(r["duration_s"] for r in samples)
        return overhead, rate

    def estimate(self, job: dict) -> tuple[float, str]:
        size = job.get("size") or 0
        for key, label in (("extractor", ""), ("category", "category ")):
            value = job.get(key)
            if not value or (key == "category" and size < BIG):
                continue  # a category's per-byte rate says nothing about small units (analyses, finalize steps)
            for where, rows in self.sources:
                samples = [r for r in rows if r.get(key) == value and (key == "extractor" or r["size"] >= BIG)]
                fit = self._fit(samples)
                if fit is None:
                    continue
                if key == "extractor" and size >= BIG and fit[1] == 0.0:
                    # history only has small units of this extractor: borrow the category's per-byte rate
                    cat = self._fit([r for r in rows if r.get("category") == job.get("category") and r["size"] >= BIG])
                    if cat and cat[1] > 0:
                        return fit[0] + cat[1] * size, f"{label}{where} ({len(samples)}) + category rate"
                return fit[0] + fit[1] * size, f"{label}{where} ({len(samples)})"
        for where, rows in self.sources:  # any extractor's per-byte rate on big units here beats a constant
            rates = [r["duration_s"] / r["size"] for r in rows if r["size"] >= BIG and r["duration_s"] > 0]
            if rates and size >= BIG:
                return GUESS_OVERHEAD_S + statistics.median(rates) * size, f"guess (median rate of {where})"
        return GUESS_OVERHEAD_S + GUESS_RATE_S_PER_GB * size / (1 << 30), "guess (no history)"


# --- scheduler simulation -----------------------------------------------------------------------------

def _rules(extractor: str) -> tuple[tuple[str, ...], bool]:
    from gst.extractors import registry

    try:
        cls = registry.get(extractor)
    except KeyError:
        return (), False
    return tuple(cls.after), bool(getattr(cls, "after_earlier_stages", False))


def _stages(jobs) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {t: set() for t in levels.TRACKS}
    for j in jobs:
        out[levels.track_of(j["extractor"])].add(j.get("stage") or 0)
    return out


def simulate(running: list[dict], pending: list[dict], slots: int, ram_available: int, *, weight=None) -> float:
    """Seconds until every job finished, scheduling like ``Runner.run``: jobs in (stage, seq) order, at most
    ``slots`` at once, one ``heavy``, ``after`` / ``after_earlier_stages`` waits (same track only), the RAM rule.
    ``running`` jobs carry ``left_s``; pending ones ``est_s``."""
    weight = weight or (lambda j: j.get("ram_weight") or budget.ram_weight(j.get("size") or 0, j.get("resource") or "cpu"))
    rules = {j["extractor"]: _rules(j["extractor"]) for j in pending}
    clock = 0.0
    active = [{**j, "end": max(0.0, j["left_s"]), "w": weight(j)} for j in running]
    queue = [dict(j) for j in pending]
    while queue or active:
        live_ext = {j["extractor"] for j in queue} | {j["extractor"] for j in active}
        live_stages = _stages([*queue, *active])
        started = False
        for job in list(queue):
            if len(active) >= slots:
                break
            if job.get("resource") == "heavy" and any(a.get("resource") == "heavy" for a in active):
                continue
            after, after_stages = rules.get(job["extractor"], ((), False))
            if after_stages and any(s < (job.get("stage") or 0) for s in live_stages[levels.track_of(job["extractor"])]):
                continue
            if any(dep in live_ext and dep != job["extractor"] for dep in after):
                continue
            w = weight(job)
            if active and not budget.ram_fits(ram_available, sum(a["w"] for a in active), w):
                break
            queue.remove(job)
            active.append({**job, "end": clock + job["est_s"], "w": w})
            started = True
        if not active:
            if not started:  # nothing can start (a dependency outside the queue): count the rest serially
                return clock + sum(j["est_s"] for j in queue)
            continue
        active.sort(key=lambda a: a["end"])
        done = active.pop(0)
        clock = max(clock, done["end"])
    return clock


# --- the estimate -------------------------------------------------------------------------------------

def default_slots(cpus: int) -> int:
    """What ``gst study`` uses without --jobs (``Runner.__init__``)."""
    return max(1, min(4, cpus // 2))


def _elapsed(started_at: str | None) -> float:
    if not started_at:
        return 0.0
    try:
        return max(0.0, time.time() - time.mktime(time.strptime(started_at[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        try:
            return max(0.0, time.time() - time.mktime(time.strptime(started_at[:19], "%Y-%m-%d %H:%M:%S")))
        except ValueError:
            return 0.0


def estimate(ws: Path, *, home: Path | None = None, jobs: int | None = None, sample_s: float = SAMPLE_S,
             mach: dict | None = None, study_running: bool | None = None) -> dict:
    from gst.pipeline.jobs import StudyLock, db_path

    ws = Path(ws)
    mach = mach or machine(ws, sample_s=sample_s)
    own = read_jobs(db_path(ws))
    others = other_histories(ws, home)
    model = Model(own, others)
    ram_model = budget.RamModel(own)  # the scheduler learns RAM from this workspace only
    if study_running is None:
        study_running = StudyLock(ws).holder() is not None
    running_rows = [r for r in own if r["status"] == "running"] if study_running else []
    pending_rows = [r for r in own if r["status"] == "pending"]
    if not study_running:  # a dead study's "running" jobs go back to pending on the next gst study
        pending_rows = [r for r in own if r["status"] in ("running", "pending", "paused-disk")]
    planned = _planned(ws)
    if planned is not None:  # pending jobs outside the last plan never run
        pending_rows = [r for r in pending_rows if (r["extractor"], r["unit_id"]) in planned]

    jobs_out, running, pending = [], [], []
    for r in running_rows + pending_rows:
        est, basis = model.estimate(r)
        item = {"id": r["id"], "extractor": r["extractor"], "unit": r["unit_id"], "size": r["size"],
                "resource": r.get("resource") or "cpu", "stage": r.get("stage") or 0, "seq": r.get("seq") or 0,
                "status": "running" if r in running_rows else "pending", "est_s": round(est, 1), "basis": basis,
                "ram_weight": budget.job_weight(r["size"], r.get("resource") or "cpu", r["extractor"], ram_model,
                                                mach["ram_total"]),
                "ram_measured": bool(ram_model.measured(r["extractor"], r["size"]))}
        if item["status"] == "running":
            elapsed = _elapsed(r.get("started_at"))
            item["elapsed_s"] = round(elapsed, 1)
            item["left_s"] = round(max(est - elapsed, est * 0.1), 1)
            item["ram_used"] = budget.process_tree_ram(r.get("pid"))
            running.append(item)
        else:
            pending.append(item)
        jobs_out.append(item)

    cpus = mach["cpus"]
    busy = mach.get("cpu_busy")
    own_cores = len(running)  # each worker keeps about one core busy
    other_cores = max(0.0, (busy or 0.0) * cpus - own_cores)
    cpu_slots = max(1, int(cpus - other_cores) - 1)
    # RAM: free RAM without this study's workers; jobs reserve their weight and the scheduler keeps keep_free
    avail = mach["ram_available"] + sum(j.get("ram_used", 0) for j in running)
    work = [j for j in jobs_out if j["resource"] != "heavy"]
    # the jobs that dominate the remaining time decide the RAM limit
    longest = sorted(work, key=lambda j: j.get("left_s", j["est_s"]), reverse=True)[: max(3, len(work) // 4)]
    ram_per_job = statistics.median(j["ram_weight"] for j in longest) if longest else budget.RAM_FLOOR
    ram_slots = max(1, int((avail - budget.keep_free(avail)) // max(ram_per_job, 1)))
    unmeasured = [j for j in longest if j["resource"] != "io" and not j["ram_measured"]]
    useful = max(1, len(jobs_out))
    recommended = max(1, min(cpu_slots, ram_slots, useful))
    limiter = "cpu" if recommended == cpu_slots else "ram" if recommended == ram_slots else "jobs-left"

    candidates = sorted({1, 2, 4, default_slots(cpus), recommended, max(1, cpu_slots)} | ({jobs} if jobs else set()))
    eta = {n: round(simulate(running, pending, n, avail)) for n in candidates}

    total_left = sum(j.get("left_s", j["est_s"]) for j in jobs_out)
    guessed = sum(j.get("left_s", j["est_s"]) for j in jobs_out if j["basis"].startswith("guess"))
    reasons_ask, notes = [], []
    base = default_slots(cpus)
    if recommended > base and eta.get(base, 0) - eta.get(recommended, 0) >= ASK_SAVING_S:
        reasons_ask.append(f"--jobs {recommended} saves {_hm(eta[base] - eta[recommended])} over the default {base}, "
                           "but keeps more of the machine busy")
    if busy is not None and other_cores / cpus > ASK_OTHER_LOAD:
        reasons_ask.append(f"other programs keep about {other_cores:.1f} of {cpus} CPUs busy")
    if recommended < base and limiter == "ram":
        reasons_ask.append(f"RAM limits the run to {recommended} jobs at a time (the default is {base})")
    n_guessed = sum(1 for j in jobs_out if j["basis"].startswith("guess"))
    if jobs_out and (guessed / max(total_left, 1) > ASK_UNKNOWN_SHARE or n_guessed / len(jobs_out) > ASK_UNKNOWN_SHARE):
        reasons_ask.append(f"{n_guessed} of {len(jobs_out)} jobs ({_hm(guessed)} of the {_hm(total_left)} left) have no "
                           "history: their time is a guess")
    if mach.get("drive") in ("removable", "network"):
        reasons_ask.append(f"the workspace is on a {mach['drive']} drive: more parallel reads may be risky")
    if unmeasured:
        notes.append(f"{len(unmeasured)} of the longest jobs have no RAM measurement yet: they reserve a default (3x "
                     f"their input; {budget.RAM_UNSIZED_SHARE:.0%} of the RAM for analyses, {budget.RAM_HEAVY_SHARE:.0%} "
                     "for heavy tools) until one job of their extractor finishes in this workspace, then its measured "
                     f"peak x {budget.RAM_MARGIN} (times above assume the defaults)")
    if study_running and running:
        lost = sum(j.get("elapsed_s", 0) for j in running)
        notes.append(f"a study is running: changing --jobs means stopping it; its {len(running)} running jobs restart "
                     f"from zero (about {_hm(lost)} of work lost)")
    if jobs and jobs > recommended:
        notes.append(f"--jobs {jobs} is above what this machine sustains now ({recommended}, limit: {limiter})")
    return {
        "workspace": str(ws), "machine": mach, "study_running": study_running,
        "counts": {"running": len(running), "pending": len(pending)},
        "limits": {"cpu": cpu_slots, "ram": ram_slots, "ram_per_job": ram_per_job, "ram_unmeasured": len(unmeasured),
                   "jobs_left": useful, "default": base, "other_busy_cpus": round(other_cores, 1)},
        "recommended": recommended, "limiter": limiter, "eta_s": eta,
        "left_s": round(total_left), "guessed_s": round(guessed), "guessed_jobs": n_guessed,
        "decision": "ask" if reasons_ask else "auto", "reasons": reasons_ask, "notes": notes,
        "jobs": jobs_out,
    }


def _planned(ws: Path) -> set[tuple[str, str]] | None:
    from gst.common import read_json
    from gst.pipeline.plan import planned_units

    p = read_json(Path(ws) / "jobs" / "plan.json", None)
    return planned_units(p) if isinstance(p, dict) and p.get("extractors") else None


def level_estimates(ws: Path, game: dict, *, options: dict | None = None, home: Path | None = None,
                    jobs: int | None = None, mach: dict | None = None, plan_full: dict | None = None) -> dict:
    """Time of each asset level, from a read-only plan at ``full`` whose units carry a map-container flag:
    ``{"levels": [{level, jobs, in_bytes, left_out_bytes, code_s, assets_s, total_s, guessed_s}], "slots",
    "project"}``. ``code_s``/``assets_s`` simulate each track alone, ``total_s`` both together (they share the
    slots). The levels leave the whole-project export out; ``project`` (Unity, else None) prices it alone:
    ``{in_bytes, est_s, with_maps_bytes, with_maps_s, guessed}`` (without the map containers / with them at full)."""
    from gst.pipeline import plan as plan_mod
    from gst.pipeline.jobs import db_path

    ws = Path(ws)
    mach = mach or machine(ws, sample_s=0)
    own = read_jobs(db_path(ws), immutable=True) if db_path(ws).exists() else []  # writes no -wal/-shm
    model = Model(own, other_histories(ws, home, immutable=True))
    ram_model = budget.RamModel(own)
    if plan_full is None:
        plan_full = plan_mod.build(ws, game, options={**(options or {}), "assets": "full", "unity_project": True,
                                                      "_todo": True}, write=False)
    slots = jobs or default_slots(mach["cpus"])
    items = []
    for e in plan_full["extractors"]:
        for unit_id, size, is_map in e.get("todo") or []:
            job = {"extractor": e["name"], "unit_id": unit_id, "category": e["category"], "resource": e["resource"],
                   "stage": e["stage"], "seq": len(items), "size": size or 0}
            est, basis = model.estimate(job)
            job.update(est_s=est, basis=basis, map=bool(is_map), track=e["track"],
                       ram_weight=budget.job_weight(size or 0, e["resource"], e["name"], ram_model, mach["ram_total"]))
            items.append(job)
    share = _non_map_share(game)
    project = None
    for j in items:
        if j["extractor"] in levels.PROJECT:
            no_maps = _project_bytes(ws, game, j["extractor"], j["size"])
            est, basis = model.estimate({**j, "size": no_maps})
            project = {"in_bytes": no_maps, "est_s": round(est), "with_maps_bytes": j["size"],
                       "with_maps_s": round(j["est_s"]), "guessed": basis.startswith("guess")}
    rows = []
    for level in levels.LEVELS:
        mine = [j for j in items if levels.included(j["extractor"], level, False) and (level == "full" or not j["map"])]
        if level != "full" and share < 1.0:  # asset analyses read only the non-map files below full
            mine = [{**j, "est_s": j["est_s"] * share} if j["track"] == "assets" and j["extractor"].startswith("analysis.")
                    else j for j in mine]
        code = [j for j in mine if j["track"] == "code"]
        assets = [j for j in mine if j["track"] == "assets"]
        sim = lambda js: round(simulate([], js, slots, mach["ram_available"])) if js else 0  # noqa: E731
        left_out = sum(j["size"] for j in items if j["track"] == "assets") - sum(j["size"] for j in assets)
        rows.append({"level": level, "jobs": len(assets), "in_bytes": sum(j["size"] for j in assets),
                     "left_out_bytes": left_out, "code_s": sim(code), "assets_s": sim(assets), "total_s": sim(mine),
                     "guessed_s": round(sum(j["est_s"] for j in mine if j["basis"].startswith("guess")))})
    return {"levels": rows, "slots": slots, "code_jobs": sum(1 for j in items if j["track"] == "code"),
            "project": project}


def _project_bytes(ws: Path, game: dict, name: str, fallback: int) -> int:
    """Input bytes of the project export below ``full`` (its own discover, without the map containers)."""
    from gst.extractors import registry
    from gst.pipeline.worker import context_for

    try:
        ctx = context_for(ws, game, {"assets": "normal"})
        return sum(u.size for u in registry.get(name)().discover(ctx))
    except Exception:
        return fallback


def _non_map_share(game: dict) -> float:
    """Share of the Unity data bytes outside map containers (1.0 for other engines or without map containers):
    the history of an asset analysis comes from runs that read everything, the levels below full read this share."""
    if not str((game.get("engine") or {}).get("key") or "").startswith("unity") or not game.get("install_dir"):
        return 1.0
    from gst.engines.unity import loader

    total = maps = 0
    try:
        for rel, _kind, size in loader.iter_unity_files(Path(game["install_dir"])):
            total += size
            maps += size if levels.is_map_container(game["install_dir"], rel) else 0
    except OSError:
        return 1.0
    return (total - maps) / total if total and maps else 1.0


def render_levels(result: dict) -> str:
    """The asset-level table of ``gst study --dry-run`` (English; the skill asks the user in pt-BR)."""
    lines = [f"asset levels (gst study --assets <level>; estimates for --jobs {result['slots']}, code and assets run "
             "side by side):"]
    for r in result["levels"]:
        guess = f"; {_hm(r['guessed_s'])} of it guessed (no history)" if r["guessed_s"] else ""
        lines.append(f"  {r['level']:<7} {r['jobs']:>5} asset jobs {_size(r['in_bytes']):>9}  assets ~{_hm(r['assets_s'])}, "
                     f"code ~{_hm(r['code_s'])}, whole study ~{_hm(r['total_s'])}{guess}")
    pr = result.get("project")
    if pr:
        guess = " (guessed: no history)" if pr["guessed"] else ""
        lines.append(f"unity project (--unity-project yes; AssetRipper, after the other jobs): {_size(pr['in_bytes'])} "
                     f"~{_hm(pr['est_s'])} without the map containers; at --assets full with them "
                     f"{_size(pr['with_maps_bytes'])} ~{_hm(pr['with_maps_s'])}{guess}")
    return "\n".join(lines)


def _hm(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 90:
        return f"{seconds} s"
    h, m = divmod(seconds // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def _size(n: float | None) -> str:
    from gst.common import human_size

    return human_size(n or 0)


def render(result: dict, *, top: int = 15, all_jobs: bool = False) -> str:
    m, lim = result["machine"], result["limits"]
    busy = "?" if m.get("cpu_busy") is None else f"{m['cpu_busy'] * 100:.0f}%"
    lines = [f"machine: {m['cpus']} CPUs ({busy} busy, ~{lim['other_busy_cpus']} by other programs), "
             f"RAM {_size(m['ram_available'])} free of {_size(m['ram_total'])}, disk {_size(m['disk_free'])} free "
             f"({m.get('drive')})",
             f"jobs: {result['counts']['running']} running, {result['counts']['pending']} pending; "
             f"work left ~{_hm(result['left_s'])} of job time"
             + (f" ({result['guessed_jobs']} jobs guessed, no history)" if result['guessed_jobs'] else ""),
             f"parallel limit: CPU {lim['cpu']}, RAM {lim['ram']} (reserves ~{_size(lim['ram_per_job'])}/job"
             + (f", {lim['ram_unmeasured']} long jobs not measured yet" if lim['ram_unmeasured'] else ", measured") + ")"
             + f"; default --jobs {lim['default']}",
             f"recommended: --jobs {result['recommended']} (limit: {result['limiter']})",
             "time to finish: " + ", ".join(f"--jobs {n}: {_hm(s)}" for n, s in result["eta_s"].items())]
    lines.append(f"decision: {result['decision']}")
    lines += [f"  - {r}" for r in result["reasons"]]
    lines += [f"note: {n}" for n in result["notes"]]
    rows = sorted(result["jobs"], key=lambda j: j.get("left_s", j["est_s"]), reverse=True)
    shown = rows if all_jobs else rows[:top]
    if shown:
        lines.append(f"{'longest jobs' if not all_jobs else 'jobs'} (estimate, basis):")
        for j in shown:
            left = f" ({_hm(j['left_s'])} left)" if "left_s" in j else ""
            lines.append(f"  {j['id']:>6} {j['status']:<8} {j['extractor']:<26} {_size(j['size']):>9}  "
                         f"~{_hm(j['est_s'])}{left}  [{j['basis']}]  {j['unit']}")
        if len(rows) > len(shown):
            lines.append(f"  ... {len(rows) - len(shown)} more (--all)")
    return "\n".join(lines)
