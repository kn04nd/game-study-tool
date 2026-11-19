"""Engine-independent extractors: ``audio.vgmstream`` (every audio container in the install, one unit per
file, every stream to WAV) and ``maps.list`` (level files in the install and the asset inventories)."""

from __future__ import annotations

from pathlib import Path

from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.code import _tool_version
from gst.extractors.files import walk_files
from gst.extractors.registry import register


@register()
class AudioContainers(Extractor):
    name = "audio.vgmstream"
    category = "audio"
    resource = "cpu"
    stage = 50

    def discover(self, ctx: Context):
        from gst.assets.audio import AUDIO_EXT

        for rel, size in walk_files(ctx.install_dir):
            low = rel.lower()
            if Path(low).suffix in AUDIO_EXT and not low.endswith(".strings.bank"):
                yield Unit(id=rel, extractor=self.name, relpaths=[rel], size=size)

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst import tools
        from gst.assets import audio, safe_name
        from gst.pipeline.worker import commit, partial_dir, tree_bytes

        if tools.bin_path("vgmstream") is None:
            return Result(status="failed", error="vgmstream-cli is not installed (gst tools install vgmstream)")
        src = ctx.install_dir / unit.relpaths[0]
        staging = partial_dir(ctx, unit)
        result = audio.convert(ctx.workspace, [src], out_root=staging, build=str(ctx.game.get("buildid") or "?"))
        folder = staging / safe_name(src.stem, 60)
        version = _tool_version("vgmstream")
        if not folder.exists() or not any(folder.iterdir()):
            status = "unsupported" if not result["matched"] else "failed"
            return Result(status=status, error=f"vgmstream read {result['matched']} streams from {unit.id}\n"
                                               + "\n".join(result["failed"]), tool="vgmstream", tool_version=version)
        final = ctx.workspace / "assets" / "audio" / Path(unit.id).parent.as_posix().replace("/", "__") / folder.name
        commit(folder, final)
        return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=tree_bytes(final), tool="vgmstream",
                      tool_version=version, status="done",
                      notes=[f"{sum(result['exported'].values())} of {result['matched']} streams"]
                      + [f"stream failed: {f}" for f in result["failed"]])


@register()
class MapsList(Extractor):
    name = "maps.list"
    category = "map"
    resource = "io"
    stage = 60
    after = ("godot.inventory", "unreal.inventory", "unity.inventory")

    def discover(self, ctx: Context):
        yield Unit(id="levels", extractor=self.name, relpaths=[], size=0)

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.assets import levels

        s = levels.find(ctx.workspace, ctx.install_dir)
        out = ctx.workspace / s["out"]
        return Result(outputs=[s["out"]], out_bytes=out.stat().st_size, tool="gst maps",
                      notes=[f"{s['levels']} level files: " + ", ".join(f"{k} {v}" for k, v in s["by_kind"].items())])
