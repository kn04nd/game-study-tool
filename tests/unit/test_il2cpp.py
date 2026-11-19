"""IL2CPP backend: metadata header reader on synthetic files, tool choice by version, log parsers, the
address table, the extractors with fake tools, and the no-ceilings rule for engines/il2cpp."""

from __future__ import annotations

import ast
import json
import struct
from pathlib import Path

import pytest

import gst
from gst.engines.il2cpp import cpp2il, decompile, il2cppdumper, inspector, metadata, tooling
from gst.extractors.base import Context, Unit

SRC = Path(gst.__file__).resolve().parent


# --- synthetic global-metadata.dat -----------------------------------------------------------------------------------

def make_metadata(path: Path, *, version: int, names: list[str], layout: str, image_names=("Assembly-CSharp.dll", "mscorlib.dll"),
                  type_counts=(3, 5), image_size: int | None = None, counts: dict | None = None, magic: int = metadata.MAGIC) -> Path:
    """A metadata file whose sections are laid out back to back after the header. Only the string heap and the image
    table carry real content; other sections get ``counts[name] * record`` zero bytes."""
    counts = dict(counts or {})
    triples = layout == "triples"
    stride = 12 if triples else 8
    header_size = 8 + stride * len(names)
    heap = b""
    name_index = []
    for n in image_names:
        name_index.append(len(heap))
        heap += n.encode() + b"\0"
    heap += b"Foo\0Bar\0"
    if triples:
        width = 2
        rec = image_size or 32 + 2 * width
    else:
        width = 4
        rec = image_size or 40
    images = b""
    start = 0
    for idx, tc in zip(name_index, type_counts):
        row = struct.pack("<ii", idx, 0)
        row += struct.pack("<H" if width == 2 else "<i", start) + struct.pack("<i", tc)
        row += b"\0" * (rec - len(row))
        images += row
        start += tc
    blobs = {}
    for name in names:
        if name == "strings":
            blobs[name] = (heap, heap.count(b"\0"))
        elif name == "images":
            blobs[name] = (images, len(image_names))
        else:
            n = counts.get(name, 0)
            blobs[name] = (b"\0" * (n * 12), n)
    out = bytearray(struct.pack("<Ii", magic, version))
    offset = header_size
    body = b""
    for name in names:
        data, count = blobs[name]
        out += struct.pack("<ii", offset, len(data)) + (struct.pack("<i", count) if triples else b"")
        body += data
        offset += len(data)
    path.write_bytes(bytes(out) + body)
    return path


def test_v39_triples_layout(tmp_path):
    p = make_metadata(tmp_path / "global-metadata.dat", version=39, names=metadata.LAYOUTS["38"], layout="triples",
                      counts={"methods": 7, "fields": 4, "typeDefinitions": 8})
    info = metadata.summarize(p)
    assert info["standard"] and info["version"] == 39 and info["layout"] == "offset-size-count"
    assert info["section_names_known"] and len(info["sections"]) == 31
    c = info["counts"]
    assert c["methods"] == {"count": 7, "exact": True} and c["fields"]["count"] == 4 and c["types"]["count"] == 8
    assert c["images"]["count"] == 2 and c["strings"]["count"] == 4
    assert [i["name"] for i in info["images"]] == ["Assembly-CSharp.dll", "mscorlib.dll"]
    assert info["images"][1]["type_start"] == 3 and info["images"][1]["type_count"] == 5  # 2-byte TypeDefinitionIndex
    assert metadata.version_key(info) == "39"


@pytest.mark.parametrize("version,key,n,image_size,sub", [
    (24, "24.0", 33, 40, "24.1"),
    (24, "24.0", 33, 32, "24.0"),
    (24, "24.2", 32, 40, "24.2"),
    (27, "27", 31, 40, "27"),
    (29, "29", 31, 40, "29"),
    (31, "29", 31, 40, "31"),
])
def test_pair_layouts_and_subversions(tmp_path, version, key, n, image_size, sub):
    names = metadata.LAYOUTS[key]
    assert len(names) == n
    p = make_metadata(tmp_path / "m.dat", version=version, names=names, layout="pairs", image_size=image_size,
                      image_names=("A.dll", "B.dll", "C.dll"), type_counts=(1, 2, 3), counts={"fields": 9})
    info = metadata.summarize(p)
    assert info["layout"] == "offset-size" and info["section_names_known"]
    assert metadata.version_key(info) == sub
    assert [i["name"] for i in info["images"]] == ["A.dll", "B.dll", "C.dll"]
    assert info["counts"]["types"] == {"count": 6, "exact": True, "from": "images"}
    assert info["counts"]["fields"] == {"count": 9, "exact": False}


