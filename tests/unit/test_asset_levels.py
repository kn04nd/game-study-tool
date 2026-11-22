"""Asset levels and tracks of gst study: which extractors each level plans, map containers left out below full,
the runner running only the current plan, code analyses not waiting for the asset track, the per-track status,
the level estimate and the question when no level was chosen."""

from __future__ import annotations

import json
import sys
import textwrap
import time

import pytest

from gst import cli
from gst.extractors import registry
from gst.pipeline import capacity, levels, plan, progress
from gst.pipeline.jobs import JobsDB
from gst.pipeline.runner import Runner
from gst.recon import stores


def test_tracks_and_levels():
    assert levels.track_of("code.dotnet") == "code" and levels.track_of("rea.import") == "code"
    assert levels.track_of("analysis.code_architecture") == "code" and levels.track_of("analysis.systems") == "code"
    assert levels.track_of("unity.textures") == "assets" and levels.track_of("analysis.prefabs") == "assets"
    assert [levels.included("unity.inventory", lv) for lv in levels.LEVELS] == [False, True, True, True]
    assert [levels.included("unity.gltf", lv) for lv in levels.LEVELS] == [False, False, True, True]
    assert [levels.included("unity.assetripper_project", lv) for lv in levels.LEVELS] == [False, False, False, True]
    assert all(levels.included("code.dotnet", lv) for lv in levels.LEVELS)
    assert levels.included("unity.gltf", None)  # no level: everything
    with pytest.raises(Exception):
        levels.check("most")


def test_map_containers_are_left_out_below_full(monkeypatch):
    monkeypatch.setattr(levels, "is_map_container", lambda install, rel: "maps/" in rel)
    rel = ["Game_Data/StreamingAssets/maps/levels0-31.assetbundle"]
    assert levels.unit_left_out("unity.textures", rel, "normal", "x")
    assert levels.unit_left_out("unity.inventory", rel, "basic", "x")
    assert not levels.unit_left_out("unity.textures", rel, "full", "x")
    assert not levels.unit_left_out("maps.list", rel, "basic", "x")  # it only lists them
    assert not levels.unit_left_out("unity.textures", ["Game_Data/resources.assets"], "normal", "x")


def test_scene_bundle_detection(tmp_path, monkeypatch):
    from gst.engines.unity import bundle

    for name in ("scenes.bundle", "props.bundle"):
        (tmp_path / name).write_bytes(b"UnityFS\0" + b"\0" * 64)
    (tmp_path / "level1").write_bytes(b"\0" * 64)
    nodes = {"scenes.bundle": [(0, 1, 4, "BuildPlayer-Level_1.sharedAssets"), (0, 1, 4, "BuildPlayer-Level_1")],
             "props.bundle": [(0, 1, 4, "CAB-123")]}
    monkeypatch.setattr(bundle, "read_header", lambda p: type("H", (), {"nodes": nodes[str(p).replace("\\", "/").rsplit("/", 1)[-1]]}))
    levels._scene_bundle.cache_clear()
    assert levels.is_map_container(tmp_path, "scenes.bundle")
    assert not levels.is_map_container(tmp_path, "props.bundle")
    assert not levels.is_map_container(tmp_path, "level1")  # the game's own scenes are not map containers
    assert not levels.is_map_container(tmp_path, "missing")


def test_selected_by_level_keeps_an_extractor_named_in_only():
    names = lambda classes: {c.name for c in classes}  # noqa: E731
    none = names(plan.selected("unity-mono", level="none"))
    assert "code.dotnet" in none and "analysis.code_architecture" in none and "unity.inventory" not in none
    basic = names(plan.selected("unity-mono", level="basic"))
    assert {"unity.inventory", "maps.list", "analysis.dcc"} <= basic and "unity.textures" not in basic
    normal = names(plan.selected("unity-mono", level="normal"))
    assert {"unity.textures", "unity.gltf", "analysis.prefabs"} <= normal and "unity.assetripper_project" not in normal
    assert "unity.assetripper_project" in names(plan.selected("unity-mono", level="full"))
    forced = names(plan.selected("unity-mono", only=["unity.assetripper_project"], level="basic"))
    assert forced == {"unity.assetripper_project"}


