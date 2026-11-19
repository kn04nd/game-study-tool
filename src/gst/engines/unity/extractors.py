"""Unity extractors (unity-mono and unity-il2cpp), one Unit per serialized file or bundle.

``ctx.out_root`` is the Unity asset root of the workspace (``<ws>/assets/unity``); each extractor writes
``<out_root>/<Type>/<source>/...`` and the inventory goes to ``<out_root>/_inventory/`` (merged into
``_inventory/INDEX.json``; read it with ``inventory.iter_rows``). Units cover every ``*_Data`` folder, ``StreamingAssets/**`` and the
Addressables ``aa/`` bundles; ``.resS``/``.resource`` files are read on demand. When a per-file
inventory exists, ``discover`` skips files without the extractor's types.

Options (``ctx.options``, all optional, none by default): ``name`` (regex on object names), ``limit``
(objects per unit, explicit only), ``thumbs`` (texture thumbnails, default on), ``file`` (regex on
relpaths, used by the dev harness), ``catalog_envs`` (externals kept open, default 4).
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Iterable

from gst.engines.unity import inventory, loader
from gst.engines.unity.bundle import BundleTooLarge
from gst.engines.unity.export import Session, Writer, matches
from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.registry import register

ENGINES = ("unity-mono", "unity-il2cpp")
OUT_SUBDIR = "assets/unity"  # ctx.out_root of every Unity extractor, relative to the workspace (Extractor.out_subdir)


def build_of(ctx: Context) -> str:
    return str((ctx.game or {}).get("buildid") or "?")


def inventory_dir(ctx: Context) -> Path:
    return Path(ctx.out_root) / inventory.INVENTORY_DIR


def _file_units(ext: Extractor, ctx: Context, types: tuple[str, ...] | None) -> Iterable[Unit]:
    pat = ctx.options.get("file")
    inv = inventory_dir(ctx)
    for rel, kind, size in loader.iter_unity_files(ctx.install_dir):
        if pat and not re.search(pat, rel, re.I):
            continue
        if types:
            summary = inventory.summary_for(inv, rel)
            if summary is not None and not any(t in summary.get("types", {}) for t in types):
                continue
        meta = {"kind": kind}
        total = size
        if kind == "bundle":
            try:
                from gst.engines.unity.bundle import read_header

                h = read_header(Path(ctx.install_dir) / rel)
                meta.update(uncompressed=h.uncompressed_size, compression=h.compression, largest_block=h.largest_block)
            except Exception as exc:
                meta["header_error"] = f"{exc.__class__.__name__}: {exc}"
        else:
            total += loader.resource_size(ctx.install_dir, rel)
        yield Unit(id=rel, extractor=ext.name, relpaths=[rel], size=total, meta=meta)


class UnityFileExtractor(Extractor):
    """Base: open the unit's file streaming, hand objects of ``types`` to ``export`` one by one."""

    types: tuple[str, ...] = ()
    export: Callable | None = None
    # Ordering (extractors/base.py stage/after): exports after the per-file inventory, which lets
    # discover() skip files without the extractor's types. IL2CPP games also wait for the dummy DLLs (the
    # typetree source when the BackUp folder is gone); a dependency with no jobs (Mono games) does not block.
    stage = 50
    after: tuple[str, ...] = ("unity.inventory", "code.il2cpp.dummydll")
    out_subdir = OUT_SUBDIR

    def applies(self, ctx: Context) -> bool:
        try:
            return bool(loader.data_dirs(ctx.install_dir))
        except Exception:
            return False

    def discover(self, ctx: Context) -> Iterable[Unit]:
        yield from _file_units(self, ctx, self.types or None)

    # hooks
    def begin(self, s: Session) -> None:
        pass

    def per_file(self, s: Session, sf) -> None:
        pass

    def finish(self, s: Session) -> None:
        pass

    def per_object(self, s: Session, obj) -> list:
        return type(self).export(s, obj) if type(self).export else []

    def run(self, unit: Unit, ctx: Context) -> Result:
        t0 = time.time()
        rel = unit.relpaths[0]
        tool_version = loader.unitypy_version()
        try:
            env = loader.open_env(Path(ctx.install_dir) / rel)
        except BundleTooLarge as exc:
            return Result(status="unsupported", error=str(exc), tool="UnityPy", tool_version=tool_version,
                          notes=["fallback assetripper: bundle block too large for the free RAM"])
        except (ValueError, NotImplementedError) as exc:
            return Result(status="unsupported", error=f"{exc.__class__.__name__}: {exc}", tool="UnityPy",
                          tool_version=tool_version)
        w = Writer(ctx.out_root, ctx.workspace, source=rel, build=build_of(ctx), tool="UnityPy",
                   tool_version=tool_version, extractor=self.name, unit=unit.id)
        s = Session(ctx.install_dir, rel, env, w, workspace=ctx.workspace, build=build_of(ctx), options=ctx.options,
                    log=ctx.log)
        s.catalog.max_envs = int(ctx.options.get("catalog_envs") or 4)
        limit = ctx.options.get("limit")
        exported = 0
        try:
            self.begin(s)
            for sf in loader.serialized_files(env, release=True):
                self.per_file(s, sf)
                if not self.types:
                    continue
                for obj in list(sf.objects.values()):
                    if loader.type_name(obj) not in self.types:
                        continue
                    if ctx.options.get("name") and not matches(loader.peek_name(obj), ctx.options):
                        continue
                    if limit is not None and exported >= int(limit):
                        break
                    try:
                        if self.per_object(s, obj):
                            exported += 1
                            s.counts[loader.type_name(obj)] = s.counts.get(loader.type_name(obj), 0) + 1
                    except MemoryError:
                        raise
                    except Exception as exc:  # one broken object never stops the unit
                        s.fail(obj, exc)
                if limit is not None and exported >= int(limit):
                    break
            self.finish(s)
        finally:
            s.close()
            loader.close_env(env)
        status = "failed" if s.failures and not w.outputs else "done"
        notes = list(s.notes)
        if s.failures:
            notes.append(f"{len(s.failures)} objects failed (see error)")
        notes.append(f"{exported} objects exported in {time.time() - t0:.1f} s")
        return Result(outputs=w.outputs, out_bytes=w.out_bytes, status=status,
                      error="\n".join(s.failures) or None, tool="UnityPy", tool_version=tool_version, notes=notes)


