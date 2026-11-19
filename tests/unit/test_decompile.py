"""gst decompile dotnet: assembly discovery, selection, policy and the ilspycmd orchestration."""

import struct
from pathlib import Path

import pytest

from gst import policy
from gst.common import GsError
from gst.decompile import dotnet


def pe_bytes(managed: bool, pe32_plus: bool = False) -> bytes:
    """Minimal PE header: just enough for dotnet.is_managed (CLR data directory 14)."""
    buf = bytearray(512)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, 0x80)
    buf[0x80:0x84] = b"PE\0\0"
    opt = 0x80 + 24
    struct.pack_into("<H", buf, opt, 0x20B if pe32_plus else 0x10B)
    count_at, dirs_at = (opt + 108, opt + 112) if pe32_plus else (opt + 92, opt + 96)
    struct.pack_into("<I", buf, count_at, 16)
    if managed:
        struct.pack_into("<II", buf, dirs_at + 14 * 8, 0x2008, 0x48)
    return bytes(buf)


@pytest.fixture
def unity_install(tmp_path):
    managed = tmp_path / "Game" / "Game_Data" / "Managed"
    managed.mkdir(parents=True)
    for name in ("Assembly-CSharp", "Assembly-CSharp-firstpass", "UnityEngine.CoreModule", "mscorlib",
                 "Newtonsoft.Json", "GameLogic"):
        (managed / f"{name}.dll").write_bytes(pe_bytes(True))
    (managed / "GameLogic.pdb").write_bytes(b"pdb")
    (managed / "native_plugin.dll").write_bytes(pe_bytes(False))
    return tmp_path / "Game"


def test_is_managed_on_synthetic_pe(tmp_path):
    assert not dotnet.is_managed(Path(__file__))  # not a PE at all
    for plus in (False, True):
        (tmp_path / "m.dll").write_bytes(pe_bytes(True, plus))
        (tmp_path / "n.dll").write_bytes(pe_bytes(False, plus))
        assert dotnet.is_managed(tmp_path / "m.dll") and not dotnet.is_managed(tmp_path / "n.dll")


def test_find_and_select_game_assemblies(unity_install):
    found = dotnet.find_assemblies(unity_install)
    names = {a["name"]: a for a in found}
    assert "native_plugin" not in names
    game = sorted(a["name"] for a in found if a["game"])
    assert game == ["Assembly-CSharp", "Assembly-CSharp-firstpass", "GameLogic"]
    assert names["GameLogic"]["pdb"] and not names["Assembly-CSharp"]["pdb"]
    assert [a["name"] for a in dotnet.select(found, ["newtonsoft.json"])] == ["Newtonsoft.Json"]
    assert len(dotnet.select(found, everything=True)) == 6
    with pytest.raises(GsError, match="not a managed assembly"):
        dotnet.select(found, ["NoSuch"])


def test_command_flags(tmp_path):
    cmd = dotnet.command(Path("ilspycmd.exe"), tmp_path / "Managed" / "A.dll", tmp_path / "out", pdb=True)
    assert cmd[:6] == ["ilspycmd.exe", "-p", "--nested-directories", "--disable-updatecheck", "-lv", "Latest"]
    assert "--use-varnames-from-pdb" in cmd and cmd[-1].endswith("A.dll")
    assert cmd[cmd.index("-r") + 1] == str(tmp_path / "Managed")


def fake_run_one(calls):
    def run(exe, assembly, out_root):
        calls.append(assembly["name"])
        out = out_root / assembly["name"]
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{assembly['name']}.cs").write_text("class X {}", encoding="utf-8")
        return {"source": assembly["path"], "size": assembly["size"], "pdb": assembly["pdb"], "files": 1, "errors": 0,
                "exit_code": 0, "seconds": 0.0, "command": ["ilspycmd"]}
    return run


def test_decompile_orchestration_reuse_and_policy(unity_install, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(dotnet.tools, "bin_path", lambda tool: Path("ilspycmd.exe"))
    monkeypatch.setattr(dotnet.tools, "installed_version", lambda tool: "11.1.0.9782")
    monkeypatch.setattr(dotnet, "_run_one", fake_run_one(calls))
    ws = tmp_path / "ws"
    game = {"name": "Game", "install_dir": str(unity_install), "verdict": policy.OFFLINE_OK, "buildid": "42"}
    result = dotnet.decompile(ws, game)
    assert sorted(calls) == ["Assembly-CSharp", "Assembly-CSharp-firstpass", "GameLogic"]
    manifest = (ws / "decomp" / "cs" / "MANIFEST.json").read_text(encoding="utf-8")
    assert '"tool_version": "11.1.0.9782"' in manifest and '"sha256"' in manifest
    assert set(result["skipped"]) == {"UnityEngine.CoreModule", "mscorlib", "Newtonsoft.Json"}
    calls.clear()
    again = dotnet.decompile(ws, game)
    assert calls == [] and all(v["reused"] for v in again["assemblies"].values())
    with pytest.raises(GsError, match="rule 2"):
        dotnet.decompile(ws, {**game, "verdict": policy.NO_RUNTIME})
