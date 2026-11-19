"""REA contract: a fake ``rea`` (a Python script speaking the same CLI) returns valid Evidence, so the runner,
split, import into code.db, ``gst q callers/callees`` via ``rea_calls``, ``analyze systems`` and the extractors are
tested without Node, network or the real REA. Real-REA checks live behind ``-m tools``."""

from __future__ import annotations

import ast
import json
import sqlite3
import struct
import sys
from pathlib import Path

import pytest

from gst.common import GsError
from gst.extractors.base import Context, Unit
from gst.rea import evidence, extractors, import_index, runner, split

SRC = Path(__file__).resolve().parents[2] / "src" / "gst"

FAKE_REA = r'''
import json, os, sys
argv = sys.argv[1:]
log = os.environ.get("FAKE_REA_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv) + "\n")
caps = os.environ.get("FAKE_REA_CAPS") == "1"
if argv == ["--version"]:
    print("6.9.0"); sys.exit(0)
cmd = argv[0]
if "--help" in argv:
    opts = {"inspect-managed-members": ["--max-snapshot-bytes <value>", "--max-metadata-representation-bytes <value>"],
            "inspect-il2cpp-metadata": ["--binary <string>"] + (["--max-input-bytes <value>", "--max-output-bytes <value>"] if caps else []),
            "inspect-artifact": ["--artifact-limits <value>"]}.get(cmd, [])
    print("Options:\n  " + "\n  ".join(opts)); sys.exit(0)
def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None
def record(op, path, nr, params=None):
    return {"evidence_id": "ev_" + op + "_" + os.path.basename(path), "operation": op, "provider": {"id": "fake"},
            "subject": {"name": os.path.basename(path), "digest": {"sha256": "sha-" + os.path.basename(path)},
                        "local_path": path}, "parameters": params or {"input_path": path}, "normalized_result": nr}
if cmd == "inspect-managed-members":
    path = argv[1]
    if os.environ.get("FAKE_REA_FAIL"):
        sys.stderr.write("E" * 20000 + "END-OF-STDERR")
        print(json.dumps({"code": "resource_constraint", "message": "too big"})); sys.exit(1)
    nr = {"artifact": {"path": path, "sha256": "sha-" + os.path.basename(path)},
          "assembly": {"name": "Assembly-CSharp"},
          "types": [{"full_name": "Game.PlayerAvatar", "name": "PlayerAvatar", "namespace": "Game"},
                    {"full_name": "Game.SaveLoader", "name": "SaveLoader", "namespace": "Game"}],
          "methods": [
              {"token": "0x06000001", "declaring_type": "Game.PlayerAvatar", "name": "Update", "rva": 8272,
               "signature": {"return_type": "void", "parameter_types": []},
               "body": {"anchors": [{"opcode": "ldstr", "operand": "0x70000001"}]}},
              {"token": "0x06000002", "declaring_type": "Game.PlayerAvatar", "name": ".ctor", "rva": 8300},
              {"token": "0x06000003", "declaring_type": "Game.SaveLoader", "name": "Save", "rva": 8400}],
          "fields": [{"declaring_type": "Game.PlayerAvatar", "name": "health", "flags": 1,
                      "signature": {"field_type": "int"}}],
          "call_edges": [
              {"caller_token": "0x06000001", "caller": "Game.PlayerAvatar.Update", "opcode": "call", "il_offset": 18,
               "target_name": "Save", "target_declaring_type": "Game.SaveLoader"},
              {"caller_token": "0x06000001", "caller": "Game.PlayerAvatar.Update", "opcode": "callvirt", "il_offset": 30,
               "target_name": "SetBool", "target_declaring_type": "UnityEngine.Animator"},
              {"caller_token": "0x06000002", "caller": "Game.PlayerAvatar..ctor", "opcode": "newobj", "il_offset": 2,
               "target_name": ".ctor", "target_declaring_type": "Game.SaveLoader"}],
          "field_accesses": [{"method_token": "0x06000001", "opcode": "ldfld", "field_name": "health",
                              "field_declaring_type": "Game.PlayerAvatar"}],
          "user_strings": {"literals": [{"token": "0x70000001", "value": "Jumping"}]}}
    print(json.dumps(record("inspect_managed_members", path, nr))); sys.exit(0)
if cmd == "project-managed-application-graph":
    req = json.load(open(argv[1], encoding="utf-8"))
    mm = req["managed_members"]
    nr = {"root_artifact_sha256": mm["subject"]["digest"]["sha256"], "graph": {"nodes": [
        {"node_id": "n1", "observations": [{"label": "Game.PlayerAvatar.Update"}]},
        {"node_id": "n2", "observations": [{"label": "Game.SaveLoader.Save"}]}],
        "edges": [{"source_node_id": "n1", "target_node_id": "n2", "relation": "calls"}]}}
    print(json.dumps(record("project_managed_application_graph", mm["subject"]["local_path"], nr))); sys.exit(0)
if cmd == "inspect-il2cpp-metadata":
    path, binary = argv[1], opt("--binary")
    if binary and not opt("--max-output-bytes"):
        print(json.dumps({"code": "resource_constraint", "category": "resource_constraint",
                          "message": "Complete IL2CPP metadata exceeds the retained report budget"}))
        sys.exit(1)
    nr = {"artifact": {"path": path, "sha256": "meta-sha", "bytes": 10},
          "images": [{"name": "Assembly-CSharp.dll", "type_start": 0, "type_count": 3}],
          "types": [{"name": "<Module>", "namespace": "", "method_start": -1, "method_count": 0, "declaring_type": -1},
                    {"name": "Animal", "namespace": "Safari", "method_start": 0, "method_count": 2, "declaring_type": -1},
                    {"name": "Brain", "namespace": "", "method_start": 2, "method_count": 1, "declaring_type": 1}],
          "methods": [{"name": "Update", "declaring_type": 1}, {"name": "Die", "declaring_type": 1},
                      {"name": "Think", "declaring_type": 2}],
          "fields": [{"name": "hp", "declaring_type": 1}, {"name": "mood", "declaring_type": 2}],
          "binary": None, "method_addresses": [], "field_types": []}
    if binary:
        nr["binary"] = {"artifact": {"path": binary, "sha256": "bin-sha", "bytes": 20}}
        nr["method_addresses"] = [{"state": "code", "address": "0x180001000", "rva": "0x1000"},
                                  {"state": "no-body", "address": None, "rva": None},
                                  {"state": "code", "address": "0x180002000", "rva": "0x2000"}]
        nr["field_types"] = [{"type": 1, "name": "float"}, {"type": 2, "name": "Safari.Mood"}]
    print(json.dumps(record("inspect_il2cpp_metadata", path, nr))); sys.exit(0)
if cmd == "inspect-artifact":
    entries = json.loads(os.environ.get("FAKE_PCK_ENTRIES", "[]"))
    occ = [{"occurrence_id": "root", "parent_occurrence_id": None, "entry_kind": "file", "logical_path": "."}]
    occ += [{"occurrence_id": f"o{i}", "parent_occurrence_id": "root", "entry_kind": "file", "logical_path": e["path"],
             "declared_size": e["size"], "source_location": {"offset": e["offset"]}, "hash_status": "verified"}
            for i, e in enumerate(entries)]
    sub = record("inventory_artifact", argv[1], {"occurrences": occ})
    print(json.dumps(record("inspect_artifact", argv[1], {"substeps": [{"evidence": sub}]}))); sys.exit(0)
print(json.dumps({"code": "unknown_command"})); sys.exit(2)
'''


