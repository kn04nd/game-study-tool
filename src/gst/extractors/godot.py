"""Godot extractors over GDRE Tools: ``godot.inventory`` (every res:// path of the pack) and
``godot.recover`` (the whole project in ONE GDRE call: scenes/resources to text, textures to PNG,
audio to WAV/OGG, into ``assets/godot/project/``). A selection (``--opt godot.include=<glob>,<glob>``)
goes in batches whose command lines stay under 30k characters (WinError 206).
"""

from __future__ import annotations

import collections
import subprocess
from pathlib import Path

from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.code import GodotScripts, _blocked, _tool_version
from gst.extractors.registry import register

EXT_CATEGORY = {
    "scene": "prefab_scene", "resource": "data", "texture": "texture", "audio": "audio", "shader": "material_shader",
    "font": "font", "text": "data", "script": "code", "model": "mesh",
}
EXTRA_EXT = {".translation": "localization", ".po": "localization", ".anim": "animation", ".ogv": "video",
             ".import": "other", ".tres": "data", ".material": "material_shader", ".mesh": "mesh"}


def category_of(path: str) -> str:
    from gst.assets.godot import kind_of

    ext = Path(path).suffix.lower()
    if ext in EXTRA_EXT:
        return EXTRA_EXT[ext]
    return EXT_CATEGORY.get(kind_of(path), "other")


@register("godot")
class GodotInventory(GodotScripts):
    name = "godot.inventory"
    category = "data"
    resource = "io"
    stage = 40

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.assets import godot
        from gst.common import GsError

        try:
            summary = godot.inventory(ctx.workspace, ctx.install_dir, ctx.game.get("name") or "")
        except GsError as exc:
            return Result(status="blocked-policy" if exc.code == 3 else "failed", error=str(exc), tool="gdre_tools")
        outs = ["assets/inventory.jsonl", "assets/INVENTORY.md"]
        return Result(outputs=outs, out_bytes=sum((ctx.workspace / o).stat().st_size for o in outs), tool="gdre_tools",
                      tool_version=_tool_version("gdre"),
                      notes=[f"{summary['files']} files in the pack: " + ", ".join(f"{k} {v}" for k, v in summary["types"].items())])


@register("godot")
class GodotRecover(GodotScripts):
    name = "godot.recover"
    category = "prefab_scene"
    resource = "heavy"
    stage = 50

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.assets import write_folder_sidecar
        from gst.common import GsError
        from gst.decompile import godot
        from gst.pipeline.worker import commit, partial_dir, tree_bytes

        pack = ctx.install_dir / unit.relpaths[0]
        try:
            godot.check_pack(pack)
            exe = godot.gdre()
        except GsError as exc:
            return Result(status="blocked-policy" if exc.code == 3 else "failed", error=str(exc))
        staging = partial_dir(ctx, unit)
        out = staging / "project"
        includes = [g.strip() for g in str(ctx.options.get("godot.include") or "").split(",") if g.strip()]
        excludes = ("*.cs",)  # C# comes from code.dotnet (ilspycmd with PDB names)
        if includes:
            cmds = godot.batched_commands(exe, pack, out, includes, excludes)
        else:
            cmds = [godot.command(exe, pack, out, includes=(), excludes=excludes)]  # the whole project, one call
        code, logs = 0, []
        for i, cmd in enumerate(cmds):
            rc, text = godot.run(cmd, out, f"gdre-project-{i + 1}.log")
            code = code or rc
            logs.append(f"$ {subprocess.list2cmdline(cmd)}\n(exit {rc})\n{text}")
        files = [p for p in out.rglob("*") if p.is_file()] if out.exists() else []
        version = _tool_version("gdre")
        if not files:
            return Result(status="failed", error=f"GDRE recovered nothing (exit {code})\n" + "\n".join(logs),
                          tool="gdre_tools", tool_version=version)
        counts = collections.Counter(category_of(p.name) for p in files)
        final = ctx.workspace / "assets" / "godot" / "project"
        commit(out, final)
        for log in staging.glob("gdre-project-*.log"):
            commit(log, ctx.workspace / "assets" / "godot" / log.name)
        write_folder_sidecar(final, {"source": unit.relpaths[0], "res_path": "res://", "tool": "gdre_tools",
                                     "tool_version": version, "build": ctx.game.get("buildid"), "extractor": self.name,
                                     "unit": unit.id, "mode": "project" if not includes else "selection",
                                     "commands": len(cmds), "exit_code": code})
        notes = [f"count {cat} {n}" for cat, n in sorted(counts.items())]
        if code != 0:
            notes.append(f"GDRE exited with {code} but recovered {len(files)} files: see assets/godot/gdre-project-*.log")
        return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=tree_bytes(final),
                      tool="gdre_tools", tool_version=version, notes=notes)
