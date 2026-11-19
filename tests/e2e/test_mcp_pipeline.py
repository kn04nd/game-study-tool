"""F13 end to end: `gst mcp add ghidra` on the sample game's Ghidra project, then a real call through the stdlib
MCP client - pyghidra-mcp decompiles sg::Crc32 (the CRC-32 polynomial shows in the code).

Needs the native export of the sample (gst decompile native, F8) and pyghidra-mcp (gst tools install pyghidra-mcp).
Writes only to GS_HOME/_selftest-sample. pyghidra-mcp takes about 40 s to start.
Run with: uv run --project cli pytest -m tools tests/e2e/test_mcp_pipeline.py
"""

import json
from pathlib import Path

import pytest

from gst import config, mcp, tools, workspace
from gst.decompile import native
from gst.mcp_client import Session, text_of
from gst.recon import scan, stores

pytestmark = pytest.mark.tools
SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "sample-game"


def test_ghidra_mcp_answers():
    exe = SAMPLE / "build" / "SampleGame.exe"
    if not exe.exists() or tools.bin_path("pyghidra-mcp") is None:
        pytest.skip("needs the built sample game and pyghidra-mcp")
    ws = config.gs_home() / "_selftest-sample"
    if not (ws / "game.json").exists():
        game = stores.resolve_game(str(exe.parent), games=[])
        workspace.create(game, scan.run_recon(game), "_selftest-sample")
    game = workspace.load(ws)
    native.decompile(ws, game)  # reused when unchanged
    mcp.add(ws, game, "ghidra")
    assert mcp.audit_text((ws / ".mcp.json").read_text(encoding="utf-8")) == []
    settings = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "ghidra" in settings["enabledMcpjsonServers"]
    with Session(mcp.configured(ws)["ghidra"]) as session:
        names = {t["name"] for t in session.tools()}
        assert {"decompile_function", "list_xrefs", "delete_project_binary"} <= names
        listed = json.loads(text_of(session.call("list_project_binaries")))
        binary = listed["programs"][0]["name"]
        code = json.loads(text_of(session.call("decompile_function", {"binary_name": binary, "name_or_address": "sg::Crc32"})))
    assert "0xedb88320" in code["code"]
