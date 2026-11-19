"""gst selftest without tools or games: the report, the game lookup, recon expectations and the exit code."""

import json

import pytest

from gst import cli, guard, selftest
from gst.common import GsError
from gst.recon import scan


@pytest.fixture
def registry(tmp_path, monkeypatch):
    games = {
        "oxygen-not-included": {"name": "Oxygen Not Included", "appid": 457140, "install_dir": str(tmp_path / "ONI")},
        "oni": {"name": "Oxygen Not Included", "appid": 457140, "install_dir": str(tmp_path / "ONI"),
                "workspace": str(tmp_path / "study" / "oni")},
        "project-zomboid": {"name": "Project Zomboid", "appid": 108600, "install_dir": str(tmp_path / "PZ")},
    }
    monkeypatch.setattr(guard, "registry_games", lambda: games)
    return games


def test_report_keeps_going_after_a_broken_check():
    def missing():
        raise GsError("ghidra is not installed")

    report = selftest.Report()
    report.run("env", "fine", lambda: ("ok", "all good"))
    report.run("tools", "missing", missing)
    report.run("tools", "bug", lambda: 1 / 0)
    rows = {r["name"]: r for r in report.rows}
    assert rows["fine"]["status"] == "ok" and rows["missing"]["detail"] == "ghidra is not installed"
    assert rows["bug"]["status"] == "fail" and rows["bug"]["detail"].startswith("ZeroDivisionError")
    assert report.failed == 2


def test_find_game_prefers_the_study_workspace(registry):
    assert selftest._find_game("457140")["slug"] == "oni"  # the library entry and the study share the AppID
    assert selftest._find_game("Oxygen")["slug"] == "oni"
    assert selftest._find_game("ONI")["slug"] == "oni"
    assert selftest._find_game("zomboid")["slug"] == "project-zomboid"
    with pytest.raises(GsError, match="no game 'tf2'"):
        selftest._find_game("tf2")


def test_games_compare_recon_with_the_expectations(registry, monkeypatch):
    found = {"engine": {"key": "unity-mono", "details": {"version": "6000.3.5f2"}}, "verdict": "offline-ok", "protections": []}
    monkeypatch.setattr(scan, "run_recon", lambda game: found)
    report = selftest.Report()
    selftest.games(report, ["oni", "tf2"])
    rows = {r["name"]: r for r in report.rows}
    assert rows["oni: recon"] == {**rows["oni: recon"], "status": "ok", "detail": "unity-mono 6000.3.5f2, offline-ok"}
    assert rows["tf2"]["status"] == "fail" and "oni: code index" not in rows  # no index in the study folder yet
    found["engine"]["key"] = "unity-il2cpp"
    report = selftest.Report()
    selftest.games(report, ["oni"])
    assert report.rows[0]["status"] == "fail" and "engine unity-il2cpp (expected unity-mono)" in report.rows[0]["detail"]


def test_cli_exit_code_follows_failures(monkeypatch, capsys):
    monkeypatch.setattr(selftest, "environment", lambda report: report.run("env", "doctor", lambda: ("warn", "LongPathsEnabled=0")))
    assert cli.main(["selftest", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["failed"] == 0 and out["checks"][0]["status"] == "warn"
    monkeypatch.setattr(selftest, "environment", lambda report: report.run("env", "doctor", lambda: ("fail", "uv missing")))
    assert cli.main(["selftest"]) == 1
    assert "FAIL  env     doctor" in capsys.readouterr().out
