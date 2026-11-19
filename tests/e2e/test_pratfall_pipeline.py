"""F5 end to end on Pratfall (Godot 4.6 .NET): GDScript + C# with PDB names, index, Godot assets.

Writes only to GS_HOME/_selftest-pratfall (outside any repo).
Run with: uv run --project cli pytest -m games tests/e2e/test_pratfall_pipeline.py
"""

import re
from pathlib import Path

import pytest

from gst import config, index, workspace
from gst.index import query
from gst.recon import scan, stores

pytestmark = pytest.mark.games
APPID = "2286960"


@pytest.fixture(scope="module")
def pratfall():
    games = [g for g in stores.all_games() if (g.get("name") or "").lower() == "pratfall"]
    if not games:
        pytest.skip("Pratfall is not installed")
    game = games[0]
    ws = config.gs_home() / "_selftest-pratfall"
    if not (ws / "game.json").exists():
        workspace.create(game, scan.run_recon(game), "_selftest-pratfall")
    return ws, workspace.load(ws)


def test_decompile_godot_and_dotnet(pratfall):
    from gst.cmd_decompile import backends_for
    from gst.decompile import dotnet, godot

    ws, game = pratfall
    assert backends_for(game) == ["godot", "dotnet"]
    gd = godot.decompile(ws, game)
    assert gd["scripts"] >= 1 and all(e.endswith(".gd") for e in gd["examples"])
    net = dotnet.decompile(ws, game)
    assert list(net["assemblies"]) == ["Pratfall"] and net["assemblies"]["Pratfall"]["pdb"]
    summary = index.build(ws)
    assert summary["symbols"] > 30_000 and summary["failed"] == 0


def test_pdb_names_and_queries(pratfall):
    ws, _ = pratfall
    con = index.connect(ws)
    try:
        assert query.defs(con, "Player", kind="class")[0]["path"].endswith("Pratfall/Player.cs")
        assert query.defs(con, "ImGuiConfig")  # a GDScript class from GDRE
    finally:
        con.close()
    sources = list((ws / "decomp" / "cs" / "Pratfall").rglob("*.cs"))
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sources[:400])
    declared = re.findall(r"\b(?:var|int|float|bool|string)\s+(\w+)\s*=", text)
    generic = [n for n in declared if re.fullmatch(r"(?:num|text|flag|val)\d*", n)]
    assert declared and len(generic) / len(declared) < 0.2  # PDB names, not ILSpy's num/num2


def test_godot_assets(pratfall):
    from gst.assets import godot

    ws, game = pratfall
    install = Path(game["install_dir"])
    inv = godot.inventory(ws, install, game["name"])
    assert inv["files"] > 10_000 and inv["types"]["scene"] > 1000
    # exported scenes live at .godot/exported/<hash>/export-<hash>-<name>.scn; GDRE restores the original path
    result = godot.extract(ws, install, types=("scene",), name=r"-player\.scn$", limit=1)
    assert result["exported"] == {"scene": 1}
    tscn = next((ws / "assets" / "godot" / "extract").rglob("player.tscn"))
    assert "Player.cs" in tscn.read_text(encoding="utf-8", errors="replace")
