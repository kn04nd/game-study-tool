"""F10 end to end: who writes the player's HP in the sample game, and the policy keeping runtime tools away
from protected games.

- The sample game runs headless in real time; a read-only scan finds HP (100, then decreasing), a hardware
  watchpoint catches the writes and the Ghidra export names the function (sg::Enemy::Update).
- GTA V Enhanced (BattlEye) and Helldivers 2 (GameGuard + Themida), as recorded by `gst recon --library`:
  runtime targets and debuggers are refused.

Writes only to GS_HOME/_selftest-sample. The sample process is stopped by its PID.
Run with: uv run --project cli pytest -m "tools or games" tests/e2e/test_runtime_pipeline.py
"""

import subprocess
import time
from pathlib import Path

import pytest

from gst import config, guard, runtime, workspace
from gst.common import GsError
from gst.decompile import native
from gst.runtime import memory, watch
from gst.recon import scan, stores

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "sample-game"


@pytest.mark.tools
def test_who_writes_the_hp():
    exe = SAMPLE / "build" / "SampleGame.exe"
    if not exe.exists():
        pytest.skip("build the sample game first (samples/sample-game/build.ps1)")
    ws = config.gs_home() / "_selftest-sample"
    if not (ws / "game.json").exists():
        game = stores.resolve_game(str(exe.parent), games=[])
        workspace.create(game, scan.run_recon(game), "_selftest-sample")
    game = workspace.load(ws)
    native.decompile(ws, game)  # reused when the build did not change
    proc = subprocess.Popen([str(exe), "--headless", "--realtime", "--ticks", "6000"], stdout=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        assert runtime.target(game, None)["pid"] == proc.pid
        handle = memory.open_process(proc.pid)
        try:
            hits, _ = memory.first_scan(handle, "100", "int32")
            before = {a: memory.read(handle, a, 4) for a in hits}
            kept = []
            for _ in range(40):
                time.sleep(1.0)
                kept, _now = memory.next_scan(handle, hits, "int32", value=None, mode="decreased", previous=before)
                if kept:
                    break
            assert len(kept) == 1, f"{len(kept)} candidates decreased"
            result = watch.watch(proc.pid, kept[0], 4, max_hits=4, seconds=40)
            assert result["stopped_by"] == "hits"
            sym = runtime.Symbolizer(ws, runtime.modules(handle))
            writers = {sym.describe(h["rip"], inside_previous=True).get("function") for h in result["hits"]}
        finally:
            runtime.win.CloseHandle(handle)
        assert "sg.Enemy.Update" in writers  # the inlined Entity::TakeDamage
        assert writers <= {"sg.Enemy.Update", "sg.Player.Update"}  # damage, and the regeneration (inlined Heal)
        assert proc.poll() is None  # still running after the detach
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.games
@pytest.mark.parametrize("slug", ["grand-theft-auto-v-enhan", "helldiverstm-2"])
def test_protected_games_are_refused(slug):
    entry = guard.registry_games().get(slug)
    if not entry:
        pytest.skip(f"{slug} is not in the registry (gst recon --library)")
    assert entry["verdict"] == "no-runtime"
    with pytest.raises(GsError, match="runtime tools are not allowed"):
        runtime.target(entry, None)
    exe = next(iter(sorted(Path(entry["install_dir"]).glob("*.exe"))), Path(entry["install_dir"]) / "game.exe")
    decision = guard.check_command(f'x64dbg.exe "{exe}"')
    assert not decision.allowed and decision.rule in (2, 3)
