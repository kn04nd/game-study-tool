"""Coexistence with the legacy gs: config fallback, GS_* env names, tools roots, v1 workspaces."""

import json

import pytest

from gst import config, tools, workspace
from gst.common import GsError
from gst.compat import legacy


@pytest.fixture
def clean_env(isolated_env, monkeypatch):
    for name in ("GST_HOME", "GST_TOOLS", "GST_ONLINE", "GST_GAME", "GS_GAME", "GS_TOOLS"):
        monkeypatch.delenv(name, raising=False)
    return isolated_env


def write_legacy_config(state, text):
    folder = state / "game-study"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.toml").write_text(text, encoding="utf-8")


def test_legacy_config_is_inherited_until_gst_has_its_own(clean_env, monkeypatch, tmp_path):
    monkeypatch.delenv("GS_HOME", raising=False)
    write_legacy_config(clean_env["state"], f'gs_home = "{(tmp_path / "legacy-home").as_posix()}"\n')
    assert config.effective_config_path() == config.legacy_config_path()
    assert config.gs_home() == tmp_path / "legacy-home"
    config.save_config({"gs_home": (tmp_path / "own").as_posix()})
    assert config.effective_config_path() == config.config_path()
    assert config.gs_home() == tmp_path / "own"
    assert "legacy-home" in config.legacy_config_path().read_text(encoding="utf-8")  # never written


def test_gst_env_names_win_over_legacy_ones(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GS_HOME", str(tmp_path / "a"))
    assert config.gs_home() == tmp_path / "a"
    monkeypatch.setenv("GST_HOME", str(tmp_path / "b"))
    assert config.gs_home() == tmp_path / "b"
    monkeypatch.setenv("GS_GAME", "old")
    assert config.current_game() == "old"
    monkeypatch.setenv("GST_GAME", "new")
    assert config.current_game() == "new"


def test_tools_fall_back_to_the_legacy_root_and_lock(clean_env):
    legacy_root = clean_env["state"] / "game-study" / "tools"
    exe = legacy_root / "fake" / "bin" / "fake.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    (legacy_root / "tools.lock.json").write_text(json.dumps({"fake": {"version": "1.0", "bins": {"fake.exe": str(exe)}}}),
                                                 encoding="utf-8")
    assert config.tools_dirs() == [config.tools_dir(), legacy_root]
    tool = tools.Tool(id="fake", title="Fake", kind="zip", profiles=[], bins=["fake.exe"], version="1.0")
    assert tools.bin_path(tool) == exe
    assert tools.read_lock_entry("fake")["version"] == "1.0"
    assert config.tools_dir() == clean_env["state"] / "game-study-tool" / "tools"


def test_zip_tools_are_found_under_the_legacy_install_dir(clean_env):
    tool = tools.Tool(id="zipped", title="Z", kind="zip", profiles=[], bins=["z/z.exe"], version="2")
    target = clean_env["state"] / "game-study" / "tools" / "zipped" / "2" / "z" / "z.exe"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"MZ")
    assert tools.bin_path(tool) == target


def make_v1(home, slug="oldgame"):
    ws = home / slug
    for sub in legacy.V1_SUBDIRS:
        (ws / sub).mkdir(parents=True)
    (ws / "decomp" / "cs").mkdir()
    (ws / "decomp" / "cs" / "A.cs").write_text("class A {}", encoding="utf-8")
    game = {"gs_workspace": 1, "slug": slug, "name": "Old Game", "install_dir": str(home / "install"),
            "engine": {"key": "unity-mono", "label": "Unity (Mono)", "version": "2022.3"}, "verdict": "offline-ok"}
    (ws / "game.json").write_text(json.dumps(game), encoding="utf-8")
    return ws


def test_v1_workspace_resolves_by_slug_and_env(clean_env, monkeypatch, tmp_path):
    ws = make_v1(clean_env["home"])
    monkeypatch.chdir(tmp_path)
    assert workspace.current(slug="oldgame")[0] == ws
    assert workspace.current(slug="Old Game")[0] == ws
    with pytest.raises(GsError):
        workspace.current(slug="nope")
    assert workspace.current(required=False) == (None, None)
    monkeypatch.setenv("GS_GAME", "oldgame")
    assert workspace.current()[0] == ws


def test_migration_plan_is_read_only(clean_env):
    ws = make_v1(clean_env["home"])
    before = sorted(p.relative_to(ws) for p in ws.rglob("*"))
    plan = legacy.plan_migration(ws)
    assert plan.version == 1 and plan.create_dirs == ["jobs", "analysis"]
    assert plan.import_as_legacy_jobs == {"decomp/cs": 1}
    assert sorted(p.relative_to(ws) for p in ws.rglob("*")) == before


def test_status_and_migrate_commands_take_a_slug(clean_env, monkeypatch, tmp_path, capsys):
    from gst import cli

    make_v1(clean_env["home"])
    monkeypatch.chdir(tmp_path)
    assert cli.main(["status", "oldgame", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "Old Game"
    assert cli.main(["migrate", "oldgame", "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["plans"][0]["create_dirs"] == ["jobs", "analysis"]
    assert cli.main(["migrate", "oldgame"]) == 0  # the writing migration (tests/unit/test_migrate.py)
