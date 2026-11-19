"""Cross-game library (gst lib build / compare / search) over tiny synthetic workspaces (Unity, Godot, Unreal)."""

import json
import sqlite3
from pathlib import Path

import pytest

from gst import cli, notes, notetemplates
from gst.common import GsError
from gst.library import build, compare, db_path, facets, search


def analysis(ws: Path, name: str, data, summary: dict | None = None) -> None:
    (ws / "analysis").mkdir(parents=True, exist_ok=True)
    (ws / "analysis" / f"{name}.json").write_text(json.dumps(
        {"header": {"analysis": name, "version": 1, "generated_at": "2026-01-01T00:00:00"}, "summary": summary or {},
         "data": data}), encoding="utf-8")


def workspace(home: Path, slug: str, name: str, engine: str, *, net_voice=True, middleware=("Photon",)) -> Path:
    ws = home / slug
    (ws / "recon").mkdir(parents=True)
    (ws / "game.json").write_text(json.dumps({"gs_workspace": 1, "gst_workspace": 2, "slug": slug, "name": name,
                                              "buildid": "42", "engine": {"key": engine, "label": engine, "version": "1.0"}}),
                                  encoding="utf-8")
    recon = {"middleware": list(middleware), "scanned_at": "2026-01-01T00:00:00"}
    if net_voice:
        recon["net_voice"] = [{"name": "Photon Voice", "kind": "voice", "evidence": ["Game_Data/Managed/PhotonVoice.dll"]}]
    (ws / "recon" / "recon.json").write_text(json.dumps(recon), encoding="utf-8")
    return ws


