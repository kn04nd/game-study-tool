"""F7 end to end on Unreal: Deep Rock Galactic (UE 4.27, pak v11, unencrypted) is listed and exported to
JSON with Blueprint bytecode and to PNG; Witch It (UE 5.7, encrypted and signed paks, VAC) stops at the
diagnosis with the file list taken from Manifest_UFSFiles.

Writes only to GS_HOME/_selftest-drg and _selftest-witch-it (outside any repo).
Run with: uv run --project cli pytest -m games tests/e2e/test_unreal_pipeline.py
"""

import json

import pytest

from gst import config, workspace
from gst.common import GsError
from gst.recon import scan, stores

pytestmark = pytest.mark.games


def _workspace(appid: str, slug: str):
    games = [g for g in stores.all_games() if str(g.get("appid")) == appid]
    if not games:
        pytest.skip(f"Steam app {appid} is not installed")
    ws = config.gs_home() / slug
    if not (ws / "game.json").exists():
        workspace.create(games[0], scan.run_recon(games[0]), slug)
    return ws, workspace.load(ws)


@pytest.fixture(scope="module")
def drg():
    return _workspace("548430", "_selftest-drg")


@pytest.fixture(scope="module")
def witch_it():
    return _workspace("559650", "_selftest-witch-it")


def test_drg_inventory_and_exports(drg):
    from pathlib import Path

    from gst.assets import unreal

    ws, game = drg
    assert game["verdict"] == "offline-ok" and game["engine"]["version"] == "4.27.2"
    install = Path(game["install_dir"])
    inv = unreal.inventory(ws, install, game)
    assert inv["source"] == "containers" and not inv["encrypted"]
    assert inv["files"] > 100_000 and inv["blueprints"] > 1000 and inv["game_enum"] == "GAME_UE4_27"
    out = unreal.extract(ws, install, game, name=r"Character/BP_PlayerCharacter\.uasset$", bytecode=True)
    exported = json.loads((Path(out["out"]) / "FSD/Content/Character/BP_PlayerCharacter.json").read_text(encoding="utf-8"))
    cls = next(e for e in exported if e.get("Type") == "BlueprintGeneratedClass")
    assert cls["SuperStruct"]["ObjectName"] == "Class'PlayerCharacter'"  # Blueprint over the native C++ class
    assert any(e.get("Type") == "Function" and e.get("ScriptBytecode") for e in exported)
    icons = unreal.extract(ws, install, game, name=r"Icons/Creatures/Icon_Glyphid_GruntGuard_1024x256px\.uasset$", mode="textures")
    png = Path(icons["out"]) / "FSD/Content/UI/Menu_MinersManual/Assets/Icons/Creatures/Icon_Glyphid_GruntGuard_1024x256px.png"
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # the first JSON survives a second, narrower export (uas gets the union of every filter)
    unreal.extract(ws, install, game, name=r"Enemies/Spider/Grunt/ENE_Spider_Grunt_Base\.uasset$")
    assert (Path(out["out"]) / "FSD/Content/Character/BP_PlayerCharacter.json").exists()


def test_witch_it_stops_at_the_diagnosis(witch_it):
    from pathlib import Path

    from gst.assets import unreal

    ws, game = witch_it
    assert game["verdict"] == "no-runtime" and game["engine"]["version"].startswith("5.")
    assert {"vac", "encrypted-content"} <= {p["kind"] for p in game["protections"]}
    install = Path(game["install_dir"])
    inv = unreal.inventory(ws, install, game)
    assert inv["source"].startswith("manifest Manifest_UFSFiles") and inv["encrypted"] and inv["files"] > 10_000
    with pytest.raises(GsError, match="rule 4"):
        unreal.extract(ws, install, game, name="Character")