def test_code_analyses_wait_for_code_only():
    import gst.analysis.stage  # noqa: F401  (registers the analysis jobs)

    systems = registry.get("analysis.systems")
    assert "analysis.prefabs" not in systems.after  # optional input from the asset track: no wait
    assert {"code.index", "rea.import"} <= set(systems.after)
    controller = registry.get("analysis.controller")
    assert {"code.index", "rea.import"} <= set(controller.after)  # asset analysis reading the code index


def test_simulate_runs_the_tracks_side_by_side(monkeypatch):
    rules = {"analysis.code_architecture": ((), True), "unity.textures": ((), False)}
    monkeypatch.setattr(capacity, "_rules", lambda e: rules.get(e, ((), False)))
    jobs = [{"extractor": "unity.textures", "stage": 50, "seq": 1, "est_s": 100, "resource": "cpu", "ram_weight": 1},
            {"extractor": "analysis.code_architecture", "stage": 60, "seq": 2, "est_s": 10, "resource": "cpu",
             "ram_weight": 1}]
    assert capacity.simulate([], jobs, 2, 1 << 40) == 100  # the code analysis did not wait for the export


def test_model_learns_from_jobs_planned_again():
    rows = [{"extractor": "analysis.dcc", "category": "other", "size": 0, "status": "pending", "duration_s": 600.0},
            {"extractor": "analysis.rig", "category": "other", "size": 0, "status": "failed", "duration_s": 3.0}]
    m = capacity.Model(rows, [])
    assert m.estimate({"extractor": "analysis.dcc", "size": 0}) == (600.0, "this workspace (1)")
    assert m.estimate({"extractor": "analysis.rig", "size": 0})[1].startswith("guess")


def test_track_rows_and_line(tmp_path):
    with JobsDB(tmp_path / "jobs.db") as db:
        for i, (ext, status, dur) in enumerate([("code.dotnet", "done", 5.0), ("code.index", "pending", None),
                                                ("unity.textures", "done", 60.0), ("unity.textures", "pending", None),
                                                ("unity.gltf", "failed", 2.0)]):
            db.upsert(extractor=ext, unit_id=f"u{i}", relpaths=[], inputs=[], fingerprint="f", buildid="1",
                      category="texture", resource="cpu", stage=50, seq=i, size=0, meta={})
            if status != "pending":
                db.con.execute("UPDATE jobs SET status=?, duration_s=? WHERE unit_id=?", (status, dur, f"u{i}"))
        planned = {("code.dotnet", "u0"), ("code.index", "u1"), ("unity.textures", "u2"), ("unity.textures", "u3"),
                   ("unity.gltf", "u4")}
        rows = progress.track_rows(db, planned, [], 2)
        md = progress.status_markdown(tmp_path, db, tracks=rows)
    by = {r["track"]: r for r in rows}
    assert by["code"]["total"] == 2 and by["code"]["finished"] == 1
    assert by["assets"]["total"] == 3 and by["assets"]["finished"] == 2 and by["assets"]["counts"]["failed"] == 1
    assert by["assets"]["left_s"] == 60  # the pending texture job, from the done one
    line = progress.track_line(rows)
    assert line.startswith("code 1/2") and "assets 2/3 (1 failed)" in line
    assert "## By track (this plan)" in md