@pytest.fixture
def fake_rea(tmp_path, monkeypatch):
    script = tmp_path / "fake_rea.py"
    script.write_text(FAKE_REA, encoding="utf-8")
    log = tmp_path / "rea-argv.jsonl"
    monkeypatch.setenv(runner.ENV_EXE, str(script))
    monkeypatch.setenv("FAKE_REA_LOG", str(log))
    monkeypatch.delenv("FAKE_REA_CAPS", raising=False)
    monkeypatch.delenv("FAKE_REA_FAIL", raising=False)
    runner._VERSIONS.clear()
    runner._HELP.clear()
    extractors._TAGS.clear()

    def calls() -> list[list[str]]:
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    return {"script": script, "calls": calls}


def _ws(tmp_path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


# --- runner ---------------------------------------------------------------------------------------

def test_runner_saves_evidence_request_and_raises_caps(tmp_path, fake_rea):
    ws = _ws(tmp_path)
    dll = tmp_path / "game" / "Assembly-CSharp.dll"
    dll.parent.mkdir()
    dll.write_bytes(b"MZ")
    rea = runner.find()
    assert rea is not None and rea.version == "6.9.0" and rea.argv[0] == sys.executable
    res = runner.run(ws, "inspect_managed_members", "Assembly-CSharp", path=dll)
    assert res.ok and res.returncode == 0
    out = ws / "dumps" / "rea" / "evidence" / "inspect_managed_members"
    assert Path(res.evidence) == out / "Assembly-CSharp.json"
    doc = json.loads((out / "Assembly-CSharp.json").read_text(encoding="utf-8"))
    assert doc["operation"] == "inspect_managed_members"
    req = json.loads((out / "Assembly-CSharp.request.json").read_text(encoding="utf-8"))
    assert req["path"] == runner.rea_path(dll) and req["max_snapshot_bytes"] == str(runner.RAISED_CAP_BYTES)
    meta = json.loads((out / "Assembly-CSharp.run.json").read_text(encoding="utf-8"))
    assert meta["status"] == "done" and meta["caps"] == {"--max-snapshot-bytes": 2147483648,
                                                         "--max-metadata-representation-bytes": 2147483648}
    argv = fake_rea["calls"]()[-1]
    assert argv[:2] == ["inspect-managed-members", runner.rea_path(dll)] and argv[-1] == "--json"
    assert "--max-metadata-representation-bytes" in argv and "\\" not in argv[1]
    assert not list(out.glob(".partial-*"))


def test_runner_keeps_full_stderr_and_error_json_on_failure(tmp_path, fake_rea, monkeypatch):
    ws = _ws(tmp_path)
    monkeypatch.setenv("FAKE_REA_FAIL", "1")
    res = runner.run(ws, "inspect_managed_members", "Big", path=tmp_path / "Big.dll")
    assert not res.ok and res.returncode == 1
    assert runner.error_code(res) == "resource_constraint"
    stderr = Path(res.stderr).read_text(encoding="utf-8")
    assert len(stderr) == 20000 + len("END-OF-STDERR") and stderr.endswith("END-OF-STDERR")
    assert res.error.endswith("END-OF-STDERR") and "E" * 20000 in res.error  # never cut
    out = ws / "dumps" / "rea" / "evidence" / "inspect_managed_members"
    assert not (out / "Big.json").exists() and (out / "Big.error.json").exists()


def test_runner_is_static_only_and_refuses_unc(tmp_path, fake_rea):
    for command in ("instrument_with_frida", "capture_process_scenario", "start_frida_session", "debug_with_gdb",
                    "list_adb_devices", "observe_native_calls", "decompile"):
        with pytest.raises(GsError):
            runner.run(_ws(tmp_path) if not (tmp_path / "ws").exists() else tmp_path / "ws", command, "x")
    assert fake_rea["calls"]() == []  # nothing started
    with pytest.raises(GsError):
        runner.rea_path("\\\\localhost\\D$\\SteamLibrary\\x.dll")
    assert runner.rea_path("\\\\?\\D:\\Games\\x.dll") == "D:/Games/x.dll"


def test_il2cpp_caps_detected_from_help(tmp_path, fake_rea, monkeypatch):
    assert runner.il2cpp_caps(runner.find()) is False
    assert runner._help_cache_file().is_file()  # every study job is a new process: --help is cached on disk
    runner._HELP.clear()
    monkeypatch.setenv("FAKE_REA_CAPS", "1")
    assert runner.il2cpp_caps(runner.find()) is False  # same REA entry and mtime: the cached help
    runner._HELP.clear()
    runner._help_cache_file().unlink()
    assert runner.il2cpp_caps(runner.find()) is True


# --- split ----------------------------------------------------------------------------------------

def _members_record(tmp_path, fake_rea) -> tuple[Path, dict]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    res = runner.run(ws, "inspect_managed_members", "Assembly-CSharp", path=tmp_path / "Assembly-CSharp.dll")
    assert res.ok
    return ws, json.loads(Path(res.evidence).read_text(encoding="utf-8"))


def test_split_members_per_type_like_the_study_script(tmp_path, fake_rea):
    ws, rec = _members_record(tmp_path, fake_rea)
    types = split.members_by_type(rec)
    avatar = types["Game.PlayerAvatar"]
    assert avatar["engine_calls"] == {"Animator.SetBool": 1}
    assert avatar["game_calls"] == {"SaveLoader": 2}
    assert avatar["strings"] == ["Jumping"] and avatar["field_accesses"] == 1
    assert [m["name"] for m in avatar["methods"]] == ["Update", ".ctor"]
    assert avatar["fields"] == [{"name": "health", "type": "int", "flags": 1}]
    summary = split.split_file(ws / "dumps/rea/evidence/inspect_managed_members/Assembly-CSharp.json",
                               ws / "dumps/rea/split")
    assert summary["assemblies"] == ["Assembly-CSharp"] and summary["types"] == 2
    md = (ws / "dumps/rea/split/Assembly-CSharp/Game.md").read_text(encoding="utf-8")
    assert "## PlayerAvatar" in md and "`Animator.SetBool` 1" in md and "`Jumping`" in md
    data = json.loads((ws / "dumps/rea/split/Assembly-CSharp/Game.json").read_text(encoding="utf-8"))
    assert set(data) == {"Game.PlayerAvatar", "Game.SaveLoader"}
    assert "| Game | 2 | 3 |" in (ws / "dumps/rea/split/Assembly-CSharp/INDEX.md").read_text(encoding="utf-8")


def test_split_bundle_by_artifact(tmp_path):
    def rec(sha, name):
        return {"operation": "inspect_managed_members", "subject": {"name": name, "digest": {"sha256": sha}},
                "normalized_result": {"artifact": {"path": f"D:/x/{name}", "sha256": sha}}}

    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"artifacts": [], "records": [rec("aa" * 32, "A.dll"), rec("bb" * 32, "B.dll"),
                                                               rec("aa" * 32, "A.dll")]}), encoding="utf-8")
    out = split.split_bundle(bundle, tmp_path / "out")
    assert [p.name for p in out] == ["A.dll-aaaaaaaaaaaa.json", "B.dll-bbbbbbbbbbbb.json"]
    assert len(json.loads(out[0].read_text(encoding="utf-8"))["records"]) == 2


