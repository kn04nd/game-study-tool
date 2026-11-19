"""Native backend without Ghidra: module discovery, the headless command line, the .c extractor and the
queries over native symbols (synthetic files only)."""

import json
from pathlib import Path

import pytest

from gst import index, policy
from gst.common import GsError
from gst.decompile import native
from gst.index import ghidra, query

C_FILE = """// Game.exe - Sim (Ghidra 12.1.4, gst export)

// Game.exe+0x1000 Sim::Step

void __thiscall Sim::Step(Sim *this,float dt)

{
  Sim::UpdateCell(this,0);
  CreateFileW(L"save.dat",0,0,0,0,0,0);
  return;
}

// Game.exe+0x1100 Sim::UpdateCell

void __thiscall Sim::UpdateCell(Sim *this,int cell)

{
  printf("cell %d\\n",cell);
  return;
}
"""

C_MAP = {"module": "Game.exe", "namespace": "Sim", "kind": "class", "functions": [
    {"q": "Sim.Step", "name": "Step", "kind": "method", "rva": "0x1000", "sig": "void __thiscall Step(Sim * this, float dt)",
     "line": 3, "end": 11, "calls": [[8, "Sim.UpdateCell"], [9, "kernel32.CreateFileW"]]},
    {"q": "Sim.UpdateCell", "name": "UpdateCell", "kind": "method", "rva": "0x1100", "sig": "void __thiscall UpdateCell(Sim * this, int cell)",
     "line": 13, "end": 20, "calls": [[18, "printf"]]},
]}


def write_export(ws: Path) -> Path:
    src = ws / "decomp" / "ghidra" / "Game.exe" / "src"
    src.mkdir(parents=True)
    (src / "Sim.c").write_text(C_FILE, encoding="utf-8")
    (src / "Sim.c.map.json").write_text(json.dumps(C_MAP), encoding="utf-8")
    return src / "Sim.c"


def test_extractor_reads_the_sidecar(tmp_path):
    path = write_export(tmp_path)
    data = ghidra.extract("decomp/ghidra/Game.exe/src/Sim.c", path.read_bytes(), str(path))
    kinds = {s[4]: (s[2], s[6], s[7], s[8]) for s in data["symbols"]}
    assert kinds["Sim"][0] == "class" and kinds["Sim.Step"] == ("method", 3, 11, "rva:0x1000")
    assert ("call", "kernel32.CreateFileW", "CreateFileW", 9, 1) in data["refs"]
    assert ("save.dat", 9, 1) in data["strings"] and any(s[0].startswith("cell %d") and s[2] == 2 for s in data["strings"])
    bare = ghidra.extract("decomp/x.c", b"int main(){}\n", str(tmp_path / "x.c"))
    assert bare["lines"] == 1 and not bare["symbols"]


def test_native_queries_over_the_index(tmp_path):
    write_export(tmp_path)
    summary = index.build(tmp_path)
    assert summary["failed"] == 0 and summary["symbols"] == 3
    con = index.connect(tmp_path)
    try:
        assert query.defs(con, "Sim::Step")[0]["qname"] == "Sim.Step"  # C++ spelling accepted
        callers = query.callers(con, "Sim.UpdateCell")
        assert [c["caller"] for c in callers] == ["Sim.Step"] and not callers[0]["by_name"]
        assert {c["target"] for c in query.callees(con, "Sim.Step")} == {"Sim.UpdateCell", "kernel32.CreateFileW"}
        assert query.callers(con, "CreateFileW")[0]["caller"] == "Sim.Step"  # an import: by name
        assert [r["qname"] for r in query.outline(con, "decomp/ghidra/Game.exe/src/Sim.c")] == ["Sim", "Sim.Step", "Sim.UpdateCell"]
        assert query.outline(con, "Sim")[0]["kind"] == "class"
        assert query.strings(con, "save.dat")[0]["owner"] == "Sim.Step"
    finally:
        con.close()