FAKE = '''
import time
from gst.extractors.base import Extractor, Result, Unit
from gst.extractors.registry import register


@register("fake-engine")
class Export(Extractor):
    name, category, resource, stage = "fake.export", "texture", "io", 50

    def discover(self, ctx):
        yield Unit(id="big", extractor=self.name, relpaths=[], size=0)

    def run(self, unit, ctx):
        time.sleep(4)
        (ctx.workspace / "export.done").write_text(str(time.time()), encoding="utf-8")
        return Result(tool="fake")


@register("fake-engine")
class Old(Extractor):
    name, category, resource, stage = "fake.old", "texture", "io", 50

    def discover(self, ctx):
        yield Unit(id="old", extractor=self.name, relpaths=[], size=0)

    def run(self, unit, ctx):
        (ctx.workspace / "old.ran").write_text("x", encoding="utf-8")
        return Result(tool="fake")


@register("fake-engine")
class CodeAnalysis(Extractor):
    name, category, resource, stage = "code.fake_analysis", "code", "io", 60
    after_earlier_stages = True

    def discover(self, ctx):
        yield Unit(id="all", extractor=self.name, relpaths=[], size=0)

    def run(self, unit, ctx):
        (ctx.workspace / "analysis.done").write_text(str(time.time()), encoding="utf-8")
        return Result(tool="fake")
'''


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    mod_dir = tmp_path / "mods"
    mod_dir.mkdir()
    (mod_dir / "gst_fake_levels.py").write_text(textwrap.dedent(FAKE), encoding="utf-8")
    monkeypatch.syspath_prepend(str(mod_dir))
    monkeypatch.setenv("PYTHONPATH", str(mod_dir))
    monkeypatch.setenv(registry.ENV_PROVIDERS, "gst_fake_levels")
    monkeypatch.setattr(registry, "PROVIDERS", [])
    monkeypatch.setattr(registry, "_REGISTRY", {})
    monkeypatch.delitem(sys.modules, "gst_fake_levels", raising=False)
    install = tmp_path / "install"
    install.mkdir()
    ws = tmp_path / "ws"
    for sub in ("jobs", "logs"):
        (ws / sub).mkdir(parents=True)
    game = {"gs_workspace": 1, "gst_workspace": 2, "slug": "fake", "name": "Fake", "buildid": "1", "install_dir": str(install),
            "engine": {"key": "fake-engine"}, "verdict": "offline-ok"}
    (ws / "game.json").write_text(json.dumps(game), encoding="utf-8")
    return {"ws": ws, "game": game}


def test_runner_runs_only_the_plan_and_tracks_run_side_by_side(fake_env):
    ws, game = fake_env["ws"], fake_env["game"]
    plan.build(ws, game)  # every extractor pending, fake.old included
    p = plan.build(ws, game, options={"skip": ["fake.old"]})
    assert plan.planned_units(p) == {("fake.export", "big"), ("code.fake_analysis", "all")}
    summary = Runner(ws, jobs=2, python=sys.executable, planned=plan.planned_units(p), status_every=0).run()
    assert summary["started"] == 2
    assert not (ws / "old.ran").exists()  # pending from the earlier plan, outside this one: never runs
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        assert db.find("fake.old", "old")["status"] == "pending"
    # the code-track analysis (stage 60) did not wait for the asset export (stage 50) to finish
    assert float((ws / "analysis.done").read_text()) < float((ws / "export.done").read_text())
    events = [json.loads(line) for f in (ws / "logs").glob("study-*.jsonl") for line in f.read_text().splitlines()]
    assert any(e["event"] == "tracks" for e in events)
    assert "## By track (this plan)" in (ws / "jobs" / "STATUS.md").read_text(encoding="utf-8")


