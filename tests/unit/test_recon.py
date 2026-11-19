"""Recon on synthetic game trees (no real games, no binaries from games)."""

import json
import struct

import pytest

from gst import cli, policy
from gst.recon import fingerprint, protections, rules, scan, sniff, stores
from gst.recon.index import FileIndex


def make(root, files: dict):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return root


def engine_of(root):
    ix = FileIndex(root)
    hits, _ = fingerprint.detect(ix, rules.match(ix.files, ix.dirs))
    return hits[0]


def pak_with_footer(encrypted: bool, version: int = 11) -> bytes:
    body = b"\x00" * 64
    footer = b"\x00" * 16 + (b"\x01" if encrypted else b"\x00") + struct.pack("<II", sniff.UE_PAK_MAGIC, version) + b"\x00" * 60
    return body + footer


def gdpc(major=4, minor=6, patch=1, fmt=3, flags=2) -> bytes:
    return b"GDPC" + struct.pack("<4I", fmt, major, minor, patch) + struct.pack("<I", flags) + b"\x00" * 64


def test_unity_mono_with_version(tmp_path):
    root = make(tmp_path / "G", {
        "Game.exe": b"MZ", "UnityPlayer.dll": b"MZ", "MonoBleedingEdge/x.dll": b"x",
        "Game_Data/globalgamemanagers": b"\x00\x00junk 6000.3.5f2 junk",
        "Game_Data/Managed/Assembly-CSharp.dll": b"MZ",
        "Game_Data/app.info": "Klei\nOxygen Not Included\n",
    })
    hit = engine_of(root)
    assert hit["key"] == "unity-mono"
    assert hit["details"]["version"] == "6000.3.5f2"
    assert hit["details"]["company"] == "Klei"


def test_unity_il2cpp(tmp_path):
    root = make(tmp_path / "G", {
        "UnityPlayer.dll": b"MZ", "GameAssembly.dll": b"MZ",
        "G_Data/globalgamemanagers": b"2022.3.10f1", "G_Data/il2cpp_data/Metadata/global-metadata.dat": b"\xaf\x1b\xb1\xfa",
    })
    assert engine_of(root)["key"] == "unity-il2cpp"


def test_unreal_pak_footer_and_encryption(tmp_path):
    root = make(tmp_path / "G", {
        "FSD/Binaries/Win64/FSD-Win64-Shipping.exe": b"MZ" + "++UE4+Release-4.27".encode("utf-16-le"),
        "FSD/Content/Paks/FSD-WindowsNoEditor.pak": pak_with_footer(encrypted=False),
    })
    hit = engine_of(root)
    assert hit["key"] == "unreal"
    assert hit["details"]["pak_version"] == 11
    assert hit["details"]["engine_version"] == "UE4+Release-4.27"
    enc = make(tmp_path / "E", {"W/Content/Paks/pakchunk0-Windows.pak": pak_with_footer(encrypted=True, version=12)})
    ix = FileIndex(enc)
    hits, _ = fingerprint.detect(ix, {})
    assert any(p["kind"] == "encrypted-content" for p in protections.encrypted_content(ix, hits))


def test_unreal_engine_version_from_the_file_version(tmp_path, monkeypatch):
    root = make(tmp_path / "G", {
        "W/Binaries/Win64/W-Win64-Shipping.exe": b"MZ", "Engine/Binaries/Win64/CrashReportClient.exe": b"MZ",
        "W/Content/Paks/pakchunk0-Windows.pak": pak_with_footer(encrypted=False, version=12),
    })
    monkeypatch.setattr(sniff, "pe_fixed_version", lambda p: "5.7.1.0")
    details = engine_of(root)["details"]
    assert details["engine_version"] == "5.7.1" and details["engine_version_source"].endswith("crashreportclient.exe")
    monkeypatch.setattr(sniff, "pe_fixed_version", lambda p: "1.0.3.0")  # a game that stamps its own version
    assert "engine_version" not in engine_of(root)["details"]


def test_pe_fixed_version_on_a_real_pe(tmp_path):
    import sys
    from pathlib import Path

    assert sniff.pe_fixed_version(Path(sys._base_executable)).split(".")[0] == "3"  # python.exe 3.x
    assert sniff.pe_fixed_version(make(tmp_path / "G", {"x.exe": b"MZ"}) / "x.exe") is None