# --- concrete extractors -----------------------------------------------------------------------------

def _lazy(module: str, func: str):
    def call(s, obj):
        import importlib

        return getattr(importlib.import_module(f"gst.engines.unity.export.{module}"), func)(s, obj)

    return call


@register(*ENGINES)
class UnityInventory(Extractor):
    name = "unity.inventory"
    category = "other"
    resource = "cpu"
    stage = 40
    after: tuple[str, ...] = ()
    out_subdir = OUT_SUBDIR

    def applies(self, ctx: Context) -> bool:
        return UnityFileExtractor.applies(self, ctx)  # type: ignore[arg-type]

    def discover(self, ctx: Context) -> Iterable[Unit]:
        pat = ctx.options.get("file")
        for rel, kind, size in loader.iter_unity_files(ctx.install_dir):
            if pat and not re.search(pat, rel, re.I):
                continue
            yield Unit(id=rel, extractor=self.name, relpaths=[rel], size=size, meta={"kind": kind})
        # INDEX.json + INVENTORY.md: finalize(ctx), run by the pipeline after every file unit

    def finalize(self, ctx: Context) -> Result:
        """INDEX.json + INVENTORY.md from the per-file summaries (idempotent; the pipeline runs it after every
        file unit of the inventory, and again whenever one of them ran)."""
        info = inventory.merge(inventory_dir(ctx), inventory_dir(ctx) / inventory.INDEX,
                               Path(ctx.out_root).parent / "INVENTORY.md", (ctx.game or {}).get("name", ""))
        paths = (inventory_dir(ctx) / inventory.INDEX, Path(ctx.out_root).parent / "INVENTORY.md")
        outs = [p.relative_to(ctx.workspace).as_posix() for p in paths]
        return Result(outputs=outs, out_bytes=sum((Path(ctx.workspace) / o).stat().st_size for o in outs),
                      tool="UnityPy", tool_version=loader.unitypy_version(),
                      notes=[f"merged {info['files']} files, {info['objects']} objects, {info['types']} types"])

    def run(self, unit: Unit, ctx: Context) -> Result:
        tv = loader.unitypy_version()
        if unit.meta.get("merge"):  # a `_merge` unit planned before finalize() existed (old jobs.db)
            return self.finalize(ctx)
        rel = unit.relpaths[0]
        from gst.engines.unity.catalog import Catalog

        cat = Catalog(ctx.install_dir, max_envs=int(ctx.options.get("catalog_envs") or 4))
        try:
            summary = inventory.inventory_file(ctx.install_dir, rel, inventory_dir(ctx), catalog=cat)
        except BundleTooLarge as exc:
            return Result(status="unsupported", error=str(exc), tool="UnityPy", tool_version=tv,
                          notes=["fallback assetripper"])
        except (ValueError, NotImplementedError) as exc:
            return Result(status="unsupported", error=f"{exc.__class__.__name__}: {exc}", tool="UnityPy", tool_version=tv)
        finally:
            cat.close()
        outs = [inventory.table_path(inventory_dir(ctx), rel),
                inventory_dir(ctx) / f"{inventory.safe_rel(rel)}.summary.json"]
        return Result(outputs=[p.relative_to(ctx.workspace).as_posix() for p in outs],
                      out_bytes=sum(p.stat().st_size for p in outs), tool="UnityPy", tool_version=tv,
                      notes=[f"{summary['objects']} objects, {len(summary['types'])} types in {summary['seconds']} s"])