def test_split_and_import_il2cpp_with_addresses(tmp_path, fake_rea, monkeypatch):
    ws = _ws(tmp_path)
    monkeypatch.setenv("FAKE_REA_CAPS", "1")
    res = runner.run(ws, "inspect_il2cpp_metadata", "GameAssembly", path=tmp_path / "global-metadata.dat",
                     options={"--binary": runner.rea_path(tmp_path / "GameAssembly.dll")})
    assert res.ok and "--max-output-bytes" in res.argv
    images = split.il2cpp_by_image(json.loads(Path(res.evidence).read_text(encoding="utf-8")))
    animal = images["Assembly-CSharp.dll"]["Safari.Animal"]
    assert [(m["name"], m.get("rva")) for m in animal["methods"]] == [("Update", "0x1000"), ("Die", None)]
    brain = images["Assembly-CSharp.dll"]["Safari.Animal/Brain"]
    assert brain["namespace"] == "Safari" and brain["fields"] == [{"name": "mood", "type": "Safari.Mood", "index": 1}]
    split.split_file(res.evidence, ws / "dumps/rea/split")
    assert "| `Update` | code | 0x1000 |" in (ws / "dumps/rea/split/Assembly-CSharp/Safari.md").read_text(encoding="utf-8")
    summary = import_index.import_workspace(ws)
    assert summary["methods"] == 3 and summary["fields"] == 2
    con = sqlite3.connect(ws / "index" / "code.db")
    rows = con.execute("SELECT type, method, assembly, rva, state FROM rea_methods ORDER BY token").fetchall()
    assert rows[0] == ("Safari.Animal", "Update", "Assembly-CSharp", "0x1000", "code")
    assert con.execute("SELECT field_type FROM rea_fields WHERE field='hp'").fetchone()[0] == "float"


