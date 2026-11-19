"""gst analyze: framework contract, CLI, the analysis stage of gst study, and the Unity/system analyses on
synthetic objects (no game data)."""

from __future__ import annotations

import gzip
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from gst import analysis

FAKE = '''
from gst.analysis import Report, analysis, md_table


@analysis("fake_a", engines=("fake-engine",), description="first fake", options={"n": "how many"})
def fake_a(ctx):
    n = int(ctx.opt("n", 3))
    ctx.use("note", ctx.workspace / "notes" / "x.txt")
    ctx.write_side_json({"n": n}, "items", "one.json")
    rows = [{"k": f"item{i}", "v": i} for i in reversed(range(n))]
    rows.sort(key=lambda r: r["k"])
    return Report(data={"rows": rows}, markdown=md_table(["k", "v"], [(r["k"], r["v"]) for r in rows]),
                  summary={"rows": n})


@analysis("fake_b", engines=("fake-engine",), uses=("analysis:fake_a",), description="reads fake_a")
def fake_b(ctx):
    a = ctx.load("fake_a")
    return Report(data={"saw": a["summary"]["rows"] if a else None}, summary={"saw": a["summary"]["rows"] if a else 0})


@analysis("fake_needs", engines=("*",), needs=("code.index",), description="needs the code index")
def fake_needs(ctx):
    return Report(data={})


@analysis("fake_broken", engines=("fake-engine",), description="raises")
def fake_broken(ctx):
    raise RuntimeError("boom")
'''


@pytest.fixture
def fake_analyses(tmp_path, monkeypatch):
    mod_dir = tmp_path / "amods"
    mod_dir.mkdir()
    (mod_dir / "gst_fake_analyses.py").write_text(textwrap.dedent(FAKE), encoding="utf-8")
    monkeypatch.syspath_prepend(str(mod_dir))
    monkeypatch.setenv("PYTHONPATH", str(mod_dir))
    monkeypatch.setenv(analysis.ENV_PROVIDERS, "gst_fake_analyses")
    monkeypatch.setattr(analysis, "PROVIDERS", [])
    monkeypatch.setattr(analysis, "_REGISTRY", {})
    monkeypatch.delitem(sys.modules, "gst_fake_analyses", raising=False)
    ws = tmp_path / "gs-home" / "fake"
    (ws / "notes").mkdir(parents=True)
    (ws / "notes" / "x.txt").write_text("x", encoding="utf-8")
    install = tmp_path / "install"
    install.mkdir()
    game = {"gs_workspace": 1, "gst_workspace": 2, "slug": "fake", "name": "Fake", "buildid": "7",
            "install_dir": str(install), "engine": {"key": "fake-engine", "version": "1.0"}, "verdict": "offline-ok"}
    (ws / "game.json").write_text(json.dumps(game), encoding="utf-8")
    return {"ws": ws, "game": game, "install": install}


def test_run_writes_json_and_md_with_header(fake_analyses):
    ws, game = fake_analyses["ws"], fake_analyses["game"]
    res = analysis.run(ws, game, "fake_a", {"n": "2"})
    assert res["status"] == "done" and res["json"] == "analysis/fake_a.json" and res["md"] == "analysis/fake_a.md"
    doc = json.loads((ws / "analysis" / "fake_a.json").read_text(encoding="utf-8"))
    h = doc["header"]
    assert h["analysis"] == "fake_a" and h["game"] == "Fake" and h["build"] == "7" and h["engine"] == "fake-engine"
    assert h["options"] == {"n": "2"} and h["tools"]["gst"] and h["generated_at"]
    assert h["inputs"] == [{"label": "note", "path": "notes/x.txt", "bytes": 1, "mtime": h["inputs"][0]["mtime"]}]
    assert doc["summary"] == {"rows": 2} and doc["data"]["rows"][0] == {"k": "item0", "v": 0}
    md = (ws / "analysis" / "fake_a.md").read_text(encoding="utf-8")
    assert md.startswith("# first fake") and "build 7" in md and "| item1 | 1 |" in md
    assert (ws / "analysis" / "fake_a" / "items" / "one.json").is_file()
    # deterministic: a second run differs only in generated_at / seconds
    analysis.run(ws, game, "fake_a", {"n": "2"})
    again = json.loads((ws / "analysis" / "fake_a.json").read_text(encoding="utf-8"))
    for d in (doc, again):
        d["header"].pop("generated_at"), d["header"].pop("seconds")
    assert doc == again