def test_unknown_layout_gets_generic_names(tmp_path):
    names = [f"s{i}" for i in range(7)] + ["strings", "images"]
    p = make_metadata(tmp_path / "m.dat", version=106, names=names, layout="triples")
    info = metadata.read_header(p)
    assert info["layout"] == "offset-size-count" and not info["section_names_known"]
    assert info["sections"][0]["name"] == "section_0"


def test_wrong_magic_is_not_decrypted(tmp_path):
    p = make_metadata(tmp_path / "m.dat", version=39, names=metadata.LAYOUTS["38"], layout="triples", magic=0x12345678)
    info = metadata.summarize(p)
    assert info["standard"] is False and "does not decrypt" in info["reason"] and "sections" not in info


def test_truncated_file(tmp_path):
    p = tmp_path / "m.dat"
    p.write_bytes(b"\xaf\x1b")
    with pytest.raises(metadata.MetadataError):
        metadata.read_header(p)


def test_find_inputs(tmp_path):
    install = tmp_path / "Game"
    meta = install / "Game_Data" / "il2cpp_data" / "Metadata"
    meta.mkdir(parents=True)
    (meta / "global-metadata.dat").write_bytes(b"x")
    (install / "GameAssembly.dll").write_bytes(b"MZ")
    assert metadata.find_inputs(install) == [{"metadata": "Game_Data/il2cpp_data/Metadata/global-metadata.dat",
                                              "binary": "GameAssembly.dll", "data_dir": "Game_Data"}]


def test_binary_info_not_pe(tmp_path):
    p = tmp_path / "GameAssembly.dll"
    p.write_bytes(b"not a pe")
    assert "error" in metadata.binary_info(p)


# --- tool choice -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("spec,version,ok", [
    ("39", "39", True), ("39", "38", False), ("24-29.1", "24.2", True), ("24-29.1", "29.1", True),
    ("24-29.1", "29", True), ("24-29.1", "31", False), ("24-29.1", "24", True), ("104-106", "105", True),
    ("16-29.1", "29.2", False), ("31", "31.0", True),
])
def test_covers(spec, version, ok):
    assert tooling.covers(spec, version) is ok


def test_supporting_from_catalog():
    assert [t.id for t in tooling.supporting("39")] == ["cpp2il", "il2cppinspector"]
    assert [t.id for t in tooling.supporting("24.2")] == ["cpp2il", "il2cppdumper", "il2cppinspector"]
    assert [t.id for t in tooling.supporting("31", ("il2cppinspector", "il2cppdumper"))] == ["il2cppinspector", "il2cppdumper"]
    assert tooling.supporting("200") == []
    assert "no tool in the gst catalog supports it" in tooling.unsupported_reason("200")


def test_catalog_pins():
    from gst import tools

    cat = tools.load_catalog()
    for tid in ("cpp2il", "il2cppinspector", "il2cppdumper"):
        t = cat[tid]
        assert t.metadata_versions and t.version and t.url.startswith("https://github.com/")
        assert t.sha256 in ("github", "tofu") or len(t.sha256) == 64
    assert "Legacy.CLI" in cat["il2cppinspector"].url  # the new CLI never exits (ASP.NET server)


# --- log parsers -----------------------------------------------------------------------------------------------------

CPP2IL_LOG = """[Info] [Program] Determined game's unity version to be 6000.3.20f1
[Info] [Library] 	Using actual IL2CPP Metadata version 39
[Info] [Library] Got Binary codereg: 0x1870569B0, metareg: 0x18825A540 in 1397ms.
[Info] [Program] Done. Total execution time: 30410.2863ms
"""


def test_cpp2il_parse_and_command(tmp_path):
    facts = cpp2il.parse_log(CPP2IL_LOG)
    assert facts == {"unity_version": "6000.3.20f1", "metadata_version": "39", "code_registration": "0x1870569B0",
                     "metadata_registration": "0x18825A540", "tool_ms": 30410.2863}
    cmd = cpp2il.command(Path("c.exe"), tmp_path / "out", install=tmp_path / "game")
    assert cmd[1:3] == ["--game-path", str(tmp_path / "game")] and "attributeinjector" in cmd and cmd[-2:] == ["--output-to", str(tmp_path / "out")]
    forced = cpp2il.command(Path("c.exe"), tmp_path, binary=Path("GA.dll"), metadata=Path("m.dat"), unity_version="2022.3.1f1")
    assert "--force-unity-version" in forced and "--game-path" not in forced


