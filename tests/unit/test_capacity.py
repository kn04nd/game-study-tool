"""gst jobs estimate: duration model, scheduler simulation, limits and the auto/ask decision; peak_ram bookkeeping."""

from __future__ import annotations

import json
import sqlite3

import pytest

from gst.pipeline import capacity
from gst.pipeline.jobs import JobsDB
from gst.pipeline.worker import peak_ram

GB = 1 << 30
MB = 1 << 20


def row(extractor, size, duration=None, status="done", category="texture", resource="cpu", stage=50, **kw):
    return {"id": kw.pop("id", 0), "extractor": extractor, "unit_id": kw.pop("unit_id", f"u{size}"), "category": category,
            "resource": resource, "stage": stage, "seq": kw.pop("seq", 0), "size": size, "status": status,
            "duration_s": duration, "started_at": None, "peak_ram": kw.pop("peak_ram", None)}


def test_model_fits_overhead_plus_rate_per_extractor():
    own = [row("unity.textures", 1 * MB, 2.0), row("unity.textures", 2 * MB, 2.0),
           row("unity.textures", 1 * GB, 602.0), row("unity.textures", 2 * GB, 1202.0)]
    model = capacity.Model(own, [])
    est, basis = model.estimate(row("unity.textures", 2 * GB, status="pending"))
    assert est == pytest.approx(1202.0, rel=0.01)
    assert basis == "this workspace (4)"
    small, _ = model.estimate(row("unity.textures", 3 * MB, status="pending"))
    assert small == pytest.approx(2.0 + 600.0 * 3 / 1024, rel=0.01)


def test_model_falls_back_to_other_workspaces_then_category_then_guess():
    others = [row("unity.meshes", 1 * GB, 250.0, category="mesh")]
    own = [row("unity.gltf", 1 * GB, 550.0, category="mesh")]
    model = capacity.Model(own, others)
    assert model.estimate(row("unity.meshes", GB, status="pending", category="mesh"))[1] == "other workspaces (1)"
    est, basis = model.estimate(row("unity.newthing", GB, status="pending", category="mesh"))
    assert basis == "category this workspace (1)" and est == pytest.approx(550.0)
    # a small unit never borrows a category rate: no per-byte signal for analyses and finalize steps
    assert model.estimate(row("analysis.rig", 0, status="pending", category="mesh"))[1].startswith("guess")
    # no history of this extractor or category: the workspace's median per-byte rate, flagged as a guess
    est, basis = model.estimate(row("unity.fonts", GB, status="pending", category="font"))
    assert basis.startswith("guess (median rate") and est > 500


def test_simulate_respects_slots_heavy_and_ram_rule():
    jobs = [{"id": i, "extractor": f"x{i}", "resource": "cpu", "stage": 50, "size": 0, "est_s": 100.0} for i in range(4)]
    big_ram = 64 * GB
    assert capacity.simulate([], jobs, 1, big_ram) == 400
    assert capacity.simulate([], jobs, 2, big_ram) == 200
    assert capacity.simulate([], jobs, 4, big_ram) == 100
    heavy = [{**j, "resource": "heavy"} for j in jobs]
    assert capacity.simulate([], heavy, 4, big_ram) == 400  # one heavy job at a time
    # RAM rule: 3x the unit against 80% of available RAM -> 2 GB units on 10 GB fit one at a time
    bundles = [{**j, "size": 2 * GB} for j in jobs]
    assert capacity.simulate([], bundles, 4, 10 * GB) == 400
    assert capacity.simulate([], bundles, 4, 20 * GB) == 200
    # running jobs count with what is left
    running = [{"id": 9, "extractor": "r", "resource": "cpu", "stage": 50, "size": 0, "left_s": 50.0}]
    assert capacity.simulate(running, jobs[:1], 1, big_ram) == 150