def test_needs_engines_failures_and_order(fake_analyses):
    ws, game = fake_analyses["ws"], fake_analyses["game"]
    skipped = analysis.run(ws, game, "fake_needs")
    assert skipped["status"] == "skipped" and "code.index" in skipped["reason"]
    broken = analysis.run(ws, game, "fake_broken")
    assert broken["status"] == "failed" and "RuntimeError: boom" in broken["reason"]
    other = analysis.run(ws, {**game, "engine": {"key": "godot"}}, "fake_a")
    assert other["status"] == "skipped" and "does not apply" in other["reason"]
    names = [s.name for s in analysis.for_engine("fake-engine")]
    assert names.index("fake_a") < names.index("fake_b") and "fake_needs" in names
    results = analysis.run_all(ws, game, {"fake_a.n": "4", "other": "x"})
    by = {r["name"]: r for r in results}
    assert by["fake_b"]["summary"] == {"saw": 4}  # ran after fake_a, with fake_a's own option
    assert analysis.options_for("fake_a", {"fake_a.n": "4", "n": "1", "fake_b.x": "2"}) == {"n": "4"}
    with pytest.raises(ValueError):
        analysis.analysis("bad", needs=("no.such.thing",))(lambda ctx: None)


def test_helpers():
    assert analysis.counted({"b": 1, "a": 1, "c": 5}) == {"c": 5, "a": 1, "b": 1}
    assert analysis.stats([5, 1, 3]) == {"n": 3, "min": 1, "median": 3, "p90": 5, "max": 5, "sum": 9}
    assert analysis.stats([]) == {}
    assert analysis.md_table(["a", "b"], [("x|y", None)]) == "| a | b |\n| --- | --- |\n| x\\|y | - |"