def test_godot_pck_and_dotnet(tmp_path):
    root = make(tmp_path / "G", {
        "Pratfall.exe": b"MZ", "Pratfall.pck": gdpc(),
        "data_Pratfall_windows_x86_64/GodotSharp.dll": b"MZ", "data_Pratfall_windows_x86_64/Pratfall.dll": b"MZ",
    })
    hit = engine_of(root)
    assert hit["key"] == "godot"
    assert hit["details"]["version"] == "4.6.1" and hit["details"]["dotnet"] is True
    assert hit["details"]["dir_encrypted"] is False


def test_godot_encrypted_pck_is_flagged(tmp_path):
    root = make(tmp_path / "G", {"g.exe": b"MZ", "g.pck": gdpc(flags=1)})
    ix = FileIndex(root)
    hits, _ = fingerprint.detect(ix, {})
    assert any("Godot PCK" in p["name"] for p in protections.encrypted_content(ix, hits))


def test_gamemaker(tmp_path):
    root = make(tmp_path / "G", {"game.exe": b"MZ", "data.win": b"FORM\x00\x00\x00\x00GEN8\x00\x00\x00\x00\x00" + bytes([17]) + b"\x00" * 40})
    hit = engine_of(root)
    assert hit["key"] == "gamemaker" and hit["details"]["bytecode_version"] == 17


def test_rpgmaker_mv_encrypted_assets(tmp_path):
    root = make(tmp_path / "G", {
        "Game.exe": b"MZ", "nw.dll": b"MZ", "www/js/rpg_core.js": "//",
        "www/data/System.json": json.dumps({"hasEncryptedImages": True, "encryptionKey": "abc"}),
    })
    ix = FileIndex(root)
    hits, _ = fingerprint.detect(ix, rules.match(ix.files, ix.dirs))
    assert hits[0]["key"] == "rpgmaker-mvmz"
    enc = protections.encrypted_content(ix, hits)
    assert enc and enc[0]["scheme"] == "rpgmaker-mv-systemjson"


@pytest.mark.parametrize("encrypted", [True, False])
def test_pyinstaller_key_archive_is_flagged(tmp_path, encrypted):
    toc = (b"pyimod00_crypto_key\x00" if encrypted else b"pyimod01_archive\x00") + b"PYZ-00.pyz\x00"
    root = make(tmp_path / "G", {"Game.exe": b"MZ" + b"\x00" * 200 + toc + b"\x00" * 16 + fingerprint.PYI_COOKIE + b"\x00" * 24})
    ix = FileIndex(root)
    hits, _ = fingerprint.detect(ix, rules.match(ix.files, ix.dirs))
    assert hits[0]["key"] == "pyinstaller"
    names = [p["name"] for p in protections.encrypted_content(ix, hits)]
    assert (policy.PYINSTALLER_KEY_ARCHIVE in names) is encrypted


@pytest.mark.parametrize("files, key", [
    ({"game/csgo/gameinfo.gi": "x", "game/bin/win64/engine2.dll": b"MZ"}, "source2"),
    ({"Data/Skyrim.esm": b"x", "Data/Skyrim - Misc.bsa": b"x", "SkyrimSE.exe": b"MZ"}, "creation"),
    ({"jre64/release": 'JAVA_VERSION="25.0.1"\nIMPLEMENTOR="Azul Systems, Inc."\n', "projectzomboid.jar": b"PK"}, "java"),
    ({"GAMEDATA/PCBANKS/NMSARC.Globals.pak": b"x", "Binaries/NMS.exe": b"MZ"}, "nms"),
    ({"DW/Data/engine_PC.rpack": b"x", "engine_x64_rwdi.dll": b"MZ"}, "chrome"),
    ({"Data/.build.config": b"x", "Diablo IV.exe": b"MZ"}, "blizzard-casc"),
    ({"media/zipmanifest.xml": b"x", "forzahorizon6.exe": b"MZ"}, "forzatech"),
    ({"bin/lua51.dll": b"MZ", "data/bundle_database.data": b"x"}, "stingray"),
    ({"hlboot.dat": b"HLB\x04", "libhl.dll": b"MZ"}, "hashlink"),
    ({"RPG_RT.exe": b"MZ", "RPG_RT.ldb": b"x"}, "rpgmaker-2k"),
    ({"re_chunk_000.pak": b"x"}, "re-engine"),
    ({"resources/app.asar": b"x", "Game.exe": b"MZ"}, "electron"),
])
def test_engine_signatures(tmp_path, files, key):
    assert engine_of(make(tmp_path / "G", files))["key"] == key


