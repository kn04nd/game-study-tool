"""gst migrate on a synthetic gs home: dry run writes nothing, the real run is additive and idempotent, archives
and stray folders are handled, the v1 reader (and the gs keys) still work afterwards."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from gst import cli, workspace
from gst.compat import legacy

INSTALL_FILES = {"Old Game.exe": b"MZ" + b"\0" * 64}


def tree(root):
    """(relpath, kind, size, mtime_ns) of every file and folder under root: a byte- and mtime-exact snapshot."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.stat(p)
            out.append((os.path.relpath(p, root).replace("\\", "/"), "d" if name in dirnames else "f",
                        0 if name in dirnames else st.st_size, st.st_mtime_ns))
    return sorted(out)


def write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def make_v1(home, slug, install, *, buildid="100"):
    """A workspace as the legacy gs leaves it: v1 layout, outputs, notes, logs (all synthetic)."""
    ws = home / slug
    for sub in legacy.V1_SUBDIRS:
        (ws / sub).mkdir(parents=True)
    game = {"gs_workspace": 1, "slug": slug, "name": "Old Game", "store": "steam", "appid": "4242", "buildid": buildid,
            "install_dir": str(install), "engine": {"key": "unity-mono", "label": "Unity (Mono)", "version": "2022.3"},
            "verdict": "offline-ok", "phase": "decompile"}
    write(ws / "game.json", json.dumps(game, indent=2) + "\n")
    write(ws / "STUDY.md", "# Study\n\n### 2026-01-01\n- 10:00 recon\n")
    write(ws / "FACTS.md", "| fact | evidence |\n| --- | --- |\n| a | b |\n")
    write(ws / "ARCHITECTURE.md", "# Architecture\n")
    write(ws / "recon" / "recon.json", "{}")
    write(ws / "recon" / "RECON.md", "# Recon\n")
    write(ws / "decomp" / "cs" / "Game" / "Player.cs", "class Player {}\n")
    write(ws / "decomp" / "cs" / "Game" / "Enemy.cs", "class Enemy {}\n")
    write(ws / "index" / "code.db", b"SQLite format 3\0" + b"\0" * 100)
    write(ws / "assets" / "audio" / "sfx" / "jump.wav", b"RIFF" + b"\0" * 40)
    write(ws / "assets" / "inventory.jsonl", '{"path": "a"}\n')
    write(ws / "dumps" / "rea" / "evidence.json", "{}")
    write(ws / "notes" / "code" / "save.md", "# Save\n")
    write(ws / "logs" / "ilspy.log", "ok\n")
    return ws


@pytest.fixture
def gs_home(isolated_env, tmp_path, monkeypatch):
    """A gs home like D:/game-study: workspaces, a _selftest one, archives, home files, a stray sibling,
    a folder that is not a workspace and one with a broken game.json."""
    home = isolated_env["home"]
    install = tmp_path / "SteamLibrary" / "Old Game"
    for name, data in INSTALL_FILES.items():
        write(install / name, data)
    make_v1(home, "oldgame", install)
    make_v1(home, "_selftest-oldgame", install)
    write(home / "oldgame.zip", b"PK\x03\x04" + b"\0" * 30)
    write(home / "INDEX.md", "# Studies\n")
    write(home / "registry.json", json.dumps({"games": {"oldgame": {"workspace": str(home / "oldgame")}}}))
    stray = home / "oldgame@200"  # what an older `gst study --dry-run` left on a new build
    for sub in legacy.V1_SUBDIRS + legacy.V2_SUBDIRS:
        (stray / sub).mkdir(parents=True)
    write(stray / "game.json", json.dumps({"gs_workspace": 1, "gst_workspace": 2, "slug": "oldgame@200", "name": "Old Game",
                                           "buildid": "200", "install_dir": str(install),
                                           "engine": {"key": "unity-mono"}}))
    write(stray / "recon" / "recon.json", "{}")
    write(stray / "jobs" / "plan.json", "{}")
    write(home / "junk" / "readme.txt", "not a study")
    write(home / "_library" / "library.json", "{}")
    monkeypatch.chdir(tmp_path)
    return home


def run_json(capsys, *argv):
    code = cli.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def by_slug(result):
    return {p["slug"]: p for p in result["plans"]}


