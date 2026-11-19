"""Java and Lua: index extractors, jar package selection, Lua bytecode headers and the unluac fallback."""

import subprocess
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_java")

from gst.decompile import java as java_backend  # noqa: E402
from gst.decompile import lua as lua_backend  # noqa: E402
from gst.index import java, lua  # noqa: E402

JAVA_SRC = b'''package zombie.characters;
import java.util.List;
public class IsoPlayer extends IsoLivingCharacter implements Talker, Runnable {
    public static IsoPlayer instance;
    private final List<String> names = new ArrayList<>();
    public IsoPlayer(IsoCell cell) { super(cell); }
    public float getHealth() { return this.getBodyDamage().getHealth(); }
    public void update() { GameTime.getInstance().update(); System.out.println("tick"); }
    enum State { IDLE, RUN }
}
'''
LUA_SRC = b'''require "ISUI/ISPanel"
ISInventoryPane = ISPanel:derive("ISInventoryPane");
local function helper(a, b) return a + b end
function ISInventoryPane:new(x, y, width, height)
    local o = ISPanel.new(self, x, y, width, height)
    getPlayer():Say("hello")
    return o
end
function ISInventoryPane.onMouseUp(x, y) helper(x, y) end
M = {}
M.run = function(n) print("run", n) end
'''


def test_java_extractor():
    data = java.extract("x.java", JAVA_SRC)
    by_q = {s[4]: s[2] for s in data["symbols"]}
    assert by_q["zombie.characters.IsoPlayer"] == "class"
    assert by_q["zombie.characters.IsoPlayer.getHealth"] == "method"
    assert by_q["zombie.characters.IsoPlayer.IsoPlayer"] == "ctor"
    assert by_q["zombie.characters.IsoPlayer.instance"] == "field"
    assert by_q["zombie.characters.IsoPlayer.State.RUN"] == "enum_member"
    assert {b for _, b in data["bases"]} == {"IsoLivingCharacter", "Talker", "Runnable"}
    calls = {(k, n) for k, _t, n, _l, _w in data["refs"]}
    assert {("call", "getHealth"), ("call", "getInstance"), ("call", "println"), ("new", "ArrayList")} <= calls
    assert any(v == '"tick"' for v, _l, _w in data["strings"])


def test_lua_extractor_maps_derive_classes():
    data = lua.extract("decomp/lua/media/lua/client/ISUI/ISInventoryPane.lua", LUA_SRC)
    by_q = {s[4]: s[2] for s in data["symbols"]}
    assert by_q["ISInventoryPane"] == "class" and by_q["ISInventoryPane.new"] == "method"
    assert by_q["ISInventoryPane.onMouseUp"] == "function" and by_q["helper"] == "function"
    assert by_q["M"] == "table" and by_q["M.run"] == "function"
    assert ("ISInventoryPane", "ISPanel") in {(data["symbols"][local][4], base) for local, base in data["bases"]}
    owners = {(data["symbols"][w][4] if w is not None else None, name) for _k, _t, name, _l, w in data["refs"]}
    assert ("ISInventoryPane.new", "Say") in owners and ("ISInventoryPane.onMouseUp", "helper") in owners


def make_jar(path: Path, entries: list[str], main: str | None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        if main:
            z.writestr("META-INF/MANIFEST.MF", f"Manifest-Version: 1.0\nMain-Class: {main}\n")
        for e in entries:
            z.writestr(e, b"\xca\xfe\xba\xbe")
    return path


def test_jar_game_roots(tmp_path):
    jar = make_jar(tmp_path / "game.jar", ["zombie/A.class", "zombie/b/C.class", "org/lwjgl/G.class", "kotlin/K.class",
                                           "astar/P.class", "se/krka/kahlua/V.class"], "zombie.gameStates.MainScreenState")
    info = java_backend.jar_roots(jar)
    assert info["main"] == "zombie/gameStates/MainScreenState"
    assert java_backend.game_roots(info) == ["astar/", "se/", "zombie/"]
    studio = make_jar(tmp_path / "s.jar", ["com/acme/game/Main.class", "com/google/X.class"], "com.acme.game.Main")
    assert java_backend.game_roots(java_backend.jar_roots(studio)) == ["com/acme/"]
    cmd = java_backend.command(Path("java"), Path("vf.jar"), jar, tmp_path / "out", ["zombie/"], 8)
    assert "--folder" in cmd and "--only=zombie/" in cmd and cmd[-2:] == [str(jar), str(tmp_path / "out")]


def test_lua_headers_scan_and_unluac_fallback(tmp_path, monkeypatch):
    game = tmp_path / "game"
    (game / "media" / "lua" / "client").mkdir(parents=True)
    (game / "media" / "lua" / "client" / "ui.lua").write_text("print('hi')\n", encoding="utf-8")
    kahlua = bytes.fromhex("1b4c7561510000040404080000000000")  # Lua 5.1, big-endian
    (game / "stdlib.lbc").write_bytes(kahlua + b"\0" * 32)
    (game / "stdlib.lua").write_text("function assert(v) return v end\n", encoding="utf-8")
    (game / "fast.luac").write_bytes(b"\x1bLJ\x02" + b"\0" * 16)
    info = lua_backend.header(game / "stdlib.lbc")
    assert info["version"] == "5.1" and info["big_endian"] and info["sizeof_number"] == 8
    plain, compiled = lua_backend.scan(game)
    assert {p.name for p in plain} == {"ui.lua", "stdlib.lua"} and {p.name for p in compiled} == {"stdlib.lbc", "fast.luac"}

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "function assert(_ARG_0_)\nend\n", "java.lang.IllegalStateException: TEMP\n at x")
    monkeypatch.setattr(lua_backend.subprocess, "run", fake_run)
    monkeypatch.setattr(lua_backend.tools, "bin_path", lambda tool: Path(f"{tool}.exe"))
    monkeypatch.setattr(lua_backend.tools, "installed_version", lambda tool: "2025-12-23")
    ws = tmp_path / "ws"
    result = lua_backend.decompile(ws, {"install_dir": str(game), "verdict": "offline-ok"})
    assert result["copied"] == 2
    by_file = {b["file"]: b for b in result["bytecode"]}
    assert by_file["stdlib.lbc"]["decompiled"] is False and by_file["stdlib.lbc"]["source_twin"] == "stdlib.lua"
    assert "TEMP" in by_file["stdlib.lbc"]["error"]
    partial = (ws / "decomp" / "lua" / "stdlib.partial.lua").read_text(encoding="utf-8")
    assert partial.startswith("-- gst: unluac stopped") and "_ARG_0_" in partial
    assert by_file["fast.luac"]["kind"] == "luajit" and not by_file["fast.luac"]["decompiled"]
    assert (ws / "decomp" / "lua" / "media" / "lua" / "client" / "ui.lua").exists()

    calls = []
    monkeypatch.setattr(lua_backend.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    protected = lua_backend.decompile(tmp_path / "ws2", {"install_dir": str(game), "verdict": "no-runtime"})
    assert protected["copied"] == 2 and not calls  # plain scripts are read like any file; bytecode stays closed
    assert all("rule 2" in b["error"] for b in protected["bytecode"])


def test_module_names_skip_generic_folders():
    from gst.index import module_of

    assert module_of("decomp/lua/media/lua/client/ISUI/X.lua") == "client"
    assert module_of("decomp/cs/Assembly-CSharp/Klei/AI/X.cs") == "Assembly-CSharp"
    assert module_of("decomp/java/projectzomboid/zombie/X.java") == "projectzomboid"
