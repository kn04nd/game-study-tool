"""gst gfx without RenderDoc: the frame summary, RenderDoc's privacy settings, the sandbox and policy gates."""

import json
from pathlib import Path

import pytest

from gst import gfx, policy
from gst.common import GsError

FRAME = {
    "api": "D3D11", "degraded": False, "events": 9, "draws": 6, "dispatches": 0, "clears": 2, "final": "targets/final.png",
    "disassembly": "DXBC",
    "passes": [
        {"index": 0, "markers": [], "first_eid": 15, "last_eid": 23, "draws": 5, "dispatch": False, "indices": 180,
         "outputs": ["46"], "depth": "49", "shaders": {"vs": ["51"], "ps": ["52"]}, "images": ["targets/pass00_46.png"],
         "calls": ["ID3D11DeviceContext::DrawIndexed"]},
        {"index": 1, "markers": ["Post", "Tonemap"], "first_eid": 30, "last_eid": 30, "draws": 1, "dispatch": False,
         "indices": 0, "outputs": ["35"], "depth": None, "shaders": {"vs": ["53"], "ps": ["54"]}, "images": [], "calls": []},
    ],
    "shaders": {
        "51": {"stage": "vs", "reflection": {"entry": "main", "constant_blocks": [
            {"name": "Frame", "bytes": 96, "variables": ["viewProj", "lightDir", "tint"]}], "textures": []}, "lines": 27},
        "54": {"stage": "ps", "reflection": {"entry": "main", "constant_blocks": [], "textures": ["scene"]}, "lines": 22},
    },
    "textures": {
        "35": {"name": "Swapchain Image 35", "width": 960, "height": 540, "depth": 1, "mips": 1, "array": 1,
               "format": "R8G8B8A8_UNORM", "msaa": 1},
        "46": {"name": "Scene HDR", "width": 960, "height": 540, "depth": 1, "mips": 1, "array": 1,
               "format": "R16G16B16A16_FLOAT", "msaa": 1},
        "49": {"name": "", "width": 960, "height": 540, "depth": 1, "mips": 1, "array": 1, "format": "D24S8", "msaa": 4},
    },
}


def test_frame_markdown_tables_and_limit(tmp_path):
    ws = tmp_path / "ws"
    capture = ws / "traces" / "gfx" / "x_frame10.rdc"
    text = gfx.frame_markdown(FRAME, capture, ws)
    assert "API D3D11; 9 actions: 6 draws" in text and "`traces/gfx/x_frame10.rdc`" in text
    assert "| 0 | 15-23 | 5 | 46 `Scene HDR` 960x540 R16G16B16A16_FLOAT | 49 960x540 D24S8 | vs 51, ps 52 |" in text
    assert "| 1 (Post > Tonemap) | 30-30 | 1 |" in text
    assert "Frame (96 B: viewProj, lightDir, tint)" in text and "| 54 | ps | main | - | scene | 22 |" in text
    assert "| 49 | - | 960x540 | D24S8 MSAA x4 |" in text
    big = dict(FRAME, passes=FRAME["passes"] * 400)
    cut = gfx.frame_markdown(big, capture, ws)
    assert len(cut.encode("utf-8")) <= 10_100 and cut.endswith("(cut at 10 KB: the full data is in frame.json)\n")


def test_privacy_settings_for_renderdoc(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = gfx.prepare_config()
    assert json.loads(path.read_text(encoding="utf-8")) == gfx.PRIVACY
    path.write_text(json.dumps({"Analytics_TotalOptOut": False, "Theme": "dark"}), encoding="utf-8")
    data = json.loads(gfx.prepare_config().read_text(encoding="utf-8"))
    assert data["Analytics_TotalOptOut"] is True and data["CheckUpdate_AllowChecks"] is False and data["Theme"] == "dark"


def test_capture_needs_the_sandbox_and_the_verdict(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    game = {"name": "G", "exes": ["game.exe"], "verdict": policy.OFFLINE_OK, "sandbox_dir": str(ws / "sandbox")}
    with pytest.raises(GsError, match="sandbox copy"):
        gfx.sandbox_exe(ws, game)
    (ws / "sandbox" / "bin").mkdir(parents=True)
    exe = ws / "sandbox" / "bin" / "Game.exe"
    exe.write_bytes(b"MZ")
    assert gfx.sandbox_exe(ws, game) == exe
    gfx._check(game, exe)  # offline-ok, inside the sandbox: allowed
    with pytest.raises(GsError, match="sandbox copy"):
        gfx._check(game, Path("C:/Games/G/game.exe"))  # the original install: never injected
    with pytest.raises(GsError, match="runtime tools are not allowed"):
        gfx._check(dict(game, verdict=policy.NO_RUNTIME), exe)
