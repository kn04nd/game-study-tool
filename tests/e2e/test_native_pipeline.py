"""F8 end to end: native code through Ghidra headless with the shipped PDB.

- The sample game (samples/sample-game, built with MSVC when missing): classes with their layout and
  bases, the HP write in sg::Enemy::Update, exact call edges.
- Oxygen Not Included's SimDLL.dll (C++ simulation, ships SimDLL.pdb).

Writes only to GS_HOME/_selftest-sample and _selftest-oni (outside any repo). Ghidra takes minutes.
Run with: uv run --project cli pytest -m "tools or games" tests/e2e/test_native_pipeline.py
"""

import re
import subprocess
from pathlib import Path

import pytest

from gst import config, index, workspace
from gst.decompile import native
from gst.index import query
from gst.recon import scan, stores

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "sample-game"


def _workspace(game: dict, slug: str):
    ws = config.gs_home() / slug
    if not (ws / "game.json").exists():
        workspace.create(game, scan.run_recon(game), slug)
    return ws, workspace.load(ws)


@pytest.fixture(scope="module")
def sample():
    exe = SAMPLE / "build" / "SampleGame.exe"
    if not exe.exists():
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SAMPLE / "build.ps1")],
                              capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            pytest.skip(f"the sample game did not build (MSVC/.NET SDK missing?): {proc.stdout[-300:]}{proc.stderr[-300:]}")
    return _workspace(stores.resolve_game(str(exe.parent), games=[]), "_selftest-sample")


@pytest.mark.tools
def test_sample_game_native(sample):
    ws, game = sample
    assert game["name"] == "sample-game" and game["verdict"] == "offline-ok"
    result = native.decompile(ws, game)
    module = result["modules"]["SampleGame.exe"]
    assert module["pdb"] and module["decompiled"] == module["functions"] > 500
    out = ws / "decomp" / "ghidra" / "SampleGame.exe" / "src" / "sg"
    entity = (out / "Entity.c").read_text(encoding="utf-8")
    assert re.search(r"\+0x34\s+int hp", entity)  # the layout header: what runtime tools look for in memory
    update = (out / "Enemy.c").read_text(encoding="utf-8")
    assert ".hp = " in update and "_padding_" not in update  # class layout from the PDB, base class included
    index.build(ws)
    con = index.connect(ws)
    try:
        derived = {d["qname"] for d in query.hierarchy(con, "sg::Entity")["derived"]}
        assert derived == {"sg.Enemy", "sg.Player"}
        assert {c["caller"] for c in query.callers(con, "sg::Crc32")} == {"sg.SaveWorld", "sg.LoadWorld"}
        assert query.strings(con, "SGSV")[0]["owner"] == "sg.SaveWorld"
    finally:
        con.close()


@pytest.mark.games
def test_oni_simdll():
    games = [g for g in stores.all_games() if str(g.get("appid")) == "457140"]
    if not games:
        pytest.skip("Oxygen Not Included is not installed")
    ws, game = _workspace(games[0], "_selftest-oni")
    found = {m["name"]: m for m in native.find_modules(Path(game["install_dir"]), game)}
    assert found["SimDLL.dll"]["game"] and found["SimDLL.dll"]["pdb"] and not found["UnityPlayer.dll"]["game"]
    result = native.decompile(ws, game)
    simdll = result["modules"]["SimDLL.dll"]
    assert simdll["pdb"] and simdll["functions"] > 4000 and simdll["decompiled"] == simdll["functions"]
    index.build(ws)
    con = index.connect(ws)
    try:
        assert query.defs(con, "SimBase", kind="class")[0]["path"].endswith("decomp/ghidra/SimDLL.dll/src/SimBase.c")
        assert any(m["name"] == "UpdateData" for m in query.outline(con, "SimBase"))
    finally:
        con.close()
