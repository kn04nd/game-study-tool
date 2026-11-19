"""F9 end to end: readers prove a layout by rewriting every sample byte for byte.

- The sample game's SGSV saves (made by the game itself, headless): fully mapped, CRC checked; the
  same layout as an ImHex pattern; two saves diffed to find the fields that changed.
- Deep Rock Galactic's GVAS saves (Unreal SaveGame): the container round-trips with the property
  stream kept raw.

Writes only to GS_HOME/_selftest-sample (outside any repo); the DRG saves are only read.
Run with: uv run --project cli pytest -m "tools or games" tests/e2e/test_formats_pipeline.py
"""

import json
import subprocess
from pathlib import Path

import pytest

from gst import config, formats, workspace
from gst.formats import readers
from gst.recon import scan, stores

FIXTURES = Path(__file__).resolve().parents[2] / "src" / "gst" / "data" / "readers"  # example readers shipped with gst
SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "sample-game"

SGSV_PATTERN = """#pragma endian little
struct Header { char magic[4]; u16 version; u16 flags; u32 entity_count; u64 timestamp; u32 tick; };
struct Entity { u8 type; u8 name_len; char name[name_len]; float x; float y; float z; s32 hp; s32 max_hp; u32 extra; float timer; };
Header header @ 0x00;
Entity entities[header.entity_count] @ $;
u32 crc @ $;
"""


@pytest.fixture(scope="module")
def sample_saves():
    exe = SAMPLE / "build" / "SampleGame.exe"
    if not exe.exists():
        pytest.skip("build the sample game first (samples/sample-game/build.ps1)")
    game = stores.resolve_game(str(exe.parent), games=[])
    ws = config.gs_home() / "_selftest-sample"
    if not (ws / "game.json").exists():
        workspace.create(game, scan.run_recon(game), "_selftest-sample")
    saves = []
    for ticks in (300, 1800):
        out = ws / "dumps" / f"run{ticks}.sgs"
        subprocess.run([str(exe), "--headless", "--ticks", str(ticks), "--save", str(out), "--timestamp", "1700000000"],
                       check=True, capture_output=True, timeout=120)
        saves.append(out)
    return ws, saves


@pytest.mark.tools
def test_sample_saves_round_trip_and_diff(sample_saves):
    ws, saves = sample_saves
    assert formats.identify(saves[0])["kind"] == "unknown"  # our own format: no public signature
    result = readers.roundtrip(FIXTURES / "sgsv.py", saves)
    assert result["identical"] == 2
    early, late = (r["describe"] for r in result["results"])
    assert early["crc_ok"] and early["unmapped_bytes"] == 0 and early["entities"] == 4
    assert late["player_hp"] < early["player_hp"]  # the enemies did their job
    changed = formats.diff(*saves)
    assert changed["same_size"] and changed["ranges"][0]["start"] == 0x14  # the tick field comes first


@pytest.mark.tools
def test_sample_save_as_an_imhex_pattern(sample_saves, tmp_path):
    ws, saves = sample_saves
    pattern = tmp_path / "sgsv.hexpat"
    pattern.write_text(SGSV_PATTERN, encoding="utf-8")
    out = formats.run_pattern(pattern, saves[0], ws / "dumps" / "formats" / "sgsv" / "run300.json")
    tree = json.loads(Path(out["json"]).read_text(encoding="utf-8"))
    assert tree["header"]["magic"] == "SGSV" and tree["header"]["tick"] == 300
    assert [e["name"] for e in tree["entities"]] == ["Hero", "Slime", "Bat", "Golem"]


@pytest.mark.games
def test_drg_gvas_saves_round_trip():
    games = [g for g in stores.all_games() if str(g.get("appid")) == "548430"]
    if not games:
        pytest.skip("Deep Rock Galactic is not installed")
    saves = sorted((Path(games[0]["path"]) / "FSD" / "Saved" / "SaveGames").glob("*.sav"))
    if not saves:
        pytest.skip("no Deep Rock Galactic saves yet")
    assert formats.identify(saves[0])["kind"] == "ue-gvas"
    result = readers.roundtrip(FIXTURES / "gvas_header.py", saves)
    assert result["identical"] == len(saves)
    first = result["results"][0]["describe"]
    assert first["engine"] == "4.27.2" and first["save_class"] == "/Script/FSD.FSDSaveGame" and first["property_bytes"] > 1000