# --- import + gst q -------------------------------------------------------------------------------

def test_import_and_q_callers_via_rea_calls_without_source_index(tmp_path, fake_rea):
    from gst.index import query

    ws, _rec = _members_record(tmp_path, fake_rea)
    summary = import_index.import_workspace(ws)  # no decomp/: code.db is created with the rea_* tables only
    assert summary["calls"] == 3 and summary["fields"] == 1 and summary["methods"] == 3 and summary["field_accesses"] == 1
    con = sqlite3.connect(ws / "index" / "code.db")
    con.row_factory = sqlite3.Row
    hits = query.callers(con, "SaveLoader.Save")
    assert [(h["caller"], h["source"], h["line"]) for h in hits] == [("Game.PlayerAvatar.Update", "rea", "IL_0012")]
    assert query.callers(con, "Animator.SetBool")[0]["caller"] == "Game.PlayerAvatar.Update"
    assert query.callers(con, "Other.Save") == []
    ctor = query.callers(con, "SaveLoader")  # a type: who constructs it
    assert [h["caller"] for h in ctor] == ["Game.PlayerAvatar..ctor"]
    callees = query.callees(con, "PlayerAvatar.Update")
    assert [(c["target"], c["kind"], c["resolved_by"]) for c in callees] == [
        ("Game.SaveLoader.Save", "call", "rea"), ("UnityEngine.Animator.SetBool", "call", "rea")]
    assert import_index.stats(ws)["rea_calls"] == 3


