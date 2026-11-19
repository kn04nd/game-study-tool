import re

import pytest

from gst.recon import rules


@pytest.mark.parametrize(
    "expr, literal",
    [
        (r"(?:^|/)UnityPlayer\.dll$", "unityplayer.dll"),
        (r"(?:^|/)[a-f0-9]{16}\.stream$", ".stream"),
        (r"(?:^|/)(?:lib)?godotsteam", "godotsteam"),
        (r"(?:^|/)snd?_\w++\.ogg$", ".ogg"),
        (r"(?:^|/)(?:vphysics|bsppack)\.(?:dylib|dll|so)$", ""),
        (r"\.pak$", ".pak"),
        (r"(?:^|/)Engine/Binaries/ThirdParty/", "engine/binaries/thirdparty/"),
        (r"(?:^|/)BEService(?:_x64)?\.exe$", "beservice"),
        (r"(?:^|/)EasyAntiCheat_(?:EOS_)?Setup\.exe$", "easyanticheat_"),
        (r"(?:^|/)(?:EasyAntiCheat(?:_x64)?|eac_server64)\.dll$", ".dll"),
        (r"a|b", ""),
    ],
)
def test_required_literal(expr, literal):
    assert rules.required_literal(expr) == literal


def test_required_literal_is_always_contained_in_matches():
    """Property check on the real rule files: every sample match contains the literal."""
    samples = [
        "oxygennotincluded_data/managed/assembly-csharp.dll", "unityplayer.dll", "fsd/content/paks/fsd-windowsnoeditor.pak",
        "engine/binaries/thirdparty/ogg/win64/libogg.dll", "battleye/beservice_x64.exe", "bin/gameguard.des",
        "game/csgo/gameinfo.gi", "data/bundles.00.nxa", "pratfall.pck", "dw/data/engine_pc.rpack",
        "easyanticheat/easyanticheat_eos_setup.exe", "media/zipmanifest.xml", "data/skyrim.esm",
    ]
    for rule in rules.all_rules():
        for s in samples:
            if rule.regex.search(s):
                assert rule.literal in s, (rule.name, rule.regex.pattern, rule.literal, s)


def test_all_rules_compile_and_have_sections():
    all_rules = rules.all_rules()
    assert len(all_rules) > 400
    sections = {r.section for r in all_rules}
    assert {"Engine", "AntiCheat", "SDK", "Launcher", "Loader"} <= sections


def test_match_engine_and_anticheat():
    paths = ["oxygennotincluded_data/globalgamemanagers", "unityplayer.dll", "battleye/beclient_x64.dll",
             "battleye/beservice_x64.exe", "gta5_enhanced_be.exe"]
    hits = rules.match(paths, {"battleye"})
    assert "Unity" in hits["Engine"]
    assert "BattlEye" in hits["AntiCheat"]


def test_awacy_lookup_normalises_names():
    entry = rules.awacy_lookup("730")
    assert entry is not None and "VAC" in entry["anticheats"]
    assert rules.awacy_lookup("999999999") is None