def test_dry_run_writes_nothing_and_reports_every_entry(gs_home, capsys, tmp_path):
    before = tree(gs_home)
    report = tmp_path / "out" / "migrate.md"
    code, out = run_json(capsys, "migrate", "--all", "--dry-run", "--report", str(report))
    assert tree(gs_home) == before  # read-only: not one file, folder or mtime changed
    assert code == 0 and out["dry_run"]
    plans = by_slug(out)
    assert set(plans) == {"oldgame", "_selftest-oldgame", "oldgame@200"}
    old = plans["oldgame"]
    assert old["status"] == "would-migrate" and old["create_dirs"] == ["jobs", "analysis"] and old["mark_game_json"]
    assert old["import_as_legacy_jobs"] == {"recon": 2, "decomp/cs": 2, "index/code.db": 1, "assets/audio": 1,
                                            "assets/inventory.jsonl": 1, "dumps/rea": 1}
    cats = {u["relpath"]: (u["extractor"], u["category"]) for u in old["units"]}
    assert cats["decomp/cs"] == ("legacy.decomp", "code") and cats["assets/audio"] == ("legacy.assets", "audio")
    assert cats["assets/inventory.jsonl"][1] == "data" and cats["recon"] == ("legacy.recon", "other")
    assert plans["_selftest-oldgame"]["status"] == "would-migrate"
    stray = plans["oldgame@200"]
    assert stray["status"] == "stray" and "empty sibling of oldgame" in stray["problems"][0] and not stray["units"]
    ignored = {i["name"]: i["reason"] for i in out["ignored"]}
    assert ignored["oldgame.zip"].startswith("archive") and "without game.json" in ignored["junk"]
    assert set(ignored) == {"oldgame.zip", "INDEX.md", "registry.json", "junk", "_library"}
    text = report.read_text(encoding="utf-8")
    assert "# gst migrate: dry run" in text and "## oldgame@200 (stray)" in text and "`decomp/cs`" in text
    assert cli.main(["migrate", "--all", "--dry-run"]) == 0
    human = capsys.readouterr().out
    assert "would import 6 outputs" in human and "INDEX.md: index of the studies" in human and "(dry run: nothing written)" in human
    assert tree(gs_home) == before


def test_migrate_is_additive_idempotent_and_keeps_the_v1_reader(gs_home, capsys):
    ws = gs_home / "oldgame"
    v1_before = {r for r in tree(ws) if r[1] == "f" and r[0] not in ("game.json", "STUDY.md")}
    stray_before, zip_before = tree(gs_home / "oldgame@200"), (gs_home / "oldgame.zip").stat()
    registry_before = (gs_home / "registry.json").read_bytes()
    code, out = run_json(capsys, "migrate", "--all")
    assert code == 0
    plans = by_slug(out)
    assert plans["oldgame"]["status"] == "migrated" and plans["oldgame"]["written"]["imported"] == 6
    assert plans["oldgame@200"]["status"] == "stray"
    # additive: every v1 file is untouched, the stray, the archive and the registry too
    assert v1_before <= set(tree(ws))
    assert tree(gs_home / "oldgame@200") == stray_before
    assert (gs_home / "oldgame.zip").stat().st_mtime_ns == zip_before.st_mtime_ns
    assert (gs_home / "registry.json").read_bytes() == registry_before
    game = json.loads((ws / "game.json").read_text(encoding="utf-8"))
    assert game["gs_workspace"] == 1 and game["gst_workspace"] == 2 and game["gst_migrated"]["from_version"] == 1
    assert game["phase"] == "decompile" and game["engine"]["key"] == "unity-mono"  # gs keys unchanged
    assert (ws / "analysis").is_dir() and (ws / "jobs" / "COVERAGE.md").is_file()
    assert "gst migrate: 6 legacy outputs" in (ws / "STUDY.md").read_text(encoding="utf-8")
    with sqlite3.connect(ws / "jobs" / "jobs.db") as con:
        rows = con.execute("SELECT extractor, unit_id, status, tool, category FROM jobs ORDER BY stage, seq").fetchall()
    assert {r[2] for r in rows} == {"done"} and {r[3] for r in rows} == {"legacy"}
    assert ("legacy.decomp", "decomp/cs", "done", "legacy", "code") in rows
    # the v1 reader (and what the legacy gs checks: a game.json with gs_workspace) still opens it
    found = legacy.open_workspace(ws)
    assert found.version == 2 and found.slug == "oldgame" and not found.missing_v2
    assert workspace.load(ws)["gs_workspace"] == 1
    assert workspace.current(slug="oldgame")[0] == ws
    # idempotent: a second run writes nothing at all
    after_first = tree(gs_home)
    code, again = run_json(capsys, "migrate", "--all")
    assert code == 0 and {p["slug"]: p["status"] for p in again["plans"]} == {
        "oldgame": "up-to-date", "_selftest-oldgame": "up-to-date", "oldgame@200": "stray"}
    assert tree(gs_home) == after_first