@register(*ENGINES)
class UnityTextures(UnityFileExtractor):
    name, category, types = "unity.textures", "texture", ("Texture2D",)
    export = staticmethod(_lazy("texture", "export_texture"))


@register(*ENGINES)
class UnitySprites(UnityFileExtractor):
    name, category, types = "unity.sprites", "texture", ("Sprite",)
    export = staticmethod(_lazy("texture", "export_sprite"))


@register(*ENGINES)
class UnityAudio(UnityFileExtractor):
    name, category, types = "unity.audio", "audio", ("AudioClip",)
    export = staticmethod(_lazy("audio", "export_audio"))


@register(*ENGINES)
class UnityMeshes(UnityFileExtractor):
    name, category, types = "unity.meshes", "mesh", ("Mesh",)
    export = staticmethod(_lazy("mesh", "export_mesh"))


@register(*ENGINES)
class UnityGltf(UnityFileExtractor):
    name, category, types = "unity.gltf", "mesh", ("SkinnedMeshRenderer",)
    export = staticmethod(_lazy("gltf", "export_skinned"))


class _MultiType(UnityFileExtractor):
    funcs: dict[str, tuple[str, str]] = {}

    def per_object(self, s, obj) -> list:
        module, func = self.funcs[loader.type_name(obj)]
        return _lazy(module, func)(s, obj)


@register(*ENGINES)
class UnityAnimations(_MultiType):
    name, category = "unity.animations", "animation"
    types = ("AnimationClip", "AnimatorController", "AnimatorOverrideController", "Avatar")
    funcs = {"AnimationClip": ("anim", "export_clip"), "AnimatorController": ("anim", "export_controller"),
             "AnimatorOverrideController": ("anim", "export_controller"), "Avatar": ("anim", "export_controller")}


@register(*ENGINES)
class UnityMaterials(UnityFileExtractor):
    name, category, types = "unity.materials", "material_shader", ("Material",)
    export = staticmethod(_lazy("material", "export_material"))


@register(*ENGINES)
class UnityShaders(UnityFileExtractor):
    name, category, types = "unity.shaders", "material_shader", ("Shader",)
    export = staticmethod(_lazy("material", "export_shader"))


@register(*ENGINES)
class UnityText(UnityFileExtractor):
    name, category, types = "unity.text", "data", ("TextAsset",)
    export = staticmethod(_lazy("misc", "export_text"))


@register(*ENGINES)
class UnityFonts(UnityFileExtractor):
    name, category, types = "unity.fonts", "font", ("Font",)
    export = staticmethod(_lazy("misc", "export_font"))


@register(*ENGINES)
class UnityVideo(UnityFileExtractor):
    name, category, types = "unity.video", "video", ("VideoClip",)
    export = staticmethod(_lazy("misc", "export_video"))


@register(*ENGINES)
class UnityTerrain(UnityFileExtractor):
    name, category, types = "unity.terrain", "map", ("TerrainData",)
    export = staticmethod(_lazy("misc", "export_terrain"))


@register(*ENGINES)
class UnityNavMesh(UnityFileExtractor):
    name, category, types = "unity.navmesh", "map", ("NavMeshData",)
    export = staticmethod(_lazy("misc", "export_navmesh"))


