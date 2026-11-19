"""gst study pipeline: jobs.db resume rules, isolated failures, interrupted runs, new builds, paused-disk.

Fake extractors live in a module written to tmp_path and named in GST_EXTRACTOR_PROVIDERS, so the
worker subprocesses (python -X utf8 -m gst.pipeline.worker) see them too.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from gst.extractors import registry
from gst.pipeline import plan
from gst.pipeline.jobs import JobsDB, fingerprint_of, stat_inputs
from gst.pipeline.runner import Runner

FAKE = '''
import os, time
from pathlib import Path
from gst.extractors.base import Extractor, Result, Unit
from gst.extractors.registry import register


@register("fake-engine")
class Copy(Extractor):
    name = "fake.copy"
    category = "data"
    resource = "io"
    stage = 10

    def discover(self, ctx):
        for p in sorted(ctx.install_dir.glob("*.dat")):
            yield Unit(id=p.name, extractor=self.name, relpaths=[p.name], size=p.stat().st_size)

    def run(self, unit, ctx):
        from gst.pipeline.worker import commit, partial_dir
        data = (ctx.install_dir / unit.relpaths[0]).read_bytes()
        if data.startswith(b"RAISE"):
            raise RuntimeError("broken unit " + unit.id)
        if data.startswith(b"CRASH"):
            os._exit(3)  # a native crash: only this job dies
        if data.startswith(b"SLOW"):
            time.sleep(30)
        staging = partial_dir(ctx, unit)
        (staging / "out.txt").write_bytes(data.upper())
        final = ctx.workspace / "assets" / "fake" / unit.id
        commit(staging, final)
        marks = ctx.workspace / "runs"
        marks.mkdir(exist_ok=True)
        (marks / f"{time.time_ns()}-{unit.id}").write_text("", encoding="utf-8")
        return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=len(data), tool="fake")


@register("fake-engine")
class After(Extractor):
    name = "fake.after"
    category = "other"
    stage = 20
    after = ("fake.copy",)

    def discover(self, ctx):
        yield Unit(id="all", extractor=self.name, relpaths=[], size=0)

    def run(self, unit, ctx):
        done = sorted(p.name for p in (ctx.workspace / "assets" / "fake").iterdir())
        return Result(notes=["saw " + ",".join(done)], tool="fake")
'''


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    mod_dir = tmp_path / "mods"
    mod_dir.mkdir()
    (mod_dir / "gst_fake_extractors.py").write_text(textwrap.dedent(FAKE), encoding="utf-8")
    monkeypatch.syspath_prepend(str(mod_dir))
    monkeypatch.setenv("PYTHONPATH", str(mod_dir))
    monkeypatch.setenv(registry.ENV_PROVIDERS, "gst_fake_extractors")
    monkeypatch.setattr(registry, "PROVIDERS", [])
    monkeypatch.setattr(registry, "_REGISTRY", {})
    monkeypatch.delitem(sys.modules, "gst_fake_extractors", raising=False)  # re-register in this test
    install = tmp_path / "install"
    install.mkdir()
    for name, data in {"a.dat": b"alpha", "b.dat": b"beta", "c.dat": b"gamma"}.items():
        (install / name).write_bytes(data)
    ws = tmp_path / "ws"
    for sub in ("jobs", "logs", "assets"):
        (ws / sub).mkdir(parents=True)
    game = {"gs_workspace": 1, "gst_workspace": 2, "slug": "fake", "name": "Fake", "buildid": "100",
            "install_dir": str(install), "engine": {"key": "fake-engine"}, "verdict": "offline-ok"}
    (ws / "game.json").write_text(json.dumps(game), encoding="utf-8")
    return {"ws": ws, "install": install, "game": game}


def run_study(env, **kw):
    plan.build(env["ws"], env["game"], options=kw.pop("options", {}))
    return Runner(env["ws"], jobs=kw.pop("jobs", 2), python=sys.executable, **kw).run()


def statuses(ws: Path) -> dict[str, str]:
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        return {f"{r['extractor']}:{r['unit_id']}": r["status"] for r in db.rows()}


def runs(ws: Path) -> list[str]:
    """Units run so far, in order (one marker file per run: workers run in parallel)."""
    marks = ws / "runs"
    if not marks.is_dir():
        return []
    return [name.split("-", 1)[1] for name in sorted(p.name for p in marks.iterdir())]


def test_fingerprint_changes_with_size_mtime_and_build(tmp_path):
    (tmp_path / "x").write_bytes(b"1")
    inputs = stat_inputs(tmp_path, ["x", "missing"])
    assert inputs[1] == ["missing", -1, 0]
    a = fingerprint_of(inputs, "1")
    assert a == fingerprint_of(stat_inputs(tmp_path, ["x", "missing"]), "1")
    assert a != fingerprint_of(inputs, "2")
    (tmp_path / "x").write_bytes(b"22")
    assert a != fingerprint_of(stat_inputs(tmp_path, ["x", "missing"]), "1")


def test_run_then_resume_skips_done_jobs(fake_env):
    ws = fake_env["ws"]
    summary = run_study(fake_env)
    assert summary["totals"] == {"done": 4}
    assert sorted(runs(ws)) == ["a.dat", "b.dat", "c.dat"]
    assert (ws / "assets" / "fake" / "a.dat" / "out.txt").read_bytes() == b"ALPHA"
    assert not any((ws / ".partial").iterdir())
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        after = db.find("fake.after", "all")
    assert after["notes"] == ["saw a.dat,b.dat,c.dat"]  # ran only after every fake.copy job
    again = run_study(fake_env)
    assert again["started"] == 0 and len(runs(ws)) == 3
    # a changed input reruns that unit only
    (fake_env["install"] / "b.dat").write_bytes(b"beta-2")
    third = run_study(fake_env, options={"only": ["fake.copy"]})
    assert third["started"] == 1 and runs(ws)[-1] == "b.dat"


def test_interrupted_running_jobs_go_back_to_pending(fake_env):
    ws = fake_env["ws"]
    plan.build(ws, fake_env["game"])
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        job = db.find("fake.copy", "a.dat")
        db.claim(job["id"])  # the study died while this job ran
        assert db.recover_stale() == 1
        assert db.get(job["id"])["status"] == "pending"
    summary = Runner(ws, jobs=1, python=sys.executable).run()
    assert summary["totals"] == {"done": 4}


def test_killed_runner_leaves_running_and_next_study_resumes(fake_env):
    import subprocess
    import time

    ws = fake_env["ws"]
    (fake_env["install"] / "a.dat").write_bytes(b"SLOW")
    plan.build(ws, fake_env["game"])
    code = ("import sys; from pathlib import Path; from gst.pipeline.runner import Runner; "
            f"Runner(Path(r'{ws}'), jobs=1).run()")
    proc = subprocess.Popen([sys.executable, "-X", "utf8", "-c", code])
    try:
        deadline = time.time() + 30
        while time.time() < deadline and "running" not in statuses(ws).values():
            time.sleep(0.2)
        assert "running" in statuses(ws).values()
    finally:
        proc.kill()  # the study process only; its workers die with its Job Object
        proc.wait()
    time.sleep(1)  # Windows releases the dead processes' file handles
    (fake_env["install"] / "a.dat").write_bytes(b"alpha")
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        assert db.recover_stale() >= 1
    summary = run_study(fake_env)
    assert set(statuses(ws).values()) == {"done"} and summary["totals"] == {"done": 4}


def test_failures_are_isolated(fake_env):
    (fake_env["install"] / "a.dat").write_bytes(b"RAISE")
    (fake_env["install"] / "b.dat").write_bytes(b"CRASH")
    summary = run_study(fake_env)
    st = statuses(fake_env["ws"])
    assert st["fake.copy:a.dat"] == "failed" and st["fake.copy:b.dat"] == "failed" and st["fake.copy:c.dat"] == "done"
    with JobsDB(fake_env["ws"] / "jobs" / "jobs.db") as db:
        a, b = db.find("fake.copy", "a.dat"), db.find("fake.copy", "b.dat")
    assert "RuntimeError: broken unit a.dat" in a["error"] and "Traceback" in a["error"]
    assert "worker exited with code 3" in b["error"]
    assert summary["totals"]["failed"] == 2
    # failed stays failed on a plain rerun; --retry-failed runs it again
    assert run_study(fake_env)["started"] == 0
    retried = run_study(fake_env, options={"retry_failed": True})
    assert retried["started"] == 2 and retried["totals"]["failed"] == 2


def test_paused_disk_then_resume(fake_env, monkeypatch):
    ws = fake_env["ws"]
    summary = run_study(fake_env, min_free=1 << 62)  # an impossible floor: pause, never cut
    assert summary["paused"] and summary["started"] == 0
    assert set(statuses(ws).values()) == {"paused-disk"}
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        assert db.resume_paused() == 4
    assert run_study(fake_env)["totals"] == {"done": 4}


def test_timeout_is_optional_and_stops_only_that_job(fake_env):
    (fake_env["install"] / "a.dat").write_bytes(b"SLOW")
    summary = run_study(fake_env, timeout=2, options={"only": ["fake.copy"]})
    st = statuses(fake_env["ws"])
    assert st["fake.copy:a.dat"] == "failed" and st["fake.copy:b.dat"] == "done"
    with JobsDB(fake_env["ws"] / "jobs" / "jobs.db") as db:
        assert "--timeout" in db.find("fake.copy", "a.dat")["error"]
    assert summary["totals"]["failed"] == 1


def test_new_build_gets_a_sibling_workspace(tmp_path, isolated_env, monkeypatch):
    from gst.pipeline import study
    from gst.recon import stores

    install = tmp_path / "Some Game"
    install.mkdir()
    (install / "game.exe").write_bytes(b"MZ" + b"\0" * 100)
    (install / "data.ini").write_text("[a]\nb=1\n", encoding="utf-8")
    build = {"id": "111"}
    monkeypatch.setattr(stores, "all_games", lambda: [{"store": "steam", "appid": "999", "name": "Some Game",
                                                       "path": str(install), "buildid": build["id"]}])
    ws1, game1 = study.resolve(str(install))
    assert ws1.name == "some-game" and game1["buildid"] == "111" and game1["gst_workspace"] == 2
    assert (ws1 / "recon" / "recon.json").exists() and (ws1 / "jobs").is_dir()
    again, _ = study.resolve(str(install))
    assert again == ws1
    build["id"] = "222"
    ws2, game2 = study.resolve(str(install))
    assert ws2.name == "some-game@222" and game2["buildid"] == "222"
    assert study.resolve("some-game@222")[0] == ws2


FAKE_FINAL = '''
import time
from gst.extractors.base import Extractor, Result, Unit
from gst.extractors.registry import register


@register("fake-engine")
class Merge(Extractor):
    name = "fake.merge"
    category = "data"
    stage = 15
    out_subdir = "assets/merged"

    def discover(self, ctx):
        for p in sorted(ctx.install_dir.glob("*.dat")):
            yield Unit(id=p.name, extractor=self.name, relpaths=[p.name], size=p.stat().st_size)

    def run(self, unit, ctx):
        if (ctx.install_dir / unit.relpaths[0]).read_bytes().startswith(b"RAISE"):
            raise RuntimeError("broken")
        parts = ctx.out_root / "parts"
        parts.mkdir(parents=True, exist_ok=True)
        time.sleep(0.3)
        (parts / unit.id).write_text(str(time.time_ns()), encoding="utf-8")
        return Result(outputs=[f"assets/merged/parts/{unit.id}"])

    def finalize(self, ctx):
        parts = sorted(p.name for p in (ctx.out_root / "parts").iterdir())
        (ctx.out_root / "INDEX.txt").write_text(",".join(parts), encoding="utf-8")
        n = ctx.out_root / "finalized"
        n.write_text(str(int(n.read_text()) + 1 if n.exists() else 1), encoding="utf-8")
        return Result(outputs=["assets/merged/INDEX.txt"], notes=[f"merged {len(parts)}"])


@register("fake-engine")
class Reader(Extractor):
    name = "fake.reader"
    stage = 16
    after = ("fake.merge",)

    def discover(self, ctx):
        yield Unit(id="one", extractor=self.name, relpaths=[], size=0)

    def run(self, unit, ctx):
        idx = ctx.workspace / "assets" / "merged" / "INDEX.txt"
        return Result(notes=["index " + (idx.read_text(encoding="utf-8") if idx.exists() else "missing")])
'''


@pytest.fixture
def final_env(fake_env, monkeypatch):
    mod_dir = fake_env["ws"].parent / "mods"
    (mod_dir / "gst_fake_final.py").write_text(textwrap.dedent(FAKE_FINAL), encoding="utf-8")
    monkeypatch.setenv(registry.ENV_PROVIDERS, "gst_fake_extractors,gst_fake_final")
    monkeypatch.delitem(sys.modules, "gst_fake_final", raising=False)
    return fake_env


def test_finalize_runs_once_after_all_units_and_is_resumable(final_env):
    from gst.pipeline.worker import FINALIZE

    ws = final_env["ws"]
    summary = run_study(final_env, jobs=3, options={"only": ["fake.merge", "fake.reader"]})
    st = statuses(ws)
    assert st[f"fake.merge:{FINALIZE}"] == "done" and summary["totals"] == {"done": 5}
    out = ws / "assets" / "merged"  # out_root = <ws>/<out_subdir>
    assert (out / "INDEX.txt").read_text(encoding="utf-8") == "a.dat,b.dat,c.dat"
    assert (out / "finalized").read_text() == "1"
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        fin = db.find("fake.merge", FINALIZE)
        reader = db.find("fake.reader", "one")
        units = [db.find("fake.merge", n) for n in ("a.dat", "b.dat", "c.dat")]
    assert fin["notes"] == ["merged 3"] and fin["outputs"] == ["assets/merged/INDEX.txt"]
    assert fin["started_at"] >= max(u["finished_at"] for u in units)
    assert reader["notes"] == ["index a.dat,b.dat,c.dat"]  # dependents wait for the finalize job too
    # nothing changed: neither the units nor finalize run again
    assert run_study(final_env, options={"only": ["fake.merge", "fake.reader"]})["started"] == 0
    # one changed unit: that unit and finalize run again
    (final_env["install"] / "b.dat").write_bytes(b"beta-2")
    again = run_study(final_env, options={"only": ["fake.merge"]})
    assert again["started"] == 2 and (out / "finalized").read_text() == "2"


def test_finalize_reruns_after_a_retried_unit(final_env):
    from gst.pipeline.worker import FINALIZE

    ws = final_env["ws"]
    (final_env["install"] / "a.dat").write_bytes(b"RAISE")
    run_study(final_env, options={"only": ["fake.merge"]})
    st = statuses(ws)
    assert st["fake.merge:a.dat"] == "failed" and st[f"fake.merge:{FINALIZE}"] == "done"
    assert (ws / "assets" / "merged" / "INDEX.txt").read_text(encoding="utf-8") == "b.dat,c.dat"
    (final_env["install"] / "a.dat").write_bytes(b"alpha")  # fixed: changed fingerprint -> unit and finalize
    run_study(final_env, options={"only": ["fake.merge"]})
    assert (ws / "assets" / "merged" / "INDEX.txt").read_text(encoding="utf-8") == "a.dat,b.dat,c.dat"


def test_dry_run_lists_the_finalize_unit(final_env):
    from gst.pipeline.worker import FINALIZE

    p = plan.build(final_env["ws"], final_env["game"], options={"only": ["fake.merge"]}, write=False)
    entry = next(e for e in p["extractors"] if e["name"] == "fake.merge")
    assert entry["finalize"] and entry["unit_ids"][-1] == FINALIZE and entry["to_run"] == 4
