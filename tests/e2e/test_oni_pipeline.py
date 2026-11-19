"""F4 end to end on Oxygen Not Included (Unity Mono): decompile -> index -> queries, assets, audio.

Writes only to GS_HOME/_selftest-oni (outside any repo). Re-runs reuse the decompiled assemblies.
Run with: uv run --project cli pytest -m games tests/e2e/test_oni_pipeline.py
"""

from pathlib import Path

import pytest

from gst import config, index, workspace
from gst.index import query
from gst.recon import scan, stores

pytestmark = pytest.mark.games
APPID = "457140"


@pytest.fixture(scope="module")
def oni():
    games = [g for g in stores.all_games() if str(g.get("appid")) == APPID]
    if not games:
        pytest.skip("Oxygen Not Included is not installed")
    game = games[0]
    ws = config.gs_home() / "_selftest-oni"
    if not (ws / "game.json").exists():
        workspace.create(game, scan.run_recon(game), "_selftest-oni")
    return ws, workspace.load(ws)


def test_decompile_and_index(oni):
    from gst.decompile import dotnet

    ws, game = oni
    result = dotnet.decompile(ws, game)
    assert set(result["assemblies"]) == {"Assembly-CSharp", "Assembly-CSharp-firstpass"}
    assert all(v["files"] > 1000 and v.get("exit_code") in (0, None) for v in result["assemblies"].values())
    summary = index.build(ws)
    assert summary["symbols"] > 100_000 and summary["failed"] == 0


def test_known_types_and_calls(oni):
    ws, _ = oni
    con = index.connect(ws)
    try:
        assert query.defs(con, "SaveLoader")[0]["path"].endswith("Assembly-CSharp/SaveLoader.cs")
        callers = {c["caller"] for c in query.callers(con, "SaveLoader.Save")}
        assert {"PauseScreen.DoSave", "Game.DelayedSave"} <= callers
        resolved = {c["target"]: c["defined_as"] for c in query.callees(con, "Game.Update")}
        assert resolved["gasConduitSystem.Update"] == ["UtilityNetworkManager.Update"]
        assert any(d["qname"].startswith("Klei.AI") for d in query.symbols(con, "Klei.AI.Attribute", limit=5))
        assert query.strings(con, "_kanim", limit=3)
    finally:
        con.close()


def test_assets_inventory_extract_and_audio(oni):
    from gst.assets import audio, build_index, unity

    ws, game = oni
    install = Path(game["install_dir"])
    inv = unity.inventory(ws, install, game["name"])
    assert inv["objects"] > 50_000 and inv["types"]["Texture2D"] > 1000
    result = unity.extract(ws, install, types=("Texture2D",), name="^hatch_0$", limit=1)
    assert result["exported"] == {"Texture2D": 1}
    png = ws / "assets" / "unity" / "Texture2D" / "hatch_0.png"
    assert png.exists() and Path(str(png) + ".gs.json").exists()
    assert build_index(ws)["files"] >= 1
    banks = {f.name: f for f in audio.find(install)}
    assert len(audio.streams(banks["Creatures.bank"])) > 2000
