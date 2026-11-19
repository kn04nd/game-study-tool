"""WinError 206: no GDRE or uas command line grows past 30k characters, however many files are selected."""

from __future__ import annotations

import subprocess
from pathlib import Path

from gst.assets import unreal
from gst.decompile import godot


def test_5000_files_never_exceed_30k_chars_per_command(tmp_path):
    exe = tmp_path / "tools" / "gdre_tools.exe"
    pack = tmp_path / "Game Folder" / "Game.pck"
    out = tmp_path / "ws" / "assets" / "godot" / "extract"
    files = [f"res://assets/characters/npc_{i:05d}/textures/npc_{i:05d}_albedo_with_a_long_name.png" for i in range(5000)]
    cmds = godot.batched_commands(exe, pack, out, files, excludes=("*.cs",))
    assert len(cmds) > 1
    for cmd in cmds:
        assert len(subprocess.list2cmdline(cmd)) <= godot.MAX_CMDLINE
        assert cmd[:4] == [str(exe), "--headless", f"--recover={pack}", f"--output={out}"] and "--exclude=*.cs" in cmd
    selected = [c[len("--include="):] for cmd in cmds for c in cmd if c.startswith("--include=")]
    assert selected == files  # every file, once, in order


def test_whole_project_is_one_call(tmp_path):
    cmd = godot.command(tmp_path / "gdre.exe", tmp_path / "g.pck", tmp_path / "out", includes=(), excludes=("*.cs",))
    assert not any(c.startswith("--include=") for c in cmd) and len(subprocess.list2cmdline(cmd)) < 1000

def test_unreal_filter_of_5000_paths_is_one_uas_call(tmp_path, monkeypatch):
    """No batches (each narrower batch would delete the previous outputs): one call, the filter in a response file."""
    import re
    from types import SimpleNamespace

    from gst.engines import unreal as ue
    from gst.engines.unreal import uas

    calls = []
    monkeypatch.setattr(ue, "tool_bin", lambda tool: Path("C:/tools/uas.exe"))
    monkeypatch.setattr(ue, "run", lambda cmd, timeout=None: calls.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    paths = [f"Game/Content/Characters/Hero_{i:05d}/BP_Hero_{i:05d}.uasset" for i in range(5000)]
    flt = unreal.exact_filter(paths)
    uas.export(tmp_path, tmp_path / "out", "json", "GAME_UE4_27", log=tmp_path / "l.log", rsp=tmp_path / "a.rsp", flt=flt)
    assert len(calls) == 1 and len(subprocess.list2cmdline(calls[0])) < 1000
    rx = re.compile(flt)
    assert all(rx.search(p) for p in paths[::250]) and not rx.search("Game/Content/Other/BP_Hero_00001.uasset")
