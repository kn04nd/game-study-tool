"""Hook handlers with JSON payloads, as Claude Code sends them."""

import io
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

from gst import config, guard, hooks, policy, workspace
from gst.common import norm_path
from gst.hooks import handlers
from gst.recon import scan


def run_hook(name, payload, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main(name) == 0
    out = capsys.readouterr().out
    return json.loads(out) if out.strip() else None


@pytest.fixture
def ws(tmp_path, isolated_env, monkeypatch):
    root = tmp_path / "Steam" / "Clean Game"
    for rel, data in {"CleanGame.exe": b"MZ", "UnityPlayer.dll": b"MZ", "CleanGame_Data/globalgamemanagers": b"2021.3.1f1",
                      "CleanGame_Data/Managed/Assembly-CSharp.dll": b"MZ"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    game = {"name": "Clean Game", "store": None, "appid": None, "path": str(root)}
    w = workspace.create(game, scan.run_recon(game), "clean")
    monkeypatch.setattr("gst.doctor.brief", lambda refresh=False: ["gst 0.1.0 | core tools 13/13 ok", "warn x"])
    return w


def test_hook_names_match_handlers():
    assert set(hooks.HOOKS) == set(handlers.HANDLERS)


def test_session_start_outside_workspace_is_one_line(tmp_path, monkeypatch, capsys):
    out = run_hook("session-start", {"cwd": str(tmp_path), "source": "startup"}, monkeypatch, capsys)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "Game Study Toolkit" in ctx and len(ctx) < 400


def test_session_start_in_workspace_has_digest_and_title(ws, monkeypatch, capsys, tmp_path):
    env_file = tmp_path / "env.sh"
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
    out = run_hook("session-start", {"cwd": str(ws), "source": "startup"}, monkeypatch, capsys)["hookSpecificOutput"]
    assert "Clean Game" in out["additionalContext"] and "offline-ok" in out["additionalContext"]
    assert out["sessionTitle"].startswith("gst: Clean Game")
    assert len(out["additionalContext"]) <= handlers.CONTEXT_LIMIT
    assert "game-study-path" in env_file.read_text(encoding="utf-8")
    run_hook("session-start", {"cwd": str(ws), "source": "clear"}, monkeypatch, capsys)
    assert env_file.read_text(encoding="utf-8").count("game-study-path") == 1


def test_compact_digest_reinjects_facts(ws, monkeypatch, capsys):
    (ws / "FACTS.md").write_text("| Player HP | GameAssembly.dll+0x1A2B3C | x | sim |\n", encoding="utf-8")
    out = run_hook("compact-digest", {"cwd": str(ws), "source": "compact"}, monkeypatch, capsys)
    assert "GameAssembly.dll+0x1A2B3C" in out["hookSpecificOutput"]["additionalContext"]


def test_pre_compact_autocaptures_addresses(ws, monkeypatch, capsys, tmp_path):
    transcript = tmp_path / "t.jsonl"
    msgs = [{"message": {"role": "assistant", "content": [{"type": "text", "text": "The health field lives at Game.exe+0x4F2A10 in build 42."}]}},
            {"message": {"role": "user", "content": "ok"}}]
    transcript.write_text("\n".join(json.dumps(m) for m in msgs), encoding="utf-8")
    assert run_hook("pre-compact", {"cwd": str(ws), "trigger": "auto", "transcript_path": str(transcript)}, monkeypatch, capsys) is None
    auto = (ws / "notes" / "_autocaptured.md").read_text(encoding="utf-8")
    assert "Game.exe+0x4F2A10" in auto
    assert "compaction checkpoint" in (ws / "STUDY.md").read_text(encoding="utf-8")


def test_pre_tool_denies_steamless(tmp_path, monkeypatch, capsys):
    out = run_hook("pre-tool", {"tool_name": "Bash", "tool_input": {"command": "Steamless.CLI.exe x.exe"}, "cwd": str(tmp_path)},
                   monkeypatch, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_allows_silently(tmp_path, monkeypatch, capsys):
    out = run_hook("pre-tool", {"tool_name": "Bash", "tool_input": {"command": "rg -n Player"}, "cwd": str(tmp_path)},
                   monkeypatch, capsys)
    assert out is None


def test_pre_tool_rewrites_bare_gs_in_powershell(tmp_path, monkeypatch, capsys):
    fake = tmp_path / "gst.exe"
    fake.write_bytes(b"MZ")
    monkeypatch.setattr(hooks, "GS_EXE", str(fake))
    call = f"& '{fake.as_posix()}'"
    assert hooks.gs_call() == call
    payload = {"tool_name": "PowerShell", "tool_input": {"command": "gst recon; gst status", "timeout": 5}, "cwd": str(tmp_path)}
    out = run_hook("pre-tool", payload, monkeypatch, capsys)["hookSpecificOutput"]  # fast path (no guard)
    assert out["updatedInput"] == {"command": f"{call} recon; {call} status", "timeout": 5}
    assert out["permissionDecision"] == "defer"
    payload["tool_input"]["command"] = "gst config show"  # "config" sends it through the guard first
    out = run_hook("pre-tool", payload, monkeypatch, capsys)["hookSpecificOutput"]
    assert out["updatedInput"]["command"] == f"{call} config show"
    payload["tool_input"]["command"] = "Get-Date"
    assert run_hook("pre-tool", payload, monkeypatch, capsys) is None
    # once gst.exe's folder is on PATH (VS Code restarted), keep `gst` so PowerShell(gst *) rules match
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    payload["tool_input"]["command"] = "gst recon"
    assert hooks.gs_on_path() and run_hook("pre-tool", payload, monkeypatch, capsys) is None


def test_workspace_settings_allow_the_rewritten_gs(ws):
    settings = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert f"PowerShell({hooks.gs_call()} *)" in settings["permissions"]["allow"]


def test_status_short_never_fails_outside_a_workspace(tmp_path, monkeypatch, capsys):
    from gst import cli

    monkeypatch.chdir(tmp_path)
    assert cli.main(["status", "--short"]) == 0
    assert "no study workspace here" in capsys.readouterr().out
    assert cli.main(["status"]) == 1


def test_pre_tool_denies_decompiled_write_into_git(git_repo, monkeypatch, capsys):
    code = "\n".join(f"undefined8 FUN_14000{i:04x}(void);" for i in range(8))
    out = run_hook("pre-tool", {"tool_name": "Write", "tool_input": {"file_path": str(git_repo / "src" / "x.c"), "content": code}},
                   monkeypatch, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_agent_scope(ws, monkeypatch, capsys):
    payload = {"tool_name": "Write", "agent_type": "gst:recon",
               "tool_input": {"file_path": str(ws / "notes" / "x.md"), "content": "hi"}}
    out = run_hook("pre-tool", payload, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_read_hint_for_huge_decomp(ws, monkeypatch, capsys):
    big = ws / "decomp" / "cs" / "Huge.cs"
    big.parent.mkdir(parents=True)
    big.write_text("x" * 500_000, encoding="utf-8")
    out = run_hook("pre-tool", {"tool_name": "Read", "tool_input": {"file_path": str(big)}}, monkeypatch, capsys)
    assert "offset/limit" in out["hookSpecificOutput"]["additionalContext"]
    small = ws / "decomp" / "cs" / "Small.cs"
    small.write_text("class A {}", encoding="utf-8")
    assert run_hook("pre-tool", {"tool_name": "Read", "tool_input": {"file_path": str(small)}}, monkeypatch, capsys) is None


def test_post_tool_logs_and_journals(ws, monkeypatch, capsys):
    run_hook("post-tool", {"tool_name": "Bash", "cwd": str(ws), "tool_input": {"command": "gst decompile dotnet"},
                           "tool_response": {}}, monkeypatch, capsys)
    run_hook("post-tool", {"tool_name": "Bash", "cwd": str(ws), "tool_input": {"command": "gst status"},
                           "tool_response": {}}, monkeypatch, capsys)
    log = (ws / "logs" / "commands.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == 2
    study = (ws / "STUDY.md").read_text(encoding="utf-8")
    assert "`gst decompile dotnet`" in study and "`gst status`" not in study


def test_post_tool_outside_workspace_does_nothing(tmp_path, monkeypatch, capsys):
    run_hook("post-tool", {"tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": "gst recon"}}, monkeypatch, capsys)
    assert not (tmp_path / "logs").exists()


def test_unknown_hook_and_garbage_input_never_fail(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    assert hooks.main("pre-tool") == 0
    assert hooks.main("nope") == 0


def test_payload_with_bom_and_bytes(monkeypatch, capsys, tmp_path):
    raw = "\ufeff" + json.dumps({"tool_name": "Bash", "tool_input": {"command": "scyllahide"}, "cwd": str(tmp_path)})
    stdin = io.TextIOWrapper(io.BytesIO(raw.encode("utf-8")), encoding="cp1252")
    monkeypatch.setattr("sys.stdin", stdin)
    assert hooks.main("pre-tool") == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_fast_dumps_round_trips():
    doc = {"a": [1, -2.5, 1e16, True, False, None, "x"], "s": "café ☃ \\ \"q\"\n\t", "n": {"k": {}, "l": []}}
    text = hooks.dumps(doc)
    assert text.isascii() and json.loads(text) == doc
    with pytest.raises(TypeError):
        hooks.dumps({"x": object()})


def test_fast_loads_matches_json():
    doc = {"a": [1, 2.5, -3e2, True, False, None], "s": "caf\u00e9 \\ \"q\" \n", "n": {"x": {}}}
    assert hooks.loads("  \n" + json.dumps(doc)) == doc
    assert hooks.loads(json.dumps(doc, ensure_ascii=False)) == doc
    assert hooks.loads("   ") == {}
    with pytest.raises(ValueError):
        hooks.loads("{bad")


def test_fast_find_workspace_matches_config(ws, tmp_path):
    deep = ws / "notes" / "sub"
    deep.mkdir(parents=True)
    assert Path(hooks.find_workspace(str(deep))).resolve() == config.find_workspace(deep).resolve()
    assert hooks.find_workspace(str(tmp_path)) is None


# --- the screen in front of the guard ---------------------------------------------------------------

RULE_SAMPLES = [
    "Steamless.CLI.exe --quiet Game.exe",
    "ScyllaHide.exe -p 1234",
    "renderdoc-pro capture game.exe",
    "RenderDoc.NET.exe",
    "AESDumpster.exe Game-Win64-Shipping.exe",
    "aes key finder x.exe",
    "AESFinder game.exe",
    "UE4 AES Key extractor",
    "Forza-Crypto-Tool.exe decrypt media.zip",
    "cricodecs x.acb --recover-key",
    "cricodecs x.acb --build out",
    "010Editor.exe save.bin -process 1234",
    "\"C:/Program Files/010 Editor/010 Editor.exe\" -process 77",
    "quickbms.exe script.bms process://1234 out",
    "UXM.exe patch C:/Games/DS3",
    "TTD.exe -accepteula -monitor game.exe",
    "sc stop BEService",
    "sc.exe delete EasyAntiCheat_EOS",
    "sc config vgc start= disabled",
    "Stop-Service -Name BEService",
    "Set-Service vgk -StartupType Disabled",
    "Remove-Service FACEIT",
    "taskkill /F /IM EasyAntiCheat.exe",
    "Stop-Process -Name xhunter1 -Force",
    "dbk64 load",
    "enable dbvm now",
    "gdre_tools --headless --pck-patch=Game.pck --patch-file=x.gd",
    "gdre_tools.exe --headless --pck-create=out.pck --directory=mod",
    "repak pack mod_dir out_P.pak",
    "retoc to-zen legacy_P.pak out.utoc",
    "UAssetGUI.exe fromjson edited.json Hero.uasset VER_UE4_27",
    "pyghidra-mcp --project-path D:/game-study/x/p.gpr",
    "pyghidra-mcp --no-symbols -t streamable-http --host 0.0.0.0",
]
TRIGGER_SAMPLES = [
    "git add decomp/x.cs",
    "git -C D:/game-study/oni commit -m x",
    "git init",
    "git stage assets/a.png",
    'echo x > "C:/Program Files (x86)/Steam/steamapps/common/Game/x.txt"',
    "dir >> /c/XboxGames/x.txt",
    "type a 2>&1 >C:\\GOG Games\\x.txt",
    "echo x > \"' C:/GOG Games/x'\"",
    "Get-Content a | Out-File -FilePath 'C:/GOG Games/x'",
    "Get-Content a | Set-Content C:/x/y",
    "echo hi | tee 'C:/GOG Games/x'",
    "repak unpack C:/x/pakchunk0.pak",
    "hgpaktool -U C:/NMS/GAMEDATA/PCBANKS/NMSARC.pak",
    "repak --aes-key 0x1234 unpack x.pak -o out",
    "repak -a0x1234 unpack x.pak -o out",
    "retoc -a 0x1234 to-legacy x.utoc out",
    "rustydemon --tact-keys keys.txt extract D:/Games/Diablo IV",
    "gdre_tools --headless --key=abc --recover=x.pck",
    "retoc -k abc to-legacy x.utoc out",
    "unrpa -k 0x42 archive.rpa",
    "pyinstxtractor-ng --key abc game.exe",
    "uas export json --aes=abc",
    "uas export Json --paks P --game GAME_UE5_7 -a 0x1234 --filter x",
    "uas.exe list -p P -g G -A keys.txt",
    "gdre_tools --headless \\\n --pck-create=out.pck",
    "x64dbg.exe C:/Games/Game.exe",
    "x32dbg", "x96dbg.exe",
    "frida-trace -p 1234 -i CreateFileW",
    "frida-ps -a",
    "procdump64 -ma 1234 out.dmp",
    "windbgx -p 12",
    "cdb -pn game.exe",
    "qrenderdoc --py rd_export.py",
    "renderdoccmd capture game.exe",
    "pixtool.exe programmatic-capture",
    "ngfx-capture --exe game.exe",
    "apitrace trace game.exe",
    "drrun -t drltrace -- game.exe",
    "drstrace game.exe",
    "pe-sieve /pid 12",
    "hollows_hunter /pname game.exe",
    "WinApiOverride64.exe",
    "tinytracer run",
    "PresentMon-2.6.0-x64.exe --process_name game.exe",
    "cheatengine-x86_64.exe",
    "ttd -attach 123 -out C:/t",
]
BENIGN = [
    "rg -n Player decomp/",
    "gst status",
    "gst recon --deep",
    "ls -la",
    "git status",
    "git diff --stat",
    "git log --oneline -5",
    "python script.py 2>&1",
    "cat a.txt > /dev/null",
    "echo hi > notes/x.md",
    "pytest -q tests/unit",
    "Get-ChildItem -Recurse | Select-Object -First 5",
]


def guard_triggers(command: str) -> bool:
    """Every regex gate of guard.check_command (independent of registry/workspace state)."""
    absolute = lambda t: re.match(r"^[a-z]:", norm_path(t.strip("\"'"))) is not None  # noqa: E731
    command = guard._CONTINUATION.sub(" ", command)
    return bool(
        policy.deny_rule_for(command)
        or policy.RUNTIME_TOOLS.search(command)
        or (policy.KEY_TOOLS.search(command) and policy.KEY_FLAGS.search(command))
        or guard._GIT_MUTATING.search(command)
        or any(absolute(m.group("t")) for m in guard._REDIRECT.finditer(command))
        or re.search(r"\brepak(?:\.exe)?\s+unpack\b", command, re.IGNORECASE)
        or re.search(r"\bhgpaktool\b", command, re.IGNORECASE)
    )


def test_every_deny_rule_has_a_sample():
    for rule in policy.DENY_RULES:
        assert any(rule.pattern.search(s) for s in RULE_SAMPLES), f"add a RULE_SAMPLES entry for {rule}"


@pytest.mark.parametrize("command", RULE_SAMPLES + TRIGGER_SAMPLES)
def test_screen_passes_everything_the_guard_could_stop(command):
    assert guard_triggers(command), "sample does not exercise the guard"
    assert hooks.screen_command(command)
    assert hooks.screen_command(command.upper())


@pytest.mark.parametrize("command", BENIGN)
def test_screen_skips_ordinary_commands(command):
    assert not guard_triggers(command)
    assert not hooks.screen_command(command)


def test_screen_is_a_superset_of_the_guard_on_random_commands():
    rng = random.Random(20261007)
    words = [s.split()[0] for s in RULE_SAMPLES + TRIGGER_SAMPLES] + [
        "git", "add", "commit", "-C", "x", "-k", "--key", "--aes", "aes=1", ">", ">>", "2>&1", "|", "tee", ";", "&&",
        "C:/Program Files (x86)/Steam/steamapps/common/G/x", "/c/XboxGames/y", "'C:/GOG Games/z'", "/dev/null",
        "unpack", "-o", "out", "stop", "BEService", "-monitor", "-process", "process://1", "patch", "load", "dbk32",
        "ſteamless", "taskkıll", "\u212aill", "frıda", "gst", "status", "\t", "\n", "-a", "-A", "uas", "\\\n", "`\n",
    ]
    for _ in range(20000):
        command = " ".join(rng.choice(words) for _ in range(rng.randint(1, 6)))
        if rng.random() < 0.3:
            command = command.swapcase()
        if guard_triggers(command):
            assert hooks.screen_command(command), command


GS_AT_START = re.compile(r"(^|;\s*)gst(?=\s|$)")  # the specification rewrite_gs implements


def test_rewrite_gs_matches_the_regex_spec():
    exe = "C:\\Users\\O'Neil\\.local\\bin\\gs.exe"
    call = "& 'C:\\Users\\O''Neil\\.local\\bin\\gs.exe'"
    spec = lambda c: GS_AT_START.sub(lambda m: m.group(1) + call, c)  # noqa: E731
    fixed = ["gst recon; gst status", "Get-Date;  gst status", "gst", "x;gst", "gst;gst", "gsx; gst\tq", " gst status", "gst\n", "a;\n gst q"]
    rng = random.Random(7)
    pieces = ["gst", "gst ", " gst", ";", "; ", "\t", "\n", "x", "status", "gsx", "\u00a0", "\u2003gs", "rg"]
    rand = ["".join(rng.choice(pieces) for _ in range(rng.randint(1, 7))) for _ in range(5000)]
    for command in fixed + rand:
        assert hooks.rewrite_gs(command, exe) == spec(command), repr(command)


def test_hooks_json_uses_known_hook_names():
    root = Path(hooks.__file__).resolve().parents[3] / "plugins" / "gst" / "hooks"
    data = json.loads((root / "hooks.json").read_text(encoding="utf-8"))
    names = set()
    for groups in data["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                m = re.fullmatch(r'GS_HOOK=([a-z-]+) \. "\$\{CLAUDE_PLUGIN_ROOT\}/hooks/run\.sh"', handler["command"])
                assert m, handler["command"]
                names.add(m.group(1))
    assert names == set(hooks.HOOKS)


def test_benign_pre_tool_imports_nothing_heavy(tmp_path):
    """The hot path must not import re/json/pathlib or the handlers (each costs 30-100 ms on Windows)."""
    src = str(Path(hooks.__file__).resolve().parents[2])
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rg -n Player"}, "cwd": str(tmp_path)})
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from gst.hooks import main; main('pre-tool'); "
            "print(sorted(m for m in ('re', 'json', 'pathlib', 'gst.hooks.handlers', 'gst.guard') if m in sys.modules))")
    proc = subprocess.run([sys.executable, "-S", "-E", "-c", code, src], input=payload, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"


# --- launcher config (run.sh fast path) ---------------------------------------------------------------

def test_ensure_launcher_cfg_and_state(isolated_env):
    assert hooks.launcher_state()["state"] == "missing"
    path = hooks.ensure_launcher_cfg()
    assert path and Path(path).parent == config.state_dir()
    exe, src = Path(path).read_text(encoding="utf-8").splitlines()
    assert Path(exe).is_file() and (Path(src) / "gst" / "hooks" / "__init__.py").is_file()
    assert hooks.launcher_state()["state"] == "ok"
    Path(path).write_text("C:/old/python.exe\nC:/old/src\n", encoding="utf-8")
    assert hooks.launcher_state()["state"] == "stale"
    hooks.ensure_launcher_cfg()
    assert hooks.launcher_state()["state"] == "ok"


def test_cli_fast_path_runs_hook_and_records_cfg(monkeypatch, capsys, tmp_path, isolated_env):
    from gst import cli

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path), "source": "startup"})))
    assert cli.main(["hook", "session-start"]) == 0
    assert "additionalContext" in capsys.readouterr().out
    assert hooks.launcher_state()["state"] == "ok"


@pytest.mark.skipif(os.name != "nt", reason="Git Bash launcher")
def test_run_sh_fast_path_end_to_end(tmp_path, isolated_env):
    bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
    if not bash.is_file():
        pytest.skip("Git Bash not installed")
    hooks.ensure_launcher_cfg()
    run_sh = Path(hooks.__file__).resolve().parents[3] / "plugins" / "gst" / "hooks" / "run.sh"
    env = {**os.environ, "GST_EXE": str(tmp_path / "missing.exe"), "HOME": str(tmp_path)}  # no gst.exe fallback
    deny = json.dumps({"tool_name": "Bash", "tool_input": {"command": "sc stop BEService"}, "cwd": str(tmp_path)})
    for argv in ([str(bash), str(run_sh), "pre-tool"],                        # bash run.sh <name>
                 [str(bash), "-c", f'GS_HOOK=pre-tool . "{run_sh}"']):        # as hooks.json runs it
        proc = subprocess.run(argv, input=deny, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", proc.stderr
    proc = subprocess.run([str(bash), "-c", f'GS_HOOK=nothing-known . "{run_sh}"'], input="{}", capture_output=True,
                          text=True, encoding="utf-8", env=env, timeout=60)
    assert proc.returncode == 0 and proc.stdout == ""


def test_recon_agent_shell_goes_through_the_guard(tmp_path, monkeypatch, capsys):
    payload = {"tool_name": "Bash", "agent_type": "gst:recon", "tool_input": {"command": "gst new oni"}, "cwd": str(tmp_path)}
    out = run_hook("pre-tool", payload, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "read-only" in out["hookSpecificOutput"]["permissionDecisionReason"]
    payload["tool_input"]["command"] = "gst recon"
    assert run_hook("pre-tool", payload, monkeypatch, capsys) is None


@pytest.mark.parametrize("name", ["compact-digest", "pre-compact", "session-end"])
def test_workspace_only_hooks_skip_the_handlers_outside_studies(name, tmp_path):
    src = str(Path(hooks.__file__).resolve().parents[2])
    code = (f"import sys; sys.path.insert(0, sys.argv[1]); from gst.hooks import main; main({name!r}); "
            "print('gst.hooks.handlers' in sys.modules)")
    proc = subprocess.run([sys.executable, "-S", "-E", "-c", code, src], input=json.dumps({"cwd": str(tmp_path)}),
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.stdout.strip() == "False", proc.stderr
