"""scripts/check_no_derived.py: this repo tracks no derived game material."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("check_no_derived", ROOT / "scripts" / "check_no_derived.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repository_is_clean():
    mod = load()
    assert mod.problems(ROOT) == []
    assert mod.main([str(ROOT)]) == 0


def test_detects_derived_files(tmp_path):
    mod = load()
    files = ["src/gst/x.py", "assets/a.png.gs.json", "notes/inventory.jsonl", "out/tex.png", "m.glb",
             "tests/fixtures/ok.png", "a.wav"]
    found = mod.problems(ROOT, files, home=tmp_path)
    assert len(found) == 5 and not any("ok.png" in f or "x.py" in f for f in found)


def test_repo_inside_gs_home_fails(tmp_path):
    mod = load()
    repo = tmp_path / "game" / "repo"
    repo.mkdir(parents=True)
    assert mod.problems(repo, [], home=tmp_path)
