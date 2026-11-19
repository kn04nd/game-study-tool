"""Extractor registry, keyed by engine.

Engine keys are the ``engine.key`` values of recon/game.json (``unity-mono``, ``unity-il2cpp``,
``godot``, ``unreal``...) plus ``*`` for extractors that apply to every game (loose files, audio
containers). Modules register at import time::

    from gst.extractors.registry import register

    @register("unity-mono", "unity-il2cpp")
    class UnityTextures(Extractor):
        name = "unity.textures"
        ...

The pipeline (``gst.pipeline.plan``) imports :data:`PROVIDERS` lazily so ``gst --help`` stays fast.
"""

from __future__ import annotations

import importlib
from typing import Callable, TypeVar

from gst.extractors.base import Extractor

ANY = "*"
E = TypeVar("E", bound=type[Extractor])

_REGISTRY: dict[str, dict[str, type[Extractor]]] = {}

# Modules that register extractors when imported. One line per module, so parallel work does not collide.
PROVIDERS: list[str] = [
    "gst.extractors.files",            # loose files, user data
    "gst.extractors.code",             # dotnet, godot scripts, native (Ghidra), code index
    "gst.extractors.godot",            # GDRE whole-project recovery
    "gst.engines.unreal.extractors",   # Unreal inventory, retoc to-legacy, uas per container and mode
    "gst.extractors.generic",          # audio containers (vgmstream), maps list
    "gst.engines.unity.extractors",    # Unity streaming extractors
    "gst.engines.il2cpp.ghidra_names",  # code.il2cpp.native (Ghidra on GameAssembly with IL2CPP names)
    "gst.analysis.stage",              # gst analyze as jobs (analysis.<name>, stage 60)
    "gst.engines.il2cpp.extractors",   # IL2CPP metadata, dummy DLLs, name maps, C# stubs (stage 20)
    "gst.rea.extractors",              # REA static jobs (managed members/graph, IL2CPP metadata, artifacts)
    "gst.extractors.javalua",          # code.java (Vineflower per jar/class tree), code.lua (per script folder)
]
ENV_PROVIDERS = "GST_EXTRACTOR_PROVIDERS"  # extra modules (comma-separated), e.g. test fixtures seen by workers


def register(*engines: str) -> Callable[[E], E]:
    """Class decorator: register an Extractor subclass for one or more engine keys (default ``*``)."""
    keys = engines or (ANY,)

    def deco(cls: E) -> E:
        if not cls.name:
            raise ValueError(f"{cls.__name__} has no name")
        for key in keys:
            bucket = _REGISTRY.setdefault(key, {})
            if cls.name in bucket and bucket[cls.name] is not cls:
                raise ValueError(f"extractor name '{cls.name}' already registered for {key}")
            bucket[cls.name] = cls
        return cls

    return deco


def load_providers() -> None:
    import os

    extra = [m.strip() for m in os.environ.get(ENV_PROVIDERS, "").split(",") if m.strip()]
    for module in PROVIDERS + extra:
        importlib.import_module(module)


def for_engine(engine_key: str | None) -> list[type[Extractor]]:
    """Extractors for an engine (its own + the ``*`` ones), in registration order, no duplicates."""
    load_providers()
    seen: dict[str, type[Extractor]] = {}
    for key in ((engine_key,) if engine_key else ()) + (ANY,):
        for name, cls in _REGISTRY.get(key, {}).items():
            seen.setdefault(name, cls)
    return list(seen.values())


def get(name: str) -> type[Extractor]:
    load_providers()
    for bucket in _REGISTRY.values():
        if name in bucket:
            return bucket[name]
    raise KeyError(name)


def all_extractors() -> dict[str, list[str]]:
    """engine key -> extractor names (for docs, `gst study --dry-run` and tests)."""
    load_providers()
    return {key: sorted(bucket) for key, bucket in _REGISTRY.items()}


def _clear() -> None:
    """Tests only."""
    _REGISTRY.clear()
