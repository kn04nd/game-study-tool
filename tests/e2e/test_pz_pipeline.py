"""F6 end to end on Project Zomboid (Java 25 fat jar + Lua): Vineflower on the game's packages, Lua
snapshot, Kahlua stdlib.lbc diagnosed, index over Java and Lua.

Writes only to GS_HOME/_selftest-pz (outside any repo). Vineflower takes a few minutes the first time.
Run with: uv run --project cli pytest -m games tests/e2e/test_pz_pipeline.py
"""

import pytest

from gst import config, index, workspace
from gst.index import query
from gst.recon import scan, stores

pytestmark = pytest.mark.games
APPID = "108600"


@pytest.fixture(scope="module")
def pz():
    games = [g for g in stores.all_games() if str(g.get("appid")) == APPID]
    if not games:
        pytest.skip("Project Zomboid is not installed")
    game = games[0]
    ws = config.gs_home() / "_selftest-pz"
    if not (ws / "game.json").exists():
        workspace.create(game, scan.run_recon(game), "_selftest-pz")
    return ws, workspace.load(ws)


def test_java_and_lua(pz):
    from gst.cmd_decompile import backends_for
    from gst.decompile import java, lua

    ws, game = pz
    assert backends_for(game) == ["java", "lua"]
    result = java.decompile(ws, game)
    jar = result["jars"]["projectzomboid"]
    assert "zombie/" in jar["only"] and "org/" not in jar["only"] and jar["files"] > 3000  # inner classes share their outer class file
    out = lua.decompile(ws, game)
    assert out["copied"] > 1000
    stdlib = next(b for b in out["bytecode"] if b["file"] == "stdlib.lbc")
    assert stdlib["version"] == "5.1" and stdlib["big_endian"] and stdlib["source_twin"] == "stdlib.lua"
    summary = index.build(ws)
    assert summary["failed"] == 0 and summary["symbols"] > 100_000


def test_queries_cross_java_and_lua(pz):
    ws, _ = pz
    con = index.connect(ws)
    try:
        player = query.defs(con, "IsoPlayer", kind="class")
        assert player and player[0]["path"].endswith("zombie/characters/IsoPlayer.java")
        pane = query.defs(con, "ISInventoryPane", kind="class")
        assert pane and pane[0]["path"].endswith(".lua")
        assert query.hierarchy(con, "ISInventoryPane")["bases"] == ["ISPanel"]  # X = ISPanel:derive("X")
        assert query.hierarchy(con, "ISPanel", limit=2000)["derived"]  # hundreds of UI classes derive from it
    finally:
        con.close()
