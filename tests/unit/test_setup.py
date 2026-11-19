import json

from gst import cmd_setup


def test_register_declarative_merges_and_backs_up(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"model": "opus", "enabledPlugins": {"other@x": True}}), encoding="utf-8")
    root = tmp_path / "game-study-toolkit"
    cmd_setup.register_declarative(root, settings)
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    assert data["enabledPlugins"]["other@x"] is True
    assert data["enabledPlugins"]["gst@gst-local"] is True
    assert data["extraKnownMarketplaces"]["gst-local"]["source"] == {"source": "directory", "path": root.as_posix()}
    assert list(settings.parent.glob("settings.json.bak-gs-*"))


def test_register_declarative_creates_file(tmp_path):
    settings = tmp_path / "new" / "settings.json"
    cmd_setup.register_declarative(tmp_path / "tk", settings)
    assert json.loads(settings.read_text(encoding="utf-8"))["enabledPlugins"]["gst@gst-local"]


def test_toolkit_root_finds_this_checkout():
    root = cmd_setup.toolkit_root()
    assert (root / ".claude-plugin" / "marketplace.json").is_file()


def test_marketplace_entries_match_the_plugin_names():
    """`claude plugin install gst@gst-local` resolves the marketplace entry name: it must be the plugin's name."""
    root = cmd_setup.toolkit_root()
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["name"] == "gst-local"
    for entry in market["plugins"]:
        manifest = root / entry["source"] / ".claude-plugin" / "plugin.json"
        assert json.loads(manifest.read_text(encoding="utf-8"))["name"] == entry["name"]
    assert "gst" in {e["name"] for e in market["plugins"]}


def test_install_ps1_never_disables_the_legacy_plugin_and_is_ascii():
    root = cmd_setup.toolkit_root()
    for name in ("install.ps1", "scripts/system-setup.ps1"):
        data = (root / name).read_bytes()
        assert data.isascii(), f"{name}: Windows PowerShell 5.1 reads a BOM-less script as ANSI"
    text = (root / "install.ps1").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "plugin disable" in line:
            assert line.lstrip().startswith(("Write-Host", "#")), line  # printed for the user, never run
    for switch in ("$DryRun", "SupportsShouldProcess", "$Plugin", "$EnableLongPaths", "$Tools", "Test-Admin"):
        assert switch in text


def test_doctor_reports_the_legacy_plugin_next_to_gst(monkeypatch, tmp_path):
    from gst import doctor

    claude = tmp_path / "user" / ".claude"
    (claude / "plugins").mkdir(parents=True)
    (claude / "settings.json").write_text(json.dumps({"enabledPlugins": {"game-study@game-study-local": True}}),
                                          encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    state = doctor.plugin_state()
    assert state["legacy"] == {"key": "game-study@game-study-local", "installed": True, "enabled": True}
    assert not state["installed"]
    report = {"gs_home": {"inside_git": None}, "tools": [], "long_paths": True, "anticheat": [], "user_bin_on_path": True,
              "plugin": state, "hooks": {"state": "ok"}}
    assert any("legacy plugin game-study@game-study-local is enabled" in m for _, m in doctor.findings(report))
    (claude / "settings.json").write_text(json.dumps({"enabledPlugins": {"game-study@game-study-local": True,
                                                                         "gst@gst-local": True}}), encoding="utf-8")
    report["plugin"] = doctor.plugin_state()
    assert any("both plugins are enabled" in m for _, m in doctor.findings(report))
