"""Runtime tools without a game: the policy gate, read-only scans of this test process, a hardware write
watchpoint on a child process (which must keep running after the detach), snapshots, sandbox, symbols."""

import ctypes
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gst import policy, runtime
from gst.common import GsError
from gst.runtime import memory, sandbox, snapshot, watch


@pytest.mark.parametrize("verdict", [policy.NO_RUNTIME, policy.STATIC_ONLY])
def test_runtime_refused_by_verdict(verdict, tmp_path):
    game = {"name": "Protected", "install_dir": str(tmp_path), "verdict": verdict}
    with pytest.raises(GsError, match="runtime tools are not allowed") as exc:
        runtime.target(game, None)
    assert exc.value.code == 3
    with pytest.raises(GsError, match="no sandbox for this game"):
        sandbox.create(tmp_path / "ws", game)


def test_target_accepts_only_the_games_processes(tmp_path, monkeypatch):
    game = {"name": "G", "install_dir": str(tmp_path / "G"), "verdict": policy.OFFLINE_OK}
    monkeypatch.setattr(runtime, "processes", lambda: [
        {"pid": 10, "name": "G.exe", "path": str(tmp_path / "G" / "G.exe")},
        {"pid": 11, "name": "browser.exe", "path": "C:/Program Files/Browser/browser.exe"}])
    assert runtime.target(game, None)["pid"] == 10
    with pytest.raises(GsError, match="not a process of G"):
        runtime.target(game, 11)


def test_scan_and_next_on_this_process():
    marker = random.randint(0x10000000, 0x7FFFFFF0)
    buf = (ctypes.c_int32 * 4)(marker, 7, marker, 9)
    handle = memory.open_process(os.getpid())
    try:
        hits, _scanned = memory.first_scan(handle, str(marker), "int32")
        first = ctypes.addressof(buf)
        assert first in hits and first + 8 in hits
        before = {a: memory.read(handle, a, 4) for a in hits}
        buf[0] = marker - 5
        kept, now = memory.next_scan(handle, hits, "int32", value=None, mode="decreased", previous=before)
        assert list(kept) == [first] and memory.decode(now[first], "int32") == marker - 5
        kept, _ = memory.next_scan(handle, hits, "int32", value=str(marker), mode="", previous=before)
        assert first + 8 in kept and first not in kept
    finally:
        runtime.win.CloseHandle(handle)


CHILD = r"""
import ctypes, sys, time
value = ctypes.c_int32(0)
print(hex(ctypes.addressof(value)), flush=True)
for i in range(4000):
    value.value = i
    time.sleep(0.005)
print("done", value.value, flush=True)
"""


def test_watch_catches_writes_and_detaches(tmp_path):
    # the base interpreter: a venv's python.exe is a launcher that runs the real one in another process
    python = getattr(sys, "_base_executable", sys.executable)
    child = subprocess.Popen([python, "-c", CHILD], stdout=subprocess.PIPE, text=True)
    try:
        address = int(child.stdout.readline(), 16)
        result = watch.watch(child.pid, address, 4, max_hits=3, seconds=15)
        assert result["stopped_by"] == "hits" and len(result["hits"]) == 3
        values = [int.from_bytes(bytes.fromhex(h["bytes"]), "little") for h in result["hits"]]
        assert values == sorted(values) and values[0] > 0
        time.sleep(0.5)
        assert child.poll() is None  # still alive: the debug registers were cleared before detaching
        with pytest.raises(GsError, match="aligned"):
            watch.watch(child.pid, address + 1, 4)
    finally:
        child.kill()
        child.wait(timeout=10)


def test_snapshot_and_diff(tmp_path):
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "slot1.sav").write_bytes(b"SAVE" + bytes([10, 0, 0, 0]) + bytes(24))
    (saves / "options.ini").write_text("volume=3\n", encoding="utf-8")
    ws = tmp_path / "ws"
    assert snapshot.snapshot(ws, "before", [saves])["files"] == 2
    (saves / "slot1.sav").write_bytes(b"SAVE" + bytes([25, 0, 0, 0]) + bytes(24))
    (saves / "slot2.sav").write_bytes(b"SAVE" + bytes(28))
    snapshot.snapshot(ws, "after", [saves])
    d = snapshot.diff(ws, "before", "after")
    assert d["added"] == ["0/slot2.sav"] and not d["removed"]
    changed = d["changed"][0]
    assert changed["file"] == "0/slot1.sav" and changed["ranges"][0]["start"] == 4 and changed["ranges"][0]["b"] == "19"
    with pytest.raises(GsError, match="exists"):
        snapshot.snapshot(ws, "before", [saves])


def test_sandbox_copies_the_install(tmp_path):
    install = tmp_path / "Game"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "Game.exe").write_bytes(b"MZ" + bytes(100))
    ws = tmp_path / "ws"
    ws.mkdir()
    game = {"name": "G", "install_dir": str(install), "verdict": policy.OFFLINE_OK}
    made = sandbox.create(ws, game)
    assert (ws / "sandbox" / "bin" / "Game.exe").read_bytes() == (install / "bin" / "Game.exe").read_bytes()
    assert made["files"] == 1 and sandbox.status(ws)["exists"]
    with pytest.raises(GsError, match="already exists"):
        sandbox.create(ws, game)
    assert sandbox.remove(ws)["removed"] and not sandbox.status(ws)["exists"]


def test_symbolizer_names_functions_and_flags_stale_exports(tmp_path):
    exe = tmp_path / "Game.exe"
    exe.write_bytes(b"MZ" + bytes(64))
    export = tmp_path / "ws" / "decomp" / "ghidra" / "Game.exe"
    export.mkdir(parents=True)
    rows = [{"q": "sg.Enemy.Update", "rva": "0x1000", "size": 0x80, "file": "src/sg/Enemy.c", "line": 40},
            {"q": "sg.Player.Heal", "rva": "0x1100", "size": 0x20}]
    (export / "functions.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    from gst.decompile.dotnet import sha256_of

    (export / "meta.json").write_text(json.dumps({"sha256": sha256_of(exe)}), encoding="utf-8")
    mods = [{"base": 0x140000000, "size": 0x10000, "path": str(exe), "name": "Game.exe"}]
    sym = runtime.Symbolizer(tmp_path / "ws", mods)
    hit = sym.describe(0x140001040, inside_previous=True)
    assert hit["function"] == "sg.Enemy.Update" and hit["where"] == "Game.exe+0x1040" and hit["line"] == 40
    assert sym.describe(0x7FF000000000)["where"].startswith("outside any module")
    (export / "meta.json").write_text(json.dumps({"sha256": "0" * 64}), encoding="utf-8")
    stale = runtime.Symbolizer(tmp_path / "ws", mods).describe(0x140001040)
    assert "another build" in stale["hint"] and "function" not in stale
