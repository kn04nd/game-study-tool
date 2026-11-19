import json

import pytest

from gst import cli, config, guard, workspace
from gst.common import GsError
from gst.recon import scan


@pytest.fixture
def fake_game(tmp_path):
    root = tmp_path / "Steam" / "Clean Game"
    for rel, data in {"CleanGame.exe": b"MZ", "UnityPlayer.dll": b"MZ", "CleanGame_Data/globalgamemanagers": b"2021.3.1f1",
                      "CleanGame_Data/Managed/Assembly-CSharp.dll": b"MZ"}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return {"name": "Clean Game", "store": "steam", "appid": "123", "buildid": "42", "path": str(root)}


def test_create_workspace_layout(fake_game, isolated_env):
    report = scan.run_recon(fake_game)
    ws = workspace.create(fake_game, report, "clean")
    for name in ("CLAUDE.md", "STUDY.md", "FACTS.md", "ARCHITECTURE.md", "game.json", ".mcp.json",
                 ".claude/settings.json", ".claude/rules/decomp.md"):
        assert (ws / name).exists(), name
    for sub in workspace.SUBDIRS:
        assert (ws / sub).is_dir()
    meta = json.loads((ws / "game.json").read_text(encoding="utf-8"))
    assert meta["gs_workspace"] == 1 and meta["engine"]["key"] == "unity-mono" and meta["verdict"] == "offline-ok"
    claude_md = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Clean Game" in claude_md and "{" not in claude_md.split("## Flow")[0]
    settings = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["env"]["CLAUDE_CODE_USE_POWERSHELL_TOOL"] == "1"
    assert any(r.startswith("Edit(") and "Clean Game" in r for r in settings["permissions"]["deny"])
    assert "Bash(git add *)" in settings["permissions"]["deny"]
    entry = guard.registry_games()["clean"]
    assert entry["workspace"] == str(ws) and entry["verdict"] == "offline-ok"
    assert config.find_workspace(ws / "notes") == ws


def test_rule_path_format():
    assert workspace.rule_path(r"D:\SteamLibrary\steamapps\common\Deep Rock Galactic") == "//d/SteamLibrary/steamapps/common/Deep Rock Galactic"


def test_create_refuses_existing_and_git(fake_game, isolated_env, git_repo):
    report = scan.run_recon(fake_game)
    workspace.create(fake_game, report, "clean")
    with pytest.raises(GsError, match="already exists"):
        workspace.create(fake_game, report, "clean")
    with pytest.raises(GsError, match="inside the git repository"):
        workspace.create(fake_game, report, "x", home=git_repo / "studies")


def test_journal_appends_under_today(fake_game, isolated_env):
    ws = workspace.create(fake_game, scan.run_recon(fake_game), "clean")
    workspace.journal(ws, "tested something")
    workspace.journal(ws, "and another thing")
    text = (ws / "STUDY.md").read_text(encoding="utf-8")
    assert text.count("tested something") == 1 and "and another thing" in text


def test_status_command(fake_game, isolated_env, monkeypatch, capsys):
    ws = workspace.create(fake_game, scan.run_recon(fake_game), "clean")
    monkeypatch.chdir(ws)
    assert cli.main(["status", "--short"]) == 0
    assert "Clean Game" in capsys.readouterr().out


def test_refresh_merges_settings_and_keeps_study_files(fake_game, isolated_env, monkeypatch, capsys):
    import json

    from gst import cli

    ws = workspace.create(fake_game, scan.run_recon(fake_game), "clean")
    settings_path = ws / ".claude" / "settings.json"
    old = json.loads(settings_path.read_text(encoding="utf-8"))
    old["permissions"]["allow"] = ["Bash(gst *)", "Bash(my-own-tool *)"]  # an older toolkit + a user rule
    old["enabledMcpjsonServers"] = ["ghidra"]
    settings_path.write_text(json.dumps(old), encoding="utf-8")
    (ws / ".claude" / "rules" / "decomp.md").write_text("stale", encoding="utf-8")
    (ws / "FACTS.md").write_text("my facts", encoding="utf-8")

    monkeypatch.chdir(ws)
    assert cli.main(["refresh"]) == 0
    assert ".claude/settings.json" in capsys.readouterr().out
    new = json.loads(settings_path.read_text(encoding="utf-8"))
    fresh = workspace.workspace_settings(ws, workspace.load(ws))
    assert set(fresh["permissions"]["allow"]) <= set(new["permissions"]["allow"])
    assert "Bash(my-own-tool *)" in new["permissions"]["allow"]
    assert new["enabledMcpjsonServers"] == ["ghidra"]
    assert (ws / ".claude" / "rules" / "decomp.md").read_text(encoding="utf-8") == workspace.template("rules/decomp.md")
    assert (ws / "FACTS.md").read_text(encoding="utf-8") == "my facts"
    assert workspace.refresh(ws) == []  # idempotent
    (ws / "CLAUDE.md").write_text("edited", encoding="utf-8")
    assert workspace.refresh(ws, claude_md=True) == ["CLAUDE.md"]
    assert (ws / "CLAUDE.md.bak").read_text(encoding="utf-8") == "edited"


def test_claude_trust_state(fake_game, isolated_env, monkeypatch, tmp_path):
    import json

    ws = workspace.create(fake_game, scan.run_recon(fake_game), "clean")
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    assert workspace.claude_trusted(ws) is None  # no ~/.claude.json
    (home / ".claude.json").write_text(json.dumps({"projects": {}}), encoding="utf-8")
    assert workspace.claude_trusted(ws) is False
    key = ws.as_posix()  # Claude Code keys projects with forward slashes
    (home / ".claude.json").write_text(json.dumps({"projects": {key: {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    assert workspace.claude_trusted(ws) is True
    settings = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "Skill(gst:game-study *)" in settings["permissions"]["allow"]
