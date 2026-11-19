import hashlib
import re
import zipfile

import pytest

from gst import tools
from gst.common import GsError
from gst.tools import installer

VALID_POLICY = {"runtime", "injection", "kernel-driver"}


def test_catalog_entries_are_complete():
    catalog = tools.load_catalog()
    assert "ghidra" in catalog and "ilspycmd" in catalog
    for tool in catalog.values():
        assert tool.kind in tools.KINDS, tool.id
        assert tool.bins or tool.kind == "python", f"{tool.id}: no bins"
        assert tool.profiles, f"{tool.id}: no profiles"
        assert tool.title and tool.purpose and tool.home, tool.id
        assert set(tool.policy) <= VALID_POLICY, tool.id
        for dep in tool.requires:
            assert dep in catalog, f"{tool.id} requires unknown {dep}"
        if tool.kind == "winget":
            assert tool.winget and tool.version, tool.id
        if tool.kind in {"zip", "file"}:
            assert tool.url.startswith("https://"), tool.id
            assert re.fullmatch(r"[0-9a-f]{64}|github|tofu", tool.sha256), tool.id
        if tool.kind == "dotnet-tool":
            assert tool.package and tool.version, tool.id
        if tool.kind == "guided":
            assert tool.steps, tool.id
        if tool.version_re:
            re.compile(tool.version_re)


def test_winget_ids_unique():
    ids = [t.winget for t in tools.load_catalog().values() if t.winget]
    assert len(ids) == len(set(ids))


def _make_zip(path, root="pkg-1.0"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{root}/bin/tool.exe", b"MZ fake")
        zf.writestr(f"{root}/README.txt", "hi")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_tool(url, sha, **kw):
    return tools.Tool(id="faketool", title="Fake", kind="zip", profiles=["core"], bins=["bin/tool.exe"],
                      version="1.0", url=url, sha256=sha, root="pkg-1.0", home="https://example.org",
                      purpose="test", **kw)


def test_zip_install_verifies_hash_and_records_lock(tmp_path):
    archive = tmp_path / "pkg.zip"
    digest = _make_zip(archive)
    tool = _fake_tool(archive.as_uri(), digest)
    installer._install_download(tool)
    exe = tool.install_dir / "bin" / "tool.exe"
    assert exe.exists()
    lock = tools.read_lock()["faketool"]
    assert lock["sha256"] == digest and lock["verified"] is True
    assert lock["bins"]["bin/tool.exe"].endswith("tool.exe")


def test_zip_install_rejects_bad_hash(tmp_path):
    archive = tmp_path / "pkg.zip"
    _make_zip(archive)
    tool = _fake_tool(archive.as_uri(), "0" * 64)
    with pytest.raises(GsError, match="SHA-256 mismatch"):
        installer._install_download(tool)
    assert not tool.install_dir.exists()


def test_extract_rejects_path_traversal(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../outside.txt", "x")
    with pytest.raises(GsError, match="unsafe path"):
        installer.extract_zip(evil, tmp_path / "out")


def test_github_digest_parsing(monkeypatch):
    html = ('<li><a href="/vgmstream/vgmstream/releases/download/r2117/vgmstream-win64.zip">vgmstream-win64.zip</a>'
            '<span class="Truncate">sha256:' + "ab" * 32 + "</span></li>")
    monkeypatch.setattr(installer, "fetch_text", lambda url, retries=3: html)
    url = "https://github.com/vgmstream/vgmstream/releases/download/r2117/vgmstream-win64.zip"
    assert installer.github_digest(url) == "ab" * 32
    assert installer.github_digest("https://example.org/x.zip") is None


def test_bin_path_uses_search_globs(tmp_path):
    exe = tmp_path / "Program Files" / "Thing-7.1" / "thing.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    tool = tools.Tool(id="thing", title="Thing", kind="winget", profiles=["core"], bins=["thing.exe"],
                      winget="X.Thing", version="7.1", search=[str(tmp_path / "Program Files" / "Thing-*" / "thing.exe")],
                      home="https://example.org", purpose="test")
    assert tools.bin_path(tool) == exe


def test_same_version_normalisation():
    assert tools._same_version("21.0.12", "21.0.12.101")
    assert tools._same_version("7.1.2-31", "7.1.2.31")
    assert not tools._same_version("6.0.0", "6.1.0")


def test_status_missing_for_absent_tool(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    tool = tools.Tool(id="nothing", title="Nothing", kind="winget", profiles=["core"], bins=["nothing-here.exe"],
                      winget="No.Such", version="1", home="https://example.org", purpose="test")
    assert tools.status(tool)["state"] == "missing"


def test_python_tools_match_pyproject_extras():
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    extras = {req for reqs in pyproject["project"]["optional-dependencies"].values() for req in reqs}
    for tool in tools.load_catalog().values():
        if tool.kind == "python":
            assert tool.modules and tool.requirements, tool.id
            missing = set(tool.requirements) - extras
            assert not missing, f"{tool.id}: {missing} not pinned in pyproject extras"


def test_python_tool_status_uses_the_running_environment(monkeypatch):
    tool = tools.Tool(id="pytest", title="pytest", kind="python", profiles=[], bins=[], version="8",
                      package="pytest>=8", modules=["pytest"])
    st = tools.status(tool)
    assert st["state"] in {"ok", "other-version"} and st["version"] and st["path"]
    missing = tools.Tool(id="nope", title="nope", kind="python", profiles=[], bins=[], version="1",
                         package="definitely-not-installed-pkg==1", modules=["definitely_not_installed_pkg"])
    assert tools.status(missing)["state"] == "missing"