def test_new_legacy_output_after_migrate_is_imported_as_changed(gs_home, capsys):
    ws = gs_home / "oldgame"
    assert cli.main(["migrate", "oldgame"]) == 0
    capsys.readouterr()
    write(ws / "decomp" / "cs" / "Game" / "Boss.cs", "class Boss {}\n")  # the old gs kept working here
    write(ws / "dumps" / "maps" / "level1.json", "{}")
    code, out = run_json(capsys, "migrate", "oldgame", "--dry-run")
    actions = {u["relpath"]: u["action"] for u in out["plans"][0]["units"]}
    assert actions["decomp/cs"] == "changed" and actions["dumps/maps"] == "new" and actions["recon"] == "kept"
    code, out = run_json(capsys, "migrate", "oldgame")
    assert out["plans"][0]["written"] == {"created": [], "imported": 1, "updated": 1, "game_json": False,
                                          "coverage": str(ws / "jobs" / "COVERAGE.md")}
    with sqlite3.connect(ws / "jobs" / "jobs.db") as con:
        meta = json.loads(con.execute("SELECT meta FROM jobs WHERE unit_id='decomp/cs'").fetchone()[0])
        maps = con.execute("SELECT category FROM jobs WHERE unit_id='dumps/maps'").fetchone()[0]
    assert meta["files"] == 3 and maps == "map"


def test_status_coverage_jobs_and_workspace_list_open_migrated_and_v1(gs_home, capsys):
    code, listing = run_json(capsys, "workspace")
    rows = {r["folder"]: r for r in listing["workspaces"]}
    assert rows["oldgame"]["version"] == 1 and rows["oldgame"]["registry"] and rows["oldgame"]["install_found"]
    assert rows["oldgame@200"]["stray_of"] == "oldgame" and not rows["_selftest-oldgame"]["registry"]
    for slug in ("oldgame", "_selftest-oldgame", "oldgame@200"):  # v1 and v2 alike, before the migration
        assert run_json(capsys, "status", slug)[1]["name"] == "Old Game"
    assert cli.main(["migrate", "--all"]) == 0
    capsys.readouterr()
    for slug in ("oldgame", "_selftest-oldgame"):
        assert run_json(capsys, "status", slug)[1]["files"]["decomp"] == 2
        code, cov = run_json(capsys, "coverage", slug)
        assert code == 0 and cov["categories"]["code"]["files_out"] == 4  # decomp/cs (2 files), index/code.db, dumps/rea
        assert cov["categories"]["audio"]["done"] == 1
    assert {r["folder"]: r["migrated"] for r in run_json(capsys, "workspace")[1]["workspaces"]} == {
        "oldgame": True, "_selftest-oldgame": True, "oldgame@200": False}


def test_broken_game_json_is_an_error_but_the_others_migrate(gs_home, capsys):
    write(gs_home / "broken" / "game.json", "{not json")
    code, out = run_json(capsys, "migrate", "--all", "--dry-run")
    assert code == 1
    plans = by_slug(out)
    assert plans["broken"]["status"] == "error" and "not valid JSON" in plans["broken"]["problems"][0]
    assert plans["oldgame"]["status"] == "would-migrate"


def test_a_workspace_studied_by_gst_keeps_its_jobs(gs_home, capsys):
    from gst.pipeline.jobs import JobsDB

    ws = gs_home / "oldgame"
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        db.upsert(extractor="code.dotnet", unit_id="Assembly-CSharp.dll", relpaths=[], inputs=[], fingerprint="f",
                  buildid="100", category="code", resource="heavy", stage=20, seq=1, size=1, meta={})
    (ws / "analysis").mkdir()
    code, out = run_json(capsys, "migrate", "oldgame", "--dry-run")
    plan = out["plans"][0]
    assert plan["import_as_legacy_jobs"] == {} and "gst study already ran here" in plan["notes"][0]
    assert plan["status"] == "would-migrate" and plan["mark_game_json"]  # only the v2 marker is missing
    assert cli.main(["migrate", "oldgame"]) == 0
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        assert [r["extractor"] for r in db.rows()] == ["code.dotnet"]


def test_missing_install_and_report_json(gs_home, capsys, tmp_path):
    game = json.loads((gs_home / "oldgame" / "game.json").read_text(encoding="utf-8"))
    game["install_dir"] = str(tmp_path / "uninstalled")
    (gs_home / "oldgame" / "game.json").write_text(json.dumps(game), encoding="utf-8")
    report = tmp_path / "migrate.json"
    code, out = run_json(capsys, "migrate", "oldgame", "--dry-run", "--report", str(report))
    assert any("install folder not found" in p for p in out["plans"][0]["problems"])
    assert json.loads(report.read_text(encoding="utf-8"))["plans"][0]["slug"] == "oldgame"


@pytest.mark.parametrize("sub, name, category", [
    ("assets", "unreal", "other"), ("dumps", "rea", "code"), ("dumps", "audio_scan.json", "audio"),
    ("assets", "textures", "texture"), ("assets", "models", "mesh"), ("dumps", "maps", "map"),
    ("dumps", "game.dat.strings.txt", "other"), ("assets", "inventory.jsonl", "data"), ("decomp", "ghidra", "code"),
    ("index", "code.db", "code"), ("recon", "recon", "other"), ("assets", "particles", "material_shader"),
])
def test_category_of_matches_whole_name_tokens(sub, name, category):
    assert legacy.category_of(sub, name) == category