def _workspace(tmp_path, rows):
    ws = tmp_path / "ws"
    (ws / "jobs").mkdir(parents=True)
    (ws / "game.json").write_text(json.dumps({"gs_workspace": 1, "gst_workspace": 2, "slug": "ws", "name": "W",
                                              "install_dir": str(tmp_path)}), encoding="utf-8")
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        for i, r in enumerate(rows, 1):
            db.upsert(extractor=r["extractor"], unit_id=f"u{i}", relpaths=[], inputs=[], fingerprint=str(i), buildid="1",
                      category=r["category"], resource=r["resource"], stage=r["stage"], seq=i, size=r["size"], meta={})
            job = db.find(r["extractor"], f"u{i}")
            if r["status"] == "done":
                db.finish(job["id"], status="done", duration_s=r["duration_s"], peak_ram=r.get("peak_ram"))
    return ws


def _machine(cpus=12, busy=0.0, avail=32 * GB, drive="fixed"):
    return {"cpus": cpus, "cpu_busy": busy, "ram_total": 64 * GB, "ram_available": avail, "disk_free": 500 * GB,
            "drive": drive}


def test_estimate_small_jobs_on_an_idle_machine_is_auto(tmp_path):
    rows = [row("unity.text", 10 * MB, 5.0, category="data") for _ in range(3)]
    rows += [row("unity.text", 10 * MB, status="pending", category="data") for _ in range(6)]
    ws = _workspace(tmp_path, rows)
    result = capacity.estimate(ws, mach=_machine(cpus=8), study_running=False)
    assert result["counts"] == {"running": 0, "pending": 6}
    assert result["limits"]["cpu"] == 7
    assert result["recommended"] == 6 and result["limiter"] == "jobs-left"
    assert result["eta_s"][1] == 30 and result["eta_s"][6] == 5
    assert result["decision"] == "auto", result["reasons"]
    text = capacity.render(result)
    assert "recommended: --jobs 6" in text and "decision: auto" in text


def test_estimate_asks_when_unmeasured_ram_limits_or_machine_is_busy_or_history_missing(tmp_path):
    rows = [row("unity.textures", 2 * GB, 1200.0) for _ in range(2)]  # done before peak_ram existed
    rows += [row("unity.textures", 2 * GB, status="pending") for _ in range(6)]
    rows += [row("unity.fonts", 10 * MB, status="pending", category="font") for _ in range(7)]
    ws = _workspace(tmp_path, rows)
    result = capacity.estimate(ws, mach=_machine(cpus=12, busy=0.5, avail=19 * GB), study_running=False)
    assert result["limits"]["ram"] == 2  # 3x a 2 GB bundle = 6 GB each, 3.8 GB kept free of 19 GB
    assert result["recommended"] == 2 and result["limiter"] == "ram"
    assert result["limits"]["ram_unmeasured"] > 0
    reasons = " | ".join(result["reasons"])
    assert result["decision"] == "ask"
    assert "RAM limits the run" in reasons and "other programs" in reasons and "no history" in reasons
    assert any("no RAM measurement yet" in n for n in result["notes"])


def test_estimate_measured_peaks_lift_the_ram_limit(tmp_path):
    rows = [row("unity.textures", 2 * GB, 1200.0, peak_ram=700 * MB) for _ in range(2)]
    rows += [row("unity.textures", 2 * GB, status="pending") for _ in range(12)]
    ws = _workspace(tmp_path, rows)
    result = capacity.estimate(ws, mach=_machine(cpus=12, avail=19 * GB), study_running=False)
    assert result["limits"]["ram_per_job"] == pytest.approx(1050 * MB, rel=0.01)  # 700 MB x 1.5
    assert result["limits"]["ram"] > 10 and result["limiter"] == "cpu" and result["recommended"] == 11
    assert result["limits"]["ram_unmeasured"] == 0
    assert result["eta_s"][2] == 7200 and result["eta_s"][11] == 2400  # 12 x 20 min: 6 waves vs 2


