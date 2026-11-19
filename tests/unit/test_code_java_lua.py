"""code.java / code.lua: units per jar, loose class tree and script folder, fingerprints that follow the options,
the in-process path of `gst decompile java|lua`, and a `gst study` run (worker processes) followed by code.index,
then a resume with nothing redone. Vineflower and unluac are faked; every fixture is synthetic."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

import gst
from gst.decompile import java, lua
from gst.extractors import javalua, registry
from gst.extractors.base import Context

KAHLUA = bytes.fromhex("1b4c7561510000040404080000000000")  # Lua 5.1 header, big-endian


def make_class(internal: str) -> bytes:
    """A minimal class file whose constant pool names ``internal`` (with a Long: two slots)."""
    pool = [b"\x05" + struct.pack(">q", 42),  # #1-#2 Long
            b"\x01" + struct.pack(">H", len(internal)) + internal.encode(),  # #3 Utf8
            b"\x07" + struct.pack(">H", 3),  # #4 Class this
            b"\x01" + struct.pack(">H", 16) + b"java/lang/Object",  # #5
            b"\x07" + struct.pack(">H", 5)]  # #6 Class super
    return (b"\xca\xfe\xba\xbe" + struct.pack(">HHH", 0, 61, 7) + b"".join(pool)
            + struct.pack(">HHHHHHH", 0x21, 4, 6, 0, 0, 0, 0))


def make_jar(path: Path, entries: list[str], main: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        if main:
            z.writestr("META-INF/MANIFEST.MF", f"Manifest-Version: 1.0\nMain-Class: {main}\n")
        for e in entries:
            z.writestr(e, make_class(e[:-len(".class")]))
    return path


def write(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(data)
    return path


LUA_PANE = 'ISInventoryPane = ISPanel:derive("ISInventoryPane")\nfunction ISInventoryPane:refresh(x)\n  helperSort(x)\nend\n'
LUA_UTIL = "function helperSort(t)\n  table.sort(t)\nend\n"


def make_install(root: Path) -> Path:
    make_jar(root / "game.jar", ["com/acme/game/Main.class", "com/acme/game/world/World.class", "org/lib/L.class"],
             "com.acme.game.Main")
    make_jar(root / "libs" / "lib.jar", ["org/x/Y.class"])
    make_jar(root / "jre64" / "lib" / "rt.jar", ["java/lang/Object.class"])  # bundled JRE: never a unit
    write(root / "zombie" / "core" / "Core.class", make_class("zombie/core/Core"))
    write(root / "zombie" / "Zed.class", make_class("zombie/Zed"))
    write(root / "bin" / "classes" / "org" / "foo" / "F.class", make_class("org/foo/F"))
    write(root / "plugins" / "Plug.class", make_class("Plug"))  # default package
    write(root / "media" / "lua" / "client" / "ISUI" / "ISInventoryPane.lua", LUA_PANE)
    write(root / "media" / "lua" / "client" / "ui.lua", "print('ui')\n")
    write(root / "media" / "lua" / "shared" / "util.lua", LUA_UTIL)
    write(root / "media" / "luaexamples" / "a.lua", "print('a')\n")
    write(root / "media" / "luaexamples" / "b.lua", "print('b')\n")
    write(root / "stdlib.lua", "function assert(v) return v end\n")
    write(root / "stdlib.lbc", KAHLUA + b"\0" * 32)
    write(root / "steamapps" / "workshop" / "m.lua", "print('workshop')\n")  # skipped like jre*
    return root


def game_for(install: Path, verdict: str = "offline-ok") -> dict:
    return {"gs_workspace": 1, "gst_workspace": 2, "slug": "jl", "name": "JL", "buildid": "7", "install_dir": str(install),
            "engine": {"key": "java"}, "verdict": verdict}


@pytest.fixture
def env(tmp_path):
    javalua.clear_cache()
    install = make_install(tmp_path / "install")
    ws = tmp_path / "ws"
    (ws / "logs").mkdir(parents=True)
    game = game_for(install)
    (ws / "game.json").write_text(json.dumps(game), encoding="utf-8")
    yield {"install": install, "ws": ws, "game": game}
    javalua.clear_cache()


# --- fakes -------------------------------------------------------------------------------------------

def fake_vineflower(cmd, **kw):
    """Writes one .java per class of the input jar under the --only prefixes, like Vineflower --folder."""
    source, out = Path(cmd[-2]), Path(cmd[-1])
    prefixes = tuple(a.split("=", 1)[1] for a in cmd if a.startswith("--only="))
    with zipfile.ZipFile(source) as z:
        for name in z.namelist():
            if name.endswith(".class") and (not prefixes or name.startswith(prefixes)):
                internal = name[:-len(".class")]
                package, _, simple = internal.rpartition("/")
                target = out / (internal + ".java")
                target.parent.mkdir(parents=True, exist_ok=True)
                head = f"package {package.replace('/', '.')};\n" if package else ""
                target.write_text(f"{head}public class {simple} {{\n    public void tick() {{ }}\n}}\n", encoding="utf-8")
    return subprocess.CompletedProcess(cmd, 0, "", "")


def fake_unluac(cmd, **kw):
    return subprocess.CompletedProcess(cmd, 0, "function assert(_ARG_0_)\n  return _ARG_0_\nend\n", "")


@pytest.fixture
def fakes(monkeypatch):
    calls = {"java": [], "lua": []}

    def run(cmd, **kw):
        kind = "java" if "vineflower" in " ".join(map(str, cmd)) else "lua"
        calls[kind].append(cmd)
        return fake_vineflower(cmd) if kind == "java" else fake_unluac(cmd)

    monkeypatch.setattr(java.tools, "bin_path", lambda tool, *a, **k: Path(f"{getattr(tool, 'id', tool)}.exe"))
    monkeypatch.setattr(subprocess, "run", run)  # java.subprocess and lua.subprocess are this module
    return calls


# --- discovery ---------------------------------------------------------------------------------------

def test_class_name_reads_the_constant_pool(tmp_path):
    assert java.class_name(write(tmp_path / "A.class", make_class("zombie/ai/states/IdleState"))) == "zombie/ai/states/IdleState"
    assert java.class_name(write(tmp_path / "B.class", b"not a class")) is None
    assert java.class_name(write(tmp_path / "C.class", make_class("x/Y")[:20])) is None  # truncated


def test_java_units_jars_and_class_trees(env):
    units = {u["id"]: u for u in java.units(env["install"], "java")}
    assert set(units) == {"game.jar", "libs/lib.jar", "zombie/", "bin/classes/org/", "plugins/(default)/"}
    game_jar = units["game.jar"]
    assert game_jar["game"] and game_jar["roots"] == ["com/acme/"] and game_jar["out"] == "game"
    assert not units["libs/lib.jar"]["game"]
    zombie = units["zombie/"]
    assert zombie["root"] == "." and zombie["out"] == "classes/zombie" and zombie["game"]
    assert sorted(zombie["relpaths"]) == ["zombie/Zed.class", "zombie/core/Core.class"]
    assert units["bin/classes/org/"]["root"] == "bin/classes" and not units["bin/classes/org/"]["game"]
    assert units["plugins/(default)/"]["out"] == "classes/plugins/_default"
    assert java.prefixes_for(game_jar, {}) == ["com/acme/"]
    assert java.prefixes_for(game_jar, {"java.filter": "com.acme.game.world"}) == ["com/acme/game/world"]
    assert java.prefixes_for(game_jar, {"java.all": "1", "java.filter": "x/"}) == []
    assert java.wanted(units["libs/lib.jar"], {"java.units": "lib"}) and java.wanted(units["libs/lib.jar"], {"java.all": "yes"})


def test_lua_units_per_script_folder(env):
    units = {u["id"]: u for u in lua.units(env["install"])}
    assert set(units) == {".", "media/lua/client", "media/lua/shared", "media/luaexamples"}
    assert units["media/lua/client"]["recursive"] and len(units["media/lua/client"]["plain"]) == 2
    assert not units["media/luaexamples"]["recursive"] and not units["."]["recursive"]
    assert units["."]["bytecode"] == ["stdlib.lbc"] and units["."]["plain"] == ["stdlib.lua"]


def test_extractors_apply_to_any_engine_and_feed_the_index(env, tmp_path):
    from gst.extractors.code import CodeIndex

    assert {"code.java", "code.lua"} <= set(registry.all_extractors()["*"])
    assert {"code.java", "code.lua"} <= set(CodeIndex.after)
    empty = tmp_path / "empty"
    empty.mkdir()
    for install, expected in ((env["install"], True), (empty, False)):
        ctx = Context(workspace=env["ws"], game={**env["game"], "engine": {"key": "unity-mono"}, "install_dir": str(install)},
                      install_dir=install, out_root=env["ws"], policy="offline-ok")
        assert javalua.JavaCode().applies(ctx) is expected and javalua.LuaCode().applies(ctx) is expected
    ctx = Context(workspace=env["ws"], game=env["game"], install_dir=env["install"], out_root=env["ws"], policy="offline-ok")
    index_inputs = next(CodeIndex().discover(ctx)).relpaths
    assert "game.jar" in index_inputs and "media/lua/shared/util.lua" in index_inputs


def test_fingerprints_follow_inputs_and_options(env):
    ctx = Context(workspace=env["ws"], game=env["game"], install_dir=env["install"], out_root=env["ws"], policy="offline-ok")
    base = {u.id: u.fingerprint for u in javalua.JavaCode().discover(ctx)}
    ctx.options = {"java.filter": "com/acme/game/world/"}
    filtered = {u.id: u.fingerprint for u in javalua.JavaCode().discover(ctx)}
    assert base["game.jar"] != filtered["game.jar"]
    ctx.options = {}
    os.utime(env["install"] / "zombie" / "Zed.class", ns=(1, 1))
    assert {u.id: u.fingerprint for u in javalua.JavaCode().discover(ctx)}["zombie/"] != base["zombie/"]


# --- gst decompile (in-process) ------------------------------------------------------------------------

def test_decompile_java_inprocess_reuses_and_filters(env, fakes):
    ws, game = env["ws"], env["game"]
    result = java.decompile(ws, game)
    assert set(result["jars"]) == {"game", "classes/zombie", "classes/plugins/_default"}
    assert result["jars"]["game"]["files"] == 2 and result["jars"]["game"]["only"] == ["com/acme/"]
    assert result["jars"]["game"]["skipped_roots"] == ["org"]
    assert {s["unit"] for s in result["skipped"]} == {"libs/lib.jar", "bin/classes/org/"}
    assert (ws / "decomp/java/game/com/acme/game/world/World.java").is_file()
    assert (ws / "decomp/java/classes/zombie/core/Core.java").is_file()  # package path kept for loose classes
    assert (ws / "decomp/java/game.log").is_file()
    manifest = json.loads((ws / "decomp/java/MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["backend"] == "java" and set(manifest["jars"]) == set(result["jars"])
    assert not (ws / ".partial").exists()
    ran = len(fakes["java"])
    assert all("--skip-extra-files" in cmd for cmd in fakes["java"])  # resources of the jar are not code
    again = java.decompile(ws, game)
    assert all(v["reused"] for v in again["jars"].values()) and len(fakes["java"]) == ran
    narrowed = java.decompile(ws, game, only=["com/acme/game/world/"])
    assert narrowed["jars"]["game"]["files"] == 1 and not narrowed["jars"]["game"]["reused"]
    assert any("no class under" in s["why"] for s in narrowed["skipped"])
    lib = java.decompile(ws, game, jars=["lib"])
    assert list(lib["jars"]) == ["lib"] and lib["jars"]["lib"]["only"] == []  # named: decompiled although a library


def test_decompile_java_policy_blocks(env, fakes):
    with pytest.raises(gst.common.GsError, match="rule 2") as err:
        java.decompile(env["ws"], {**env["game"], "verdict": "no-runtime"})
    assert err.value.code == 3 and not fakes["java"]


def test_decompile_lua_inprocess(env, fakes):
    ws, game = env["ws"], env["game"]
    result = lua.decompile(ws, game)
    assert result["copied"] == 6 and result["units"] == 4 and result["reused"] == 0
    stdlib = next(b for b in result["bytecode"] if b["file"] == "stdlib.lbc")
    assert stdlib["decompiled"] and stdlib["source_twin"] == "stdlib.lua" and stdlib["output"] == "stdlib.decompiled.lua"
    out = ws / "decomp" / "lua"
    assert (out / "stdlib.lua").read_text(encoding="utf-8").startswith("function assert(v)")  # the plain twin is kept
    assert "_ARG_0_" in (out / "stdlib.decompiled.lua").read_text(encoding="utf-8")
    assert (out / "media/lua/client/ISUI/ISInventoryPane.lua").is_file() and not (out / "steamapps").exists()
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["plain_files"] == 6 and set(manifest["units"]) == {".", "media/lua/client", "media/lua/shared",
                                                                         "media/luaexamples"}
    again = lua.decompile(ws, game)
    assert again["reused"] == 4 and len(fakes["lua"]) == 1
    (env["install"] / "media/luaexamples/b.lua").unlink()  # a folder that shares its parent: only its own files go
    javalua.clear_cache()
    third = lua.decompile(ws, game)
    assert third["reused"] == 3 and not (out / "media/luaexamples/b.lua").exists() and (out / "media/luaexamples/a.lua").exists()
    assert (out / "media/lua/client/ui.lua").exists()


def test_lua_bytecode_only_unit_is_blocked_or_unsupported(tmp_path, monkeypatch):
    javalua.clear_cache()
    install = tmp_path / "g"
    write(install / "scripts" / "main.luac", KAHLUA + b"\0" * 8)
    write(install / "fast" / "jit.luac", b"\x1bLJ\x02" + b"\0" * 8)
    calls = []

    def unluac_fails(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "java.lang.IllegalStateException: bad header\n\tat unluac.Main")

    monkeypatch.setattr(subprocess, "run", unluac_fails)
    monkeypatch.setattr(lua.tools, "bin_path", lambda tool, *a, **k: Path(f"{tool}.exe"))
    ws = tmp_path / "ws"
    rows = {r["unit"]: r for r in javalua.run_inprocess(ws, game_for(install, "no-runtime"), javalua.LuaCode, {})}
    assert {r["status"] for r in rows.values()} == {"blocked-policy"} and "rule 2" in rows["scripts"]["error"] and not calls
    rows = {r["unit"]: r for r in javalua.run_inprocess(ws, game_for(install), javalua.LuaCode, {})}  # verdict changed
    assert rows["fast"]["status"] == "unsupported" and "no decompiler" in rows["fast"]["error"]
    assert rows["scripts"]["status"] == "unsupported" and "\tat unluac.Main" in rows["scripts"]["error"]  # full stderr
    javalua.clear_cache()


def test_decompile_cli_backends_include_lua_next_to_any_engine(env):
    from gst.cmd_decompile import backends_for

    assert backends_for(env["game"]) == ["java", "lua"]
    assert backends_for({**env["game"], "engine": {"key": "native"}}) == ["native", "java", "lua"]


# --- gst study: worker processes, code.index after, resume ----------------------------------------------

FAKE_PROVIDER = '''
import subprocess, sys, zipfile
from pathlib import Path
from gst import tools
from gst.decompile import java, lua

_real_bin = tools.bin_path


def _bin(tool, *a, **k):
    tool_id = getattr(tool, "id", tool)
    return Path(f"{tool_id}.exe") if tool_id in ("temurin21", "vineflower", "unluac") else _real_bin(tool, *a, **k)


class _Sub:
    CompletedProcess = subprocess.CompletedProcess
    DEVNULL = subprocess.DEVNULL
    list2cmdline = staticmethod(subprocess.list2cmdline)

    @staticmethod
    def run(cmd, **kw):
        if "vineflower" not in " ".join(map(str, cmd)):
            return subprocess.CompletedProcess(cmd, 0, "function assert(_ARG_0_) end\\n", "")
        source, out = Path(cmd[-2]), Path(cmd[-1])
        prefixes = tuple(a.split("=", 1)[1] for a in cmd if a.startswith("--only="))
        with zipfile.ZipFile(source) as z:
            for name in z.namelist():
                if name.endswith(".class") and (not prefixes or name.startswith(prefixes)):
                    internal = name[:-6]
                    package, _, simple = internal.rpartition("/")
                    target = out / (internal + ".java")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text((f"package {package.replace('/', '.')};\\n" if package else "")
                                      + f"public class {simple} {{\\n    public void tick() {{ }}\\n}}\\n", encoding="utf-8")
        marks = Path(__file__).parent / "runs"
        marks.mkdir(exist_ok=True)
        (marks / source.name).write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")


if Path(sys.argv[0]).name == "worker.py":  # only inside the study's worker processes, never the test process
    tools.bin_path = _bin
    java.subprocess = _Sub
    lua.subprocess = _Sub
'''


def test_study_runs_java_lua_then_index_and_resumes(env, tmp_path, monkeypatch):
    pytest.importorskip("tree_sitter_java")
    from gst import index
    from gst.index import query
    from gst.pipeline import plan
    from gst.pipeline.jobs import JobsDB
    from gst.pipeline.runner import Runner
    from gst.pipeline.worker import write_options

    mods = tmp_path / "mods"
    write(mods / "gst_fake_javalua.py", textwrap.dedent(FAKE_PROVIDER))
    src = str(Path(gst.__file__).resolve().parents[1])
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(mods), src]))
    monkeypatch.setenv(registry.ENV_PROVIDERS, "gst_fake_javalua")
    monkeypatch.syspath_prepend(str(mods))
    ws, game = env["ws"], env["game"]
    options = {"only": ["code.java", "code.lua", "code.index"]}

    def study(opts):
        javalua.clear_cache()
        write_options(ws, opts)
        built = plan.build(ws, game, options=opts)
        return built, Runner(ws, jobs=2, python=sys.executable).run()

    built, summary = study(options)
    names = [e["name"] for e in built["extractors"] if e.get("applies")]
    assert names == ["code.java", "code.lua", "code.index"]
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        rows = {f"{r['extractor']}:{r['unit_id']}": r for r in db.rows()}
    status = {k: r["status"] for k, r in rows.items()}
    assert status["code.java:game.jar"] == "done" and status["code.java:zombie/"] == "done"
    assert status["code.java:libs/lib.jar"] == "skipped" and status["code.java:<finalize>"] == "done"
    assert status["code.lua:media/lua/client"] == "done" and status["code.lua:."] == "done"
    assert status["code.index:code.db"] == "done", rows["code.index:code.db"]["error"]
    assert sorted(p.name for p in (mods / "runs").iterdir()) == ["classes.jar", "game.jar"]
    con = index.connect(ws)
    try:
        assert query.defs(con, "helperSort")[0]["path"] == "decomp/lua/media/lua/shared/util.lua"
        assert {c["caller"] for c in query.callers(con, "helperSort")} == {"ISInventoryPane.refresh"}
        assert query.defs(con, "World", kind="class")[0]["path"] == "decomp/java/game/com/acme/game/world/World.java"
        assert query.defs(con, "Core", kind="class")[0]["path"] == "decomp/java/classes/zombie/core/Core.java"
    finally:
        con.close()
    _built, again = study(options)
    assert again["started"] == 0  # resume: nothing redone
    built, third = study({**options, "java.filter": "com/acme/game/world/"})
    java_entry = next(e for e in built["extractors"] if e["name"] == "code.java")
    assert java_entry["to_run"] >= 2 and third["started"] >= 3  # the units whose prefixes changed, finalize, index


def test_recon_lists_java_and_lua_next_to_any_engine(env):
    from gst.recon import scan
    from gst.recon.index import FileIndex

    found = scan.script_code(FileIndex(env["install"]))
    assert found["java"]["jars"] == 2 and found["java"]["class_files"] == 4  # the bundled JRE's jar is not counted
    assert found["lua"] == {"scripts": 6, "compiled": 1}  # steamapps/ (workshop content) is not the game
    steps = scan.recommended_steps("native", "offline-ok", {}, available={"decompile", "assets", "index", "map"},
                                   scripts=found)
    assert "gst decompile java" in steps and "gst decompile lua" in steps
    java_steps = scan.recommended_steps("java", "offline-ok", {}, available={"decompile"}, scripts=found)
    assert java_steps.count("gst decompile java") == 1 and "gst decompile lua" in java_steps
    assert "Lua: 6 .lua, 1 compiled chunks (code.lua)" in scan._scripts_line(found)