def test_q_callers_merges_index_and_rea_without_duplicates(tmp_path, fake_rea):
    from gst import index
    from gst.index import query

    ws, _rec = _members_record(tmp_path, fake_rea)
    code = ws / "decomp" / "cs" / "Game"
    code.mkdir(parents=True)
    (code / "PlayerAvatar.cs").write_text("namespace Game {\npublic class PlayerAvatar\n{\n    public void Update() "
                                          "{ SaveLoader.Save(); }\n}\n}\n", encoding="utf-8")
    (code / "Hud.cs").write_text("namespace Game {\npublic class Hud\n{\n    public void Tick() { SaveLoader.Save(); }\n}\n}\n",
                                 encoding="utf-8")
    (code / "SaveLoader.cs").write_text("namespace Game {\npublic class SaveLoader\n{\n    public static void Save() { }\n}\n}\n",
                                        encoding="utf-8")
    index.build(ws)
    import_index.import_workspace(ws)
    index.build(ws)  # an incremental rebuild keeps the rea_* tables
    con = index.connect(ws)
    hits = query.callers(con, "SaveLoader.Save")
    sources = sorted((h["caller"].rsplit(".", 2)[-2] + "." + h["caller"].rsplit(".", 1)[-1], h["source"]) for h in hits)
    assert sources == [("Hud.Tick", "index"), ("PlayerAvatar.Update", "index")]  # REA's PlayerAvatar.Update deduped


def test_import_dedupes_artifacts_and_prefers_members_over_graph(tmp_path, fake_rea):
    ws, rec = _members_record(tmp_path, fake_rea)
    (ws / "dumps/rea/manual-bundle.json").write_text(json.dumps({"records": [rec]}), encoding="utf-8")
    req = {"managed_members": rec}
    res = runner.run(ws, "project_managed_application_graph", "Assembly-CSharp", request=req)
    assert res.ok
    summary = import_index.import_workspace(ws)
    assert summary["skipped_duplicates"] == 1 and summary["calls"] == 3  # graph edges skipped: members cover them
    (ws / "dumps/rea/evidence/inspect_managed_members/Assembly-CSharp.json").unlink()
    (ws / "dumps/rea/manual-bundle.json").unlink()
    summary = import_index.import_workspace(ws)
    assert summary["calls"] == 1  # only the graph's calls edge now


def test_systems_reads_single_records_and_bundles_once(tmp_path, fake_rea):
    from gst.analysis.systems import rea_edges

    ws, rec = _members_record(tmp_path, fake_rea)
    (ws / "dumps/rea/old-bundle.json").write_text(json.dumps({"records": [rec]}), encoding="utf-8")
    (ws / "dumps/rea/split").mkdir(parents=True, exist_ok=True)
    edges, n, files = rea_edges(ws / "dumps" / "rea")
    assert n == 3 and edges[("PlayerAvatar", "SaveLoader")] == 2 and edges[("PlayerAvatar", "Animator")] == 1
    assert [Path(f).name for f in files] == ["Assembly-CSharp.json"]


# --- extractors -----------------------------------------------------------------------------------

def _ctx(tmp_path, install, engine="unity-mono", **options) -> Context:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    game = {"slug": "g", "install_dir": str(install), "engine": {"key": engine}, "verdict": "offline-ok", "buildid": "1"}
    return Context(workspace=ws, game=game, install_dir=install, out_root=ws, policy="offline-ok", options=options)


def test_extractors_registered_for_the_engines():
    from gst.extractors import registry

    names = lambda key: {c.name for c in registry.for_engine(key)}  # noqa: E731
    for key in ("unity-mono", "unity-il2cpp", "godot"):
        assert {"rea.managed_members", "rea.managed_graph", "rea.import"} <= names(key)
    assert "rea.il2cpp_metadata" in names("unity-il2cpp") and "rea.il2cpp_metadata" not in names("unity-mono")
    assert "rea.artifact" in names("godot") and "rea.artifact" not in names("unreal")
    for cls in registry.for_engine("unity-il2cpp"):
        if cls.name.startswith("rea."):
            assert cls.policy_need == "static"
    assert registry.get("rea.import").stage > registry.get("code.index").stage
    assert "code.index" in registry.get("rea.import").after