def test_ram_model_and_job_weight():
    from gst.pipeline import budget

    model = budget.RamModel([row("unity.textures", 2 * GB, 1.0, peak_ram=700 * MB),
                             row("unity.textures", 1 * MB, 1.0, peak_ram=200 * MB),
                             row("unity.meshes", 1 * MB, 1.0, peak_ram=90 * MB),
                             row("unity.gltf", 1 * GB, None, status="failed", peak_ram=9 * GB),
                             row("unity.fonts", 1 * GB, None, status="pending", peak_ram=9 * GB)])
    assert model.measured("unity.textures", 4 * GB) == 1400 * MB       # ratio of the big unit
    assert model.measured("unity.textures", 10 * MB) == 200 * MB       # small units: their largest peak
    assert model.measured("unity.meshes", 2 * GB) == 90 * MB           # no big unit measured: the largest peak
    assert model.measured("unity.gltf", GB) == 9 * GB                  # a failed job's peak is a lower bound
    assert model.measured("unity.fonts", GB) is None                   # unfinished jobs teach nothing
    assert budget.job_weight(4 * GB, "cpu", "unity.textures", model) == 2100 * MB
    assert budget.job_weight(2 * GB, "cpu", "unity.sprites", model) == 6 * GB   # not measured: 3x the unit
    # not measured, no input size (analyses) or an external tool (heavy): a share of the machine's RAM
    assert budget.job_weight(0, "cpu", "analysis.rig", model, total_ram=32 * GB) == 8 * GB
    assert budget.job_weight(15 * GB, "heavy", "unity.assetripper_project", model, total_ram=32 * GB) == int(32 * GB * 0.4)
    assert budget.job_weight(2 * GB, "heavy", "unity.textures", model) == 1050 * MB  # measured wins (700 MB x 1.5)
    assert budget.job_weight(GB, "io", "files.loose", model) == budget.RAM_FLOOR


