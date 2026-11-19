"""gst audio (inventory, PCM copies, catalog check, listening page) and gst maps (level files, raster PNG)
on synthetic workspaces: no game files, no ffmpeg (faked)."""

import json
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from gst.assets import audiocat, levels
from gst.common import GsError


def wav(path: Path, *, tag: int = 1, rate: int = 22050, channels: int = 1, bits: int = 16, data: int = 44100) -> Path:
    """A RIFF/WAVE header with `data` bytes of silence (tag 1 = PCM, 0x11 = IMA ADPCM)."""
    byterate = rate * channels * bits // 8 if tag == 1 else rate * channels // 2
    fmt = struct.pack("<HHIIHH", tag, channels, rate, byterate, max(1, channels * bits // 8), bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", data) + b"\0" * data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    snd = ws / "assets" / "audio" / "vfs" / "Data" / "Audio" / "Sounds"
    wav(snd / "tankmove1.wav")
    wav(snd / "tankmove2.wav", data=22050)
    wav(snd / "Brazilian" / "tankselect.wav", tag=0x11, rate=44100, bits=4, data=44100)
    wav(snd / "orphan.wav")
    (ws / "assets" / "audio" / "pcm").mkdir(parents=True)
    wav(ws / "assets" / "audio" / "pcm" / "ignored.wav")  # derived folder: not game audio
    return ws


def test_wav_info_reads_format_and_duration():
    head = wav(Path(__import__("tempfile").mkdtemp()) / "a.wav", tag=0x11, rate=44100, bits=4, data=44100).read_bytes()
    info = audiocat.wav_info(head)
    assert info["fmt"] == "IMA-ADPCM" and info["rate"] == 44100 and info["seconds"] == 2.0
    assert audiocat.wav_info(b"OggS....")["fmt"] == "not-riff"


def test_index_keeps_provenance_and_skips_derived(tmp_path):
    ws = make_ws(tmp_path)
    rel = "assets/audio/vfs/Data/Audio/Sounds/tankmove1.wav"
    (ws / "assets" / "audio" / "files.jsonl").write_text(json.dumps({"file": rel, "archive": "AudioZH.big", "effective": True}) + "\n",
                                                         encoding="utf-8")
    s = audiocat.index(ws, probe=False)
    assert s["files"] == 4 and s["formats"] == {"PCM": 3, "IMA-ADPCM": 1} and s["unplayable"] == 1
    rows = {r["file"]: r for r in audiocat.load_files(ws)}
    assert rows[rel]["archive"] == "AudioZH.big" and rows[rel]["seconds"] == 1.0
    assert not any("/pcm/" in f for f in rows)


def test_index_without_audio_folder_says_what_to_do(tmp_path):
    with pytest.raises(GsError, match="export the game's audio first"):
        audiocat.index(tmp_path)


def test_pcm_converts_only_unplayable_wavs(tmp_path, monkeypatch):
    ws = make_ws(tmp_path)
    audiocat.index(ws, probe=False)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"RIFF")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(audiocat, "_ffmpeg", lambda: Path("ffmpeg.exe"))
    monkeypatch.setattr(audiocat.subprocess, "run", fake_run)
    r = audiocat.pcm(ws, jobs=2)
    assert r["converted"] == 1 and len(calls) == 1
    assert calls[0][-1].replace("\\", "/").endswith("assets/audio/pcm/vfs/Data/Audio/Sounds/Brazilian/tankselect.wav")
    row = next(x for x in audiocat.load_files(ws) if x["file"].endswith("tankselect.wav"))
    assert row["pcm"] == "assets/audio/pcm/vfs/Data/Audio/Sounds/Brazilian/tankselect.wav"
    assert audiocat.pcm(ws)["skipped"] == 1  # re-runs skip what exists


def catalog(ws: Path) -> None:
    base = "assets/audio/vfs/Data/Audio/Sounds/"
    events = {
        "TankMoveLoop": {"name": "TankMoveLoop", "kind": "sound", "fields": {"Volume": "40"}, "defined_at": "SoundEffects.ini:8",
                         "files": [{"name": "tankmove1", "file": base + "tankmove1.wav"}, {"name": "tankmove2", "file": base + "tankmove2.wav"}],
                         "used_by": [{"where": "Object/Tank.ini:12", "block": "Object Tank", "key": "SoundMoveStart"}]},
        "TankVoiceSelect": {"name": "TankVoiceSelect", "kind": "voice", "ini": "Voice.ini:3",
                            "files": [{"name": "tankselect", "file": base + "Brazilian/tankselect.wav"}],
                            "used_by": [{"where": "Object/Tank.ini:20", "block": "Object Tank", "key": "VoiceSelect"}]},
        "CutContent": {"name": "CutContent", "kind": "sound", "files": [{"name": "gone", "file": None}], "used_by": []},
    }
    (ws / "assets" / "audio" / "catalog.json").write_text(json.dumps(events), encoding="utf-8")


def test_check_reports_missing_unreferenced_and_unused(tmp_path):
    ws = make_ws(tmp_path)
    audiocat.index(ws, probe=False)
    with pytest.raises(GsError, match="no assets/audio/catalog.json"):
        audiocat.check(ws)
    catalog(ws)
    s = audiocat.check(ws)
    assert s["events"] == 3 and s["files_referenced"] == 3 and s["files_unreferenced"] == 1
    assert s["missing_refs"] == 1 and s["missing_samples"][0]["event"] == "CutContent"
    assert s["events_without_users"] == 1 and s["usage_keys"] == {"SoundMoveStart": 1, "VoiceSelect": 1}
    assert s["categories"]["Vozes de personagens/unidades"] == 1
    assert (ws / "assets" / "audio" / "catalog_summary.json").exists()


def test_page_links_pcm_copies_and_marks_missing(tmp_path):
    ws = make_ws(tmp_path)
    audiocat.index(ws, probe=False)
    catalog(ws)
    pcm = audiocat.path_for_pcm(ws, "assets/audio/vfs/Data/Audio/Sounds/Brazilian/tankselect.wav")
    wav(pcm)
    audiocat.index(ws, probe=False)  # picks up the pcm copy
    r = audiocat.page(ws, "Game <Test>")
    html = (ws / "assets" / "audio" / "INDEX.html").read_text(encoding="utf-8")
    assert r["items"] == 3 and r["players"] == 3 and r["without_file"] == 1
    assert "pcm/vfs/Data/Audio/Sounds/Brazilian/tankselect.wav" in html and "Game &lt;Test&gt;" in html
    assert "Object Tank (VoiceSelect)" in html
    (ws / "assets" / "audio" / "catalog.json").unlink()
    r = audiocat.page(ws, "Game")  # without a catalog: one item per file, by folder
    assert r["items"] == 4 and r["from"] == "assets/audio/files.jsonl"


def test_maps_list_finds_levels_in_install_and_inventories(tmp_path):
    ws, install = tmp_path / "ws", tmp_path / "game"
    (install / "Maps" / "Desert").mkdir(parents=True)
    (install / "Maps" / "Desert" / "Desert.map").write_bytes(b"EAR\0")
    (install / "Game_Data").mkdir()
    (install / "Game_Data" / "level0").write_bytes(b"\0")
    (install / "readme.txt").write_text("x", encoding="utf-8")
    inv = ws / "assets" / "big"
    inv.mkdir(parents=True)
    (inv / "inventory.jsonl").write_text(json.dumps({"archive": "MapsZH.big", "path": "Maps\\Alpine\\Alpine.map", "size": 9}) + "\n"
                                         + json.dumps({"archive": "W3D.big", "path": "Art\\W3D\\tank.w3d"}) + "\n", encoding="utf-8")
    s = levels.find(ws, install)
    assert s["levels"] == 3 and s["by_kind"]["Unity built scene"] == 1
    rows = json.loads((ws / "dumps" / "maps" / "levels.json").read_text(encoding="utf-8"))
    packed = next(r for r in rows if r["source"] != "install")
    assert packed["container"] == "MapsZH.big" and packed["in_level_folder"]


def test_raster_writes_a_valid_png_with_range(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    grid = bytes([0, 64, 128, 255] * 3)  # 4x3 u8 after a 5-byte header
    src = ws / "dumps" / "m.bin"
    src.parent.mkdir()
    src.write_bytes(b"HEADR" + grid)
    r = levels.raster(ws, src, width=4, height=3, offset=5, flip_y=True, name="m_height")
    png = (ws / r["out"]).read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (4, 3)
    idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
    assert zlib.decompress(idat) == (b"\0" + bytes([0, 64, 128, 255])) * 3
    assert r["min"] == 0 and r["max"] == 255 and (ws / "assets" / "maps" / "m_height.png.gs.json").exists()
    r16 = levels.raster(ws, src, width=2, height=1, offset=5, dtype="u16le")
    assert (r16["min"], r16["max"]) == (16384, 65408)
    with pytest.raises(GsError, match="needed"):
        levels.raster(ws, src, width=100, height=100)