def test_managed_discovery_game_first_and_framework_opt_in(tmp_path, fake_rea, monkeypatch):
    from gst.decompile import dotnet

    install = tmp_path / "game"
    managed = install / "Game_Data" / "Managed"
    managed.mkdir(parents=True)
    found = []
    for name, game in (("System.Core", False), ("Photon3Unity3D", False), ("Assembly-CSharp", True),
                       ("UnityEngine.CoreModule", False)):
        (managed / f"{name}.dll").write_bytes(b"MZ" + name.encode())
        found.append({"name": name, "path": str(managed / f"{name}.dll"), "size": 10, "game": game, "pdb": False})
    monkeypatch.setattr(dotnet, "find_assemblies", lambda _install: [dict(a) for a in found])
    ctx = _ctx(tmp_path, install)
    ext = extractors.ReaManagedMembers()
    units = list(ext.discover(ctx))
    assert [u.meta["name"] for u in units] == ["Assembly-CSharp", "Photon3Unity3D", "System.Core", "UnityEngine.CoreModule"]
    assert all(u.fingerprint for u in units)
    skipped = ext.run(units[2], ctx)
    assert skipped.status == "skipped" and "rea.framework=1" in skipped.notes[0]
    done = ext.run(units[0], ctx)
    assert done.status == "done" and done.outputs == ["dumps/rea/evidence/inspect_managed_members/Assembly-CSharp.json"]
    assert (ctx.workspace / "dumps/rea/split/Assembly-CSharp/Game.md").exists()
    assert ext.run(units[2], _ctx(tmp_path, install, **{"rea.framework": "1"})).status == "done"
    graph = extractors.ReaManagedGraph()
    gunits = list(graph.discover(ctx))
    res = graph.run(gunits[0], ctx)
    assert res.status == "done", res.error
    stub = json.loads((ctx.workspace / "dumps/rea/evidence/project_managed_application_graph/Assembly-CSharp.request.json")
                      .read_text(encoding="utf-8"))
    assert stub == {"managed_members": {"inlined_from": "dumps/rea/evidence/inspect_managed_members/Assembly-CSharp.json"}}
    assert graph.run(gunits[1], ctx).status == "skipped"  # Photon: no members Evidence yet
    imp = extractors.ReaImport()
    iunit = next(iter(imp.discover(ctx)))
    res = imp.run(iunit, ctx)
    assert res.status == "done" and res.outputs == ["index/code.db"] and "rea_calls 6" in res.notes[0]


def _il2cpp_install(tmp_path) -> Path:
    install = tmp_path / "il2game"
    meta = install / "Il2Game_Data" / "il2cpp_data" / "Metadata"
    meta.mkdir(parents=True)
    (meta / "global-metadata.dat").write_bytes(b"\xaf\x1b\xb1\xfa" + b"\0" * 60)
    (install / "GameAssembly.dll").write_bytes(b"MZ" + b"\0" * 100)
    return install


def test_il2cpp_unpatched_rea_is_unsupported_and_falls_back(tmp_path, fake_rea):
    install = _il2cpp_install(tmp_path)
    ctx = _ctx(tmp_path, install, engine="unity-il2cpp")
    ext = extractors.ReaIl2cppMetadata()
    assert ext.applies(ctx)
    unit = next(iter(ext.discover(ctx)))
    assert unit.relpaths == ["Il2Game_Data/il2cpp_data/Metadata/global-metadata.dat", "GameAssembly.dll"]
    res = ext.run(unit, ctx)
    assert res.status == "unsupported"
    assert "fixed 64 MiB IL2CPP report allowance" in res.error and "retained report budget" in res.error
    assert res.outputs == ["dumps/rea/evidence/inspect_il2cpp_metadata/global-metadata.json"]
    assert (ctx.workspace / "dumps/rea/evidence/inspect_il2cpp_metadata/GameAssembly.error.json").exists()


def test_il2cpp_with_the_fork_links_the_binary_at_the_m4c_path(tmp_path, fake_rea, monkeypatch):
    install = _il2cpp_install(tmp_path)
    ctx = _ctx(tmp_path, install, engine="unity-il2cpp")
    ext = extractors.ReaIl2cppMetadata()
    unit = next(iter(ext.discover(ctx)))
    assert ext.run(unit, ctx).status == "unsupported"  # first with the upstream REA: metadata-only fallback
    out = ctx.workspace / "dumps" / "rea" / "evidence" / "inspect_il2cpp_metadata"
    assert (out / "global-metadata.json").exists()
    monkeypatch.setenv("FAKE_REA_CAPS", "1")
    runner._HELP.clear()
    runner._help_cache_file().unlink()
    extractors._TAGS.clear()
    assert next(iter(ext.discover(ctx))).fingerprint != unit.fingerprint  # installing a patched REA reruns the job
    res = ext.run(unit, ctx)
    assert res.status == "done", res.error
    assert sorted(p.name for p in out.iterdir()) == ["GameAssembly.json", "GameAssembly.request.json",
                                                     "GameAssembly.run.json"]  # fallback and error files superseded
    # contract for code.il2cpp.native (docs/INTERFACES.md section 7): the raw REA JSON at this exact path
    path = ctx.workspace / "dumps" / "rea" / "evidence" / "inspect_il2cpp_metadata" / "GameAssembly.json"
    assert res.outputs == [path.relative_to(ctx.workspace).as_posix()]
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert evidence.result(doc)["method_addresses"][0]["rva"] == "0x1000"
    argv = fake_rea["calls"]()[-1]
    assert "--max-output-bytes" in argv and argv[argv.index("--max-output-bytes") + 1] == "2147483648"


