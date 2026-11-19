"""IL2CPP name map (names.jsonl), its adapters, the Ghidra command line and the code.il2cpp.native extractor.
Synthetic inputs only; the real Ghidra run is behind the `tools` marker."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gst.common import GsError
from gst.engines.il2cpp import ghidra_names, names

CS = """namespace Game.Audio;

[Token(Token = "0x200000D")]
public class SoundEvent : SoundEventBase
{
\t[Address(RVA = "0x1000", Offset = "0x400", Length = "0x7")]
\t[Token(Token = "0x60000E3")]
\tpublic SoundEvent() { }

\t[Address(RVA = "0x1010", Offset = "0x410", Length = "0x20")]
\tstatic SoundEvent() { }

\t[Address(RVA = "0x1040", Offset = "0x440", Length = "0x1D")]
\t[Token(Token = "0x60000E0")]
\tpublic bool Play(Transform owner, Dictionary<string, int> args) { }

\t[Token(Token = "0x17000001")]
\tpublic float Volume
\t{
\t\t[Address(RVA = "0x1080", Offset = "0x480", Length = "0x8")]
\t\t[CompilerGenerated]
\t\tget { } //Length: 8
\t\t[Address(RVA = "0x1090", Offset = "0x490", Length = "0x8")]
\t\tprivate set { } //Length: 8
\t}

\t[Token(Token = "0x17000002")]
\tprivate override object System.Collections.IEnumerator.Current
\t{
\t\t[Address(RVA = "0x10A0", Offset = "0x4A0", Length = "0x5")]
\t\tprivate get { } //Length: 5
\t}

\t[Address(RVA = "0x10B0", Offset = "0x4B0", Length = "0x5")]
\tpublic T Get<T>(int index) { }

\t[Address(RVA = "0x10C0", Offset = "0x4C0", Length = "0x5")]
\tpublic static SoundEvent op_Implicit(int x) { }

\t[Address(RVA = "0x10D0", Offset = "0x4D0", Length = "0x5")]
\tpublic static bool operator ==(SoundEvent a, SoundEvent b) { }

\tprivate sealed class <Run>d__5 : IEnumerator<object>
\t{
\t\t[Address(RVA = "0x1100", Offset = "0x500", Length = "0x24")]
\t\tpublic <Run>d__5(int <>1__state) { }

\t\t[Address(RVA = "0x1130", Offset = "0x530", Length = "0x30")]
\t\tprivate override bool MoveNext() { }
\t}

\t[Address(RVA = "0x1200", Offset = "0x600", Length = "0x9")]
\tprotected virtual void OnDestroy() { }

\t[Token(Token = "0x4000001")]
\tprivate int count;
}

public struct Pair<TKey, TValue>
{
\t[Address(RVA = "0x1300", Offset = "0x700", Length = "0x9")]
\tpublic Pair(TKey key, TValue value) { }
}