def test_il2cppdumper_parse_unsupported():
    text = ("Initializing metadata...\nSystem.NotSupportedException: ERROR: Metadata file supplied is not a supported "
            "version[39].\nPress any key to exit...\nUnhandled exception. System.InvalidOperationException: Cannot read keys")
    facts = il2cppdumper.parse_log(text)
    assert facts["unsupported_version"] == "39" and all("Cannot read keys" not in e for e in facts["errors"])


def test_inspector_parse_and_prune(tmp_path):
    text = ("Detected metadata version \n39.0-TypeDefinitionIndex2\nCodeRegistration struct found at 0x00000001870569B0 "
            "(file offset 0x1)\nMetadataRegistration struct found at 0x000000018825A540 (file offset 0x2)\n")
    facts = inspector.parse_log(text)
    assert facts["metadata_version"].startswith("39.0") and facts["code_registration"] == "0x00000001870569B0"
    cpp = tmp_path / "cpp"
    (cpp / "appdata").mkdir(parents=True)
    (cpp / "appdata" / "il2cpp-types.h").write_text("struct A;", encoding="utf-8")
    (cpp / "framework").mkdir()
    (cpp / "IL2CppDLL.sln").write_text("x", encoding="utf-8")
    assert sorted(inspector.prune_cpp(tmp_path)) == ["IL2CppDLL.sln", "framework"]
    assert sorted(p.name for p in cpp.iterdir()) == ["appdata"]
    cmd = inspector.command(Path("i.exe"), Path("GA.dll"), Path("m.dat"), tmp_path)
    assert "--select-outputs" in cmd and "-c" not in cmd  # C# only through decomp/cs-il2cpp (no double index)


def test_run_tool_full_output_and_cwd(tmp_path):
    import sys

    res = tooling.run_tool([sys.executable, "-X", "utf8", "-c", "import os; print('hello', os.getcwd())"],
                           cwd=tmp_path / ".cwd", log=tmp_path / "t.log")
    assert res["exit_code"] == 0 and "hello" in res["output"] and str(tmp_path / ".cwd") in res["output"]
    assert not (tmp_path / ".cwd").exists()  # empty working dir removed


# --- address table ---------------------------------------------------------------------------------------------------

CS = '''using Cpp2ILInjected;

namespace Zoo;

[Token(Token = "0x2000DAA")]
public class Animal : MonoBehaviour
{
	[SerializeField]
	[Token(Token = "0x4008A64")]
	[FieldOffset(Offset = "0x20")]
	private Handler handler;

	[Token(Token = "0x170018FA")]
	public Handler Settings
	{
		[Token(Token = "0x6008963")]
		[Address(RVA = "0x977490", Offset = "0x976490", Length = "0x5")]
		get
		{
			return null;
		}
	}

	[Token(Token = "0x6008970")]
	[Address(RVA = "0xD845C0", Offset = "0xD835C0", Length = "0x1D")]
	public void Walk(int speed)
	{
	}
}
'''


def test_scan_attributes():
    rows = decompile.scan_attributes(CS)
    assert [r["decl"] for r in rows] == ["public class Animal : MonoBehaviour", "private Handler handler;",
                                         "public Handler Settings", "get", "public void Walk(int speed)"]
    assert rows[1]["field_offset"] == "0x20" and rows[1]["token"] == "0x4008A64"
    assert rows[3]["rva"] == "0x977490" and rows[4]["length"] == "0x1D"


def test_build_addresses_joins_index(tmp_path):
    from gst import index

    ws = tmp_path / "ws"
    f = ws / "decomp" / "cs-il2cpp" / "Assembly-CSharp" / "Zoo" / "Animal.cs"
    f.parent.mkdir(parents=True)
    f.write_text(CS, encoding="utf-8")
    index.build(ws, jobs=1)
    summary = decompile.build_addresses(ws)
    assert summary["rows"] == 5 and summary["with_rva"] == 2
    import sqlite3

    con = sqlite3.connect(index.db_path(ws))
    got = {r[0]: (r[1], r[2]) for r in con.execute("SELECT decl, qname, rva FROM il2cpp_addresses")}
    assert got["public void Walk(int speed)"] == ("Zoo.Animal.Walk", "0xD845C0")
    assert got["get"] == ("Zoo.Animal.Settings", "0x977490")
    assert got["private Handler handler;"][0] == "Zoo.Animal.handler"
    tsv = (ws / decompile.ADDRESSES_TSV).read_text(encoding="utf-8").splitlines()
    assert tsv[0].split("\t") == list(decompile.COLUMNS) and len(tsv) == 6


