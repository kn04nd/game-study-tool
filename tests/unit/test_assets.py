"""gst assets: naming, sidecars, INDEX.md, audio orchestration (vgmstream faked), Unity filters."""

import json
import subprocess
from pathlib import Path

import pytest

from gst import assets
from gst.assets import audio, unity
from gst.common import GsError


def test_safe_name_and_unique_path(tmp_path):
    assert assets.safe_name('a<b>:c"/d\\e|f?g*h') == "a_b_c_d_e_f_g_h"
    assert assets.safe_name("x" * 200) == "x" * 80
    assert assets.safe_name("...") == "unnamed"
    first = assets.unique_path(tmp_path, "hatch_0", ".png", "2554")
    first.write_bytes(b"png")
    assert assets.unique_path(tmp_path, "hatch_0", ".png", "2555").name == "hatch_0__2555.png"


def test_index_from_file_and_folder_sidecars(tmp_path):
    ws = tmp_path / "ws"
    tex = ws / "assets" / "unity" / "Texture2D"
    tex.mkdir(parents=True)
    for i in range(3):
        f = tex / f"t{i}.png"
        f.write_bytes(b"x" * 10)
        assets.write_sidecar(f, {"source": "Game_Data/sharedassets0.assets", "path_id": i, "type": "Texture2D", "tool": "UnityPy"})
    (tex / "Weird.gs.json").write_text("{}", encoding="utf-8")  # an export named *.gs.json, not a sidecar
    bank = ws / "assets" / "audio" / "Creatures"
    bank.mkdir(parents=True)
    (bank / assets.FOLDER_SIDECAR).write_text(json.dumps({"source": "Creatures.bank", "tool": "vgmstream r2117", "count": 4,
                                                          "bytes": 4000, "examples": ["a.wav", "b.wav"]}), encoding="utf-8")
    summary = assets.build_index(ws)
    assert summary == {"folders": 2, "files": 7, "bytes": 4030}
    text = (ws / "assets" / "INDEX.md").read_text(encoding="utf-8")
    assert "| `unity/Texture2D` | 3 |" in text and "| `audio/Creatures` | 4 |" in text and "`a.wav`" in text
    side = json.loads((tex / "t0.png.gs.json").read_text(encoding="utf-8"))
    assert side["sha256"] and side["size"] == 10 and side["file"] == "t0.png"


VGM_LINES = [
    {"sampleRate": 48000, "numberOfSamples": 96000, "channels": 1, "encoding": "Vorbis",
     "streamInfo": {"index": 1, "name": "SG_hatch_chew_01", "total": 3}},
    {"sampleRate": 48000, "numberOfSamples": 48000, "channels": 2, "encoding": "Vorbis",
     "streamInfo": {"index": 2, "name": "SG_puft_idle", "total": 3}},
    {"sampleRate": 48000, "numberOfSamples": 24000, "channels": 1, "encoding": "Vorbis",
     "streamInfo": {"index": 3, "name": "SG_hatch_chew_02", "total": 3}},
]


