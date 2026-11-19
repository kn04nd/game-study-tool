"""Regression tests for the security review: game-controlled names and paths, batch-file arguments, tool
downloads, workspace permissions and the hook launcher."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from gst import policy, workspace
from gst.assets import safe_name
from gst.common import GsError, check_bat_args, contained
from gst.engines.unity.export import Writer
from gst.engines.unreal import uas
from gst.recon.stores import build_id
from gst.tools import installer

ROOT = Path(__file__).resolve().parents[2]


# --- batch files (cmd.exe parses every argument) ---------------------------------------------------

@pytest.mark.parametrize("arg", ["x&calc.exe&.dll", "a|b", "a>b", "%PATH%", "a^b", 'a"b', "a!b", "a\nb"])
def test_bat_args_refuse_cmd_metacharacters(arg):
    with pytest.raises(GsError, match="cmd.exe metacharacters"):
        check_bat_args(["tool.bat", "D:/ok/path", arg])


def test_bat_args_accept_plain_paths():
    check_bat_args(["tool.bat", "D:/SteamLibrary/steamapps/common/Some Game (x86)/Game_Data", "-x", "@D:/w/f.txt"])


def test_rea_batch_shim_refuses_a_game_name_with_metacharacters(tmp_path):
    from gst.rea import runner

    dll = tmp_path / "x&calc.exe&.dll"
    dll.write_bytes(b"MZ")
    shim = runner.Rea(argv=(str(tmp_path / "rea.cmd"),), path=str(tmp_path / "rea.cmd"), version="6.9.0")
    with pytest.raises(GsError, match="cmd.exe metacharacters"):
        runner.run(tmp_path / "ws", "inspect_managed_members", "x", path=dll, rea=shim, raise_caps=False)


def test_native_ghidra_command_is_checked(tmp_path):
    from gst.decompile import native

    bad = tmp_path / "R&D"
    with pytest.raises(GsError, match="cmd.exe metacharacters"):
        native.command(Path("analyzeHeadless.bat"), bad / "p" / "proj", bad / "m.exe", bad / "out", pdb=None,
                       max_functions=0, seconds=0, only_file=None, analysis_timeout=None, reuse=False, log=bad / "l.log")


# --- names and paths from game data ----------------------------------------------------------------

def test_unity_writer_collision_key_cannot_leave_the_output(tmp_path):
    w = Writer(tmp_path / "assets" / "unity", tmp_path, source="G_Data/sharedassets0.assets", build="1", tool="gst",
               tool_version="", extractor="unity.monobehaviours", unit="u")
    evil = "x/../../../../../../hooks/evil"
    first = w.target("MonoBehaviour", safe_name(evil), ".jsonl", evil, "_components")
    second = w.target("MonoBehaviour", evil, ".jsonl", evil, "_components")
    out = (tmp_path / "assets" / "unity").resolve()
    for p in (first, second):
        assert out in p.resolve().parents
        assert ".." not in p.relative_to(tmp_path).parts


@pytest.mark.parametrize("name", ["CON", "nul", "COM1.png", "LPT9"])
def test_safe_name_avoids_windows_device_names(name):
    assert safe_name(name).startswith("_")


@pytest.mark.parametrize("raw, expected", [
    ("12345678", "12345678"), ("1.2.3-CL-456+Release", "1.2.3-CL-456+Release"),
    ("1/../../Users/x", "1_.._.._Users_x"), ("..", None), ("", None), (None, None), ("C:\\x", "C_x"),
])
def test_store_build_ids_are_path_safe(raw, expected):
    got = build_id(raw)
    assert got == expected
    if got:
        assert "/" not in got and "\\" not in got and ":" not in got


@pytest.mark.parametrize("rel", ["//attacker/share/x.bin", "\\\\attacker\\share\\x.bin", "C:/Windows/win.ini",
                                 "C:x.bin", "/etc/passwd", "../../outside.bin", "a/../../outside.bin"])
def test_contained_refuses_escapes(tmp_path, rel):
    assert contained(tmp_path, rel) is None


def test_contained_keeps_relative_paths(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "m.bin").write_bytes(b"x")
    assert contained(tmp_path, "sub/m.bin") == tmp_path / "sub" / "m.bin"
    assert contained(tmp_path, "sub/../sub/m.bin") is not None


def test_gltf_buffer_uri_outside_the_folder_is_not_read(tmp_path):
    from gst.analysis import gltf_summary

    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"secret")
    model = tmp_path / "assets" / "m.gltf"
    model.parent.mkdir()
    assert gltf_summary._uri(model, "../secret.bin", True) is None
    assert gltf_summary._uri(model, str(secret), True) is None
    (model.parent / "ok.bin").write_bytes(b"ok")
    assert gltf_summary._uri(model, "ok.bin", True) == b"ok"


def test_uas_response_file_refuses_line_breaks_and_nested_files(tmp_path):
    with pytest.raises(GsError, match="refused"):
        uas.response_file(tmp_path / "a.rsp", ["--filter", "Game/x\n--mode\nRaw"])
    with pytest.raises(GsError, match="refused"):
        uas.response_file(tmp_path / "b.rsp", ["@D:/elsewhere.rsp"])
    uas.response_file(tmp_path / "c.rsp", ["--filter", "^Game/Characters/(Hero|Enemy)"])


# --- tool downloads --------------------------------------------------------------------------------

@pytest.mark.parametrize("member", ["C:/evil.exe", "C:evil.exe", "//server/share/evil.exe", "/abs/evil.exe",
                                    "a/../../evil.exe"])
def test_extract_zip_refuses_absolute_and_drive_members(tmp_path, member):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr(member, "x")
    with pytest.raises(GsError, match="unsafe path"):
        installer.extract_zip(evil, tmp_path / "out")


def test_catalog_has_no_trust_on_first_use_downloads():
    from gst import tools

    tofu = [t.id for t in tools.load_catalog().values() if getattr(t, "sha256", "") == "tofu"]
    assert tofu == []


def test_redirects_never_downgrade_to_http():
    import urllib.error
    import urllib.request

    handler = installer._HttpsOnlyRedirects()
    req = urllib.request.Request("https://example.org/tool.zip")
    with pytest.raises(urllib.error.URLError, match="refused redirect"):
        handler.redirect_request(req, None, 302, "Found", {}, "http://example.org/tool.zip")
    assert handler.redirect_request(req, None, 302, "Found", {}, "https://cdn.example.org/tool.zip") is not None


# --- workspace permissions, hooks, policy ------------------------------------------------------------

def test_workspace_asks_before_commands_that_run_workspace_files(tmp_path):
    perms = workspace.workspace_settings(tmp_path, {"install_dir": str(tmp_path / "game"), "slug": "x"})["permissions"]
    ask = perms["ask"]
    for rule in ("Bash(gst fmt roundtrip *)", "PowerShell(gst fmt roundtrip *)", "Bash(gst config set *)",
                 "Bash(gst tools install *)", "Bash(gst setup *)", "Bash(rg *--pre*)", "Bash(ast-grep *-U*)"):
        assert rule in ask
    assert "Bash(gst *)" in perms["allow"]


def test_hook_and_shims_keep_the_current_folder_off_sys_path():
    run_sh = (ROOT / "plugins/gst/hooks/run.sh").read_text(encoding="utf-8")
    assert '"$gs_py" -S -E -P ' in run_sh
    assert "python -P -X utf8 -m gst" in (ROOT / "plugins/gst/bin/gst").read_text(encoding="utf-8")
    assert "python -P -X utf8 -m gst" in (ROOT / "plugins/gst/bin/gst.cmd").read_text(encoding="utf-8")
    assert '"-P", "-X", "utf8", "-m", "gst.pipeline.worker"' in (ROOT / "src/gst/pipeline/runner.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("keys", [["x"], "x", 1, [None], [{"source": 3}]])
def test_malformed_keys_never_crash_the_policy(keys):
    assert not policy.check({"keys": keys}, "decrypt").allowed
