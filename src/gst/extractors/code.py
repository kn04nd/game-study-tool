"""Code extractors over the existing decompile backends: ``code.dotnet`` (ilspycmd, one unit per
assembly), ``code.godot`` (GDRE, GDScript), ``code.native`` (Ghidra, one unit per module, every
function, any size) and ``code.index`` (the code index, after the decompilers).

Options (``gst study --opt key=value``): ``dotnet.all=1`` also decompiles framework/third-party
assemblies; ``native.all=1`` also runs Ghidra on engine runtimes and third-party modules;
``native.function_timeout=N`` limits the decompiler per function (default: none). Java and Lua: ``javalua.py``.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from gst import policy
from gst.common import long_path, read_json, write_json, write_text
from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.registry import register

CS_MANIFESTS = Path("decomp") / "cs" / "_manifest"


def _rel(ctx: Context, path: Path | str) -> str:
    return Path(path).resolve().relative_to(ctx.install_dir.resolve()).as_posix()


def _opt(ctx: Context, key: str) -> bool:
    return str(ctx.options.get(key, "")).lower() in {"1", "true", "yes", "on"}


def _tool_version(tool_id: str) -> str:
    from gst import tools

    try:
        return tools.installed_version(tools.get(tool_id)) or ""
    except Exception:
        return ""


def _blocked(ctx: Context, target: str) -> str | None:
    decision = policy.check(ctx.game, "decompile", target)
    return None if decision.allowed else f"{decision.reason} (guardrails.md rule {decision.rule})"


@register()
class DotnetCode(Extractor):
    name = "code.dotnet"
    category = "code"
    resource = "cpu"
    stage = 20

    def _found(self, ctx: Context) -> list[dict]:
        from gst.decompile import dotnet

        return dotnet.find_assemblies(ctx.install_dir)

    def applies(self, ctx: Context) -> bool:
        return bool(self._found(ctx))

    def discover(self, ctx: Context):
        for a in self._found(ctx):
            rel = _rel(ctx, a["path"])
            relpaths = [rel] + ([rel.rsplit(".", 1)[0] + ".pdb"] if a["pdb"] else [])
            yield Unit(id=rel, extractor=self.name, relpaths=relpaths, size=a["size"],
                       meta={"name": a["name"], "game": a["game"], "pdb": a["pdb"]})

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst import tools
        from gst.decompile import dotnet
        from gst.pipeline.worker import commit, partial_dir, tree_bytes

        meta = unit.meta
        if not meta.get("game") and not _opt(ctx, "dotnet.all"):
            return Result(status="skipped", notes=[f"framework/engine/third-party assembly {meta['name']}: not the game's code "
                                                   "(gst study --opt dotnet.all=1 includes it)"])
        dll = ctx.install_dir / unit.relpaths[0]
        reason = _blocked(ctx, str(dll))
        if reason:
            return Result(status="blocked-policy", error=reason)
        exe = tools.bin_path(tools.get("ilspycmd"))
        if exe is None:
            return Result(status="failed", error="ilspycmd is not installed (gst tools install ilspycmd)")
        staging = partial_dir(ctx, unit)
        cmd = dotnet.command(exe, dll, staging, pdb=bool(meta.get("pdb")))
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        files = sum(1 for _ in staging.rglob("*.cs"))
        version = _tool_version("ilspycmd")
        if proc.returncode != 0 and not files:
            return Result(status="failed", error=f"ilspycmd exited with {proc.returncode}\n$ {subprocess.list2cmdline(cmd)}\n"
                                                 f"{proc.stdout}\n{proc.stderr}", tool="ilspycmd", tool_version=version)
        final = ctx.workspace / "decomp" / "cs" / meta["name"]
        commit(staging, final)
        write_text(final.parent / f"{meta['name']}.log", f"$ {subprocess.list2cmdline(cmd)}\n\n{proc.stdout}\n{proc.stderr}")
        from gst.assets import sha256_file

        write_json(ctx.workspace / CS_MANIFESTS / f"{meta['name']}.json", {
            "source": str(dll), "size": unit.size, "pdb": meta.get("pdb"), "files": files, "exit_code": proc.returncode,
            "errors": len(re.findall(r"(?im)^\s*(?:error|exception|failed)\b", proc.stderr or "")),
            "sha256": sha256_file(dll), "command": cmd, "tool_version": version})
        return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=tree_bytes(final), tool="ilspycmd",
                      tool_version=version, notes=[f"{files} C# files"])


@register("godot")
class GodotScripts(Extractor):
    name = "code.godot"
    category = "code"
    resource = "cpu"
    stage = 20

    def applies(self, ctx: Context) -> bool:
        from gst.common import GsError
        from gst.decompile import godot

        try:
            godot.find_pack(ctx.install_dir)
            return True
        except GsError:
            return False

    def discover(self, ctx: Context):
        from gst.decompile import godot

        pack = godot.find_pack(ctx.install_dir)
        yield Unit(id=pack.name, extractor=self.name, relpaths=[_rel(ctx, pack)], size=pack.stat().st_size)

    def run(self, unit: Unit, ctx: Context) -> Result:
        import time

        from gst.common import GsError
        from gst.decompile import godot
        from gst.pipeline.worker import commit, partial_dir, tree_bytes

        pack = ctx.install_dir / unit.relpaths[0]
        reason = _blocked(ctx, str(pack))
        if reason:
            return Result(status="blocked-policy", error=reason)
        try:
            info = godot.check_pack(pack)
            exe = godot.gdre()
        except GsError as exc:
            return Result(status="blocked-policy" if exc.code == 3 else "failed", error=str(exc))
        staging = partial_dir(ctx, unit)
        out = staging / "godot"
        cmd = godot.command(exe, pack, out)
        code, text = godot.run(cmd, out, "godot.log")
        scripts = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.gd")) if out.exists() else []
        version = _tool_version("gdre")
        if code != 0 and not scripts:
            if not out.exists() or not any(out.iterdir()):
                return Result(status="failed", error=f"GDRE exited with {code}\n$ {subprocess.list2cmdline(cmd)}\n{text}",
                              tool="gdre_tools", tool_version=version)
        out.mkdir(parents=True, exist_ok=True)
        from gst.decompile.dotnet import sha256_of

        write_json(out / "MANIFEST.json", {"backend": "godot", "tool": "gdre_tools", "tool_version": version,
                                           "source": str(pack), "sha256": sha256_of(pack), "godot_version": info.get("version"),
                                           "game_build": ctx.game.get("buildid"), "scripts": len(scripts), "command": cmd,
                                           "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        final = ctx.workspace / "decomp" / "godot"
        commit(out, final)
        log = staging / "godot.log"
        if log.exists():
            commit(log, ctx.workspace / "decomp" / "godot.log")
        return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=tree_bytes(final), tool="gdre_tools",
                      tool_version=version, notes=[f"{len(scripts)} GDScript files" + ("" if scripts else " (a C#-only game)")])


@register()
class NativeCode(Extractor):
    name = "code.native"
    category = "code"
    resource = "heavy"
    stage = 70
    # engine key -> module file names (lower case) another extractor analyzes for that engine, never this one
    # (code.il2cpp.native runs Ghidra on GameAssembly with the IL2CPP names; analyzing it twice costs hours).
    handled_elsewhere: dict[str, dict[str, str]] = {"unity-il2cpp": {"gameassembly.dll": "code.il2cpp.native"}}

    def _found(self, ctx: Context) -> list[dict]:
        from gst.decompile import native

        return native.find_modules(ctx.install_dir, ctx.game)

    def applies(self, ctx: Context) -> bool:
        return bool(self._found(ctx))

    def discover(self, ctx: Context):
        for m in self._found(ctx):
            rel = _rel(ctx, m["path"])
            relpaths = [rel] + ([rel.rsplit(".", 1)[0] + ".pdb"] if m["pdb"] else [])
            yield Unit(id=rel, extractor=self.name, relpaths=relpaths, size=m["size"],
                       meta={k: m[k] for k in ("name", "game", "third_party", "large", "pdb")})

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.common import GsError
        from gst.decompile import native
        from gst.pipeline.worker import tree_bytes

        meta = unit.meta
        engine = str((ctx.game.get("engine") or {}).get("key") or "")
        other = self.handled_elsewhere.get(engine, {}).get(str(meta.get("name", "")).lower())
        if other:
            return Result(status="skipped", notes=[f"{meta['name']}: analyzed by {other} (with the engine's names)"])
        if not meta.get("game") and not _opt(ctx, "native.all"):
            why ="third-party/runtime module" if meta.get("third_party") else "engine runtime or module without a PDB"
            return Result(status="skipped", notes=[f"{meta['name']}: {why}, not the game's own code "
                                                   "(gst study --opt native.all=1 runs Ghidra on it too)"])
        reason = _blocked(ctx, str(ctx.install_dir / unit.relpaths[0]))
        if reason:
            return Result(status="blocked-policy", error=reason)
        try:
            result = native.decompile(ctx.workspace, {**ctx.game, "install_dir": str(ctx.install_dir)}, names=[meta["name"]],
                                      max_functions=0, analysis_timeout=None,
                                      seconds=int(ctx.options.get("native.function_timeout") or 0))
        except GsError as exc:
            return Result(status="failed", error=str(exc), tool="ghidra")
        name = native.safe_name(meta["name"])
        info = result["modules"].get(name, {})
        version = _tool_version("ghidra")
        if info.get("failed"):
            log = Path(info.get("log") or "")
            console = ctx.workspace / "logs" / f"ghidra-{name}.console.txt"
            text = console.read_text(encoding="utf-8", errors="replace") if console.exists() else ""
            return Result(status="failed", error=f"Ghidra produced no export for {meta['name']} (exit {info.get('exit_code')}); "
                                                 f"log {log}\n{text}", tool="ghidra", tool_version=version)
        out = ctx.workspace / native.OUT_DIR / name
        return Result(outputs=[out.relative_to(ctx.workspace).as_posix()], out_bytes=tree_bytes(out), tool="ghidra",
                      tool_version=version, notes=[f"{info.get('decompiled')} of {info.get('functions')} functions decompiled"])


@register()
class CodeIndex(Extractor):
    name = "code.index"
    category = "code"
    resource = "cpu"
    stage = 30
    after = ("code.dotnet", "code.godot", "code.native", "code.il2cpp.native", "code.java", "code.lua")
    after_earlier_stages = True  # rebuilt (incrementally) whenever a decompile job ran again (e.g. --retry-failed)

    def discover(self, ctx: Context):
        # inputs = the code inputs of the decompilers, so the index reruns when the code changes
        from gst.extractors.javalua import JavaCode, LuaCode

        relpaths: list[str] = []
        for cls in (DotnetCode, GodotScripts, NativeCode, JavaCode, LuaCode):
            ext = cls()
            try:
                if ext.applies(ctx):
                    relpaths += [p for u in ext.discover(ctx) for p in u.relpaths]
            except Exception:
                continue
        yield Unit(id="code.db", extractor=self.name, relpaths=sorted(set(relpaths)), size=0)

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst import index

        frags = ctx.workspace / CS_MANIFESTS
        if frags.is_dir():  # per-assembly manifests (parallel workers) -> decomp/cs/MANIFEST.json
            manifest_path = ctx.workspace / "decomp" / "cs" / "MANIFEST.json"
            manifest = read_json(manifest_path, {}) or {}
            assemblies = manifest.get("assemblies", {})
            for frag in sorted(frags.glob("*.json")):
                assemblies[frag.stem] = read_json(frag, {}) or {}
            write_json(manifest_path, {**manifest, "backend": "dotnet", "tool": "ilspycmd",
                                       "game_build": ctx.game.get("buildid"), "assemblies": assemblies})
        if not (ctx.workspace / "decomp").is_dir() or not any((ctx.workspace / "decomp").iterdir()):
            return Result(status="skipped", notes=["nothing decompiled yet: no code to index"])
        summary = index.build(ctx.workspace, jobs=max(1, (os.cpu_count() or 2) // 2))
        db = index.db_path(ctx.workspace)
        failures = summary.get("failures") or []
        return Result(outputs=[db.relative_to(ctx.workspace).as_posix()], out_bytes=db.stat().st_size if db.exists() else 0,
                      tool="gst index", notes=[f"{summary.get('files')} files indexed, {summary.get('symbols')} symbols"]
                      + [f"not indexed: {f}" for f in failures])
