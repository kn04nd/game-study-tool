"""Regression: recon over the locally installed library must match the verified classification.

Expected values come from the 2026-10-05 research (local listings, PE sections, Steam store) and
were re-checked by gst recon --library on 2026-10-07. Games that are not installed are skipped.
"""

import pytest

from gst.recon import scan, stores

pytestmark = pytest.mark.games

EXPECTED = {
    "457140": ("unity-mono", "offline-ok"),       # Oxygen Not Included
    "108600": ("java", "offline-ok"),             # Project Zomboid
    "548430": ("unreal", "offline-ok"),           # Deep Rock Galactic
    "4244510": ("godot", "offline-ok"),           # Pratfall
    "2709570": ("unity-mono", "offline-ok"),      # Supermarket Together
    "3527290": ("unity-mono", "offline-ok"),      # PEAK (Steam lists Single-player)
    "239140": ("chrome", "no-runtime"),           # Dying Light (VAC + SteamStub)
    "275850": ("nms", "static-only"),             # No Man's Sky (SteamStub)
    "377160": ("creation", "static-only"),        # Fallout 4 (SteamStub)
    "489830": ("creation", "static-only"),        # Skyrim SE (SteamStub)
    "12210": ("rage", "static-only"),             # GTA IV (Rockstar launcher)
    "3240220": ("rage", "no-runtime"),            # GTA V Enhanced (BattlEye)
    "1174180": ("rage", "static-only"),           # RDR2 (Arxan + launcher)
    "553850": ("stingray", "no-runtime"),         # Helldivers 2 (GameGuard + Themida)
    "2344520": ("blizzard-casc", "no-runtime"),   # Diablo IV (always online)
    "730": ("source2", "no-runtime"),             # Counter-Strike 2 (VAC)
    "559650": ("unreal", "no-runtime"),           # Witch It (VAC, encrypted paks)
    "2483190": ("forzatech", "static-only"),      # Forza Horizon 6 (Xbox account)
}

_GAMES = {g["appid"]: g for g in stores.steam_games()}
_CACHE: dict[str, dict] = {}


def report_for(appid: str) -> dict:
    if appid not in _GAMES:
        pytest.skip(f"Steam app {appid} not installed")
    if appid not in _CACHE:
        _CACHE[appid] = scan.run_recon(_GAMES[appid])
    return _CACHE[appid]


@pytest.mark.parametrize("appid", sorted(EXPECTED))
def test_engine_and_verdict(appid):
    engine, verdict = EXPECTED[appid]
    report = report_for(appid)
    assert report["engine"]["key"] == engine, report["engine"]
    assert report["verdict"] == verdict, report["protections"]


def _names(report, kind):
    return " ".join(p["name"] for p in report["protections"] if p["kind"] == kind).lower()


def test_specific_protections():
    assert "gameguard" in _names(report_for("553850"), "anticheat")
    assert "themida" in _names(report_for("553850"), "anti-tamper")
    assert "battleye" in _names(report_for("3240220"), "anticheat")
    assert "steamstub" in _names(report_for("489830"), "drm-wrapper")
    assert "encrypted" in _names(report_for("559650"), "encrypted-content")


def test_engine_details():
    assert report_for("457140")["engine"]["details"]["version"] == "6000.3.5f2"
    drg = report_for("548430")["engine"]["details"]
    assert drg["pak_version"] == 11 and not any(f["encrypted_index"] for f in drg["pak_footers"])
    pratfall = report_for("4244510")
    assert pratfall["engine"]["details"]["version"] == "4.6.1" and pratfall["engine"]["details"]["dotnet"]
    assert any(p.endswith("pratfall.pdb") for p in pratfall["symbols"]["pdb_files"])