def test_assemblies_game_flag(tmp_path):
    for n in ("Assembly-CSharp", "UnityEngine.CoreModule", "mscorlib", "__Generated", "MagicaClothV2"):
        (tmp_path / f"{n}.dll").write_bytes(b"MZ")
    flags = {a["name"]: a["game"] for a in decompile.assemblies(tmp_path)}
    assert flags == {"Assembly-CSharp": True, "UnityEngine.CoreModule": False, "mscorlib": False, "__Generated": False,
                     "MagicaClothV2": True}


# --- extractors ------------------------------------------------------------------------------------------------------

def _install(tmp_path: Path, version: int = 39, magic: int = metadata.MAGIC) -> Path:
    install = tmp_path / "Game"
    meta = install / "Game_Data" / "il2cpp_data" / "Metadata"
    meta.mkdir(parents=True)
    make_metadata(meta / "global-metadata.dat", version=version, names=metadata.LAYOUTS["38"], layout="triples", magic=magic)
    (install / "GameAssembly.dll").write_bytes(b"MZ" + b"\0" * 64)
    return install


def _ctx(tmp_path: Path, install: Path, verdict: str = "offline-ok", **options) -> Context:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return Context(workspace=ws, game={"verdict": verdict, "buildid": "1", "name": "Game", "install_dir": str(install),
                                       "engine": {"key": "unity-il2cpp", "version": "6000.3.20f1"}},
                   install_dir=install, out_root=ws, policy=verdict, options=options)


def _unit(ext, ctx) -> Unit:
    return next(iter(ext.discover(ctx)))


def test_registry_and_ordering():
    from gst.engines.unity import extractors as unity
    from gst.extractors import registry

    names = registry.all_extractors()["unity-il2cpp"]
    for n in ("code.il2cpp.metadata", "code.il2cpp.dummydll", "code.il2cpp.maps", "code.il2cpp.decompile"):
        assert n in names
        assert registry.get(n).stage < unity.UnityInventory.stage
    assert "code.il2cpp.metadata" not in registry.all_extractors().get("unity-mono", [])
    assert "code.il2cpp.dummydll" in unity.UnityMonoBehaviours.after


def test_metadata_extractor(tmp_path, monkeypatch):
    from gst.engines.il2cpp import extractors as ex

    install = _install(tmp_path)
    ctx = _ctx(tmp_path, install)
    monkeypatch.setattr(ex, "probe_typetree", lambda *a: {"ok": False, "error": "AssertionError: failed to load il2cpp"})
    ext = ex.Il2cppMetadata()
    assert ext.applies(ctx)
    unit = _unit(ext, ctx)
    assert unit.relpaths == ["GameAssembly.dll", "Game_Data/il2cpp_data/Metadata/global-metadata.dat"]
    res = ext.run(unit, ctx)
    assert res.status == "done", res.error
    data = json.loads((ctx.workspace / metadata.OUT_JSON).read_text(encoding="utf-8"))
    assert data["version_key"] == "39" and data["tools"]["supporting"] == ["cpp2il", "il2cppinspector"]
    assert data["metadata"]["counts"]["images"]["count"] == 2 and data["typetree_il2cpp"]["ok"] is False
    # the typetree chain skips TypeTreeGenerator's il2cpp source once the probe failed
    from gst.engines.unity import typetree

    monkeypatch.setattr(typetree.loader, "data_dirs", lambda inst: [inst / "Game_Data"])
    kinds = [s["kind"] for s in typetree.sources(install, ctx.workspace, "1")]
    assert "il2cpp" not in kinds
    assert "il2cpp" in [s["kind"] for s in typetree.sources(install, tmp_path / "other-ws", "1")]


def test_unsupported_version_and_bad_magic(tmp_path):
    from gst.engines.il2cpp import extractors as ex

    install = _install(tmp_path, version=200)
    ctx = _ctx(tmp_path, install)
    res = ex.Il2cppDummyDll().run(_unit(ex.Il2cppDummyDll(), ctx), ctx)
    assert res.status == "unsupported" and "metadata version 200" in res.error
    maps = list(ex.Il2cppMaps().discover(ctx))
    assert [u.id for u in maps] == ["maps"]
    assert ex.Il2cppMaps().run(maps[0], ctx).status == "unsupported"
    assert ex.Il2cppDecompile().run(_unit(ex.Il2cppDecompile(), ctx), ctx).status == "unsupported"

    install2 = _install(tmp_path / "b", magic=0x11223344)
    ctx2 = _ctx(tmp_path / "b", install2)
    res2 = ex.Il2cppDummyDll().run(_unit(ex.Il2cppDummyDll(), ctx2), ctx2)
    assert res2.status == "unsupported" and "decrypt" in res2.error


