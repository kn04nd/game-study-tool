"""The unified sidecar schema (docs/INTERFACES.md section 2): write_sidecar is the only writer."""

from __future__ import annotations

import json

import pytest

from gst import assets
from gst.common import long_path


def read(path):
    return json.loads(open(long_path(path), encoding="utf-8").read())


def test_required_keys_and_int_size(tmp_path):
    f = tmp_path / "tex.png"
    f.write_bytes(b"x" * 33)
    side = assets.write_sidecar(f, {"source": "Game_Data\\sharedassets0.assets", "path_id": 7, "tool": "UnityPy",
                                    "size": [512, 256], "name": "A" * 200})
    meta = read(side)
    assert set(assets.REQUIRED_KEYS) <= set(meta)
    assert meta["size"] == 33 and isinstance(meta["size"], int) and meta["dimensions"] == [512, 256]
    assert meta["source"] == "Game_Data/sharedassets0.assets" and meta["build"] == "?" and meta["tool_version"] == ""
    assert meta["name"] == "A" * 200  # the full name, never cut
    assert len(meta["sha256"]) == 64


@pytest.mark.parametrize("meta", [{"path_id": 1, "tool": "x"}, {"source": "a", "path_id": 1}, {"source": "a", "tool": "x"}])
def test_missing_required_keys_raise(tmp_path, meta):
    f = tmp_path / "a.bin"
    f.write_bytes(b"1")
    with pytest.raises(ValueError):
        assets.write_sidecar(f, meta)


def test_folder_sidecar(tmp_path):
    folder = tmp_path / "project"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.tscn").write_text("x", encoding="utf-8")
    (folder / "sub" / "b.png").write_bytes(b"12345")
    meta = read(assets.write_folder_sidecar(folder, {"source": "Game.pck", "res_path": "res://", "tool": "gdre_tools",
                                                    "tool_version": "2.0", "build": 42}))
    assert meta["file"] == "." and meta["size"] == 6 and meta["count"] == 2 and meta["bytes"] == 6
    assert meta["build"] == "42" and set(assets.REQUIRED_KEYS) - {"sha256"} <= set(meta)


def test_long_output_paths(tmp_path):
    deep = tmp_path
    for i in range(12):
        deep = deep / f"folder_with_a_rather_long_name_{i:02d}"
    import os

    os.makedirs(long_path(deep))
    f = deep / ("n" * 60 + ".png")
    with open(long_path(f), "wb") as fh:
        fh.write(b"abc")
    assert len(str(f)) > 260
    meta = read(assets.write_sidecar(f, {"source": "x", "res_path": "res://x.png", "tool": "gdre_tools"}))
    assert meta["size"] == 3


def test_index_tolerates_old_list_sizes(tmp_path):
    ws = tmp_path / "ws"
    tex = ws / "assets" / "unity" / "Texture2D"
    tex.mkdir(parents=True)
    (tex / "t.png").write_bytes(b"x" * 10)
    (tex / "t.png.gs.json").write_text(json.dumps({"file": "t.png", "size": [64, 64], "source": "a", "tool": "UnityPy"}),
                                       encoding="utf-8")  # a gs v1 sidecar
    assert assets.build_index(ws) == {"folders": 1, "files": 1, "bytes": 0}
