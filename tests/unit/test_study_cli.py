"""gst study on a synthetic game folder: dry run (estimate, nothing runs), then a real run of the loose-file
catalog, a resumed run that skips done jobs, gst jobs and gst coverage."""

from __future__ import annotations

import json

from gst import cli
from gst.recon import stores


def fake_game(tmp_path, monkeypatch):
    install = tmp_path / "Fake Game"
    (install / "data").mkdir(parents=True)
    (install / "FakeGame.exe").write_bytes(b"MZ" + b"\0" * 200)
    (install / "data" / "items.json").write_text('{"sword": {"damage": 3}}', encoding="utf-8")
    (install / "data" / "config.ini").write_text("[video]\nwidth=1920\n", encoding="utf-8")
    (install / "readme.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(stores, "all_games", lambda: [])
    return install


def snapshot(root):
    return sorted((p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*"))


def test_dry_run_plans_without_running(tmp_path, monkeypatch, capsys, isolated_env):
    install = fake_game(tmp_path, monkeypatch)
    before = snapshot(install)
    assert cli.main(["study", str(install), "--dry-run", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] and out["plan"]["totals"]["units"] >= 2
    loose = next(e for e in out["plan"]["extractors"] if e["name"] == "files.loose")
    assert loose["units"] == 2 and loose["to_run"] == 2  # two folders: root and data/
    ws = isolated_env["home"] / "fake-game"
    assert out["workspace"] == str(ws) and out["would_create"]["create"] == str(ws)
    assert not ws.exists() and list(isolated_env["home"].iterdir()) == []  # a dry run writes nothing at all
    assert snapshot(install) == before  # the install is never written


def test_dry_run_on_a_new_build_creates_no_sibling_or_registry_entry(tmp_path, monkeypatch, capsys, isolated_env):
    """Regression: a dry run on a new build used to create GS_HOME/<slug>@<buildid> and its registry entry."""
    install = fake_game(tmp_path, monkeypatch)
    entry = {"store": "steam", "appid": "4242", "name": "Fake Game", "path": str(install), "buildid": "100"}
    monkeypatch.setattr(stores, "all_games", lambda: [dict(entry)])
    assert cli.main(["study", str(install), "--only", "files.loose", "--json"]) == 0
    capsys.readouterr()
    home = isolated_env["home"]
    registry = (home / "registry.json").read_text(encoding="utf-8")
    assert "fake-game" in json.loads(registry)["games"]
    before = snapshot(home)
    entry["buildid"] = "200"  # Steam updated the game
    for target in (str(install), "fake-game"):
        assert cli.main(["study", target, "--dry-run", "--json"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["would_create"] == {"create": str(home / "fake-game@200"), "sibling_of": "fake-game", "recon": True}
        assert out["plan"]["buildid"] == "200"
    assert not (home / "fake-game@200").exists()
    assert (home / "registry.json").read_text(encoding="utf-8") == registry
    assert snapshot(home) == before  # not one byte or mtime changed under GS_HOME


def test_dry_run_on_a_workspace_without_recon_writes_nothing(tmp_path, monkeypatch, capsys, isolated_env):
    install = fake_game(tmp_path, monkeypatch)
    assert cli.main(["study", str(install), "--only", "files.loose", "--json"]) == 0
    capsys.readouterr()
    ws = isolated_env["home"] / "fake-game"
    for f in (ws / "recon").iterdir():
        f.unlink()
    (ws / "jobs" / "plan.json").unlink()
    before = snapshot(isolated_env["home"])
    assert cli.main(["study", "fake-game", "--dry-run"]) == 0
    assert "in-memory recon" in capsys.readouterr().out
    assert snapshot(isolated_env["home"]) == before


def test_run_resume_jobs_and_coverage(tmp_path, monkeypatch, capsys, isolated_env):
    install = fake_game(tmp_path, monkeypatch)
    before = snapshot(install)
    assert cli.main(["study", str(install), "--only", "files.loose", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["run"]["started"] == 2 and first["run"]["totals"] == {"done": 2}
    ws = isolated_env["home"] / "fake-game"
    rows = [json.loads(line) for p in (ws / "dumps" / "loose" / "catalog").glob("*.jsonl")
            for line in p.read_text(encoding="utf-8").splitlines()]
    assert {r["path"] for r in rows} == {"FakeGame.exe", "readme.txt", "data/items.json", "data/config.ini"}
    assert (ws / "jobs" / "COVERAGE.md").exists() and (ws / "jobs" / "STATUS.md").exists()
    assert list((ws / "logs").glob("study-*.jsonl"))
    assert cli.main(["study", "fake-game", "--only", "files.loose", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["run"]["started"] == 0  # resumed: nothing left to do
    monkeypatch.chdir(ws)
    assert cli.main(["jobs", "list", "--json"]) == 0
    jobs = json.loads(capsys.readouterr().out)
    assert len(jobs) == 2 and {j["status"] for j in jobs} == {"done"}
    assert cli.main(["jobs", "show", str(jobs[0]["id"])]) == 0
    assert "extractor: files.loose" in capsys.readouterr().out
    assert cli.main(["coverage", "--json"]) == 0
    cov = json.loads(capsys.readouterr().out)
    assert cov["categories"]["config_save"]["done"] == 2
    assert cli.main(["jobs", "reset", "--extractor", "files", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["pending"] == 2
    assert snapshot(install) == before