def _fake_game(tmp_path, monkeypatch):
    install = tmp_path / "Fake Game"
    (install / "data").mkdir(parents=True)
    (install / "FakeGame.exe").write_bytes(b"MZ" + b"\0" * 200)
    (install / "data" / "items.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(stores, "all_games", lambda: [])
    return install


def test_study_asks_for_the_level_then_keeps_it(tmp_path, monkeypatch, capsys, isolated_env):
    install = _fake_game(tmp_path, monkeypatch)
    assert cli.main(["study", str(install), "--dry-run", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["level"] for r in out["levels"]["levels"]] == list(levels.LEVELS) and out["assets"] is None
    assert cli.main(["study", str(install), "--json"]) == 2  # asset extractors and no level: ask first
    asked = json.loads(capsys.readouterr().out)
    assert asked["error"] == "asset level needed" and len(asked["levels"]["levels"]) == 4
    assert cli.main(["study", str(install), "--only", "files.loose", "--json"]) == 0  # code-only plans need no level
    capsys.readouterr()
    assert cli.main(["study", str(install), "--assets", "none", "--only", "files.loose", "--json"]) == 0
    capsys.readouterr()
    ws = isolated_env["home"] / "fake-game"
    assert json.loads((ws / "game.json").read_text(encoding="utf-8"))["assets_level"] == "none"
    assert cli.main(["study", "fake-game", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["assets"] == "none"


def test_analysis_context_hides_map_containers_below_full(tmp_path, monkeypatch):
    from gst import analysis
    from gst.engines.unity import inventory

    inv = tmp_path / "assets" / "unity" / inventory.INVENTORY_DIR
    inv.mkdir(parents=True)
    for rel in ("Game_Data/resources.assets", "Game_Data/StreamingAssets/maps/levels0.bundle"):
        (inv / f"{inventory.safe_rel(rel)}.summary.json").write_text(
            json.dumps({"file": rel, "types": {"Transform": 3}}), encoding="utf-8")
    monkeypatch.setattr(levels, "is_map_container", lambda install, rel: "/maps/" in rel)
    spec = analysis.Spec(name="t", func=lambda ctx: None)
    game = {"install_dir": str(tmp_path)}
    full = analysis.Context(tmp_path, game, spec, options={})
    assert len(full.inventory_files(("Transform",))) == 2  # gst analyze without a level reads everything
    normal = analysis.Context(tmp_path, game, spec, options={"assets": "normal"})
    assert normal.inventory_files(("Transform",)) == ["Game_Data/resources.assets"]
    assert [s["file"] for s in normal.summaries()] == ["Game_Data/resources.assets"]


def test_ram_model_keeps_the_peak_of_jobs_planned_again(tmp_path):
    from gst.pipeline import budget

    with JobsDB(tmp_path / "jobs.db") as db:
        db.upsert(extractor="analysis.dcc", unit_id="dcc", relpaths=[], inputs=[], fingerprint="f", buildid="1",
                  category="other", resource="cpu", stage=60, seq=1, size=0, meta={})
        db.con.execute("UPDATE jobs SET peak_ram=? WHERE unit_id='dcc'", (600 << 20,))  # pending, measured last run
        model = budget.RamModel.from_con(db.con)
    assert model.measured("analysis.dcc", 0) == 600 << 20
    assert budget.job_weight(0, "cpu", "analysis.dcc", model, 32 << 30) == int((600 << 20) * budget.RAM_MARGIN)


def test_exports_of_hidden_map_containers_are_skipped(tmp_path, monkeypatch):
    from gst import analysis
    from gst.analysis.gltf_summary import find_files
    from gst.engines.unity import inventory

    inv = tmp_path / "assets" / "unity" / inventory.INVENTORY_DIR
    inv.mkdir(parents=True)
    for rel in ("Game_Data/resources.assets", "Game_Data/StreamingAssets/maps/levels0.bundle"):
        (inv / f"{inventory.safe_rel(rel)}.summary.json").write_text(json.dumps({"file": rel, "types": {}}),
                                                                     encoding="utf-8")
    for src in ("resources.assets", "StreamingAssets__maps__levels0.bundle"):
        d = tmp_path / "assets" / "unity" / "SkinnedMesh" / src
        d.mkdir(parents=True)
        (d / "body.gltf").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(levels, "is_map_container", lambda install, rel: "/maps/" in rel)
    ctx = analysis.Context(tmp_path, {"install_dir": str(tmp_path)}, analysis.Spec(name="t", func=lambda c: None),
                           options={"assets": "normal"})
    assert ctx.hidden_export_dirs() == {"StreamingAssets__maps__levels0.bundle"}
    found = find_files(tmp_path / "assets", ctx.hidden_export_dirs())
    assert [p.parent.name for p in found] == ["resources.assets"]
    assert len(find_files(tmp_path / "assets")) == 2
