"""Godot: GDScript extractor (index) and the GDRE backend (command, encrypted packs, pack discovery)."""

import struct
from pathlib import Path

import pytest

from gst.common import GsError
from gst.decompile import godot
from gst.index import gdscript

SCRIPT = '''@tool
class_name Enemy extends "res://scripts/base_actor.gd"

signal died(by: Node)
enum State { IDLE, CHASE }
const SPEED := 120.0
@export var health: int = 3
var _target = null

class Hitbox extends Area2D:
    var damage := 1
    func hit(other):
        other.take_damage(damage)

func _ready() -> void:
    var tween = create_tween()
    tween.tween_property(self, "modulate", Color.RED, 0.2)
    _target = Player.new()

static func spawn(scene: PackedScene) -> Enemy:
    return scene.instantiate() # "not a call"
'''


def test_gdscript_symbols_and_refs():
    data = gdscript.extract("decomp/godot/scripts/enemy.gd", SCRIPT.encode())
    by_q = {s[4]: (s[2], s[6], s[7]) for s in data["symbols"]}
    assert by_q["Enemy"][0] == "class"
    assert by_q["Enemy.died"][0] == "event" and by_q["Enemy.State"][0] == "enum"
    assert by_q["Enemy.SPEED"][0] == "const" and by_q["Enemy.health"][0] == "field"
    assert "Enemy._ready.tween" not in by_q  # locals are not members
    assert by_q["Enemy.Hitbox"][:2] == ("class", 10) and by_q["Enemy.Hitbox.hit"][0] == "method"
    assert by_q["Enemy._ready"][1:] == (15, 18) and by_q["Enemy.spawn"][0] == "method"
    assert ("Enemy", "base_actor") in [(data["symbols"][local][4], base) for local, base in data["bases"]]
    owners = {data["symbols"][who][4] if who is not None else None: (kind, name) for kind, _t, name, _l, who in data["refs"]}
    calls = {(kind, name) for kind, _t, name, _l, _w in data["refs"]}
    assert {("call", "take_damage"), ("call", "create_tween"), ("call", "tween_property"), ("new", "Player"),
            ("call", "instantiate")} <= calls
    assert owners["Enemy.Hitbox.hit"] == ("call", "take_damage")
    assert any(v == '"modulate"' for v, _l, _w in data["strings"])


def pck_bytes(encrypted: bool) -> bytes:
    flags = 1 if encrypted else 0
    return b"GDPC" + struct.pack("<IIII", 3, 4, 6, 1) + struct.pack("<I", flags) + b"\0" * 64


def test_find_and_check_pack(tmp_path):
    (tmp_path / "Game.exe").write_bytes(b"MZ" + b"\0" * 100)
    (tmp_path / "Game.pck").write_bytes(pck_bytes(False))
    pack = godot.find_pack(tmp_path)
    assert pack.name == "Game.pck" and godot.check_pack(pack).get("version")
    (tmp_path / "Game.pck").write_bytes(pck_bytes(True))
    with pytest.raises(GsError, match="rule 4"):
        godot.check_pack(pack)
    with pytest.raises(GsError, match="no Godot pack"):
        godot.find_pack(tmp_path / "missing")


def test_protected_game_pack_is_not_decompiled(tmp_path, monkeypatch):
    (tmp_path / "Game.exe").write_bytes(b"MZ" + b"\0" * 100)
    (tmp_path / "Game.pck").write_bytes(pck_bytes(False))
    monkeypatch.setattr(godot, "gdre", lambda: pytest.fail("GDRE must not start"))
    with pytest.raises(GsError, match="rule 2"):
        godot.decompile(tmp_path / "ws", {"install_dir": str(tmp_path), "verdict": "no-runtime"})


def test_gdre_command_is_read_only(tmp_path):
    cmd = godot.command(Path("gdre_tools.exe"), tmp_path / "Game.pck", tmp_path / "out")
    assert cmd[1] == "--headless" and cmd[2].startswith("--recover=")
    assert "--include=*.gd" in cmd and "--include=*.gdc" in cmd
    assert not any(c.startswith(godot.DANGEROUS) for c in cmd)


def test_gdre_write_commands_are_denied():
    from gst import guard

    for flag in ("--pck-patch=x.pck", "--pck-create=y.pck", "--compile=a.gd", "--txt-to-bin=a.tscn", "--patch-translations=t"):
        d = guard.check_command(f"gdre_tools --headless {flag}")
        assert not d.allowed and d.rule == 1, flag
    assert guard.check_command('gdre_tools --headless --recover="D:/x/Game.pck" --output=D:/ws/decomp/godot').allowed