@pytest.fixture
def fake_vgmstream(tmp_path, monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "-I" in cmd:
            out = "\n".join(json.dumps(line) for line in VGM_LINES) + "\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"RIFF....WAVE")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(audio, "vgmstream", lambda: Path("vgmstream-cli.exe"))
    monkeypatch.setattr(audio.tools, "installed_version", lambda tool: "r2117")
    monkeypatch.setattr(audio.subprocess, "run", run)
    return calls


def test_audio_streams_and_convert(tmp_path, fake_vgmstream):
    bank = tmp_path / "game" / "Data" / "Creatures.bank"
    bank.parent.mkdir(parents=True)
    bank.write_bytes(b"FSB5")
    assert audio.find(tmp_path / "game") == [bank]
    assert audio.find(tmp_path / "game", ["Data/*.bank"]) == [bank]
    extracted = tmp_path / "ws" / "assets" / "raw" / "Music.bank"  # absolute globs reach files outside the install
    extracted.parent.mkdir(parents=True)
    extracted.write_bytes(b"FSB5")
    assert audio.find(tmp_path / "game", [str(tmp_path / "ws" / "assets" / "**" / "*.bank")]) == [extracted]
    listed = audio.streams(bank)
    assert [s["name"] for s in listed] == ["SG_hatch_chew_01", "SG_puft_idle", "SG_hatch_chew_02"]
    assert listed[0]["seconds"] == 2.0
    ws = tmp_path / "ws"
    result = audio.convert(ws, [bank], name="hatch", limit=1)
    assert result["matched"] == 2 and result["exported"] == {"Creatures.bank": 1} and result["limited"]
    side = json.loads((ws / "assets" / "audio" / "Creatures" / assets.FOLDER_SIDECAR).read_text(encoding="utf-8"))
    assert side["streams"] == {"1": "SG_hatch_chew_01.wav"} and side["streams_in_source"] == 3
    convert_calls = [c for c in fake_vgmstream if "-o" in c]
    assert convert_calls[0][convert_calls[0].index("-s") + 1] == "1"
    again = audio.convert(ws, [bank], name="hatch", limit=None)  # second run adds the rest, skips the first
    assert again["exported"] == {"Creatures.bank": 1}
    side = json.loads((ws / "assets" / "audio" / "Creatures" / assets.FOLDER_SIDECAR).read_text(encoding="utf-8"))
    assert side["streams"] == {"1": "SG_hatch_chew_01.wav", "3": "SG_hatch_chew_02.wav"} and side["count"] == 2


def test_unity_filters_and_errors(tmp_path):
    row = {"type": "Texture2D", "name": "hatch_0", "file": "sharedassets0.assets", "container": "assets/x.prefab"}
    assert unity._matches(row, ("Texture2D",), "^hatch", None, None)
    assert not unity._matches(row, ("Sprite",), None, None, None)
    assert unity._matches(row, (), None, "x\\.prefab", "shared")
    with pytest.raises(GsError, match="not a Unity player build"):
        unity.data_dir(tmp_path)
    with pytest.raises(GsError, match="cannot export"):
        unity.extract(tmp_path, tmp_path, types=("Shader",))


def test_assets_commands_refuse_other_engines(isolated_env, monkeypatch):
    from gst import cmd_assets, workspace

    monkeypatch.setattr(workspace, "current", lambda required=True: (Path("ws"), {"name": "G", "engine": {"key": "gamemaker", "label": "GameMaker"}}))
    with pytest.raises(GsError, match="Unity, Godot and Unreal"):
        cmd_assets._unity_game()
    monkeypatch.setattr(workspace, "current", lambda required=True: (Path("ws"), {"name": "G", "install_dir": "D:/G", "verdict": "offline-ok",
                                                                                   "engine": {"key": "unreal", "label": "Unreal"}}))
    with pytest.raises(GsError, match="Unity-only"):
        cmd_assets._unity_game()


def test_assetripper_export_against_a_fake_server(tmp_path, monkeypatch):
    import threading
    import urllib.parse
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from gst.assets import assetripper

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>AssetRipper</html>")

        def do_POST(self):
            form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            seen.append((self.path, form["path"][0]))
            if self.path.startswith("/Export/"):
                out = Path(form["path"][0])
                (out / "Assets" / "Scenes").mkdir(parents=True, exist_ok=True)
                (out / "Assets" / "Scenes" / "Main.unity").write_text("%YAML 1.1", encoding="utf-8")
            self.send_response(200)
            self.end_headers()

    class FakeProc:
        def __init__(self, argv, **kwargs):
            port = int(argv[argv.index("--port") + 1])
            assert argv[1] == "--headless" and "--log-path" in argv
            self.server = HTTPServer(("127.0.0.1", port), Handler)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True
            self.server.shutdown()
            self.server.server_close()

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    procs = []
    monkeypatch.setattr(assetripper.subprocess, "Popen", lambda argv, **kw: procs.append(FakeProc(argv, **kw)) or procs[-1])
    monkeypatch.setattr(assetripper.tools, "bin_path", lambda tool: Path("AssetRipper.GUI.Free.exe"))
    monkeypatch.setattr(assetripper.tools, "installed_version", lambda tool: "2.0.0")
    ws, install = tmp_path / "ws", tmp_path / "Game"
    result = assetripper.export(ws, install, mode="project", settle=0)
    assert [p for p, _ in seen] == ["/LoadFolder", "/Export/UnityProject"] and seen[0][1] == str(install)
    assert result["files"] == 1 and procs[0].terminated
    side = json.loads((ws / "assets" / "assetripper" / "project" / assets.FOLDER_SIDECAR).read_text(encoding="utf-8"))
    assert side["mode"] == "project" and side["tool"] == "AssetRipper 2.0.0" and side["examples"] == ["Assets/Scenes/Main.unity"]
    with pytest.raises(GsError, match="not empty"):
        assetripper.export(ws, install, mode="project")


def test_unreal_containers_game_guess_and_manifest(tmp_path):
    import struct

    from gst.assets import unreal

    install = tmp_path / "Game"
    paks = install / "Proj" / "Content" / "Paks"
    paks.mkdir(parents=True)
    footer = b"\0" * 16 + b"\x01" + struct.pack("<IIQQ", 0x5A6F12E1, 11, 0, 0) + b"\0" * 20  # guid, enc flag, magic, version
    (paks / "Proj-WindowsNoEditor.pak").write_bytes(b"\0" * 64 + footer)
    assert unreal.paks_dir(install) == paks
    items = unreal.containers(paks)
    assert items[0]["kind"] == "pak" and items[0]["name"].endswith(".pak")
    assert unreal.ue_game(paks, {}) == "GAME_UE4_27"
    assert unreal.ue_game(paks, {"engine": {"version": "5.4.2"}}) == "GAME_UE5_4"
    assert unreal.ue_game(paks, {}, "4.25") == "GAME_UE4_25"
    (install / "Manifest_UFSFiles_Win64.txt").write_text(
        "Proj/Content/Blueprints/BP_Hero.uasset\t2023-05-05T13:28:15.700Z\nProj/Content/Maps/Main.umap\t2023\n", encoding="utf-8")
    manifest, rows = unreal.manifest_paths(install)
    assert manifest.name.startswith("Manifest_UFSFiles") and rows == ["Proj/Content/Blueprints/BP_Hero.uasset", "Proj/Content/Maps/Main.umap"]


def test_unreal_build_commands_are_denied():
    from gst import guard

    for cmd in ("repak pack mod out.pak", "retoc pack-raw x y", "retoc to-zen a b", "UAssetGUI fromjson a.json b.uasset 27"):
        d = guard.check_command(cmd)
        assert not d.allowed and d.rule == 1, cmd
    assert guard.check_command('repak list "D:/x/FSD-WindowsNoEditor.pak"').allowed
    assert guard.check_command("retoc info D:/x/Paks").allowed
