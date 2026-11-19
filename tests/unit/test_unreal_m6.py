"""Unreal: stdlib pak/utoc readers on synthetic containers, fake repak/retoc/uas, keys/usmap policy,
response files and trie filters, the extractors and the unreal_assets analysis. No game content."""

from __future__ import annotations

import json
import re
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gst import analysis
from gst.common import GsError
from gst.engines import unreal as ue
from gst.engines.unreal import keys, paks, repak, retoc, uas, usmap

KEY = "0x" + "AB" * 32
URL = "https://example.org/published-keys"


# --- synthetic containers ------------------------------------------------------------------------------

def _fstring(s: str, utf16: bool = False) -> bytes:
    if not s:
        return struct.pack("<i", 0)
    if utf16:
        raw = (s + "\0").encode("utf-16-le")
        return struct.pack("<i", -(len(raw) // 2)) + raw
    raw = (s + "\0").encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def make_pak(path: Path, files: list[str], *, version: int = 11, encrypted: bool = False, mount: str = "../../../") -> Path:
    """A pak with a v10+ index (mount point, count, no path-hash index, a full directory index) and the footer."""
    data = b"\0" * 64
    dirs: dict[str, list[str]] = {}
    for f in files:
        d, _, name = f.rpartition("/")
        dirs.setdefault(d + "/", []).append(name)
    fdi = struct.pack("<i", len(dirs))
    for d, names in dirs.items():
        fdi += _fstring(d) + struct.pack("<i", len(names))
        for i, n in enumerate(names):
            fdi += _fstring(n, utf16=not n.isascii() and version < 12) + struct.pack("<i", i)
    index_offset = len(data)
    head_wo_fdi = _fstring(mount) + struct.pack("<iQii", len(files), 7, 0, 1)
    index_size = len(head_wo_fdi) + 16 + 20 + 8
    fdi_offset = index_offset + index_size
    index = head_wo_fdi + struct.pack("<QQ", fdi_offset, len(fdi)) + b"\0" * 20 + struct.pack("<ii", 0, 0)
    assert len(index) == index_size
    footer = b"\0" * 16 + (b"\x01" if encrypted else b"\x00") + struct.pack("<IIQQ", paks.PAK_MAGIC, version, index_offset, index_size)
    footer += b"\0" * 20 + b"\0" * 160
    path.write_bytes(data + index + fdi + footer)
    return path


def make_utoc(path: Path, *, flags: int = 8, container_id: int = 0x1234, entries: int = 3, version: int = 8) -> Path:
    head = paks.UTOC_MAGIC + bytes([version, 0]) + b"\0\0" + struct.pack("<9I", 144, entries, 1, 12, 1, 32, 65536, 0, 1)
    head += struct.pack("<Q", container_id) + b"\0" * 16 + bytes([flags, 0, 0, 0]) + struct.pack("<IQII", 0, 2**64 - 1, 0, 0)
    head += b"\0" * (144 - len(head))
    path.write_bytes(head + b"\0" * 32)
    path.with_suffix(".ucas").write_bytes(b"\0" * 16)
    return path


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "Game"
    p = root / "Proj" / "Content" / "Paks"
    p.mkdir(parents=True)
    return root, p


# --- paks.py ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("version", [10, 11, 12])
def test_pak_header_and_directory_listing(tmp_path, version):
    files = ["Proj/Content/BP_Hero.uasset", "Proj/Content/BP_Hero.uexp", "Proj/Config/Default.ini", "Proj/Content/Ação/T_A.uasset"]
    pak = make_pak(tmp_path / "x.pak", files, version=version)
    info = paks.pak_info(pak)
    assert info["version"] == version and info["encrypted_index"] is False and info["mount_point"] == "../../../"
    assert info["entries"] == 4 and info["full_directory_index"] is True
    assert paks.list_pak(pak) == sorted(files)


def test_encrypted_pak_index_is_reported_not_read(tmp_path):
    pak = make_pak(tmp_path / "x.pak", ["P/Content/A.uasset"], version=12, encrypted=True)
    info = paks.pak_info(pak)
    assert info["encrypted_index"] is True and "mount_point" not in info and info["version_name"] == "Utf8PakDirectory"
    with pytest.raises(GsError) as e:
        paks.list_pak(pak)
    assert e.value.code == 3


def test_utoc_header_flags_and_global(tmp_path):
    h = paks.utoc_header(make_utoc(tmp_path / "pakchunk0-Windows.utoc", flags=2 | 4 | 8))
    assert h["encrypted"] and h["signed"] and h["indexed"] and not h["global"] and h["entries"] == 3
    assert h["version_name"] == "ReplaceIoChunkHashWithIoHash" and h["container_id"] == "0000000000001234"
    g = paks.utoc_header(make_utoc(tmp_path / "global.utoc", flags=4, container_id=paks.GLOBAL_CONTAINER_ID))
    assert g["global"] and g["flag_names"] == ["signed"]
    assert paks.utoc_header(tmp_path / "global.ucas") is None


def test_containers_kinds(install):
    _root, p = install
    make_pak(p / "pakchunk0-Windows.pak", ["Proj/Content/A.uasset"], version=12, encrypted=True)
    make_utoc(p / "pakchunk0-Windows.utoc", flags=2 | 8)
    make_utoc(p / "global.utoc", flags=0, container_id=paks.GLOBAL_CONTAINER_ID)
    by = {c["name"]: c for c in paks.containers(p)}
    assert by["pakchunk0-Windows.pak"]["kind"] == "pak" and by["pakchunk0-Windows.pak"]["encrypted"]
    assert by["pakchunk0-Windows.utoc"]["kind"] == "iostore" and by["pakchunk0-Windows.utoc"]["encrypted"]
    assert by["global.utoc"]["kind"] == "global" and by["global.utoc"]["readable"]
    assert paks.retoc_version("GAME_UE5_7") == "UE5_7" and paks.retoc_version("GAME_UE4_22") is None


# --- repak / retoc / uas with fake tools ---------------------------------------------------------------------

@pytest.fixture
def fake_tools(monkeypatch):
    calls: list[list[str]] = []
    replies: dict[str, SimpleNamespace] = {}

    def fake_run(cmd, timeout=None):
        calls.append(cmd)
        tool = Path(cmd[0]).stem
        return replies.get(tool, SimpleNamespace(returncode=0, stdout="", stderr=""))

    monkeypatch.setattr(ue, "run", fake_run)
    monkeypatch.setattr(ue, "tool_bin", lambda tool: Path(f"C:/tools/{tool}.exe"))
    monkeypatch.setattr(ue, "tool_version", lambda tool: "1.0")
    return SimpleNamespace(calls=calls, replies=replies)


def test_repak_v12_goes_to_the_stdlib_lister(tmp_path, fake_tools):
    pak = make_pak(tmp_path / "x.pak", ["P/Content/A.uasset"], version=12)
    assert repak.list_paths(pak) == (["P/Content/A.uasset"], "stdlib") and fake_tools.calls == []


def test_repak_v11_uses_repak_and_falls_back(tmp_path, fake_tools):
    pak = make_pak(tmp_path / "x.pak", ["P/Content/A.uasset"], version=11)
    fake_tools.replies["repak"] = SimpleNamespace(returncode=0, stdout="P/Content/A.uasset\n", stderr="")
    assert repak.list_paths(pak) == (["P/Content/A.uasset"], "repak")
    assert fake_tools.calls[-1] == ["C:\\tools\\repak.exe", "list", str(pak)] or fake_tools.calls[-1][1:] == ["list", str(pak)]
    fake_tools.replies["repak"] = SimpleNamespace(returncode=1, stdout="", stderr="unsupported")
    assert repak.list_paths(pak) == (["P/Content/A.uasset"], "stdlib")


def test_repak_encrypted_needs_a_published_key(tmp_path, fake_tools):
    pak = make_pak(tmp_path / "x.pak", ["P/Content/A.uasset"], version=11, encrypted=True)
    with pytest.raises(GsError) as e:
        repak.list_paths(pak)
    assert e.value.code == 3 and fake_tools.calls == []
    fake_tools.replies["repak"] = SimpleNamespace(returncode=0, stdout="P/Content/A.uasset\n", stderr="")
    assert repak.list_paths(pak, key={"key": KEY})[1] == "repak+aes"
    assert fake_tools.calls[-1][1:3] == ["--aes-key", KEY]


def test_retoc_list_and_to_legacy_batches(tmp_path, fake_tools):
    fake_tools.replies["retoc"] = SimpleNamespace(returncode=0, stderr="", stdout=(
        "pakchunk0  0000000000000001 ExportBundleData ../../../W/Content/BP_A.uasset\n"
        "pakchunk0  0000000000000002 BulkData -\n"))
    assert retoc.list_paths(tmp_path / "a.utoc") == ["W/Content/BP_A.uasset"]
    assert fake_tools.calls[-1][1:3] == ["list", "--path"]
    cmds = retoc.to_legacy_commands(tmp_path / "Paks", tmp_path / "out" / "l.pak", filters=[f"W/Content/Long/Folder_{i:05d}/" for i in range(3000)],
                                    version="UE5_3")
    assert len(cmds) > 1 and all(len(subprocess.list2cmdline(c)) <= retoc.MAX_CMDLINE + 200 for c in cmds)
    assert all("--version" in c and c[-1].endswith(".pak") for c in cmds) and len({c[-1] for c in cmds}) == len(cmds)
    assert sum(c.count("-f") for c in cmds) == 3000
    one = retoc.to_legacy_commands(tmp_path / "Paks", tmp_path / "l.pak", key={"key": KEY})
    assert one[0][1:] == ["--aes-key", KEY, "to-legacy", "--no-shaders", str(tmp_path / "Paks"), str(tmp_path / "l.pak")]
    assert "--no-shaders" not in retoc.to_legacy_commands(tmp_path / "Paks", tmp_path / "l.pak", shaders=True)[0]


def test_retoc_to_legacy_parses_counts_and_hides_the_key(tmp_path, fake_tools):
    out = tmp_path / "legacy" / "l.pak"

    def fake_run(cmd, timeout=None):
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"pak")
        return SimpleNamespace(returncode=0, stderr="", stdout='info: : Extracted 12 (1 failed) legacy assets to "x"\n')

    ue.run = fake_run  # restored by monkeypatch (fixture set it)
    r = retoc.to_legacy(tmp_path / "Paks", out, tmp_path / "retoc.log", filters=["Hero/"], key={"key": KEY})
    assert r["extracted"] == 12 and r["failed"] == 1 and r["paks"] == [str(out)]
    assert KEY not in (tmp_path / "retoc.log").read_text(encoding="utf-8")


def test_script_objects_by_module():
    text = ("/Script/Game:\n  global_index:    Some(1)\n  outer_index:     None\n  cdo_class_index: None\n"
            "Hero:\n  global_index:    Some(2)\n  outer_index:     Some(1)\n  cdo_class_index: None\n"
            "Default__Hero:\n  global_index:    Some(3)\n  outer_index:     Some(1)\n  cdo_class_index: Some(2)\n"
            "HeroStats:\n  global_index:    Some(4)\n  outer_index:     Some(1)\n  cdo_class_index: None\n"
            "Inner:\n  global_index:    Some(5)\n  outer_index:     Some(2)\n  cdo_class_index: None\n")
    objs = retoc.parse_script_objects(text)
    assert [o["name"] for o in objs] == ["/Script/Game", "Hero", "Default__Hero", "HeroStats", "Inner"]
    mods = retoc.script_modules(objs)
    assert mods == {"/Script/Game": {"objects": 4, "classes": ["Hero"]}}


def test_trie_regex_matches_exactly_the_paths():
    paths = ["FSD/Content/A/BP_A", "FSD/Content/A/BP_AB", "FSD/Content/A/BP_B", "FSD/Content/Ação (1)/T+x", "Engine/X"]
    rx = re.compile("^(?:" + uas.trie_regex(paths) + ")$")
    for p in paths:
        assert rx.match(p), p
    for p in ("FSD/Content/A/BP_", "FSD/Content/A/BP_ABC", "FSD/Content/B/BP_A", "Engine"):
        assert not rx.match(p), p
    flt = re.compile(uas.exact_filter([p + ".uasset" for p in paths[:3]]))
    assert flt.search("FSD/Content/A/BP_A.uexp") and flt.search("FSD/Content/A/BP_AB.uasset")
    assert not flt.search("FSD/Content/A/BP_ABC.uasset") and not flt.search("X/FSD/Content/A/BP_A.uasset")


def test_uas_runs_once_from_a_response_file(tmp_path, fake_tools):
    paths = [f"Game/Content/Characters/Hero_{i:05d}/BP_Hero_{i:05d}.uasset" for i in range(50_000)]
    flt = uas.exact_filter(paths)
    assert len(flt) > 32_000  # would never fit a command line
    rsp = tmp_path / "logs" / "a b.rsp"
    uas.export(tmp_path / "Pa ks", tmp_path / "out", "json", "GAME_UE4_27", log=tmp_path / "logs" / "u.log", rsp=rsp, flt=flt,
               bytecode=True, key_file=tmp_path / "k.key")
    assert len(fake_tools.calls) == 1 and fake_tools.calls[0][1] == f"@{rsp}"
    lines = rsp.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["export", "Json"] and f'"{tmp_path / "Pa ks"}"' in lines and "--script-bytecode" in lines
    assert lines[lines.index("--filter") + 1] == flt and lines[lines.index("--aes-file") + 1] == str(tmp_path / "k.key")
    assert re.search(flt, paths[777].replace(".uasset", ".uexp")) and not re.search(flt, "Game/Content/Other.uasset")


def test_uas_list_marks_usmap(tmp_path, fake_tools):
    fake_tools.replies["uas"] = SimpleNamespace(returncode=0, stderr="", stdout="[12:00 INF] Found 2\n[*] G/Content/A.uasset\nG/Content/B.uasset\n")
    paths, needs = uas.list_paths(tmp_path, "GAME_UE5_4", log=tmp_path / "l.log", rsp=tmp_path / "l.rsp")
    assert paths == ["G/Content/A.uasset", "G/Content/B.uasset"] and needs == ["G/Content/A.uasset"]


# --- keys / usmap ----------------------------------------------------------------------------------------------

def test_keys_only_with_a_source_url(tmp_path):
    ws = tmp_path / "ws"
    (ws / "notes").mkdir(parents=True)
    container = {"name": "pakchunk0-Windows.utoc", "encrypted": True, "key_guid": "0" * 32}
    with pytest.raises(GsError) as e:
        keys.require(ws, {}, container)
    assert e.value.code == 3 and "rule 4" in str(e.value)
    (ws / "notes" / "keys.json").write_text(json.dumps({"keys": [{"key": KEY}]}), encoding="utf-8")
    key, why = keys.key_for(ws, {}, "0" * 32)
    assert key is None and "no source URL" in why
    (ws / "notes" / "keys.json").write_text(json.dumps([{"key": KEY.lower()[2:], "source": URL}]), encoding="utf-8")
    assert keys.require(ws, {}, container) == {"scheme": "unreal-aes", "key": KEY, "guid": "0" * 32, "source": URL}
    assert keys.require(ws, {}, {**container, "encrypted": False}) is None
    f = keys.key_file(ws, keys.require(ws, {}, container))
    assert f.read_text(encoding="utf-8").strip() == KEY and f.is_relative_to(ws / "dumps")
    game = {"keys": [{"scheme": "unreal-aes", "key": KEY, "guid": "11" * 16, "source": URL}]}
    assert keys.key_for(tmp_path / "other", game, "11" * 16)[0]["guid"] == "11" * 16


def test_usmap_from_notes_and_detection(tmp_path):
    ws = tmp_path / "ws"
    (ws / "notes").mkdir(parents=True)
    assert usmap.find(ws, {}) == (None, "")
    (ws / "notes" / "bad.usmap").write_bytes(b"nope")
    (ws / "notes" / "game.usmap").write_bytes(struct.pack("<HBB", usmap.USMAP_MAGIC, 3, 0) + b"\0" * 8)
    p, src = usmap.find(ws, {})
    assert p.name == "game.usmap" and src.startswith("user") and usmap.header(p)["compression"] == "none"
    assert usmap.needs_mappings("ERR Package has unversioned properties but no mappings") and not usmap.needs_mappings("ok")


# --- inventory, extract, to-legacy -----------------------------------------------------------------------------

def _ws(tmp_path, root):
    ws = tmp_path / "ws"
    (ws / "notes").mkdir(parents=True)
    game = {"slug": "g", "name": "G", "buildid": "1", "install_dir": str(root), "verdict": "offline-ok",
            "engine": {"key": "unreal", "version": "5.4.1"}}
    return ws, game


def test_inventory_mixed_folder(tmp_path, install, fake_tools):
    from gst.engines.unreal import inventory

    root, p = install
    make_pak(p / "pakchunk0-Windows.pak", ["Proj/Content/BP_Hero.uasset", "Proj/Config/DefaultGame.ini"], version=12)
    make_pak(p / "pakchunk1-Windows.pak", ["Proj/Content/Locked.uasset"], version=11, encrypted=True)
    make_utoc(p / "pakchunk2-Windows.utoc", flags=8)
    make_utoc(p / "global.utoc", flags=0, container_id=paks.GLOBAL_CONTAINER_ID)
    (root / "Manifest_UFSFiles_Win64.txt").write_text("Proj/Content/Locked.uasset\t2024\nProj/Content/BP_Hero.uasset\t2024\n",
                                                      encoding="utf-8")
    fake_tools.replies["retoc"] = SimpleNamespace(returncode=0, stderr="", stdout="c 1 ExportBundleData ../../../Proj/Content/DT_Stats.uasset\n")
    ws, game = _ws(tmp_path, root)
    s = inventory.inventory(ws, root, game)
    rows = [json.loads(x) for x in (ws / "assets" / "inventory.jsonl").read_text(encoding="utf-8").splitlines()]
    by = {r["path"]: r["container"] for r in rows}
    assert by == {"Proj/Content/BP_Hero.uasset": "pakchunk0-Windows.pak", "Proj/Config/DefaultGame.ini": "pakchunk0-Windows.pak",
                  "Proj/Content/DT_Stats.uasset": "pakchunk2-Windows.utoc", "Proj/Content/Locked.uasset": "manifest"}
    assert s["blocked"] == ["pakchunk1-Windows.pak"] and "stdlib" in s["source"] and "retoc" in s["source"]
    cont = json.loads((ws / "assets" / "unreal" / "containers.json").read_text(encoding="utf-8"))
    assert {c["name"]: c.get("how") for c in cont["containers"]}["global.utoc"].startswith("global")
    assert "blocked (rule 4)" in (ws / "assets" / "INVENTORY.md").read_text(encoding="utf-8")
    assert not any(Path(c[0]).stem == "repak" for c in fake_tools.calls)  # v12 never reaches repak 0.2.3


def test_extract_one_uas_run_with_the_union_and_limit(tmp_path, install, fake_tools):
    from gst.engines.unreal import export

    root, p = install
    files = [f"Proj/Content/Enemies/E{i}.uasset" for i in range(5)] + ["Proj/Content/Hero/BP_Hero.uasset"]
    make_pak(p / "Proj-WindowsNoEditor.pak", files, version=11)
    fake_tools.replies["repak"] = SimpleNamespace(returncode=0, stdout="\n".join(files), stderr="")
    ws, game = _ws(tmp_path, root)

    def fake_run(cmd, timeout=None):
        fake_tools.calls.append(cmd)
        if Path(cmd[0]).stem == "repak":
            return fake_tools.replies["repak"]
        args = Path(cmd[1][1:]).read_text(encoding="utf-8").splitlines()
        out = Path(args[args.index("--output") + 1])
        (out / "x").mkdir(parents=True, exist_ok=True)
        (out / "x" / "a.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ue.run = fake_run
    r = export.extract(ws, root, game, name="Enemies/", limit=2)  # --limit: the first 2, no error
    assert r["limited"] and r["selected"] == 2 and r["exported"] == 1
    r2 = export.extract(ws, root, game, name="Hero/BP_")
    uas_calls = [c for c in fake_tools.calls if Path(c[0]).stem == "uas"]
    assert len(uas_calls) == 2 and all(len(c) == 2 and c[1].startswith("@") for c in uas_calls)
    rsp = Path(uas_calls[-1][1][1:]).read_text(encoding="utf-8").splitlines()
    flt = re.compile(rsp[rsp.index("--filter") + 1])
    assert flt.search("Proj/Content/Hero/BP_Hero.uasset") and flt.search("Proj/Content/Enemies/E1.uasset")  # union kept
    assert not flt.search("Proj/Content/Enemies/E3.uasset")  # the limit picked E0, E1 only
    side = json.loads((ws / "assets" / "unreal" / "json" / "_folder.gs.json").read_text(encoding="utf-8"))
    assert side["filters"] == ["Hero/BP_"] and side["exact"] == ["Proj/Content/Enemies/E0.uasset", "Proj/Content/Enemies/E1.uasset"]
    assert r2["matched"] == 1


def test_extract_encrypted_without_key_is_blocked(tmp_path, install, fake_tools):
    from gst.engines.unreal import export

    root, p = install
    make_pak(p / "pakchunk0-Windows.pak", ["P/Content/A.uasset"], version=12, encrypted=True)
    (root / "Manifest_UFSFiles_Win64.txt").write_text("P/Content/A.uasset\t1\n", encoding="utf-8")
    ws, game = _ws(tmp_path, root)
    with pytest.raises(GsError) as e:
        export.extract(ws, root, game, name="A")
    assert e.value.code == 3 and "rule 4" in str(e.value)


def test_to_legacy_blocked_then_runs_with_a_key(tmp_path, install, fake_tools):
    from gst.engines.unreal import export

    root, p = install
    make_utoc(p / "pakchunk0-Windows.utoc", flags=2 | 8)
    make_utoc(p / "global.utoc", flags=0, container_id=paks.GLOBAL_CONTAINER_ID)
    ws, game = _ws(tmp_path, root)
    with pytest.raises(GsError) as e:
        export.to_legacy(ws, root, game, filters=["Hero/"])
    assert e.value.code == 3 and fake_tools.calls == []
    (ws / "notes" / "keys.json").write_text(json.dumps([{"key": KEY, "source": URL}]), encoding="utf-8")

    def fake_run(cmd, timeout=None):
        fake_tools.calls.append(cmd)
        if Path(cmd[0]).stem == "retoc":
            Path(cmd[-1]).write_bytes(b"pak")
            return SimpleNamespace(returncode=0, stderr="", stdout="Extracted 3 (0 failed) legacy assets to x")
        args = Path(cmd[1][1:]).read_text(encoding="utf-8").splitlines()
        out = Path(args[args.index("--output") + 1])
        (out / "Proj").mkdir(parents=True, exist_ok=True)
        (out / "Proj" / "BP_Hero.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ue.run = fake_run
    r = export.to_legacy(ws, root, game, filters=["Hero/"], modes=["json"], name="legacy-t")
    retoc_cmd = next(c for c in fake_tools.calls if Path(c[0]).stem == "retoc")
    assert retoc_cmd[1:3] == ["--aes-key", KEY] and ["-f", "Hero/"] == retoc_cmd[retoc_cmd.index("-f"):retoc_cmd.index("-f") + 2]
    assert "UE5_4" in retoc_cmd and Path(retoc_cmd[-1]) == ws / "dumps" / "unreal" / "legacy" / "legacy-t.pak"
    uas_rsp = Path(next(c for c in fake_tools.calls if Path(c[0]).stem == "uas")[1][1:]).read_text(encoding="utf-8").splitlines()
    assert uas_rsp[uas_rsp.index("--paks") + 1] == str(ws / "dumps" / "unreal" / "legacy")
    assert r["exported"] == 1 and r["legacy"]["extracted"] == 3


# --- extractors -------------------------------------------------------------------------------------------------

def _ctx(ws, game, root, **options):
    from gst.extractors.base import Context

    return Context(workspace=ws, game=game, install_dir=root, out_root=ws, policy="offline-ok", options=options)


def test_extractors_registered_with_categories():
    from gst.extractors import registry

    names = {cls.name: cls.category for cls in registry.for_engine("unreal")}
    assert names["unreal.inventory"] == "data" and names["unreal.iostore"] == "data" and names["unreal.export"] == "data"
    assert names["unreal.textures"] == "texture" and names["unreal.models"] == "mesh" and names["unreal.audio"] == "audio"
    assert names["unreal.animations"] == "animation"
    import gst.extractors.unreal as shim

    assert shim.UnrealExport is registry.get("unreal.export")


def test_export_units_per_container_and_mode(tmp_path, install, fake_tools):
    from gst.engines.unreal import extractors as ex

    root, p = install
    make_pak(p / "pakchunk0-Windows.pak", ["P/Content/A.uasset"], version=12)
    make_utoc(p / "pakchunk0-Windows.utoc", flags=2 | 8)
    make_utoc(p / "global.utoc", flags=0, container_id=paks.GLOBAL_CONTAINER_ID)
    ws, game = _ws(tmp_path, root)
    ctx = _ctx(ws, game, root, **{"unreal.modes": "json,audio"})
    assert ex.UnrealExport().applies(ctx) and not ex.UnrealTextures().applies(ctx)
    units = list(ex.UnrealExport().discover(ctx))
    assert [u.id for u in units] == ["pakchunk0-Windows.pak", "pakchunk0-Windows.utoc"]  # no unit for global.utoc
    assert units[1].relpaths[-1].endswith("pakchunk0-Windows.ucas") and units[0].fingerprint
    audio = list(ex.UnrealAudio().discover(ctx))
    assert audio[0].fingerprint != units[0].fingerprint
    r = ex.UnrealExport().run(units[1], ctx)
    assert r.status == "blocked-policy" and "rule 4" in r.error
    io = list(ex.UnrealIoStore().discover(_ctx(ws, game, root, **{"unreal.filter": "Hero/,Stats"})))
    assert io[0].meta["filter"] == "Hero/,Stats"
    assert ex.UnrealIoStore().run(io[0], ctx).status == "blocked-policy"
    (ws / "notes" / "keys.json").write_text(json.dumps([{"key": KEY, "source": URL}]), encoding="utf-8")
    again = list(ex.UnrealExport().discover(ctx))
    assert again[1].fingerprint != units[1].fingerprint  # a new published key reruns the blocked unit


def test_export_job_single_container_has_no_filter(tmp_path, install, fake_tools, monkeypatch):
    from gst.engines.unreal import extractors as ex

    root, p = install
    make_pak(p / "Proj-WindowsNoEditor.pak", ["P/Content/A.uasset", "P/Content/B.uasset"], version=11)
    ws, game = _ws(tmp_path, root)
    (ws / "assets").mkdir()
    (ws / "assets" / "inventory.jsonl").write_text("".join(json.dumps({"path": f"P/Content/{n}.uasset", "container": "Proj-WindowsNoEditor.pak"}) + "\n"
                                                         for n in "AB"), encoding="utf-8")
    seen = {}

    def fake_run(cmd, timeout=None):
        args = Path(cmd[1][1:]).read_text(encoding="utf-8").splitlines()
        seen["args"] = args
        out = Path(args[args.index("--output") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "A.json").write_text("[]", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ue.run = fake_run
    ctx = _ctx(ws, game, root)
    unit = next(iter(ex.UnrealExport().discover(ctx)))
    r = ex.UnrealExport().run(unit, ctx)
    assert r.status == "done" and "--filter" not in seen["args"] and "--script-bytecode" in seen["args"]
    assert (ws / "assets" / "unreal" / "containers" / "Proj-WindowsNoEditor.pak" / "json" / "A.json").is_file()


# --- CLI --------------------------------------------------------------------------------------------------------

def test_types_appends():
    from gst.cli import build_parser

    args = build_parser().parse_args(["assets", "extract", "--types", "json", "--types", "textures,json", "--name", "x"])
    assert args.types == "json,textures"
    tl = build_parser().parse_args(["assets", "to-legacy", "--filter", "Hero/", "--filter", "Stats", "--types", "json"])
    assert tl.filter == ["Hero/", "Stats"] and tl.types == "json"


def test_no_ceiling_scan_covers_engines_unreal():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_no_caps", Path(__file__).with_name("test_no_caps.py"))
    t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t)
    scanned = {p.relative_to(t.SRC).as_posix() for p in t.files()}
    for name in ("paks", "repak", "retoc", "uas", "usmap", "keys", "inventory", "export", "extractors", "uasjson"):
        assert f"engines/unreal/{name}.py" in scanned
        assert t.violations(t.SRC / "engines" / "unreal" / f"{name}.py") == []


# --- analysis ---------------------------------------------------------------------------------------------------

def test_unreal_assets_analysis(tmp_path, install, monkeypatch):
    root, p = install
    make_utoc(p / "global.utoc", flags=0, container_id=paks.GLOBAL_CONTAINER_ID)
    ws, game = _ws(tmp_path, root)
    (ws / "assets").mkdir()
    inv = ["P/Content/Hero/BP_Hero.uasset", "P/Content/Hero/SK_Hero.uasset", "P/Content/Hero/SK_Hero_Skeleton.uasset",
           "P/Content/Hero/ABP_Hero.uasset", "P/Content/Data/DT_Stats.uasset", "P/Content/Maps/Main.umap", "P/Content/Hero/BP_Hero.uexp"]
    (ws / "assets" / "inventory.jsonl").write_text("".join(json.dumps({"path": x, "container": "c.pak"}) + "\n" for x in inv),
                                                    encoding="utf-8")
    out = ws / "assets" / "unreal" / "json" / "P" / "Content"
    (out / "Data").mkdir(parents=True)
    (out / "Hero").mkdir()
    (out / "Data" / "DT_Stats.json").write_text(json.dumps([{"Type": "DataTable", "Name": "DT_Stats",
        "Properties": {"RowStruct": {"ObjectName": "ScriptStruct'HeroStats'", "ObjectPath": "/Script/Game"}},
        "Rows": {"Grunt": {"Health": 50, "Speed": {"Min": 1.5, "Max": 2}, "Boss": False},
                 "Guard": {"Health": 120, "Speed": {"Min": 1, "Max": 1.5}, "Boss": True}}}]), encoding="utf-8")
    (out / "Hero" / "BP_Hero.json").write_text(json.dumps([
        {"Type": "Function", "Name": "Jump"}, {"Type": "SCS_Node", "Name": "N1"},
        {"Type": "BlueprintGeneratedClass", "Name": "BP_Hero_C", "SuperStruct": {"ObjectName": "Class'HeroCharacter'", "ObjectPath": "/Script/Game"},
         "ChildProperties": [{"Name": "MaxAmmo"}, {"Name": "K2Node_Event_X"}]}]), encoding="utf-8")
    objs = ("/Script/Game:\n  global_index:    Some(1)\n  outer_index:     None\n  cdo_class_index: None\n"
            "HeroCharacter:\n  global_index:    Some(2)\n  outer_index:     Some(1)\n  cdo_class_index: None\n"
            "Default__HeroCharacter:\n  global_index:    Some(3)\n  outer_index:     Some(1)\n  cdo_class_index: Some(2)\n")
    monkeypatch.setattr(ue, "run", lambda cmd, timeout=None: SimpleNamespace(returncode=0, stdout=objs, stderr=""))
    monkeypatch.setattr(ue, "tool_bin", lambda tool: Path(f"{tool}.exe"))
    r = analysis.run(ws, game, "unreal_assets")
    assert r["status"] == "done", r.get("reason")
    doc = json.loads((ws / "analysis" / "unreal_assets.json").read_text(encoding="utf-8"))
    s, d = doc["summary"], doc["data"]
    assert s["packages"] == 6 and s["datatables"] == 1 and s["datatable_rows"] == 2 and s["blueprints_exported"] == 1
    assert d["guessed"]["SkeletalMesh"] == 1 and d["guessed"]["Skeleton"] == 1 and d["guessed"]["AnimBlueprint"] == 1
    assert d["by_folder"]["P/Content/Hero"]["Skeletal"] == 2
    dt = d["datatables"][0]
    assert dt["row_struct"] == "ScriptStruct'HeroStats'" and dt["columns"]["Health"] == {"min": 50, "max": 120, "rows": 2}
    assert "Boss" not in dt["columns"] and dt["columns"]["Speed.Min"]["max"] == 1.5
    side = json.loads((ws / "analysis" / "unreal_assets" / "datatables" / "DT_Stats.json").read_text(encoding="utf-8"))
    assert side["numbers"]["Guard"]["Health"] == 120
    bp = d["blueprints"][0]
    assert bp["parent"] == "HeroCharacter" and bp["native_parent"] and bp["functions"] == ["Jump"] and bp["variables"] == ["MaxAmmo"]
    assert d["script_modules"] == {"/Script/Game": {"objects": 2, "classes": 1}}
    md = (ws / "analysis" / "unreal_assets.md").read_text(encoding="utf-8")
    assert "DataTables (tunable numbers)" in md and "Native modules" in md


def test_unreal_assets_skips_without_inventory(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    r = analysis.run(ws, {"slug": "u", "engine": {"key": "unreal"}}, "unreal_assets")
    assert r["status"] == "skipped" and "unreal.inventory" in r["reason"]
    assert "unreal_assets" in [s.name for s in analysis.for_engine("unreal")]