def test_policy_blocks(tmp_path):
    from gst.engines.il2cpp import extractors as ex

    install = _install(tmp_path)
    ctx = _ctx(tmp_path, install, verdict="refuse")
    res = ex.Il2cppDummyDll().run(_unit(ex.Il2cppDummyDll(), ctx), ctx)
    assert res.status == "blocked-policy"


def test_dummydll_with_fake_tools(tmp_path, monkeypatch):
    from gst.engines.il2cpp import extractors as ex

    install = _install(tmp_path)
    ctx = _ctx(tmp_path, install)
    calls = []

    def fake(tool_id, ctx_, unit, raw, dll_dir):
        calls.append(tool_id)
        raw.mkdir(parents=True, exist_ok=True)
        (raw / f"{tool_id}.log").write_text("log", encoding="utf-8")
        if tool_id == "cpp2il":
            return {"ok": False, "dlls": 0, "facts": {}, "output": "Fatal: boom", "command": ["cpp2il"],
                    "run": {"exit_code": 1, "seconds": 0.1}}
        if dll_dir is not None:  # None = the maps run (no DLLs asked)
            dll_dir.mkdir(parents=True, exist_ok=True)
            (dll_dir / "Assembly-CSharp.dll").write_bytes(b"MZ")
        return {"ok": True, "dlls": 1, "facts": {"metadata_version": "39"}, "output": "", "command": [tool_id],
                "run": {"exit_code": 0, "seconds": 0.2}}

    monkeypatch.setattr(ex, "run_tool", fake)
    res = ex.Il2cppDummyDll().run(_unit(ex.Il2cppDummyDll(), ctx), ctx)
    assert res.status == "done" and calls == ["cpp2il", "il2cppinspector"]
    assert res.outputs == ["decomp/il2cpp/il2cppinspector", "decomp/il2cpp/dummydll"]
    dummy = ctx.workspace / "decomp" / "il2cpp" / "dummydll"
    manifest = json.loads((dummy / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["tool"] == "il2cppinspector" and manifest["ok"] and manifest["metadata_version"] == "39"
    assert (ctx.workspace / "decomp" / "il2cpp" / "cpp2il" / "MANIFEST.json").is_file()  # failed attempt kept
    assert "earlier attempt" in " ".join(res.notes)
    # maps: the inspector output is current for these inputs -> reused, not rerun
    calls.clear()
    maps = {u.id: u for u in ex.Il2cppMaps().discover(ctx)}
    assert sorted(maps) == ["il2cppinspector"]
    res2 = ex.Il2cppMaps().run(maps["il2cppinspector"], ctx)
    assert res2.status == "done" and calls == [] and "already produced" in res2.notes[0]
    ctx.options["force"] = ["code.il2cpp"]  # gst study --force code.il2cpp reruns the tool
    assert ex.Il2cppMaps().run(maps["il2cppinspector"], ctx).status == "done" and calls == ["il2cppinspector"]


def test_dummydll_tool_missing(tmp_path, monkeypatch):
    from gst.engines.il2cpp import extractors as ex

    install = _install(tmp_path)
    ctx = _ctx(tmp_path, install)
    monkeypatch.setattr(ex, "_tool_exe", lambda tool_id: None)
    res = ex.Il2cppDummyDll().run(_unit(ex.Il2cppDummyDll(), ctx), ctx)
    assert res.status == "failed" and "gst tools install cpp2il" in res.error


# --- no ceilings -----------------------------------------------------------------------------------------------------

IL2CPP_MODULES = sorted((SRC / "engines" / "il2cpp").rglob("*.py"))


@pytest.mark.parametrize("path", IL2CPP_MODULES, ids=lambda p: p.name)
def test_no_ceilings_in_il2cpp_modules(path):
    """No DEFAULT_LIMIT, no constant timeouts, no limit/timeout defaults other than None (docs/INTERFACES.md)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id != "DEFAULT_LIMIT", f"{path.name}:{node.lineno}"
        if isinstance(node, ast.keyword) and node.arg == "timeout":
            assert not isinstance(node.value, ast.Constant) or node.value.value is None, f"{path.name}:{node.lineno}"
        if isinstance(node, ast.FunctionDef):
            for arg, default in zip(reversed(node.args.args + node.args.kwonlyargs),
                                    reversed(node.args.defaults + [d for d in node.args.kw_defaults if d is not None])):
                if arg.arg in ("timeout", "limit"):
                    assert isinstance(default, ast.Constant) and default.value is None, f"{path.name}:{node.lineno}"
