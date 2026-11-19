"""The extractor contract (docs/INTERFACES.md) stays importable and the registry behaves."""

import pytest

from gst.extractors import registry
from gst.extractors.base import CATEGORIES, Context, Extractor, Result, Unit


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    monkeypatch.setattr(registry, "_REGISTRY", {})
    monkeypatch.setattr(registry, "PROVIDERS", [])


def test_register_by_engine_and_any(tmp_path):
    @registry.register("unity-mono")
    class Tex(Extractor):
        name = "unity.textures"
        category = "texture"

        def discover(self, ctx):
            yield Unit(id="a", extractor=self.name, relpaths=["Data/a"], size=1)

        def run(self, unit, ctx):
            return Result(outputs=["assets/a.png"], out_bytes=1, tool="UnityPy")

    @registry.register()
    class Loose(Extractor):
        name = "files.loose"
        category = "config_save"
        resource = "io"

        def discover(self, ctx):
            return []

        def run(self, unit, ctx):
            return Result(status="skipped")

    assert [c.name for c in registry.for_engine("unity-mono")] == ["unity.textures", "files.loose"]
    assert [c.name for c in registry.for_engine("godot")] == ["files.loose"]
    assert registry.get("unity.textures") is Tex
    ctx = Context(workspace=tmp_path, game={}, install_dir=tmp_path, out_root=tmp_path / "assets", policy="offline-ok")
    unit = next(iter(Tex().discover(ctx)))
    assert Tex().run(unit, ctx).status == "done"
    assert Loose.category in CATEGORIES


def test_duplicate_names_are_rejected():
    @registry.register("x")
    class A(Extractor):
        name = "dup"

        def discover(self, ctx):
            return []

        def run(self, unit, ctx):
            return Result()

    with pytest.raises(ValueError):
        @registry.register("x")
        class B(A):
            name = "dup"


def test_skeleton_packages_import():
    import importlib

    for mod in ("gst.pipeline.plan", "gst.pipeline.jobs", "gst.pipeline.runner", "gst.pipeline.worker",
                "gst.pipeline.budget", "gst.pipeline.progress", "gst.pipeline.coverage", "gst.engines.unity",
                "gst.engines.il2cpp", "gst.engines.godot", "gst.engines.unreal", "gst.analysis", "gst.rea",
                "gst.library", "gst.compat.legacy"):
        assert importlib.import_module(mod).__doc__
