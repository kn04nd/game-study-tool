import json

import pytest

from gst import cli


def test_help_lists_groups(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    for group in ("config", "guard"):
        assert group in out


def _group_names():
    parser = cli.build_parser()
    action = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return sorted(action.choices)


@pytest.mark.parametrize("group", _group_names())
def test_every_group_has_help(group, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([group, "--help"])
    assert exc.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.startswith("gst ")


def test_config_show_json(capsys, isolated_env):
    assert cli.main(["config", "show", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["gs_home"] == str(isolated_env["home"])


def test_config_set_rejects_home_inside_git(git_repo, capsys):
    assert cli.main(["config", "set", "gs_home", str(git_repo / "studies")]) == 1
    assert "outside any git repository" in capsys.readouterr().err


def test_config_set_and_unset(capsys):
    assert cli.main(["config", "set", "online_metadata", "yes"]) == 0
    from gst import config

    assert config.load_config()["online_metadata"] == "yes"
    assert cli.main(["config", "unset", "online_metadata"]) == 0
    assert "online_metadata" not in config.load_config()


def test_guard_cmd_exit_codes(capsys):
    assert cli.main(["guard", "cmd", "rg -n Player decomp"]) == 0
    assert cli.main(["guard", "cmd", "Steamless.CLI.exe game.exe"]) == 3