public class Box
{
\t[Address(RVA = "0x1400", Offset = "0x800", Length = "0x7")]
\tpublic Box`1() { }
}
"""


def by_name(rows):
    return {r[3]: r for r in rows if r[2] == "Game.Audio.SoundEvent"}


def test_cpp2il_diffable_cs_methods_accessors_nested_and_ctors():
    rows = list(names.parse_cpp2il_cs(CS))
    got = by_name(rows)
    assert got[".ctor"][0] == 0x1000 and got[".ctor"][2] == "Game.Audio.SoundEvent" and got[".ctor"][1] == 7
    assert got[".cctor"][0] == 0x1010
    assert got["Play"][4] == "public bool Play(Transform owner, Dictionary<string, int> args)"
    assert got["get_Volume"][0] == 0x1080 and got["set_Volume"][0] == 0x1090
    assert got["System.Collections.IEnumerator.get_Current"][0] == 0x10A0
    assert got["Get"][0] == 0x10B0 and got["op_Implicit"][0] == 0x10C0 and got["op_Equality"][0] == 0x10D0
    nested = [r for r in rows if r[2] == "Game.Audio.SoundEvent.<Run>d__5"]
    assert sorted(r[3] for r in nested) == [".ctor", "MoveNext"]
    assert got["OnDestroy"][2] == "Game.Audio.SoundEvent" and got["OnDestroy"][0] == 0x1200  # back in the outer type
    pair = [r for r in rows if r[0] == 0x1300][0]
    assert pair[3] == ".ctor" and pair[2] == "Game.Audio.Pair<TKey, TValue>"
    assert [r[3] for r in rows if r[2] == "Game.Audio.Box"] == [".ctor"]
    assert len(rows) == 14


def test_ghidra_names_and_type_paths():
    assert names.ghidra_name("  a b\tc\x00 ") == "a_b_c"
    assert names.ghidra_name("") == "_"
    long = "x" * 5000
    assert len(names.ghidra_name(long)) == names.GHIDRA_NAME_MAX and names.ghidra_name(long) != names.ghidra_name(long + "y")
    assert names.split_type("Ns.Sub.Outer.Inner<A.B, C.D>") == ["Ns", "Sub", "Outer", "Inner<A.B, C.D>"]
    assert names.split_type("Outer/Inner") == ["Outer", "Inner"]
    assert names.namespace_path("System.Collections.Generic.List<UnityEngine.Vector3>") == \
        ["System", "Collections", "Generic", "List<UnityEngine.Vector3>"]
    assert names.split_method("A.B$$C") == ("A.B", "C") and names.split_method("free") == (None, "free")


def script_json() -> dict:
    return {
        "ScriptMethod": [
            {"Address": 0x2000, "Name": "Game.Player$$Update", "Signature": "void Game_Player__Update (Game_Player_o* __this, const MethodInfo* method);"},
            {"Address": 0x2000, "Name": "Game.Enemy$$Update", "Signature": "void Game_Enemy__Update (...);"},
            {"Address": 0x2100, "Name": "Game.Player$$.ctor", "Signature": None},
        ],
        "ScriptString": [{"Address": 0x9000, "Value": "hello\nworld"}],
        "ScriptMetadata": [{"Address": 0x9100, "Name": "Game.Player_TypeInfo", "Signature": "Game_Player_c*"},
                           {"Address": 0x9108, "Name": "Field$Game.Player.hp", "Signature": None},
                           {"Address": 0x9110, "Name": "System.String_var", "Signature": "Il2CppType*"}],
        "ScriptMetadataMethod": [{"Address": 0x9200, "Name": "Method$Game.Player.Update()", "MethodAddress": 0x2000}],
        "Addresses": [0x2000, 0x2100, 0x2200],
    }


def test_script_json_adapter(tmp_path):
    p = tmp_path / "script.json"
    p.write_text(json.dumps(script_json()), encoding="utf-8")
    rows = list(names.from_script_json(p))
    kinds = {(r["rva"], r["kind"]): r for r in rows}
    assert kinds[(0x2100, "method")]["type"] == "Game.Player"
    assert kinds[(0x9000, "string_literal")]["value"] == "hello\nworld" and kinds[(0x9000, "string_literal")]["name"] == "StringLiteral_1"
    assert kinds[(0x9100, "type_info")]["type"] == "Game.Player"
    assert kinds[(0x9108, "field")]["kind"] == "field" and kinds[(0x9110, "metadata")]["signature"] == "Il2CppType*"
    assert kinds[(0x9200, "method_info")]["method_rva"] == 0x2000
    assert {r["rva"] for r in rows if r["kind"] == "function"} == {0x2000, 0x2100, 0x2200}


def test_inspector_adapter_turns_vas_into_rvas(tmp_path):
    base = 0x180000000
    p = tmp_path / "metadata.json"
    p.write_text(json.dumps({"addressMap": {
        "methodDefinitions": [
            {"virtualAddress": "0x%016X" % (base + 0x2000), "name": "_ZN4Game6Player6UpdateEf",
             "signature": "void Player_Update(Player * this, float dt, MethodInfo * method)",
             "dotNetSignature": "Void Update(Single)", "group": "Assembly-CSharp.dll/Game/Player"},
            {"virtualAddress": "0x%016X" % (base + 0x2100), "name": "_ZN4Game6PlayerC1Ev",
             "signature": "void Player__ctor(Player * this, MethodInfo * method)", "dotNetSignature": "Player()",
             "group": "Assembly-CSharp.dll/Game/Player"},
            {"virtualAddress": "0x%016X" % (base + 0x2180), "name": "_ZN4Game6Player6_cctorEv",
             "signature": "void Player__cctor(MethodInfo * method)", "dotNetSignature": "Player()",
             "group": "Assembly-CSharp.dll/Game/Player"},
            {"virtualAddress": "0x%016X" % (base + 0x2200), "name": "_ZN4Game6Player9_Run_d__5x",
             "signature": "Object * f(MethodInfo * method)",
             "dotNetSignature": "Object System.Collections.IEnumerator.get_Current()",
             "group": "Assembly-CSharp.dll/Game/Player+<Run>d__5"},
            {"virtualAddress": "0x%016X" % (base + 0x2300), "name": "_ZN4Game6Player3GetE",
             "signature": "T f()", "dotNetSignature": "Dictionary`2[String,Int32] Get[T](List`1[T])",
             "group": "Assembly-CSharp.dll/Game/Player"}],
        "stringLiterals": [{"virtualAddress": "0x%016X" % (base + 0x9000), "name": "StringLiteral_hi", "string": "hi"}],
        "typeInfoPointers": [{"virtualAddress": "0x%016X" % (base + 0x9100), "name": "_ZN8TypeInfo4Game6PlayerE",
                              "type": "struct Player__Class *", "dotNetType": "Player"}],
        "methodInfoPointers": [{"virtualAddress": "0x%016X" % (base + 0x9200), "name": "_ZN10MethodInfo4Game6Player6UpdateEf",
                                "dotNetSignature": "Void Update(Single)", "methodAddress": "0x%016X" % (base + 0x2000)}],
        "functionAddresses": ["0x00000000", "0x%016X" % (base + 0x2400)],
    }}), encoding="utf-8")
    rows = {(r["rva"], r["kind"]): r for r in names.from_inspector_json(p, base)}
    assert rows[(0x2000, "method")]["name"] == "Game.Player$$Update" and rows[(0x2000, "method")]["signature"] == "Void Update(Single)"
    assert rows[(0x2100, "method")]["name"] == "Game.Player$$.ctor" and rows[(0x2180, "method")]["name"] == "Game.Player$$.cctor"
    assert rows[(0x2200, "method")]["name"] == "Game.Player.<Run>d__5$$System.Collections.IEnumerator.get_Current"
    assert rows[(0x2300, "method")]["name"] == "Game.Player$$Get"
    assert rows[(0x9000, "string_literal")]["value"] == "hi"
    assert rows[(0x9100, "type_info")]["name"] == "Game.Player_TypeInfo"
    assert rows[(0x9200, "method_info")]["name"] == "Method$Game.Player.Update" and rows[(0x9200, "method_info")]["method_rva"] == 0x2000
    assert (0x2400, "function") in rows and not any(k[0] < 0 for k in rows)


def test_addresses_tsv_adapter(tmp_path):
    p = tmp_path / "addresses.tsv"
    head = ["rva", "va", "offset", "length", "token", "field_offset", "kind", "qname", "decl", "path", "line"]
    rows_ = [
        ["", "", "", "", "0x2000004", "", "class", "Game.Player", "public class Player", "p.cs", "3"],
        ["0x2000", "0x180002000", "0x1400", "0x3E", "0x6000001", "", "method", "Game.Player.Update", "public void Update(float dt)", "p.cs", "9"],
        ["0x2100", "0x180002100", "0x1500", "0x10", "0x6000002", "", "ctor", "Game.Player.Player", "public Player()", "p.cs", "12"],
        ["0x2190", "", "", "", "", "", "operator", "Game.Player.operator_declaration", "public static Player operator +(Player a, Player b)", "p.cs", "14"],
        ["0x2180", "0x180002180", "0x1580", "0x10", "0x6000003", "", "ctor", "Game.Player..cctor", "static Player()", "p.cs", "13"],
        ["0x2200", "0x180002200", "0x1600", "0x3E", "0x6000004", "", "method", "Game._003CRun_003Ed__5.Reset", "void IEnumerator.Reset()", "p.cs", "20"],
        ["0x2300", "0x180002300", "0x1700", "0x8", "0x6000005", "", "property", "Game.Player.Hp", "public int Hp { get; }", "p.cs", "30"],
    ]
    p.write_text("".join(chr(9).join(r) + chr(10) for r in [head] + rows_), encoding="utf-8")
    got = {r["rva"]: r for r in names.from_addresses_tsv(p)}
    assert got[0x2000]["name"] == "Game.Player$$Update" and got[0x2000]["size"] == 0x3E and got[0x2000]["source"] == "cpp2il"
    assert got[0x2100]["name"] == "Game.Player$$.ctor" and got[0x2180]["name"] == "Game.Player$$.cctor"
    assert got[0x2190]["name"] == "Game.Player$$op_Addition"
    assert got[0x2200]["type"] == "Game.<Run>d__5" and 0x2300 not in got and len(got) == 5


def rea_report(binary_sha: str = "ab" * 32) -> dict:
    return {
        "types": [{"name": "<Module>", "namespace": "", "declaring_type": -1},
                  {"name": "Player", "namespace": "Game", "declaring_type": -1},
                  {"name": "Inner", "namespace": "", "declaring_type": 1}],
        "methods": [{"name": "Update", "declaring_type": 1}, {"name": "Tick", "declaring_type": 2},
                    {"name": "Abstract", "declaring_type": 1}],
        "parameters": [{"name": "dt", "method": 1}],
        "return_types": [{"type": 1, "name": "void"}, {"type": 2, "name": "bool"}, {"type": 1, "name": "void"}],
        "parameter_types": [{"type": 3, "name": "float"}],
        "method_addresses": [{"state": "code", "address": "0x180002000", "rva": "0x2000"},
                             {"state": "code", "address": "0x180002300", "rva": "0x2300"},
                             {"state": "no-body", "address": None, "rva": None}],
        "generic_method_instances": [{"state": "code", "address": "0x180002400", "rva": "0x2400", "method_spec": 0,
                                      "method": 0, "name": "Game.Box<int>$$Get<int>"}],
        "binary": {"artifact": {"path": "D:/x/GameAssembly.dll", "sha256": binary_sha, "bytes": 1}, "image_base": "0x180000000"},
    }


def test_rea_evidence_adapter_and_seeds(tmp_path):
    evidence = {"artifacts": [], "records": [
        {"operation": "inspect_analysis_view", "normalized_result": {"kind": "summary"}},
        {"operation": "inspect_il2cpp_metadata", "normalized_result": rea_report()}]}
    p = tmp_path / "global-metadata.json"
    p.write_text(json.dumps(evidence), encoding="utf-8")
    rows = {r["rva"]: r for r in names.from_rea_evidence(p)}
    assert rows[0x2000]["name"] == "Game.Player$$Update" and rows[0x2000]["signature"] == "void Update()"
    assert rows[0x2300]["name"] == "Game.Player.Inner$$Tick" and rows[0x2300]["signature"] == "bool Tick(float dt)"
    assert rows[0x2400]["type"] == "Game.Box<int>" and len(rows) == 3
    assert names.rea_binary_sha256(p) == "ab" * 32
    metadata_only = dict(rea_report(), method_addresses=[], generic_method_instances=None)
    p.write_text(json.dumps({"records": [{"operation": "inspect_il2cpp_metadata", "normalized_result": metadata_only}]}),
                 encoding="utf-8")
    assert list(names.from_rea_evidence(p)) == []  # names without RVAs: nothing for Ghidra
    seeds = tmp_path / "ghidra-seeds.txt"
    seeds.write_text("0x180002000\tfunction\tGame.Player$$Update\n# folded 0x180002000 Game.Player$$Update also Other$$X\n"
                     "# link_base 0x180000000\n# seed_base 0x180000000\n", encoding="utf-8")
    assert [(r["rva"], r["name"]) for r in names.from_rea_seeds(seeds)] == [(0x2000, "Game.Player$$Update")]


def test_merge_precedence_folding_and_conflicts():
    rows = [
        names.row(0x2000, "Game.Player$$Update", "method", "rea", type_name="Game.Player"),
        names.row(0x2000, "Game.Player$$Update", "method", "il2cppdumper", signature="void f()"),
        names.row(0x2000, "Game.Enemy$$Update", "method", "il2cppdumper"),
        names.row(0x2100, "Game.Player$$Tick", "method", "cpp2il", signature="public void Tick()"),
        names.row(0x2100, "Game.Player$$Tock", "method", "rea"),
        names.row(0x2200, "", "function", "il2cppdumper"),
        names.row(0x9000, "StringLiteral_1", "string_literal", "il2cppdumper", value="hi"),
        names.row(0x9000, "ignored", "bogus-kind", "il2cppdumper"),
    ]
    entries, stats = names.merge(rows)
    by = {(e["rva"], e["kind"]): e for e in entries}
    e = by[(0x2000, "method")]
    assert e["source"] == "il2cppdumper" and e["name"] == "Game.Player$$Update" and e["also"] == ["Game.Enemy$$Update"]
    assert e["ns"] == ["Game", "Player"] and e["fn"] == "Update" and e["signature"] == "void f()"
    assert by[(0x2100, "method")]["source"] == "cpp2il" and by[(0x2100, "method")]["also"] == ["Game.Player$$Tock"]
    assert stats["conflicts"] == 1 and stats["conflict_pairs"] == {"cpp2il/rea": 1}
    assert by[(0x2200, "function")]["name"] == "" and by[(0x9000, "string_literal")]["value"] == "hi"
    assert stats["entries"] == 4 and stats["folded"] == 2 and stats["by_kind"]["method"] == 2
    assert [k for k in by] == sorted(by)


def make_ws(tmp_path: Path) -> tuple[Path, Path]:
    install = tmp_path / "install"
    (install / "Game_Data" / "il2cpp_data" / "Metadata").mkdir(parents=True)
    (install / "GameAssembly.dll").write_bytes(b"MZ not really a PE")
    (install / "Game_Data" / "il2cpp_data" / "Metadata" / "global-metadata.dat").write_bytes(b"\xaf\x1b\xb1\xfa")
    ws = tmp_path / "ws"
    (ws / "decomp" / "il2cpp" / "il2cppdumper").mkdir(parents=True)
    return ws, install


def test_build_writes_header_sorted_lines_and_manifest(tmp_path):
    ws, install = make_ws(tmp_path)
    (ws / names.SCRIPT_JSON).write_text(json.dumps(script_json()), encoding="utf-8")
    cs = ws / "decomp" / "il2cpp" / "cpp2il" / "diffable-cs" / "Game"
    cs.mkdir(parents=True)
    (cs / "SoundEvent.cs").write_text(CS, encoding="utf-8")
    rea = ws / names.REA_EVIDENCE
    rea.mkdir(parents=True)
    (rea / "stale.run.json").write_text(json.dumps({"argv": ["rea"], "exit_code": 0}), encoding="utf-8")  # REA runner sidecar
    (rea / "stale.json").write_text(json.dumps({"records": [{"operation": "inspect_il2cpp_metadata",
                                                             "normalized_result": rea_report("cd" * 32)}]}), encoding="utf-8")
    summary = names.build(ws, install)
    path = ws / summary["path"]
    header = names.read_header(path)
    assert header["format"] == names.FORMAT and header["game_assembly"]["file"] == "GameAssembly.dll"
    assert header["game_assembly"]["image_base"] is None  # not a PE: inspector VAs could not be converted
    assert set(header["sources"]) == {"il2cppdumper", "cpp2il"}
    assert any("another GameAssembly build" in n for n in header["notes"])  # REA linked to a different binary
    assert not any(".run.json" in n for n in header["notes"])  # the runner's sidecars are not sources
    rows = list(names.iter_names(path))
    assert [(r["rva"], r["kind"]) for r in rows] == sorted((r["rva"], r["kind"]) for r in rows)
    assert all(set(r) >= {"rva", "name", "signature", "kind", "type", "source"} for r in rows)
    assert names.manifest(ws)["entries_sha256"] == summary["entries_sha256"]
    assert path.read_bytes().count(b"\r\n") == 0


def test_build_without_sources_writes_nothing(tmp_path):
    ws, install = make_ws(tmp_path)
    summary = names.build(ws, install)
    assert summary["path"] is None and not (ws / names.NAMES).exists()


# --- Ghidra command line -------------------------------------------------------------------------

def test_command_fresh_reuse_and_bat_safety(tmp_path):
    headless = tmp_path / "ghidra" / "support" / "analyzeHeadless.bat"
    project = tmp_path / "ws" / "decomp" / "ghidra" / "_projects" / "GameAssembly.dll" / "GameAssembly.dll"
    module = tmp_path / "ws" / "decomp" / "ghidra" / "_input" / "GameAssembly.dll"
    out = tmp_path / "ws" / "decomp" / "ghidra" / "GameAssembly.dll"
    names_file = tmp_path / "ws" / "decomp" / "il2cpp" / "names.jsonl"
    log = tmp_path / "ws" / "logs" / "ghidra-GameAssembly.dll.log"
    fresh = ghidra_names.command(headless, project, module, out, names=names_file, log=log, reuse=False, apply_names=True)
    i = fresh.index("GstIl2cppNames.java")
    assert fresh[i + 1:i + 5] == [str(names_file), "pre", str(out / ghidra_names.PRE_REPORT), "ns"]
    assert fresh.index("-preScript") < fresh.index("GstIl2cppNames.java") < fresh.index("-scriptPath")
    post = len(fresh) - 1 - fresh[::-1].index("GstIl2cppNames.java")
    assert fresh[post + 2] == "post" and post < fresh.index("GsExport.java")
    assert fresh[fresh.index("GsExport.java") + 2] == "0"  # every function
    assert "-import" in fresh and "-noanalysis" not in fresh
    rename = ghidra_names.command(headless, project, module, out, names=names_file, log=log, reuse=True, apply_names=True,
                                  flat=True)
    assert "-noanalysis" in rename and "-readOnly" not in rename and "pre" not in rename and rename[rename.index("post") + 2] == "flat"
    export = ghidra_names.command(headless, project, module, out, names=names_file, log=log, reuse=True, apply_names=False)
    assert "-readOnly" in export and "GstIl2cppNames.java" not in export
    unnamed = ghidra_names.command(headless, project, module, out, names=None, log=log, reuse=False, apply_names=False)
    assert "GstIl2cppNames.java" not in unnamed and "GsExport.java" in unnamed
    with pytest.raises(GsError, match="metacharacters"):
        ghidra_names.command(headless, project, module, tmp_path / "a&b", names=names_file, log=log, reuse=False,
                             apply_names=True)
    ghidra_names.check_bat_args([str(headless), r"D:\gst-e2e\game\decomp\il2cpp\names.jsonl", "-"])


def test_script_is_java_with_both_modes():
    from gst.decompile import native

    text = (native.scripts_dir() / ghidra_names.SCRIPT).read_text(encoding="utf-8")
    assert "class GstIl2cppNames extends GhidraScript" in text and '"pre"' in text and '"post"' in text
    assert "addExternalEntryPoint" in text and "createFunction" in text and "setPlateComment" in text


# --- extractor planning --------------------------------------------------------------------------

def il2cpp_game(tmp_path: Path) -> tuple[Path, dict]:
    ws, install = make_ws(tmp_path)
    for sub in ("jobs", "logs"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    game = {"gs_workspace": 1, "gst_workspace": 2, "slug": "il2", "name": "Il2", "buildid": "7",
            "install_dir": str(install), "engine": {"key": "unity-il2cpp"}, "verdict": "static-only"}
    (ws / "game.json").write_text(json.dumps(game), encoding="utf-8")
    return ws, game


def test_extractor_registered_ordered_and_planned(tmp_path):
    from gst.extractors import registry
    from gst.pipeline import plan

    cls = registry.get("code.il2cpp.native")
    assert cls in registry.for_engine("unity-il2cpp") and cls not in registry.for_engine("unity-mono")
    assert cls.resource == "heavy" and cls.stage == 70 and cls.category == "code"
    assert "rea.il2cpp_metadata" in cls.after and any(a.startswith("code.il2cpp.") for a in cls.after)
    assert "code.il2cpp.native" in registry.get("code.index").after
    ws, game = il2cpp_game(tmp_path)
    p = plan.build(ws, game, options={"only": ["code.il2cpp.native"]}, write=False)
    entry = next(e for e in p["extractors"] if e["name"] == "code.il2cpp.native")
    assert entry["applies"] and entry["units"] == 1 and "error" not in entry
    unit = next(cls().discover(plan.context_for(ws, game, {})))
    assert unit.relpaths == ["GameAssembly.dll", "Game_Data/il2cpp_data/Metadata/global-metadata.dat"]
    before = unit.fingerprint
    (ws / names.SCRIPT_JSON).write_text(json.dumps(script_json()), encoding="utf-8")
    assert next(cls().discover(plan.context_for(ws, game, {}))).fingerprint != before  # a new names source reruns it


def test_missing_producers_do_not_block(tmp_path):
    from gst.pipeline.runner import Runner

    ws, _ = il2cpp_game(tmp_path)
    r = Runner(ws, jobs=1)
    assert r._blocked_by_deps("code.il2cpp.native", {"unity.inventory"}) is False
    assert r._blocked_by_deps("code.il2cpp.native", {"rea.il2cpp_metadata"}) is True


def test_generic_native_skips_gameassembly_of_il2cpp_games(tmp_path):
    from gst.extractors.base import Context, Unit
    from gst.extractors.code import NativeCode

    ws, game = il2cpp_game(tmp_path)
    ctx = Context(workspace=ws, game=game, install_dir=Path(game["install_dir"]), out_root=ws, policy="static-only",
                  options={"native.all": "1"})
    unit = Unit(id="GameAssembly.dll", extractor="code.native", relpaths=["GameAssembly.dll"], size=1,
                meta={"name": "GameAssembly.dll", "game": False, "third_party": True, "large": False, "pdb": False})
    result = NativeCode().run(unit, ctx)
    assert result.status == "skipped" and "code.il2cpp.native" in result.notes[0]


def test_no_ceilings_in_m4c_modules():
    import test_no_caps

    paths = [Path(names.__file__), Path(ghidra_names.__file__)]
    assert all(p in test_no_caps.files() for p in paths)
    assert [v for p in paths for v in test_no_caps.violations(p)] == []
    for p in paths:  # no constant slices of results either ([:200] style)
        import ast

        tree = ast.parse(p.read_text(encoding="utf-8"))
        slices = [n for n in ast.walk(tree) if isinstance(n, ast.Slice) and isinstance(n.upper, ast.Constant)
                  and isinstance(n.upper.value, int) and n.lower is None]
        assert slices == [], f"{p.name}: constant slice at line {slices[0].lineno}"


def test_query_accepts_il2cpp_dollar_names():
    from gst.index import query

    assert query._split("Game.Player$$Update") == ("Game.Player", "Update")
    assert query._split("Player$$Update(float)") == ("Player", "Update")


# --- real Ghidra (slow) ----------------------------------------------------------------------------

@pytest.mark.tools
def test_ghidra_applies_names_on_a_small_dll(tmp_path, real_tool, monkeypatch):
    import pefile

    from gst import tools
    from gst.decompile import native

    found = {t: real_tool(t) for t in ("ghidra", "temurin21")}
    if not all(found.values()):
        pytest.skip("Ghidra or its JDK not installed")
    monkeypatch.setattr(tools, "bin_path", lambda t, name=None: found.get(t if isinstance(t, str) else t.id))
    src = Path(r"C:\Windows\System32\cabinet.dll")
    dll = tmp_path / "in" / "cabinet.dll"
    dll.parent.mkdir()
    shutil.copy2(src, dll)
    pe = pefile.PE(str(dll))
    exports = [(e.address, e.name.decode()) for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name][:2]
    pe.close()
    lines = [{"_header": 1, "format": 1}]
    for i, (rva, name) in enumerate(exports):
        lines.append({"rva": rva, "name": f"Demo.T{i}$${name}", "signature": f"public void {name}()", "kind": "method",
                      "type": f"Demo.T{i}", "source": "cpp2il", "ns": ["Demo", f"T{i}"], "fn": name})
    names_file = tmp_path / "names.jsonl"
    names_file.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    project = tmp_path / "proj" / "cabinet.dll"
    project.parent.mkdir()
    cmd = ghidra_names.command(Path(tools.bin_path("ghidra")), project, dll, out, names=names_file, log=tmp_path / "g.log",
                               reuse=False, apply_names=True)
    assert native._run(cmd, native.environment(), tmp_path / "console.txt", None) == 0
    report = json.loads((out / ghidra_names.REPORT).read_text(encoding="utf-8"))["counts"]
    assert report.get("already_named", 0) + report.get("renamed_default", 0) + report.get("renamed_other", 0) == len(exports)
    assert (out / "src" / "Demo" / "T0.c").is_file()
    sidecar = json.loads((out / "src" / "Demo" / "T0.c.map.json").read_text(encoding="utf-8"))
    assert sidecar["namespace"] == "Demo.T0" and sidecar["functions"][0]["name"] == exports[0][1]
    # GsExport streams every string (no count cap), full values, every referencing function
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (out / "strings.jsonl").read_text(encoding="utf-8").splitlines()]
    assert meta["strings"] == len(rows) > 0 and all(isinstance(r["refs"], list) for r in rows)