def unity_ws(home: Path) -> Path:
    ws = workspace(home, "alpha", "Alpha Game", "unity-mono")
    smr = lambda prefab, bones, infl: {"prefab": prefab, "node": f"/{prefab}/Body", "bones": bones, "deform_bones": bones,
                                       "root_bone": "Hips", "detail": f"analysis/rig/res/{prefab}__1.json",
                                       "mesh": {"mesh": prefab, "verts": 900, "tris": 1200, "bindposes": bones,
                                                "max_influences": infl, "blendshapes": 2}}
    analysis(ws, "rig", {"smr": [smr("Hero", 50, 4), smr("Crate", 3, 1)],
                         "constraints": [{"prefab": "Hero", "node": "/Hero/Rig", "type": "TwoBoneIKConstraint"}]})
    analysis(ws, "avatar", {"avatars": [{"name": "HeroAvatar", "humanoid": True, "skeleton_nodes": 60, "human_bones_mapped": 22,
                                         "root_motion_bone": None, "animators": ["Hero:/Hero"]}]})
    analysis(ws, "joints", {
        "prefabs": [{"file": "res.assets", "root": "Hero", "family": "Hero", "counts": {"ConfigurableJoint": 2},
                     "total_mass": 70.0, "bodies": [{"go": "Hips"}, {"go": "Leg"}, {"go": "Arm"}],
                     "joints": [{"type": "ConfigurableJoint", "path": "/Hero/Hips/Leg", "connected": "Hips", "body": {"mass": 10.0},
                                 "drives": {"AngularX": {"spring": 2500.0, "damper": 250.0}}},
                                {"type": "ConfigurableJoint", "path": "/Hero/Hips/Arm", "connected": "Hips", "body": {"mass": 5.0},
                                 "drives": {"AngularX": {"spring": 1500.0, "damper": 150.0}}}]}],
        "ragdolls": [{"file": "res.assets", "prefab": "Hero", "classes": ["HeroRagdoll"],
                      "detail": "analysis/joints/res/Hero__1.json"}]})
    analysis(ws, "animation", {"clips": [
        {"name": "Run", "length": 0.8, "fps": 30.0, "loop": True, "kind": "humanoid", "bindings": 40, "controllers": ["Hero"],
         "events": [{"time": 0.1, "function": "Step"}, {"time": 0.5, "function": "Step"}], "root": {"root_motion": True}},
        {"name": "Idle", "length": 2.0, "fps": 30.0, "loop": True, "kind": "humanoid", "bindings": 40, "events": []}]})
    analysis(ws, "controller", {"controllers": [{"name": "Hero", "kind": "controller", "file": "res.assets",
                                                 "counts": {"params": 4, "layers": 1, "states": 6, "transitions": 9,
                                                            "blend_trees": 1, "clips": 2}}]})
    analysis(ws, "audio_catalog", {"events": [
        {"name": "Hero.step", "kind": "sound", "category": "hero", "defined_at": "decomp/cs/Hero.cs:10",
         "files": [{"name": "step1", "file": "assets/audio/step1.wav"}], "used_by": [{"where": "decomp/cs/Hero.cs:40"}]},
        {"name": "Music.theme", "kind": "music", "category": "music", "files": [{"name": "theme", "file": None}], "used_by": []}]})
    analysis(ws, "shaders", {"shaders": [{"name": "Custom/Toon", "family": "Custom", "materials": 12, "passes": 3,
                                          "keywords": ["A"], "signatures": ["amplify"]}]})
    analysis(ws, "systems", {"systems": [{"label": "Física", "types": 12, "files": 8, "score": 30,
                                          "top": [{"name": "HeroRagdoll", "path": "decomp/cs/HeroRagdoll.cs", "line": 3, "refs": 7}]}]})
    analysis(ws, "dcc", {"families": {"blender": {"verdict": "present", "strong": 10, "weak": 0, "ambiguous": 1,
                                                  "rules": {"dup_suffix_.NNN": 10}},
                                      "maya": {"verdict": "absent", "strong": 0, "weak": 0, "ambiguous": 0, "rules": {}}}})
    (ws / "assets" / "audio").mkdir(parents=True)
    (ws / "assets" / "audio" / "files.jsonl").write_text(
        "\n".join(json.dumps({"file": f"assets/audio/s{i}.wav", "ext": ".wav", "fmt": "PCM", "channels": 1, "rate": 44100,
                              "seconds": 1.5, "size": 1000}) for i in range(3)) + "\n", encoding="utf-8")
    (ws / "notes" / "logic").mkdir(parents=True)
    (ws / "notes" / "logic" / "ragdoll.md").write_text(
        "# Ragdoll do herói\n\n## Como funciona\n\nO ragdoll ativo usa física com molas.\n\n| a | b |\n|---|---|\n| x | y |\n",
        encoding="utf-8")
    (ws / "FACTS.md").write_text("# Fatos\n\n| fato | evidência |\n|---|---|\n| massa do herói 70 | joints |\n", encoding="utf-8")
    (ws / "ARCHITECTURE.md").write_text("# Arquitetura\n\nVoz por proximidade com Photon Voice.\n", encoding="utf-8")
    return ws


