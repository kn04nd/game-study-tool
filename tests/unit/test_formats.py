"""gst fmt on synthetic files: signatures, entropy, bounded hexdumps, strings, diffs, scans, readers."""

import hashlib
import json
import os
import struct
import zlib
from pathlib import Path

import pytest

from gst import formats
from gst.common import GsError
from gst.formats import readers

FIXTURES = Path(__file__).resolve().parents[2] / "src" / "gst" / "data" / "readers"  # example readers shipped with gst


@pytest.mark.parametrize("head, kind", [
    (b"\x89PNG\r\n\x1a\n\x00\x00", "png"),
    (b"GVAS\x02\x00\x00\x00", "ue-gvas"),
    (b"RIFF\x10\x00\x00\x00WAVEfmt ", "wav"),
    (b"RIFF\x10\x00\x00\x00FEV LIST", "fmod-bank"),
    (b"FORM\x00\x10\x00\x00GEN8", "gamemaker-data"),
    (b"\x78\x9c\x01\x02", "zlib"),
    (b"UnityFS\x00\x00\x00\x00\x08", "unity-bundle"),
    (b"\x1bLuaQ\x00\x01\x04", "lua-bytecode"),
    (b'{"a": 1, "b": [1, 2]}\n', "json"),
    (b"[General]\nvolume=3\n", "ini"),
    (b"\x00\x13\x37\x99\xfe\x01", "unknown"),
])
def test_signatures(head, kind):
    assert formats.identify_bytes(head)[0] == kind


def test_identify_reads_footers_and_entropy(tmp_path):
    pak = tmp_path / "Game-WindowsNoEditor.pak"
    footer = b"\x00" * 16 + b"\x00" + struct.pack("<II", 0x5A6F12E1, 11) + b"\x00" * 60
    pak.write_bytes(os.urandom(4096) + footer)
    info = formats.identify(pak)
    assert info["kind"] == "ue-pak" and "v11" in info["detail"] and info["entropy_class"] == "compressed or encrypted"
    assert formats.entropy(bytes(1024)) == 0 and formats.entropy(bytes(range(256)) * 8) == 8
    assert formats.entropy_class(0.2) == "padding/sparse" and formats.entropy_class(4.1) == "text or simple structure"