@register(*ENGINES)
class UnityMonoBehaviours(UnityFileExtractor):
    name, category, types = "unity.monobehaviours", "data", ("MonoBehaviour",)

    def begin(self, s: Session) -> None:
        from gst.engines.unity.export.mono import MonoCollector

        wanted = s.options.get("scripts")
        s.collector = MonoCollector(s, wanted=set(wanted) if wanted else None)

    def per_object(self, s, obj) -> list:
        before = s.collector.done
        s.collector.add(obj)
        return [1] if s.collector.done > before else []

    def finish(self, s: Session) -> None:
        s.collector.finish()


@register(*ENGINES)
class UnityLocalization(UnityFileExtractor):
    name, category, types = "unity.localization", "localization", ()

    def discover(self, ctx: Context) -> Iterable[Unit]:
        inv = inventory_dir(ctx)
        from gst.engines.unity.export.mono import LOCALIZATION_SCRIPTS

        for unit in _file_units(self, ctx, ("MonoBehaviour",)):
            summary = inventory.summary_for(inv, unit.id)
            if summary is not None and not set(summary.get("scripts", {})) & LOCALIZATION_SCRIPTS:
                continue
            yield unit

    def per_file(self, s: Session, sf) -> None:
        from gst.engines.unity.export.mono import export_localization

        objs = [o for o in sf.objects.values() if loader.type_name(o) == "MonoBehaviour"]
        export_localization(s, objs)


@register(*ENGINES)
class UnityPrefabsScenes(UnityFileExtractor):
    name, category, types = "unity.prefabs_scenes", "prefab_scene", ()

    def discover(self, ctx: Context) -> Iterable[Unit]:
        yield from _file_units(self, ctx, ("Transform", "RectTransform"))

    def per_file(self, s: Session, sf) -> None:
        from gst.engines.unity.export.prefab import export_hierarchy

        try:
            export_hierarchy(s, sf)
        except RecursionError as exc:
            s.failures.append(f"{sf.name}: hierarchy too deep ({exc})")


@register(*ENGINES)
class UnityAddressables(Extractor):
    name, category, resource = "unity.addressables", "data", "io"
    stage = 50
    after: tuple[str, ...] = ()
    out_subdir = OUT_SUBDIR

    def discover(self, ctx: Context) -> Iterable[Unit]:
        from gst.engines.unity.export.addressables import find_catalogs

        for p in find_catalogs(ctx.install_dir):
            rel = p.relative_to(ctx.install_dir).as_posix()
            yield Unit(id=rel, extractor=self.name, relpaths=[rel], size=p.stat().st_size)

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.engines.unity.export.addressables import export_catalog

        rel = unit.relpaths[0]
        w = Writer(ctx.out_root, ctx.workspace, source=rel, build=build_of(ctx), tool="gst", tool_version="",
                   extractor=self.name, unit=unit.id)
        export_catalog(w, Path(ctx.install_dir), Path(ctx.install_dir) / rel)
        return Result(outputs=w.outputs, out_bytes=w.out_bytes, tool="gst")


@register(*ENGINES)
class UnityAssetRipperProject(Extractor):
    """The whole Unity project reconstructed by AssetRipper (heavy, last in the plan)."""

    name, category, resource = "unity.assetripper_project", "other", "heavy"
    stage = 70  # heavy, last in the plan
    after: tuple[str, ...] = ("unity.inventory",)

    def discover(self, ctx: Context) -> Iterable[Unit]:
        total = 0
        for rel, _kind, size in loader.iter_unity_files(ctx.install_dir):
            total += size + loader.resource_size(ctx.install_dir, rel)
        yield Unit(id="project", extractor=self.name, relpaths=[], size=total, meta={"last": True})

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst import tools
        from gst.assets import assetripper
        from gst.common import GsError

        if tools.bin_path("assetripper") is None:
            return Result(status="unsupported", error="AssetRipper is not installed (gst tools install assetripper)",
                          tool="AssetRipper")
        base = Path(ctx.workspace) / "assets" / "assetripper"
        out = assetripper.fresh_dir(base, f"project@{build_of(ctx)}")
        try:
            info = assetripper.export(ctx.workspace, ctx.install_dir, mode="project", out=out,
                                      timeout=ctx.options.get("timeout"))
        except GsError as exc:
            return Result(status="failed", error=str(exc), tool="AssetRipper")
        return Result(outputs=[Path(info["out"]).relative_to(ctx.workspace).as_posix()], out_bytes=info["bytes"],
                      tool="AssetRipper", tool_version=info.get("tool_version", ""),
                      notes=[f"{info['files']} files in {info['seconds']} s"])