def test_il2cpp_dummy_assemblies_from_the_workspace(tmp_path, fake_rea):
    install = _il2cpp_install(tmp_path)
    ctx = _ctx(tmp_path, install, engine="unity-il2cpp")
    ext = extractors.ReaManagedMembers()
    assert not ext.applies(ctx)  # nothing managed in an IL2CPP install
    dummy = ctx.workspace / "decomp" / "il2cpp" / "dummydll"
    dummy.mkdir(parents=True)
    for name in ("Assembly-CSharp", "mscorlib", "UnityEngine.CoreModule"):
        (dummy / f"{name}.dll").write_bytes(b"MZ")
    assert ext.applies(ctx)
    units = list(ext.discover(ctx))
    assert units[0].id == "workspace:decomp/il2cpp/dummydll/Assembly-CSharp.dll" and units[0].relpaths == []
    assert [u.meta["framework"] for u in units] == [False, True, True]
    res = ext.run(units[0], ctx)
    assert res.status == "done", res.error
    assert fake_rea["calls"]()[-1][1] == runner.rea_path(dummy / "Assembly-CSharp.dll")
    assert "code.il2cpp.dummydll" in ext.after


def _write_pck(path: Path, files: dict[str, bytes]) -> list[dict]:
    """A Godot 4 (format 2) pck with relative file base."""
    header = b"GDPC" + struct.pack("<4I", 2, 4, 3, 0) + struct.pack("<IQ", 2, 0) + b"\0" * 64
    names = [(f"res://{name}".encode("utf-8"), data) for name, data in files.items()]
    directory_size = 4 + sum(4 + len(n) + 16 + 16 + 4 for n, _ in names)
    file_base = len(header) + directory_size
    header = header[:24] + struct.pack("<Q", file_base) + header[32:]
    directory, blobs, entries, offset = struct.pack("<I", len(names)), b"", [], 0
    for (name, data), plain in zip(names, files):
        directory += struct.pack("<I", len(name)) + name + struct.pack("<QQ", offset, len(data)) + b"\0" * 16 + b"\0\0\0\0"
        entries.append({"path": plain, "size": len(data), "offset": file_base + offset})
        blobs += data
        offset += len(data)
    path.write_bytes(header + directory + blobs)
    return entries


def test_godot_pck_crosscheck(tmp_path, fake_rea, monkeypatch):
    install = tmp_path / "godot"
    install.mkdir()
    entries = _write_pck(install / "Game.pck", {"project.binary": b"abc", "scenes/main.scn": b"0123456789"})
    own = extractors.read_pck_directory(install / "Game.pck")
    assert [(e["path"], e["size"], e["offset"]) for e in own["entries"]] == [
        (e["path"], e["size"], e["offset"]) for e in entries]
    monkeypatch.setenv("FAKE_PCK_ENTRIES", json.dumps(entries))
    ctx = _ctx(tmp_path, install, engine="godot")
    ext = extractors.ReaArtifact()
    unit = next(iter(ext.discover(ctx)))
    res = ext.run(unit, ctx)
    assert res.status == "done", res.error
    check = json.loads((ctx.workspace / "dumps/rea/artifact/Game.pck.crosscheck.json").read_text(encoding="utf-8"))
    assert check["agree"] and check["matched"] == 2
    argv = fake_rea["calls"]()[-1]
    assert json.loads(argv[argv.index("--artifact-limits") + 1])["expanded_bytes"] == 2147483648
    monkeypatch.setenv("FAKE_PCK_ENTRIES", json.dumps(entries[:1]))
    ext.run(unit, ctx)
    check = json.loads((ctx.workspace / "dumps/rea/artifact/Game.pck.crosscheck.json").read_text(encoding="utf-8"))
    assert not check["agree"] and check["only_gst"] == ["scenes/main.scn"]


