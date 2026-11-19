"""F11 end to end: a RenderDoc capture of the sample game (run from the sandbox copy) becomes frame.md -
two passes (HDR scene with depth, post-process into the swap chain), shaders with their constant buffers,
render targets as PNG.

Writes only to GS_HOME/_selftest-sample (outside any repo). The game window shows for a few seconds.
Run with: uv run --project cli pytest -m tools tests/e2e/test_gfx_pipeline.py
"""

import json
from pathlib import Path

import pytest

from gst import config, gfx, tools, workspace
from gst.decompile.dotnet import sha256_of
from gst.recon import scan, stores
from gst.runtime import sandbox

pytestmark = pytest.mark.tools
SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "sample-game"


def test_capture_and_frame_summary():
    exe = SAMPLE / "build" / "SampleGame.exe"
    if not exe.exists():
        pytest.skip("build the sample game first (samples/sample-game/build.ps1)")
    if tools.bin_path("renderdoc", "qrenderdoc.exe") is None:
        pytest.skip("RenderDoc is not installed (gst tools install renderdoc)")
    ws = config.gs_home() / "_selftest-sample"
    if not (ws / "game.json").exists():
        game = stores.resolve_game(str(exe.parent), games=[])
        workspace.create(game, scan.run_recon(game), "_selftest-sample")
    game = workspace.load(ws)
    copy = ws / "sandbox" / "SampleGame.exe"
    if not copy.exists() or sha256_of(copy) != sha256_of(exe):
        sandbox.remove(ws)
        sandbox.create(ws, game)
    result = gfx.capture(ws, game, after=2, exe="SampleGame.exe", args="--frames 900")
    assert result["api"] == "D3D11" and result["stopped"]
    out = gfx.export(ws, Path(result["captures"][0]))
    frame = json.loads((Path(out["out"]) / "frame.json").read_text(encoding="utf-8"))
    scene, post = frame["passes"]
    assert scene["draws"] == 5 and scene["depth"] and frame["textures"][scene["outputs"][0]]["format"] == "R16G16B16A16_FLOAT"
    assert post["draws"] == 1 and frame["textures"][post["outputs"][0]]["format"] == "R8G8B8A8_UNORM"
    blocks = {cb["name"] for s in frame["shaders"].values() for cb in (s["reflection"] or {}).get("constant_blocks", [])}
    assert {"Frame", "Object"} <= blocks
    md = Path(out["frame_md"]).read_text(encoding="utf-8")
    assert "## Passes" in md and len(md.encode("utf-8")) <= 10_100
    assert (Path(out["out"]) / "targets" / "final.png").stat().st_size > 10_000
    assert any((Path(out["out"]) / "thumbs").glob("*.png"))