def test_hexdump_and_range_are_bounded(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(bytes(range(256)) * 8)
    assert len(formats.read_range(f, 0, 5000)) == formats.MAX_HEX
    assert formats.read_range(f, -16, 16) == bytes(range(240, 256))
    line = formats.hexdump(b"GVAS\x00\x01", 0x10)
    assert line.startswith("00000010  47 56 41 53 00 01") and line.endswith("GVAS..")


def test_strings_ascii_and_utf16(tmp_path):
    f = tmp_path / "s.bin"
    f.write_bytes(b"\x00\x01PlayerHealth\x00\x02" + "Inventário".encode("utf-16-le")[:14] + b"\x00\x00" + "SaveSlot".encode("utf-16-le"))
    found = {(s["encoding"], s["text"]) for s in formats.strings(f, minimum=5)}
    assert ("ascii", "PlayerHealth") in found and ("utf-16le", "SaveSlot") in found


def test_diff_ranges_and_insertions(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    base = bytearray(b"HEAD" + bytes(60))
    a.write_bytes(bytes(base))
    base[10] = 7
    base[12] = 9
    base[40] = 1
    b.write_bytes(bytes(base))
    d = formats.diff(a, b)
    assert [(r["start"], r["end"]) for r in d["ranges"]] == [(10, 13), (40, 41)] and d["common_prefix"] == 10
    c.write_bytes(b"HEAD" + b"\xff\xff" + bytes(60))
    grown = formats.diff(a, c)
    assert not grown["same_size"] and grown["common_prefix"] == 4 and grown["common_suffix"] == 60
    assert formats.diff(a, a)["identical"]


def test_scan_groups_by_extension(tmp_path):
    (tmp_path / "data").mkdir()
    for i in range(3):
        (tmp_path / "data" / f"m{i}.bnk").write_bytes(b"BKHD" + os.urandom(100))
    (tmp_path / "cfg.json").write_text('{"x": 1}', encoding="utf-8")
    groups = {g["ext"]: g for g in formats.scan(tmp_path)["groups"]}
    assert groups[".bnk"]["files"] == 3 and groups[".bnk"]["kinds"] == {"wwise-bnk": 3}
    assert groups[".json"]["kinds"] == {"json": 1}


def _sgsv(entities: list[tuple]) -> bytes:
    body = b"SGSV" + struct.pack("<HHIQI", 2, 0, len(entities), 1_700_000_000, 600)
    for etype, name, hp, extra in entities:
        raw = name.encode()
        body += struct.pack("<BB", etype, len(raw)) + raw + struct.pack("<fffiiIf", 1.0, 0.0, -2.0, hp, 100, extra, 0.5)
    return body + struct.pack("<I", zlib.crc32(body))


def test_new_reader_template_round_trips(tmp_path):
    sample = tmp_path / "save.bin"
    sample.write_bytes(b"ABCD" + struct.pack("<I", 3) + os.urandom(50))
    out = readers.new_reader(tmp_path, "mysave", sample)
    reader = Path(out["reader"])
    assert reader == tmp_path / "notes" / "formats" / "mysave.py" and "// ABCD" in reader.read_text(encoding="utf-8")
    note = Path(out["note"]).read_text(encoding="utf-8")
    assert "41 42 43 44 03 00 00 00" in note and "## Layout" in note
    result = readers.roundtrip(reader, [sample])
    assert result["identical"] == 1 and result["results"][0]["describe"] == {"version": 3, "unmapped_bytes": 50}
    with pytest.raises(GsError, match="already exists"):
        readers.new_reader(tmp_path, "mysave", sample)
    with pytest.raises(GsError, match="letters, digits"):
        readers.new_reader(tmp_path, "../evil", None)


def test_sgsv_fixture_reader_and_reported_failures(tmp_path):
    save = tmp_path / "run.sgs"
    save.write_bytes(_sgsv([(1, "Hero", 87, 12), (2, "Slime", 30, 3)]))
    result = readers.roundtrip(FIXTURES / "sgsv.py", [save])
    row = result["results"][0]
    assert row["ok"] and row["sha256"] == hashlib.sha256(save.read_bytes()).hexdigest()
    assert row["describe"] == {"entities": 2, "tick": 600, "player_hp": 87, "crc_ok": True, "unmapped_bytes": 0}
    lossy = tmp_path / "lossy.py"
    lossy.write_text("def parse(data):\n    return data[:-1]\n\ndef dump(obj):\n    return obj\n", encoding="utf-8")
    bad = readers.roundtrip(lossy, [save])["results"][0]
    assert not bad["ok"] and bad["first_difference"] == len(save.read_bytes()) - 1
    broken = tmp_path / "broken.py"
    broken.write_text("def parse(data):\n    raise ValueError('field 3 unknown')\n\ndef dump(obj):\n    return b''\n", encoding="utf-8")
    assert "field 3 unknown" in readers.roundtrip(broken, [save])["results"][0]["error"]
    (tmp_path / "nothing.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(GsError, match="must define parse"):
        readers.roundtrip(tmp_path / "nothing.py", [save])


def test_cli_resolves_paths_inside_the_install(tmp_path, monkeypatch, capsys):
    from gst import cli, workspace

    install = tmp_path / "Game"
    (install / "Data").mkdir(parents=True)
    (install / "Data" / "level.gvas").write_bytes(b"GVAS\x02\x00\x00\x00" + bytes(40))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)
    monkeypatch.setattr(workspace, "current", lambda required=True: (ws, {"name": "G", "install_dir": str(install)}))
    assert cli.main(["fmt", "id", "Data/*.gvas", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["kind"] == "ue-gvas" and rows[0]["path"].endswith("level.gvas")