def godot_ws(home: Path) -> Path:
    ws = workspace(home, "beta", "Beta", "godot", middleware=("XAudio2",))
    analysis(ws, "gltf_summary", {"files": [
        {"file": "assets/godot/project/models/char.glb", "skins": [24], "verts": 700, "tris": 800,
         "infl_hist": {"1": 10, "2": 4, "4": 1}, "meshes": [{"name": "char", "targets": 0}],
         "anims": [{"name": "Walk", "channels": 48, "keys": 300, "dur": 1.0, "dt_min": 0.0333}]},
        {"file": "assets/godot/project/models/rock.glb", "skins": [], "anims": []}]})
    analysis(ws, "godot_animation", {
        "animations": [{"file": "res://door.tscn", "name": "open", "length": 0.5, "loop_mode": 0, "track_count": 2, "keys": 4,
                        "methods": {"play_sound": 1}}],
        "trees": [{"file": "res://player.tscn", "node": "AnimationTree", "stats": {
            "states": 5, "transitions": 12, "node_types": {"AnimationNodeBlend2": 2, "AnimationNodeStateMachine": 1},
            "animations": {"Walk": 1, "Idle": 1}}}],
        "skeletons": [{"file": "res://player.tscn", "node": "Armature/Skeleton3D", "bones": 24, "roots": ["root"]}],
        "ragdolls": [{"file": "res://ragdoll.tscn", "physical_bones": 12, "joint_nodes": 11,
                      "joint_node_types": {"Generic6DOFJoint3D": 11}, "builtin_joint_types": {"none": 12}, "mass_total": 3.0,
                      "bones": [{"bone_name": "head", "script": "res://Bone.cs"}]}],
        "joints": [{"file": "res://ragdoll.tscn", "path": "Head/Joint", "type": "Generic6DOFJoint3D", "node_a": "Head",
                    "node_b": "Chest", "settings": {"angular_limit_x/upper_angle": 0.5}}]})
    analysis(ws, "audio_catalog", {"events": [{"name": "jump", "kind": "sound", "category": "player",
                                               "files": [{"name": "jump", "file": "assets/audio/jump.wav"}], "used_by": []}]})
    (ws / "notes" / "assets").mkdir(parents=True)
    (ws / "notes" / "assets" / "modelos.md").write_text("# Modelos\n\nEsqueleto de 24 ossos e física simples.\n", encoding="utf-8")
    return ws


def unreal_ws(home: Path) -> Path:
    ws = workspace(home, "gamma", "Gamma", "unreal", net_voice=False, middleware=("NVIDIA_PhysX",))
    analysis(ws, "unreal_assets", {"guessed": {"SkeletalMesh": 40, "Skeleton": 9, "PhysicsAsset": 7, "SoundCue": 300,
                                               "AnimSequence": 120, "Material": 80}})
    return ws


@pytest.fixture
def lib(isolated_env):
    home = isolated_env["home"]
    wss = {"alpha": unity_ws(home), "beta": godot_ws(home), "gamma": unreal_ws(home)}
    (home / "registry.json").write_text(json.dumps({"games": {s: {"workspace": str(p)} for s, p in wss.items()}}),
                                        encoding="utf-8")
    return home, wss


def dump(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'docs_%' "
                                             "AND name != 'meta' ORDER BY name")]
        return {t: sorted(map(repr, conn.execute(f"SELECT * FROM {t}").fetchall())) for t in tables}
    finally:
        conn.close()


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


# --- build ---------------------------------------------------------------------------------------------