def test_cli_list_and_run(fake_analyses, capsys):
    from gst import cli

    ws = fake_analyses["ws"]
    assert cli.main(["analyze", "--list", "--game", "fake", "--json"]) == 0
    rows = {r["name"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["fake_a"]["applies"] and rows["fake_needs"]["missing"] == ["code.index"]
    assert rows["fake_a"]["options"] == {"n": "how many"}
    assert cli.main(["analyze", "fake_a", "--game", "fake", "--opt", "n=5", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "done" and out["summary"] == {"rows": 5}
    assert cli.main(["analyze", "fake_broken", "--game", "fake"]) == 1
    assert "STUDY.md" in {p.name for p in ws.iterdir()}  # journaled


def test_analysis_stage_runs_as_jobs_after_earlier_stages(fake_analyses, monkeypatch):
    """gst study: analysis.<name> jobs at stage 60 wait for every earlier job, follow analysis deps, and rerun
    when an earlier job runs again."""
    from gst.extractors import registry
    from gst.pipeline import plan
    from gst.pipeline.jobs import JobsDB
    from gst.pipeline.runner import Runner

    mod_dir = fake_analyses["ws"].parent.parent / "amods"
    (mod_dir / "gst_fake_slow.py").write_text(textwrap.dedent('''
        import time
        from gst.extractors.base import Extractor, Result, Unit
        from gst.extractors.registry import register

        @register("fake-engine")
        class Slow(Extractor):
            name = "fake.slow"
            stage = 50
            def discover(self, ctx):
                for p in sorted(ctx.install_dir.glob("*.dat")):
                    yield Unit(id=p.name, extractor=self.name, relpaths=[p.name], size=1)
            def run(self, unit, ctx):
                time.sleep(0.5)
                (ctx.workspace / "notes" / f"slow-{unit.id}").write_text("x", encoding="utf-8")
                return Result()
    '''), encoding="utf-8")
    for n in ("a.dat", "b.dat"):
        (fake_analyses["install"] / n).write_bytes(n.encode())
    monkeypatch.setenv(registry.ENV_PROVIDERS, "gst_fake_slow,gst.analysis.stage")
    monkeypatch.setattr(registry, "PROVIDERS", [])
    monkeypatch.setattr(registry, "_REGISTRY", {})
    for m in ("gst_fake_slow", "gst.analysis.stage"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    ws, game = fake_analyses["ws"], fake_analyses["game"]
    for sub in ("jobs", "logs"):
        (ws / sub).mkdir(exist_ok=True)
    p = plan.build(ws, game, options={"only": ["fake.slow", "analysis.fake_a", "analysis.fake_b"]})
    names = [e["name"] for e in p["extractors"]]
    assert names == ["fake.slow", "analysis.fake_a", "analysis.fake_b"]
    Runner(ws, jobs=4, python=sys.executable).run()
    with JobsDB(ws / "jobs" / "jobs.db") as db:
        rows = {r["extractor"]: r for r in db.rows()}
        slow = [r for r in db.rows() if r["extractor"] == "fake.slow"]
    assert rows["analysis.fake_a"]["status"] == "done" and rows["analysis.fake_b"]["status"] == "done"
    assert rows["analysis.fake_a"]["started_at"] >= max(r["finished_at"] for r in slow)
    assert rows["analysis.fake_b"]["started_at"] >= rows["analysis.fake_a"]["finished_at"]
    assert "analysis/fake_a.json" in rows["analysis.fake_a"]["outputs"] and "rows 3" in rows["analysis.fake_a"]["notes"]
    assert json.loads((ws / "analysis" / "fake_b.json").read_text(encoding="utf-8"))["data"] == {"saw": 3}
    # nothing changed: kept; an earlier unit changes: the analyses run again
    again = plan.build(ws, game, options={"only": ["fake.slow", "analysis.fake_a", "analysis.fake_b"]})
    assert again["totals"]["to_run"] == 0
    (fake_analyses["install"] / "a.dat").write_bytes(b"changed")
    third = plan.build(ws, game, options={"only": ["fake.slow", "analysis.fake_a", "analysis.fake_b"]})
    assert third["totals"]["to_run"] == 3


# --- Unity analyses on synthetic objects ----------------------------------------------------------

class FakeObj:
    def __init__(self, pid: int, type_name: str, tree: dict | None = None, script: str = ""):
        self.path_id = pid
        self.type = SimpleNamespace(name=type_name)
        self.tree = tree
        self.script = script


def pp(pid: int, fid: int = 0) -> dict:
    return {"m_FileID": fid, "m_PathID": pid}


def tf(go: int, father: int, children: list[int], pos=(0, 0, 0)) -> dict:
    return {"m_GameObject": pp(go), "m_Father": pp(father), "m_Children": [pp(c) for c in children],
            "m_LocalPosition": dict(zip("xyz", pos)), "m_LocalRotation": {"x": 0, "y": 0, "z": 0, "w": 1},
            "m_LocalScale": {"x": 1, "y": 1, "z": 1}}


def go(name: str, comps: list[int]) -> dict:
    return {"m_Name": name, "m_IsActive": True, "m_Layer": 0, "m_Tag": 0, "m_Component": [{"component": pp(c)} for c in comps]}


def prefab_objects() -> dict[int, FakeObj]:
    """Enemy (root) > Body (SMR + MB EnemyParent child) > Bone; Crate (root, MeshFilter + Rigidbody + Box)."""
    objs = [
        FakeObj(1, "GameObject", go("Enemy", [2, 3, 30])), FakeObj(2, "Transform", tf(1, 0, [5])),
        FakeObj(3, "Animator", {"m_Controller": pp(900), "m_Avatar": pp(0), "m_ApplyRootMotion": 0}),
        FakeObj(30, "MonoBehaviour", script="EnemyParent"),
        FakeObj(4, "GameObject", go("Body", [5, 6, 7])), FakeObj(5, "Transform", tf(4, 2, [9])),
        FakeObj(6, "SkinnedMeshRenderer", {"m_Mesh": pp(800), "m_Bones": [pp(9), pp(5)], "m_RootBone": pp(9),
                                           "m_Materials": [pp(700)]}),
        FakeObj(7, "MonoBehaviour", script="EnemyHealth"),
        FakeObj(8, "GameObject", go("Bone", [9])), FakeObj(9, "Transform", tf(8, 5, [])),
        FakeObj(10, "GameObject", go("Crate", [11, 12, 13, 14, 15])), FakeObj(11, "Transform", tf(10, 0, [], (1, 2, 3))),
        FakeObj(12, "MeshFilter", {"m_Mesh": pp(801)}),
        FakeObj(13, "Rigidbody", {"m_Mass": 2.5, "m_IsKinematic": 0, "m_UseGravity": 1}),
        FakeObj(14, "BoxCollider", {"m_IsTrigger": 0, "m_Size": {"x": 1, "y": 1, "z": 1}, "m_Center": {"x": 0, "y": 0, "z": 0}}),
        FakeObj(15, "HingeJoint", {"m_ConnectedBody": pp(13), "m_UseLimits": 1}),
    ]
    return {o.path_id: o for o in objs}


class FakeRes:
    MESHES = {800: {"mesh": "body", "verts": 100, "tris": 150, "bindposes": 2, "blendshapes": 1},
              801: {"mesh": "crate", "verts": 24, "tris": 12, "bindposes": 0, "blendshapes": 0}}

    def mesh(self, sf, value, skin=False):
        m = dict(self.MESHES.get(value["m_PathID"]) or {})
        if skin and m:
            m["max_influences"] = 2
        return m or None

    def name(self, sf, value):
        return {900: "EnemyController", 700: "Hurtable"}.get(value["m_PathID"])

    def material(self, sf, value):
        return {"name": self.name(sf, value), "shader": "Hurtable/Hurtable", "textures": {}}


def graph_and_details(objs):
    from gst.analysis.unity import prefabs as P

    g = P.Graph.from_objects(list(objs.values()), lambda o: o.tree,
                             lambda pid: objs[pid].type.name if pid in objs else None,
                             lambda pid: objs[pid].script, lambda o: o.type.name)
    return g, P.Details(None, objs, lambda o: o.tree, FakeRes())


def test_prefab_roots_summary_and_detail(tmp_path):
    from gst.analysis.unity import prefabs as P

    objs = prefab_objects()
    g, d = graph_and_details(objs)
    assert g.roots() == [2, 11]
    assert [p for _t, _d, p in g.walk(2)] == ["/Enemy", "/Enemy/Body", "/Enemy/Body/Bone"]
    enemy = P.summarize(g, 2, d, {"EnemyParent"})
    assert enemy["name"] == "Enemy" and enemy["gameobjects"] == 3 and enemy["markers"] == ["EnemyParent"]
    assert enemy["scripts"] == {"EnemyHealth": 1, "EnemyParent": 1} and enemy["smr"] == 1 and enemy["tris"] == 150
    assert enemy["animators"] == ["EnemyController"] and enemy["materials"] == ["Hurtable"]
    crate = P.summarize(g, 11, d, {"EnemyParent"})
    assert crate["rigidbodies"] == [{"go": "Crate", "mass": 2.5, "kinematic": False, "gravity": True}]
    assert crate["colliders"] == {"BoxCollider": 1} and crate["joints"] == {"HingeJoint": 1} and crate["tris"] == 12
    assert P.wants_detail(enemy, "auto", None) and not P.wants_detail(crate, "auto", None)
    assert P.wants_detail(crate, "all", None) and not P.wants_detail(enemy, "none", None)
    nodes = P.detail_tree(g, 2, d)
    smr = next(c for c in nodes[1]["components"] if c["type"] == "SkinnedMeshRenderer")
    assert smr["bone_names"] == ["Bone", "Body"] and smr["root_bone"] == "Bone" and smr["mesh"]["max_influences"] == 2
    assert smr["materials"][0]["shader"] == "Hurtable/Hurtable"
    assert nodes[2]["depth"] == 2 and nodes[0]["rot"] == [0.0, 0.0, 0.0, 1.0]
    crate_nodes = P.detail_tree(g, 11, d)
    comps = {c["type"]: c for c in crate_nodes[0]["components"]}
    assert crate_nodes[0]["pos"] == [1.0, 2.0, 3.0] and comps["Rigidbody"]["mass"] == 2.5
    assert comps["HingeJoint"]["connected"] is True and comps["BoxCollider"]["size"] == [1.0, 1.0, 1.0]
    rep = P.build_report([{"file": "Data/resources.assets", **enemy}, {"file": "Data/resources.assets", **crate}],
                         [{"file": "Data/resources.assets", "cab": None, "scene": False, "roots": 2}], {"EnemyParent"})
    assert rep.summary["prefabs"] == 2 and rep.summary["marker EnemyParent"] == 1
    assert rep.data["scripts"]["EnemyParent"] == {"prefabs": 1, "components": 1}
    assert "## Marker `EnemyParent`: 1 prefabs" in rep.markdown


def test_scene_animators_detail_only_animated_objects(tmp_path):
    """Scene files (default scenes=animators): one row + detail per Animator/Animation GameObject, not per root."""
    from gst.analysis.unity import prefabs as P

    objs = prefab_objects()  # in a scene: Enemy (Animator) is a root with children; Crate has no Animator
    objs[20] = FakeObj(20, "GameObject", go("World", [21]))
    objs[21] = FakeObj(21, "Transform", tf(20, 0, [2]))
    objs[2].tree["m_Father"] = pp(21)
    g, d = graph_and_details(objs)
    assert P.scene_animated(g) == [2] and P.scene_path(g, 2) == "/World/Enemy"

    class Ctx:
        workspace = tmp_path

        def write_side_json(self, value, folder, name):
            p = tmp_path / "analysis" / "prefabs" / folder / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(value), encoding="utf-8")
            return p

    rows = P.analyze_scene_animators(Ctx(), "Data/level0", g, d, markers=set(), cab=None)
    assert len(rows) == 1 and rows[0]["scene"] and rows[0]["scene_path"] == "/World/Enemy"
    assert rows[0]["animators"] == ["EnemyController"] and rows[0]["detail"].startswith("analysis/prefabs/scenes__level0/")
    detail = json.loads((tmp_path / rows[0]["detail"]).read_text(encoding="utf-8"))
    assert [n["path"] for n in detail["nodes"]] == ["/Enemy", "/Enemy/Body", "/Enemy/Body/Bone"]
    rep = P.build_report([], [{"file": "Data/level0", "cab": None, "scene": True, "roots": None, "animators": 1}], set(), rows)
    assert rep.summary["scene_animators"] == 1 and rep.summary["prefabs"] == 0
    assert P.detailed_rows({"data": rep.data}) == rows and "1 animated objects" in rep.markdown
    assert [P.scene_mode(v) for v in (None, "1", "all", "0", "animators")] == ["animators", "all", "all", "none", "animators"]


def test_scene_names():
    from gst.analysis.unity import is_scene, ptr

    assert is_scene("level0") and is_scene("BuildPlayer-Main") and not is_scene("BuildPlayer-Main.sharedAssets")
    assert not is_scene("resources.assets") and not is_scene("levels0-31.assetbundle")
    assert ptr({"m_FileID": 16777216, "m_PathID": 5 << 24}) == (1, 5) and ptr(None) == (0, 0)


def test_mono_values_collect_and_report(tmp_path):
    from gst.analysis.unity import mono_values as M

    infos = {1: SimpleNamespace(class_name="Health", namespace="Game", assembly="Assembly-CSharp"),
             2: SimpleNamespace(class_name="Health", namespace="Game", assembly="Assembly-CSharp"),
             3: SimpleNamespace(class_name="Other", namespace="", assembly="Assembly-CSharp"),
             4: SimpleNamespace(class_name="ItemDef", namespace="", assembly="Assembly-CSharp")}
    trees = {1: {"m_GameObject": pp(50), "m_Name": "", "hp": 10, "armor": pp(0)},
             2: {"m_GameObject": pp(51), "m_Name": "", "hp": 30, "armor": pp(7)},
             3: None,
             4: {"m_GameObject": pp(0), "m_Name": "Sword", "price": 5, "tags": ["a", "b"]}}
    objs = [SimpleNamespace(path_id=i) for i in (1, 2, 3, 4)]
    reads = []

    def read(obj, info):
        reads.append(obj.path_id)
        return trees[obj.path_id]

    aggs, untyped = {}, __import__("collections").Counter()
    writer = M.ValueWriter(str(tmp_path / "vals"))
    n = M.collect(objs, script_of=lambda o: infos[o.path_id], read=read, gameobject_name=lambda pid: f"go{pid}",
                  wanted={"Health", "ItemDef"}, file="Data/x.assets", cab=None, aggs=aggs, untyped=untyped, writer=writer)
    writer.close()
    assert n == 3 and 3 not in reads  # raw prefilter: the Other script is never read
    lines = [json.loads(x) for x in (tmp_path / "vals" / "Health.jsonl").read_text(encoding="utf-8").splitlines()]
    assert lines[1] == {"file": "Data/x.assets", "path_id": 2, "gameobject": "go51", "name": None,
                        "fields": {"hp": 30, "armor": {"ref": 7, "file": 0}}}
    rep = M.build_report(aggs, untyped, {"Health", "ItemDef", "Missing"}, writer, ["Data/x.assets"], tmp_path)
    h = rep.data["scripts"]["Health"]
    assert h["objects"] == 2 and h["components"] == 2 and h["fields"]["hp"]["min"] == 10 and h["fields"]["hp"]["max"] == 30
    assert rep.data["scripts"]["ItemDef"]["scriptable_objects"] == 1
    assert rep.data["scripts"]["ItemDef"]["fields"]["tags"]["len_max"] == 2
    assert rep.data["missing"] == ["Missing"] and h["values"] == "vals/Health.jsonl"


def test_value_writer_flushes_in_batches(tmp_path, monkeypatch):
    from gst.analysis.unity import mono_values as M

    monkeypatch.setattr(M, "FLUSH_BYTES", 30)
    w = M.ValueWriter(str(tmp_path))
    for i in range(10):
        w.write(f"S{i % 4}", {"i": i})
    w.close()
    assert sorted(len((tmp_path / f"S{k}.jsonl").read_text(encoding="utf-8").splitlines()) for k in range(4)) == [2, 2, 3, 3]
    assert [json.loads(x)["i"] for x in (tmp_path / "S1.jsonl").read_text(encoding="utf-8").splitlines()] == [1, 5, 9]


def test_catalog_refs_joins_and_families():
    from gst.analysis.unity import catalogs as C

    tree = {"m_Name": "Hat 01", "m_GameObject": pp(0), "rarity": 2, "icon": pp(40),
            "prefab": {"prefabName": "Cosmetic - Hat 01", "resourcePath": "Cosmetics/Cosmetic - Hat 01"},
            "variants": [pp(41), pp(0), pp(42, 1)]}
    assert list(C.walk_refs(tree)) == [("icon", (0, 40)), ("variants[]", (0, 41)), ("variants[]", (1, 42))]
    assert ("prefab.resourcePath", "Cosmetics/Cosmetic - Hat 01") in list(C.walk_strings(tree))
    joiner = C.Joiner({("resources.assets", 41)}, {"Cosmetic - Hat 01"}, {"cosmetics/cosmetic - hat 01"})
    agg = C.ClassAgg()
    targets = {(0, 40): ("Sprite", "sharedassets0.assets", 40), (0, 41): ("GameObject", "resources.assets", 41)}
    C.add_object(agg, "Data/sharedassets0.assets", tree, targets.get, joiner)
    assert agg.refs["icon"]["targets"] == {"Sprite": 1}
    assert agg.refs["variants[]"]["prefab_roots"] == 1 and agg.refs["variants[]"]["unresolved"] == 1
    assert agg.joins == {"prefab.prefabName": {"prefab_name": 1}, "prefab.resourcePath": {"resources_path": 1}}
    rep = C.build_report({"CosmeticAsset": agg}, {}, {}, {"catalogs": [], "bundles": []}, True)
    assert rep.data["scriptable_objects"]["CosmeticAsset"]["names"] == ["Hat 01"]
    assert rep.data["scriptable_objects"]["CosmeticAsset"]["fields"]["rarity"]["values"] == {"2": 1}
    fams = C.name_families([{"name": "Cosmetic - Hat - A", "file": "f"}, {"name": "Cosmetic - Hat - B", "file": "f"},
                            {"name": "Cosmetic - Arm", "file": "f"}, {"name": "Solo - x", "file": "f"}, {"name": "Plain"}])
    assert fams == {"Cosmetic": {"gameobjects": 3, "files": {"f": 3}, "subfamilies": {"Hat": 2}}}


def _write_inventory(ws: Path, rows_by_file: dict[str, list[dict]]):
    inv = ws / "assets" / "unity" / "_inventory"
    inv.mkdir(parents=True, exist_ok=True)
    for rel, rows in rows_by_file.items():
        safe = rel.replace("/", "__")
        with gzip.open(inv / f"{safe}.jsonl.gz", "wt", encoding="utf-8") as fh:
            fh.write(json.dumps({"_header": 1, "file": rel, "kind": "serialized"}) + "\n")
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        types = {}
        for r in rows:
            types[r["type"]] = types.get(r["type"], 0) + 1
        (inv / f"{safe}.summary.json").write_text(json.dumps({"file": rel, "types": types, "objects": len(rows),
                                                              "table": f"{safe}.jsonl.gz"}), encoding="utf-8")


def test_context_rows_stream_the_inventory(fake_analyses):
    ws, game = fake_analyses["ws"], fake_analyses["game"]
    _write_inventory(ws, {"G_Data/a.assets": [{"type": "GameObject", "name": "Cosmetic - A", "path_id": 1},
                                              {"type": "Mesh", "name": "m", "path_id": 2}],
                          "G_Data/b.assets": [{"type": "GameObject", "name": "Cosmetic - B", "path_id": 1}]})
    ctx = analysis.Context(ws, game, analysis.Spec(name="t", func=lambda c: None))
    rows = list(ctx.rows(types=("GameObject",)))
    assert [(r["file"], r["name"]) for r in rows] == [("G_Data/a.assets", "Cosmetic - A"), ("G_Data/b.assets", "Cosmetic - B")]
    assert ctx.inventory_files(("Mesh",)) == ["G_Data/a.assets"] and "unity.inventory" in ctx.inputs
    assert analysis.check_need(ws, game, "unity.inventory")[0]


def test_systems_from_index_prefabs_and_rea(fake_analyses, monkeypatch):
    from gst import index

    monkeypatch.setattr(analysis, "PROVIDERS", ["gst.analysis.systems"])
    monkeypatch.delitem(sys.modules, "gst.analysis.systems", raising=False)  # registers again in the clean registry
    ws, game = fake_analyses["ws"], fake_analyses["game"]
    code = ws / "decomp" / "cs" / "Game"
    code.mkdir(parents=True)
    (code / "EnemyParent.cs").write_text("public class EnemyParent\n{\n    public void Run() { new EnemyHealth(); }\n}\n",
                                         encoding="utf-8")
    (code / "EnemyHealth.cs").write_text("public class EnemyHealth\n{\n    public void Hit() { SaveLoader.Save(); }\n}\n",
                                         encoding="utf-8")
    (code / "SaveLoader.cs").write_text("public class SaveLoader\n{\n    public static void Save() { }\n}\n", encoding="utf-8")
    (code / "Wobble.cs").write_text("public class Wobble\n{\n}\n", encoding="utf-8")
    index.build(ws)
    (ws / "analysis").mkdir(exist_ok=True)
    (ws / "analysis" / "prefabs.json").write_text(json.dumps({"header": {}, "summary": {}, "data": {"scripts": {
        "EnemyParent": {"prefabs": 31, "components": 31}, "Wobble": {"prefabs": 4, "components": 9}}}}), encoding="utf-8")
    rea = ws / "dumps" / "rea"
    rea.mkdir(parents=True)
    (rea / "ev.json").write_text(json.dumps({"records": [{"normalized_result": {"call_edges": [
        {"caller": "Game.EnemyHealth.Hit", "target_declaring_type": "Game.SaveLoader"},
        {"caller": "EnemyHealth.Hit", "target_declaring_type": "SaveLoader"}]}}]}), encoding="utf-8")
    res = analysis.run(ws, {**game, "engine": {"key": "unity-mono"}}, "systems")
    assert res["status"] == "done", res["reason"]
    doc = json.loads((ws / "analysis" / "systems.json").read_text(encoding="utf-8"))
    systems = {s["label"]: s for s in doc["data"]["systems"]}
    combat = systems["Criaturas e entidades"]
    assert combat["prefabs"] == 31 and combat["prefab_scripts"][0]["script"] == "EnemyParent"
    assert doc["data"]["unassigned_prefab_scripts"][0]["script"] == "Wobble"
    assert doc["data"]["rea"]["edges"] == 2 and doc["data"]["sources"] == {"code_index": True, "prefabs": True, "rea": True}
    assert {e["to"] for e in doc["data"]["rea"]["system_edges"]} == {"Save e serialização"}
    labels = {i["label"] for i in doc["header"]["inputs"]}
    assert {"analysis:prefabs", "code.index", "rea:ev.json"} <= labels
    md = (ws / "analysis" / "systems.md").read_text(encoding="utf-8")
    assert "`Wobble`" in md and "| Criaturas e entidades |" in md