# --- catalog: kind npm ----------------------------------------------------------------------------

def test_npm_tool_spec_selection(monkeypatch):
    from gst import tools

    rea = tools.get("rea")
    assert rea.kind == "npm" and rea.kind in tools.KINDS and rea.version == "6.9.0"
    assert tools.npm_spec(rea) == ("rea-agents@6.9.0", "6.9.0")
    monkeypatch.setenv("GST_REA_SPEC", "D:/x/rea-agents-6.9.0-gst.tgz")
    spec, label = tools.npm_spec(rea)
    assert spec == "D:/x/rea-agents-6.9.0-gst.tgz" and label.startswith("custom-")
    assert all(d.parent.name == "rea" and d.name.startswith("custom-") for d in tools.npm_install_dirs(rea))


def test_npm_install_uses_a_private_prefix(tmp_path, monkeypatch):
    from gst import config, tools
    from gst.tools import installer

    monkeypatch.setenv("GST_TOOLS", str(tmp_path / "tools"))
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["timeout"] = cmd, kw.get("timeout", "unset")
        prefix = Path(cmd[cmd.index("--prefix") + 1])
        shim = prefix / "node_modules" / ".bin" / "rea.cmd"
        shim.parent.mkdir(parents=True)
        shim.write_text("@echo off\n", encoding="utf-8")
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(installer, "run", fake_run)
    monkeypatch.setattr(installer, "npm_exe", lambda: "npm")
    tool = tools.get("rea")
    installer._install_npm(tool)
    assert seen["cmd"][:2] == ["npm", "install"] and seen["cmd"][-1] == "rea-agents@6.9.0" and seen["timeout"] is None
    base = config.tools_dir() / "rea" / "6.9.0"
    assert (base / "node_modules" / ".bin" / "rea.cmd").is_file()
    assert tools.bin_path(tool) == base / "node_modules" / ".bin" / "rea.cmd"
    lock = tools.read_lock()["rea"]
    assert lock["spec"] == "rea-agents@6.9.0" and lock["kind"] == "npm"
    assert not list((config.tools_dir() / "rea").glob("6.9.0-*"))  # staging renamed into place


def test_npm_custom_spec_is_missing_until_installed(tmp_path, monkeypatch):
    from gst import tools

    monkeypatch.setenv("GST_TOOLS", str(tmp_path / "tools"))
    monkeypatch.setenv("GST_REA_SPEC", "D:/x/rea-agents-6.9.0-gst.tgz")
    global_shim = tmp_path / "global" / "rea.cmd"
    global_shim.parent.mkdir()
    global_shim.write_text("", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name: str(global_shim) if name.startswith("rea") else None)
    status = tools.status(tools.get("rea"))
    assert status["state"] == "missing" and status["other_install"] == str(global_shim)
    assert status["spec"] == "D:/x/rea-agents-6.9.0-gst.tgz"


# --- no ceilings ----------------------------------------------------------------------------------

REA_MODULES = sorted((SRC / "rea").glob("*.py"))


@pytest.mark.parametrize("path", REA_MODULES, ids=lambda p: p.name)
def test_no_ceilings_in_rea_modules(path):
    """No DEFAULT_LIMIT, no constant timeouts, no limit defaults, no truncated error text (docs/INTERFACES.md)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id != "DEFAULT_LIMIT", f"{path.name}:{node.lineno}"
        if isinstance(node, ast.keyword) and node.arg == "timeout":
            assert isinstance(node.value, ast.Constant) and node.value.value is None, f"{path.name}:{node.lineno}"
        if isinstance(node, ast.FunctionDef):
            for arg, default in zip(reversed(node.args.args + node.args.kwonlyargs),
                                    reversed(node.args.defaults + [d for d in node.args.kw_defaults if d is not None])):
                if arg.arg in ("timeout", "limit"):
                    assert isinstance(default, ast.Constant) and default.value is None, f"{path.name}:{node.lineno}"
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            target = ast.unparse(node.value)
            assert not any(word in target for word in ("error", "stderr", "stdout", "text")), \
                f"{path.name}:{node.lineno}: sliced {target}"


# --- real REA (slow) ------------------------------------------------------------------------------

@pytest.mark.tools
def test_real_rea_version_and_static_help(real_tool):
    exe = real_tool("rea")
    if exe is None:
        pytest.skip("REA is not installed")
    rea = runner.find(exe)
    assert rea.version and "--binary" in runner.help_text(rea, "inspect-il2cpp-metadata")