def test_build_fills_every_facet_with_provenance(lib):
    home, wss = lib
    before = {k: snapshot(p) for k, p in wss.items()}
    result = build.build(home)
    assert [g["key"] for g in result["games"]] == ["alpha", "beta", "gamma"]
    assert {k: snapshot(p) for k, p in wss.items()} == before  # workspaces are only read
    assert sorted(p.name for p in (home / "_library").iterdir()) == ["library.db"]
    conn = sqlite3.connect(db_path(home))
    conn.row_factory = sqlite3.Row
    rows = lambda sql, *a: conn.execute(sql, a).fetchall()
    rig = rows("SELECT * FROM rigs WHERE game = 'alpha' ORDER BY id")
    assert [(r["bones"], r["max_influences"], r["source"], r["ref"]) for r in rig] == [
        (50, 4, "analysis/rig.json", "data.smr[0]"), (3, 1, "analysis/rig.json", "data.smr[1]")]
    assert [(r["kind"], r["bones"], r["ref"]) for r in rows("SELECT * FROM rigs WHERE game = 'beta' ORDER BY id")] == [
        ("gltf-skin", 24, "data.files[0].skins[0]"), ("godot-skeleton", 24, "data.skeletons[0]")]
    assert rows("SELECT humanoid, human_bones FROM avatars")[0][:] == (1, 22)
    assert rows("SELECT type FROM constraints")[0][0] == "TwoBoneIKConstraint"
    rag = {r["game"]: r for r in rows("SELECT * FROM ragdolls")}
    assert (rag["alpha"]["bodies"], rag["alpha"]["joints"], rag["alpha"]["mass_total"]) == (3, 2, 70.0)
    assert rag["alpha"]["ref"] == "data.ragdolls[0] + data.prefabs[0]"
    assert (rag["beta"]["bodies"], rag["beta"]["joints"], json.loads(rag["beta"]["joint_types"])) == (12, 11, {"Generic6DOFJoint3D": 11})
    clips = {(r["game"], r["name"]): r for r in rows("SELECT * FROM clips")}
    assert clips[("alpha", "Run")]["events"] == 2 and clips[("alpha", "Run")]["root_motion"] == 1
    assert clips[("beta", "Walk")]["fps"] == pytest.approx(30.03, abs=0.01) and clips[("beta", "open")]["events"] == 1
    assert {r["kind"] for r in rows("SELECT kind FROM anim_graphs")} == {"unity-controller", "godot-tree"}
    audio = {r["name"]: r for r in rows("SELECT * FROM audio")}
    assert (audio["Music.theme"]["clips"], audio["Music.theme"]["files"]) == (1, 0)
    assert rows("SELECT files, seconds FROM audio_files")[0][:] == (3, 4.5)
    assert rows("SELECT count FROM asset_counts WHERE game = 'gamma' AND type = 'PhysicsAsset'")[0][0] == 7
    mw = {(r["game"], r["name"], r["kind"]) for r in rows("SELECT * FROM middleware")}
    assert ("alpha", "Photon Voice", "voice") in mw and ("gamma", "NVIDIA_PhysX", "sdk") in mw
    for table in facets.TABLES:
        assert not rows(f"SELECT 1 FROM {table} WHERE source IS NULL OR ref IS NULL"), table
    srcs = {(r["game"], r["path"]) for r in rows("SELECT * FROM sources")}
    assert ("alpha", "analysis/rig.json") in srcs and ("alpha", "assets/audio/files.jsonl") in srcs
    conn.close()


def test_build_is_idempotent(lib):
    home, _ = lib
    build.build(home)
    first = dump(db_path(home))
    build.build(home)
    assert dump(db_path(home)) == first
    assert not list((home / "_library").glob("*.partial-*"))


def test_missing_analyses_are_gaps_not_errors(lib):
    home, wss = lib
    (wss["alpha"] / "analysis" / "joints.json").write_text("{not json", encoding="utf-8")  # broken = absent
    result = build.build(home)
    gaps = {(g["game"], g["facet"], g["analysis"], g["kind"]) for g in result["gaps"]}
    assert ("alpha", "joints", "joints", "missing") in gaps
    assert ("beta", "systems", "systems", "missing") in gaps and ("beta", "dcc", "dcc", "missing") in gaps
    assert ("beta", "shaders", "godot_shaders", "missing") in gaps and ("beta", "rigs", None, "partial") in gaps
    assert ("gamma", "rigs", None, "partial") in gaps and ("gamma", "middleware", "recon", "partial") in gaps
    assert ("gamma", "audio", "audio_catalog", "missing") in gaps
    reason = next(g["reason"] for g in result["gaps"] if g["game"] == "beta" and g["analysis"] == "systems")
    assert "gst analyze systems --game beta" in reason
    assert not any(g["game"] == "alpha" and g["facet"] == "rigs" for g in result["gaps"])