def _pe(path: Path, size: int = 40 << 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ" + b"\0" * (size - 2))


def test_find_modules_marks_the_game_ones(tmp_path):
    install = tmp_path / "Game"
    _pe(install / "Game.exe")
    _pe(install / "bin" / "GameLogic.dll")
    (install / "bin" / "GameLogic.pdb").write_bytes(b"pdb")
    _pe(install / "bin" / "fmodstudio.dll")
    _pe(install / "tiny.dll", 1024)
    found = {m["name"]: m for m in native.find_modules(install, {"exes": ["game.exe"], "engine": {"key": "native"}})}
    assert set(found) == {"Game.exe", "GameLogic.dll", "fmodstudio.dll"}
    assert found["Game.exe"]["game"] and found["GameLogic.dll"]["game"] and found["GameLogic.dll"]["pdb"]
    assert found["fmodstudio.dll"]["third_party"] and not found["fmodstudio.dll"]["game"]
    unity = {m["name"]: m for m in native.find_modules(install, {"exes": ["game.exe"], "engine": {"key": "unity-mono"}})}
    assert not unity["Game.exe"]["game"]  # the Unity exe is a launcher stub
    assert [m["name"] for m in native.select(list(found.values()), ["gamelogic"])] == ["GameLogic.dll"]
    with pytest.raises(GsError, match="not a native module"):
        native.select(list(found.values()), ["nope.dll"])


def test_headless_command_line(tmp_path):
    project = tmp_path / "decomp/ghidra/_projects/Game.exe/Game.exe"
    out, log = tmp_path / "decomp/ghidra/Game.exe", tmp_path / "logs/ghidra-Game.exe.log"
    fresh = native.command(Path("analyzeHeadless.bat"), project, tmp_path / "_input/Game.exe", out, pdb=None,
                           max_functions=100, seconds=30, only_file=None, analysis_timeout=600, reuse=False, log=log)
    assert fresh[1:3] == [str(project.parent), "Game.exe"] and "-import" in fresh and "-overwrite" in fresh
    assert fresh[fresh.index("GsPdbPrescript.java") + 1] == "-"  # no PDB: the prescript turns PDB analysis off
    post = fresh.index("GsExport.java")
    assert fresh[post + 1:post + 5] == [str(out), "100", "30", "-"]
    again = native.command(Path("analyzeHeadless.bat"), project, tmp_path / "_input/Game.exe", out, pdb=None,
                           max_functions=100, seconds=30, only_file=tmp_path / "only.txt", analysis_timeout=600, reuse=True, log=log)
    assert again[3:7] == ["-process", "Game.exe", "-noanalysis", "-readOnly"] and "-import" not in again
    assert again[again.index("GsExport.java") + 4] == f"@{tmp_path / 'only.txt'}"
    assert native.safe_name("Ratchet & Clank!.exe") == "Ratchet___Clank_.exe"


def test_native_decompile_respects_the_policy(tmp_path, monkeypatch):
    install = tmp_path / "Game"
    _pe(install / "Game.exe")
    monkeypatch.setattr(native.tools, "bin_path", lambda tool: Path(f"{tool}.exe"))
    game = {"name": "G", "install_dir": str(install), "exes": ["game.exe"], "engine": {"key": "native"},
            "verdict": policy.NO_RUNTIME}
    with pytest.raises(GsError, match="client code is not decompiled"):
        native.decompile(tmp_path / "ws", game)


def test_class_bases_and_exact_template_callees(tmp_path):
    src = tmp_path / "decomp" / "ghidra" / "Game.exe" / "src" / "sg"
    src.mkdir(parents=True)
    vec = "std.vector<int,std::allocator<int>_>"
    files = {
        "Entity": ({"namespace": "sg.Entity", "kind": "class", "bases": [], "functions": [
            {"q": "sg.Entity.Hit", "name": "Hit", "kind": "method", "rva": "0x10", "line": 3, "end": 6,
             "calls": [[5, f"{vec}.push_back"]]}]}, "// Game.exe - sg::Entity\n\n// Game.exe+0x10 sg::Entity::Hit\nvoid Hit(void)\n{\n}\n"),
        "Player": ({"namespace": "sg.Player", "kind": "class", "bases": ["sg.Entity"], "functions": []},
                   "// Game.exe - sg::Player\n"),
        "vector": ({"namespace": vec, "kind": "class", "functions": [
            {"q": f"{vec}.push_back", "name": "push_back", "kind": "method", "rva": "0x20", "line": 2, "end": 2, "calls": []}]},
                   "// Game.exe - std::vector\n// Game.exe+0x20 push_back\n"),
    }
    for stem, (sidecar, text) in files.items():
        (src / f"{stem}.c").write_text(text, encoding="utf-8")
        (src / f"{stem}.c.map.json").write_text(json.dumps(sidecar), encoding="utf-8")
    index.build(tmp_path)
    con = index.connect(tmp_path)
    try:
        assert [d["qname"] for d in query.hierarchy(con, "sg::Entity")["derived"]] == ["sg.Player"]
        assert query.hierarchy(con, "sg.Player")["bases"] == ["sg.Entity"]
        callee = query.callees(con, "sg.Entity.Hit")[0]
        assert callee["resolved_by"] == "exact" and callee["defined_as"] == [f"{vec}.push_back"]
    finally:
        con.close()


@pytest.mark.parametrize("cmd, allowed", [
    ("pyghidra-mcp --project-path D:/game-study/x/p.gpr --no-symbols", True),
    ("pyghidra-mcp --no-symbols -t streamable-http --host 127.0.0.1 -p 8000", True),
    ("pyghidra-mcp --help", True),
    ("pyghidra-mcp --list-project-binaries --project-path p.gpr", True),
    ("pyghidra-mcp --project-path p.gpr", False),
    ("pyghidra-mcp.exe --with-symbols game.exe", False),
    ("pyghidra-mcp --no-symbols -t streamable-http --host 0.0.0.0", False),
    ("pyghidra-mcp --no-symbols -t http -o=192.168.0.10", False),
])
def test_pyghidra_mcp_guard(cmd, allowed):
    from gst import guard

    decision = guard.check_command(cmd)
    assert decision.allowed is allowed, cmd
    if not allowed:
        assert decision.rule == 9