def test_runner_reserves_only_what_running_jobs_may_still_take(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from gst.pipeline import budget
    from gst.pipeline.runner import Runner

    runner = Runner(tmp_path, jobs=8)
    monkeypatch.setattr(budget, "memory", lambda: (32 * GB, 19 * GB))
    used = {"value": 0}
    monkeypatch.setattr(budget, "process_tree_ram", lambda pid: used["value"])
    assert runner._ram_ok(6 * GB)  # nothing running: always starts
    runner.running = {1: {"weight": 6 * GB, "proc": SimpleNamespace(pid=1)}}
    assert runner._ram_ok(6 * GB)          # 19 - 6 - 6 = 7 GB left >= 3.8 GB kept free
    runner.running[2] = {"weight": 6 * GB, "proc": SimpleNamespace(pid=2)}
    assert not runner._ram_ok(6 * GB)      # 19 - 12 - 6 = 1 GB
    used["value"] = 5 * GB                 # the running jobs already hold most of their share
    assert runner._ram_ok(6 * GB)          # 19 - 2 - 6 = 11 GB


def test_runner_samples_the_process_tree_peak_into_jobs_db(tmp_path, monkeypatch):
    import subprocess
    import sys

    from gst.pipeline import budget, runner as runner_mod
    from gst.pipeline.runner import Runner

    ws = _workspace(tmp_path, [row("unity.text", MB, status="pending", category="data")])
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        job_id = db.find("unity.text", "u1")["id"]
        db.claim(job_id)
        db.finish(job_id, status="done", duration_s=1.0, peak_ram=100 * MB)  # the worker's own peak
    monkeypatch.setattr(budget, "process_tree_ram", lambda pid: 700 * MB)    # its child tool held more
    monkeypatch.setattr(runner_mod, "PEAK_SAMPLE_S", 0.0)
    run = Runner(ws, jobs=1)
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    run.running = {job_id: {"proc": proc, "fh": open(tmp_path / "log.txt", "w"), "log": tmp_path / "log.txt",
                            "t0": 0.0, "extractor": "unity.text", "unit_id": "u1", "resource": "cpu", "weight": 1,
                            "peak": 700 * MB}}
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        run._reap(db)
        assert db.get(job_id)["peak_ram"] == 700 * MB
        assert run.ram_model.measured("unity.text", MB) == 700 * MB


def test_process_tree_ram_sees_this_process():
    import os

    from gst.pipeline import budget

    value = budget.process_tree_ram(os.getpid())
    assert value > 1 * MB if os.name == "nt" else value == 0
    assert budget.process_tree_ram(None) == 0


def test_estimate_asks_when_more_jobs_save_a_lot(tmp_path):
    rows = [row("unity.sprites", 100 * MB, 600.0, category="texture") for _ in range(2)]
    rows += [row("unity.sprites", 100 * MB, status="pending", category="texture") for _ in range(20)]
    ws = _workspace(tmp_path, rows)
    result = capacity.estimate(ws, mach=_machine(cpus=16), study_running=False)
    assert result["limits"]["default"] == 4 and result["recommended"] == 15
    assert result["decision"] == "ask"
    assert any("saves" in r for r in result["reasons"])


def test_estimate_reads_other_workspaces_and_stale_running_jobs(tmp_path):
    other = _workspace(tmp_path / "home" / "other", [row("unity.meshes", GB, 300.0, category="mesh")])
    ws = _workspace(tmp_path / "home" / "me", [row("unity.meshes", GB, status="pending", category="mesh")])
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        db.claim(db.find("unity.meshes", "u1")["id"])  # left running by a study that died
    result = capacity.estimate(ws, home=other.parent, mach=_machine(), study_running=False)
    assert result["counts"] == {"running": 0, "pending": 1}
    assert result["jobs"][0]["basis"] == "other workspaces (1)"


def test_jobsdb_adds_peak_ram_to_old_databases(tmp_path):
    path = tmp_path / "jobs.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, extractor TEXT NOT NULL, unit_id TEXT NOT NULL, "
                "relpaths TEXT NOT NULL DEFAULT '[]', inputs TEXT NOT NULL DEFAULT '[]', fingerprint TEXT NOT NULL DEFAULT '', "
                "buildid TEXT, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, started_at TEXT, "
                "finished_at TEXT, duration_s REAL, outputs TEXT NOT NULL DEFAULT '[]', out_bytes INTEGER NOT NULL DEFAULT 0, "
                "error TEXT, tool TEXT, tool_version TEXT, notes TEXT NOT NULL DEFAULT '[]', category TEXT, resource TEXT, "
                "stage INTEGER NOT NULL DEFAULT 50, seq INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT 0, "
                "meta TEXT NOT NULL DEFAULT '{}', pid INTEGER, UNIQUE (extractor, unit_id))")
    con.execute("INSERT INTO jobs (extractor, unit_id) VALUES ('x', 'u')")
    con.commit()
    con.close()
    assert capacity.read_jobs(path)[0]["peak_ram"] is None  # readers cope without the column
    with JobsDB(path) as db:
        db.finish(1, status="done", duration_s=1.0, peak_ram=123)
        assert db.get(1)["peak_ram"] == 123
    assert capacity.read_jobs(path)[0]["peak_ram"] == 123


def test_worker_measures_its_peak_ram():
    value = peak_ram()
    assert value is None or value > 1 * MB


def test_cli_estimate_json(tmp_path, monkeypatch, capsys):
    from gst.cli import main

    rows = [row("unity.text", MB, 2.0, category="data"), row("unity.text", MB, status="pending", category="data")]
    ws = _workspace(tmp_path, rows)
    monkeypatch.chdir(ws)
    assert main(["jobs", "estimate", "--json", "--sample", "0.1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["pending"] == 1 and out["decision"] in ("auto", "ask") and out["recommended"] >= 1