def test_games_filter_extra_workspaces_and_legacy(lib, tmp_path):
    home, _ = lib
    other = tmp_path / "other-home"
    delta = workspace(other, "delta", "Delta", "unity-il2cpp")
    analysis(delta, "rig", {"smr": [{"prefab": "Bus", "bones": 7, "mesh": {}}]})
    legacy = tmp_path / "old-gs"
    old = legacy / "alpha"
    (old / "notes" / "logic").mkdir(parents=True)
    (old / "game.json").write_text(json.dumps({"gs_workspace": 1, "slug": "alpha", "name": "Alpha Game",
                                               "engine": {"key": "unity-mono"}}), encoding="utf-8")
    (old / "notes" / "logic" / "voz.md").write_text("# Voz\n\nVoz por proximidade via Dissonance.\n", encoding="utf-8")
    result = build.build(home, games=["alpha", "delta"], extra=[str(other)], legacy=[str(legacy)])
    assert [(g["key"], g["origin"]) for g in result["games"]] == [("alpha", "registry"), ("delta", "extra"),
                                                                  ("legacy:alpha", "legacy")]
    assert result["games"][2]["rows"] == {} and result["games"][2]["docs"] == 2
    hits = search.search("Dissonance", home=home)
    assert [(h["game"], h["path"], h["line"]) for h in hits] == [("legacy:alpha", "notes/logic/voz.md", 3)]
    assert search.search("Dissonance", home=home, games=["alpha"])[0]["file"] == f"{old.as_posix()}/notes/logic/voz.md"
    with pytest.raises(GsError, match="not in the library"):
        compare.compare("rigs", ["legacy:alpha"], home=home)  # legacy workspaces are search-only


# --- compare -------------------------------------------------------------------------------------------

def test_compare_rigs_writes_a_sourced_ptbr_table(lib):
    home, _ = lib
    build.build(home)
    result = compare.compare("rigs", ["alpha", "beta", "gamma"], home=home)
    path = Path(result["path"])
    assert path == home / "_library" / "compare" / "rigs-alpha_beta_gamma.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Comparativo: rigs e esqueletos (Alpha Game, Beta, Gamma)")
    assert "| dimensão | Alpha Game | Beta | Gamma |" in text
    assert "| ossos por rig (mín / mediana / máx) | 3 / 26.5 / 50 | 24 / 24 / 24 |" in text
    assert "| Avatar (humanoid / generic) | 1 / 0 (HeroAvatar 22 ossos humanos) |" in text
    assert "SkeletalMesh 40, Skeleton 9" in text and "blender [I]" in text
    assert "`alpha:analysis/rig.json` `data.smr[0]`" in text and "detalhe `alpha:analysis/rig/res/Hero__1.json`" in text
    lacunas = text.split("## Não verificado / lacunas", 1)[1]
    assert "**Gamma** (parcial): ossos só dos Skeleton/SkeletalMesh exportados em JSON" in lacunas
    assert "**Beta** (falta): `analysis/dcc.json` ausente" in lacunas  # the DCC row's gap is listed too
    assert {g["game"] for g in result["gaps"]} == {"beta", "gamma"}
    assert "\r\n" not in path.read_bytes().decode("utf-8")


@pytest.mark.parametrize("facet", compare.FACETS)
def test_every_facet_renders_for_every_engine(lib, facet):
    home, _ = lib
    build.build(home)
    text = Path(compare.compare(facet, [], home=home)["path"]).read_text(encoding="utf-8")
    assert "## Tabela" in text and "## Por jogo" in text and "## Não verificado / lacunas" in text
    assert text.count("### ") == 3


def test_compare_names_aliases_and_errors(lib):
    home, _ = lib
    build.build(home)
    result = compare.compare("ragdoll", ["Alpha Game", "BETA"], home=home)
    assert result["facet"] == "joints" and result["games"] == ["alpha", "beta"]
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "| ragdolls | 1: `Hero` | 1: `res://ragdoll.tscn` |" in text
    assert "| mola angular dos drives (mín / mediana / máx) | 1500 / 2000 / 2500 |" in text
    with pytest.raises(GsError, match="unknown facet"):
        compare.facet_name("weather")
    with pytest.raises(GsError, match="not in the library: nope"):
        compare.compare("rigs", ["nope"], home=home)


def test_compare_needs_a_library(isolated_env):
    with pytest.raises(GsError, match="gst lib build"):
        compare.compare("rigs", [], home=isolated_env["home"])


# --- search --------------------------------------------------------------------------------------------