def test_unknown_folder_is_native(tmp_path):
    assert engine_of(make(tmp_path / "G", {"thing.exe": b"MZ", "data.bin": b"x"}))["key"] == "native"


def test_battleye_gives_no_runtime_verdict(tmp_path):
    root = make(tmp_path / "G", {"GTA5_Enhanced.exe": b"MZ", "GTA5_Enhanced_BE.exe": b"MZ",
                                 "BattlEye/BEService_x64.exe": b"MZ", "BattlEye/BEClient_x64.dll": b"MZ"})
    report = scan.run_recon({"name": "Some Game", "path": str(root), "store": None})
    assert report["verdict"] == policy.NO_RUNTIME
    assert any(p["kind"] == "anticheat" and "BattlEye" in p["name"] for p in report["protections"])


def test_clean_unity_game_is_offline_ok_and_report_renders(tmp_path):
    root = make(tmp_path / "G", {"Game.exe": b"MZ", "UnityPlayer.dll": b"MZ", "Game_Data/globalgamemanagers": b"2021.3.1f1",
                                 "Game_Data/Managed/Assembly-CSharp.dll": b"MZ"})
    report = scan.run_recon({"name": "Clean Game", "path": str(root), "store": None})
    assert report["verdict"] == policy.OFFLINE_OK
    md = scan.render_markdown(report)
    assert "Unity (Mono)" in md and "offline-ok" in md
    steps = "\n".join(report["next_steps"])
    assert ("gst decompile dotnet" in steps) if "decompile" in cli.command_names() else ("decompile the dotnet code" in steps)


def test_online_only_title_by_name(tmp_path):
    root = make(tmp_path / "G", {"x.exe": b"MZ"})
    report = scan.run_recon({"name": "Diablo® IV", "path": str(root), "store": None})
    assert report["verdict"] == policy.NO_RUNTIME


def test_store_signals_map_to_protections():
    store = {"categories": [{"id": 8, "name": "Valve Anti-Cheat enabled"}, {"id": 36, "name": "Online PvP"}],
             "anticheat_names": ["BattlEye"], "drm_notice": "Denuvo Anti-tamper", "ext_user_account_notice": "EA Account"}
    kinds = {p["kind"] for p in protections.from_store(store)}
    assert {"vac", "anticheat", "anti-tamper", "launcher-account", "online-backend"} <= kinds


def test_pe_hints_become_protected_modules(tmp_path, monkeypatch):
    root = make(tmp_path / "G", {"SkyrimSE.exe": b"MZ"})
    monkeypatch.setattr(sniff, "pe_detail", lambda p: {"arch": "x64", "managed": False, "sections": [{"name": ".bind"}],
                                                       "imports": [], "pdb": None, "version": {},
                                                       "protector_hints": [("drm-wrapper", "Steam DRM (SteamStub)")]})
    prots, details, protected = protections.from_pe(FileIndex(root), ["skyrimse.exe"])
    assert prots[0]["kind"] == "drm-wrapper" and protected == ["skyrimse.exe"]


def test_vdf_and_resolve_game(tmp_path):
    text = '"AppState"\n{\n\t"appid"\t\t"457140"\n\t"name"\t\t"Oxygen Not Included"\n\t"installdir"\t\t"OxygenNotIncluded"\n}\n'
    assert stores.vdf(text)["AppState"]["installdir"] == "OxygenNotIncluded"
    games = [{"store": "steam", "appid": "457140", "name": "Oxygen Not Included", "path": str(tmp_path / "ONI")},
             {"store": "steam", "appid": "108600", "name": "Project Zomboid", "path": str(tmp_path / "PZ")}]
    assert stores.resolve_game("oxygen", games)["appid"] == "457140"
    assert stores.resolve_game("108600", games)["name"] == "Project Zomboid"
    folder = tmp_path / "Loose Game"
    folder.mkdir()
    assert stores.resolve_game(str(folder), games)["store"] is None


def test_script_bytecode_headers(tmp_path):
    lbc = tmp_path / "stdlib.lbc"
    lbc.write_bytes(b"\x1bLuaQ\x00\x00\x04\x04\x04\x08\x00")
    info = sniff.script_bytecode(lbc)
    assert info["kind"] == "lua" and info["version"] == "5.1" and info["big_endian"] is True
    cls = tmp_path / "A.class"
    cls.write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x45")
    assert sniff.script_bytecode(cls) == {"kind": "java-class", "major": 69}