def test_search_finds_lines_with_kind_game_and_accent_folding(lib):
    home, wss = lib
    build.build(home)
    hits = search.search("ragdoll", home=home, kind="notes")
    assert [(h["game"], h["path"], h["line"]) for h in hits] == [("alpha", "notes/logic/ragdoll.md", 1),
                                                                  ("alpha", "notes/logic/ragdoll.md", 5)]
    assert hits[1]["heading"] == "Como funciona" and "[ragdoll]" in hits[1]["snippet"]
    assert hits[1]["file"] == f"{wss['alpha'].as_posix()}/notes/logic/ragdoll.md"
    fisica = {(h["game"], h["path"]) for h in search.search("fisica", home=home)}
    assert fisica == {("alpha", "notes/logic/ragdoll.md"), ("beta", "notes/assets/modelos.md")}
    assert [h["path"] for h in search.search("fisica", home=home, games=["beta"])] == ["notes/assets/modelos.md"]
    assert [h["path"] for h in search.search("massa", home=home, kind="facts")] == ["FACTS.md"]
    assert [h["path"] for h in search.search("proximidade", home=home)] == ["ARCHITECTURE.md"]
    assert search.search("Physics joints-limits", home=home) == []  # not FTS syntax: searched as words, no crash
    assert len(search.search("ragdoll", home=home, limit=1)) == 1
    table_rows = search.search("x", home=home)  # separator rows are not indexed, table rows are
    assert [h["text"] for h in table_rows] == ["| x | y |"]


# --- CLI and the comparativo template --------------------------------------------------------------------

def test_cli_build_remembers_extra_workspaces(lib, tmp_path, capsys):
    home, _ = lib
    other = workspace(tmp_path / "elsewhere", "delta", "Delta", "unity-il2cpp")
    assert cli.main(["lib", "build", "--workspace", str(other), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "delta" in [g["key"] for g in out["games"]]
    saved = json.loads((home / "_library" / "library.json").read_text(encoding="utf-8"))
    assert saved == {"workspaces": [other.resolve().as_posix()], "legacy": []}
    assert cli.main(["lib", "build", "--json"]) == 0
    assert "delta" in [g["key"] for g in json.loads(capsys.readouterr().out)["games"]]
    assert cli.main(["lib", "build", "--forget", str(other), "--json"]) == 0
    assert "delta" not in [g["key"] for g in json.loads(capsys.readouterr().out)["games"]]
    assert cli.main(["lib", "compare", "rigs", "alpha,beta"]) == 0
    assert capsys.readouterr().out.strip().endswith("compare/rigs-alpha_beta.md")
    assert cli.main(["lib", "search", "ragdoll", "--game", "alpha", "--kind", "notes"]) == 0
    assert "notes/logic/ragdoll.md:5" in capsys.readouterr().out
    assert cli.main(["lib", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert {g["key"] for g in status["games"]} == {"alpha", "beta", "gamma"}


def test_comparativo_note_embeds_the_library_table(lib):
    home, wss = lib
    ws = wss["alpha"]
    path = notes.new(ws, "compare/rigs", template="comparativo", game={"name": "Alpha Game"}, options={"games": "alpha,beta"})
    assert "sem biblioteca: rode `gst lib build`" in path.read_text(encoding="utf-8")
    build.build(home)
    path = notes.new(ws, "compare/ossos", template="comparativo", game={"name": "Alpha Game"},
                     options={"games": "alpha,beta", "facet": "rigs"})
    text = path.read_text(encoding="utf-8")
    assert "Tabela da biblioteca (`gst lib compare rigs alpha beta`" in text
    assert "| ossos por rig (mín / mediana / máx) | 3 / 26.5 / 50 | 24 / 24 / 24 |" in text
    assert notetemplates.unfilled(text) >= 3
    other = notetemplates.render(ws, {}, "comparativo", title="T", note="notes/compare/clima.md", date="d")
    assert "não é uma faceta da biblioteca" in other
