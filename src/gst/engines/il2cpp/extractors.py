"""IL2CPP code extractors (engine key ``unity-il2cpp``), stage 20 (code), before the Unity inventory and exports.

- ``code.il2cpp.metadata`` (cpu): ``decomp/il2cpp/metadata.json`` = metadata header, counts, image names, the
  binary's basics, which catalog tools support the version, and whether UnityPy's TypeTreeGenerator reads the
  pair itself (``typetree_il2cpp``; the typetree chain skips that source when it does not).
- ``code.il2cpp.dummydll`` (heavy): dummy DLLs in ``decomp/il2cpp/dummydll/`` from the first catalog tool that
  supports the metadata version (Cpp2IL, then Il2CppDumper, then Il2CppInspector); raw output in
  ``decomp/il2cpp/<tool>/`` with ``MANIFEST.json``. The Unity exports wait for it (their ``after``), so
  MonoBehaviours get typetrees without the ``*_BackUpThisFolder*`` folder.
- ``code.il2cpp.maps`` (heavy): the name maps and headers for the Ghidra step, one unit per tool that supports the
  version: Il2CppInspector (``metadata.json``, ``il2cpp_ghidra.py``, ``cpp/appdata/*.h``) and Il2CppDumper
  (``script.json``, ``dump.cs.txt``, ``il2cpp.h``), each in ``decomp/il2cpp/<tool>/``.
- ``code.il2cpp.decompile`` (heavy): ilspycmd over the dummy DLLs -> ``decomp/cs-il2cpp/``, then the code index
  and the ``il2cpp_addresses`` table (``[Address]``/``[Token]``/``[FieldOffset]``).

Every unit's inputs are ``GameAssembly`` + ``global-metadata.dat`` (fingerprint = their size/mtime + buildid), so a
new build reruns them and an unchanged one is kept. A metadata version no catalog tool supports is ``unsupported``
with the reason, not ``failed``; an encrypted or obfuscated metadata (wrong magic) is ``unsupported`` and never
decrypted.

Options (``gst study --opt k=v``): ``il2cpp.all=1`` decompiles every dummy assembly (default: the game's own);
``il2cpp.tool=<id>`` forces the dummy DLL tool; ``il2cpp.maps=0`` skips the name maps; ``timeout`` (seconds, only
when given).
"""

from __future__ import annotations

import time
from pathlib import Path

from gst import policy
from gst.common import read_json, write_json
from gst.engines.il2cpp import metadata as md
from gst.engines.il2cpp import tooling
from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.registry import register

ENGINES = ("unity-il2cpp",)
IL2CPP_DIR = Path("decomp") / "il2cpp"
DUMMY_DIR = IL2CPP_DIR / "dummydll"
MAP_TOOLS = ("il2cppinspector", "il2cppdumper")


def _opt(ctx: Context, key: str, default: str = "") -> str:
    return str(ctx.options.get(key, default) or default)


def _on(ctx: Context, key: str, default: bool = False) -> bool:
    value = ctx.options.get(key)
    return default if value is None else str(value).lower() in {"1", "true", "yes", "on"}


def _timeout(ctx: Context) -> float | None:
    value = ctx.options.get("timeout")
    return float(value) if value not in (None, "", 0, "0") else None


def inputs(ctx: Context) -> dict | None:
    """The first IL2CPP binary + metadata pair of the install (relative paths), or None."""
    for found in md.find_inputs(ctx.install_dir):
        if found.get("binary"):
            return found
    return None


def _blocked(ctx: Context, target: Path) -> str | None:
    decision = policy.check(ctx.game, "decompile", str(target))
    return None if decision.allowed else f"{decision.reason} (guardrails.md rule {decision.rule})"


def _header(ctx: Context, found: dict) -> dict:
    try:
        return md.read_header(ctx.install_dir / found["metadata"])
    except (OSError, md.MetadataError) as exc:
        return {"standard": False, "reason": f"{exc.__class__.__name__}: {exc}"}


def _stamp(ctx: Context, found: dict) -> dict:
    """(size, mtime_ns) of the inputs: written in every MANIFEST so a later unit can tell an output is current."""
    out = {}
    for key in ("binary", "metadata"):
        st = (ctx.install_dir / found[key]).stat()
        out[found[key]] = [st.st_size, st.st_mtime_ns]
    return out


def _forced(ctx: Context, name: str) -> bool:
    """``gst study --force <name|prefix>``: rerun the tool even when its output is current."""
    force = ctx.options.get("force") or []
    force = force.split(",") if isinstance(force, str) else force
    return any(name == f or name.startswith(f + ".") for f in force if f)


def _manifest_current(path: Path, stamp: dict) -> bool:
    data = read_json(path, {}) or {}
    return data.get("inputs") == stamp and data.get("ok") is True


def _tool_exe(tool_id: str) -> Path | None:
    from gst import tools

    try:
        return tools.bin_path(tools.get(tool_id))
    except Exception:
        return None


class _Il2cpp(Extractor):
    category = "code"
    stage = 20

    def applies(self, ctx: Context) -> bool:
        return inputs(ctx) is not None

    def _unit(self, ctx: Context, uid: str, **meta) -> Unit:
        found = inputs(ctx)
        rel = [found["binary"], found["metadata"]]
        size = sum((ctx.install_dir / r).stat().st_size for r in rel)
        return Unit(id=uid, extractor=self.name, relpaths=rel, size=size, meta={**found, **meta})

    def discover(self, ctx: Context):
        yield self._unit(ctx, Path(inputs(ctx)["binary"]).name)

    def _check(self, ctx: Context, unit: Unit) -> tuple[dict | None, Result | None]:
        """Policy + header: ``(header, None)`` or ``(None, Result)`` to return as is."""
        binary = ctx.install_dir / unit.meta["binary"]
        reason = _blocked(ctx, binary)
        if reason:
            return None, Result(status="blocked-policy", error=reason)
        header = _header(ctx, unit.meta)
        if not header.get("standard"):
            return None, Result(status="unsupported", error=header.get("reason") or "not a standard IL2CPP metadata file",
                                notes=["metadata not readable as is: never decrypted (guardrails.md rule 4)"])
        return header, None


@register(*ENGINES)
class Il2cppMetadata(_Il2cpp):
    name = "code.il2cpp.metadata"
    resource = "cpu"

    def run(self, unit: Unit, ctx: Context) -> Result:
        t0 = time.time()
        meta_path = ctx.install_dir / unit.meta["metadata"]
        binary = ctx.install_dir / unit.meta["binary"]
        try:
            info = md.summarize(meta_path)
        except (OSError, md.MetadataError) as exc:
            return Result(status="failed", error=f"{exc.__class__.__name__}: {exc}", tool="gst il2cpp.metadata")
        version = md.version_key(info) if info.get("standard") else None
        data = {"format": 1, "game_build": (ctx.game or {}).get("buildid"), "inputs": _stamp(ctx, unit.meta),
                "metadata_file": unit.meta["metadata"], "binary_file": unit.meta["binary"], "metadata": info,
                "binary": md.binary_info(binary), "version_key": version,
                "tools": {"supporting": [t.id for t in tooling.supporting(version)] if version else [],
                          "catalog": tooling.support_table()},
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if info.get("standard") and _on(ctx, "il2cpp.probe_typetree", True):
            data["typetree_il2cpp"] = probe_typetree(binary, meta_path, (ctx.game.get("engine") or {}).get("version") or "")
        md.write(ctx.workspace, data)
        notes = []
        if info.get("standard"):
            counts = info.get("counts", {})
            notes.append(f"metadata v{version}: " + ", ".join(f"{counts[k]['count']} {k}" for k in
                                                               ("images", "types", "methods", "fields") if k in counts))
            notes.append("tools for this version: " + (", ".join(data["tools"]["supporting"]) or "none in the catalog"))
            if "typetree_il2cpp" in data:
                notes.append(f"TypeTreeGenerator il2cpp source: {'ok' if data['typetree_il2cpp']['ok'] else 'fails'}")
        else:
            notes.append(info.get("reason", "not a standard metadata file"))
        notes.append(f"{time.time() - t0:.1f} s")
        out = ctx.workspace / md.OUT_JSON
        return Result(outputs=[md.OUT_JSON.as_posix()], out_bytes=out.stat().st_size, tool="gst il2cpp.metadata",
                      tool_version="1", notes=notes,
                      status="done" if info.get("standard") else "unsupported",
                      error=None if info.get("standard") else info.get("reason"))


def probe_typetree(binary: Path, metadata: Path, unity_version: str) -> dict:
    """Does UnityPy's TypeTreeGenerator load this binary + metadata? (its bundled LibCpp2IL lags behind Cpp2IL)."""
    t0 = time.time()
    try:
        from gst.engines.unity import loader

        loader.unitypy()
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

        gen = TypeTreeGenerator(unity_version or "2022.3.0f1")
        gen.load_il2cpp(binary.read_bytes(), metadata.read_bytes())
        return {"ok": True, "seconds": round(time.time() - t0, 1), "unity_version": unity_version}
    except Exception as exc:  # the expected failure of an unknown metadata version included
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}", "seconds": round(time.time() - t0, 1),
                "unity_version": unity_version}


def _raw_manifest(tool_id: str, res: dict, *, version: str, stamp: dict, ctx: Context, role: str) -> dict:
    return {"tool": tool_id, "tool_version": tooling.tool_version(tool_id), "role": role, "ok": res["ok"],
            "command": res["command"], "exit_code": res["run"]["exit_code"], "seconds": res["run"]["seconds"],
            "metadata_version": version, "facts": res.get("facts", {}), "dlls": res.get("dlls", 0),
            "outputs": res.get("outputs", []), "inputs": stamp, "game_build": (ctx.game or {}).get("buildid"),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def run_tool(tool_id: str, ctx: Context, unit: Unit, raw: Path, dll_dir: Path | None) -> dict:
    """Run one IL2CPP tool with the unit's inputs; DLLs (when asked) into ``dll_dir``."""
    from gst.engines.il2cpp import cpp2il, il2cppdumper, inspector

    exe = _tool_exe(tool_id)
    if exe is None:
        return {"ok": False, "missing": True, "error": f"{tool_id} is not installed (gst tools install {tool_id})"}
    binary = ctx.install_dir / unit.meta["binary"]
    metadata = ctx.install_dir / unit.meta["metadata"]
    timeout = _timeout(ctx)
    if tool_id == "cpp2il":
        return cpp2il.run(exe, install=ctx.install_dir, raw_dir=raw, dll_dir=dll_dir or raw / "dummydll", timeout=timeout)
    if tool_id == "il2cppdumper":
        res = il2cppdumper.run(exe, binary=binary, metadata=metadata, raw_dir=raw, timeout=timeout)
        if dll_dir is not None and res["ok"] and (raw / "DummyDll").is_dir():
            import shutil

            shutil.copytree(raw / "DummyDll", dll_dir, dirs_exist_ok=True)
            res["dlls"] = len(tooling.files_under(dll_dir, "*.dll"))
        return res
    if tool_id == "il2cppinspector":
        return inspector.run(exe, binary=binary, metadata=metadata, raw_dir=raw, dll_dir=dll_dir, timeout=timeout)
    raise ValueError(tool_id)


def _failure_text(tool_id: str, res: dict) -> str:
    if res.get("missing"):
        return res["error"]
    return (f"{tool_id} exited with {res['run']['exit_code']} after {res['run']['seconds']} s\n"
            f"$ {' '.join(res['command'])}\n{res.get('output', '')}")


@register(*ENGINES)
class Il2cppDummyDll(_Il2cpp):
    name = "code.il2cpp.dummydll"
    resource = "heavy"
    after = ("code.il2cpp.metadata",)

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.pipeline.worker import commit, partial_dir, tree_bytes

        header, early = self._check(ctx, unit)
        if early:
            return early
        version = md.version_key(header)
        forced = _opt(ctx, "il2cpp.tool")
        candidates = [t.id for t in tooling.supporting(version)]
        if forced:
            candidates = [forced]
        if not candidates:
            return Result(status="unsupported", error=tooling.unsupported_reason(version),
                          notes=[f"metadata v{version}: no dummy DLL tool"])
        stamp = _stamp(ctx, unit.meta)
        staging = partial_dir(ctx, unit)
        errors = []
        for tool_id in candidates:
            raw = staging / tool_id
            dlls = staging / "dummydll"
            res = run_tool(tool_id, ctx, unit, raw, dlls)
            if res.get("missing"):
                errors.append(res["error"])
                continue
            manifest = _raw_manifest(tool_id, res, version=version, stamp=stamp, ctx=ctx, role="dummydll")
            write_json(raw / "MANIFEST.json", manifest)
            if res["ok"] and res.get("dlls"):
                write_json(dlls / "MANIFEST.json", {**manifest, "raw": (IL2CPP_DIR / tool_id).as_posix()})
                outputs = []
                for name in (tool_id, "dummydll"):
                    final = ctx.workspace / IL2CPP_DIR / name
                    commit(staging / name, final)
                    outputs.append(final)
                return Result(outputs=[p.relative_to(ctx.workspace).as_posix() for p in outputs],
                              out_bytes=sum(tree_bytes(p) for p in outputs), tool=tool_id,
                              tool_version=manifest["tool_version"],
                              notes=[f"{res['dlls']} dummy DLLs from {tool_id} (metadata v{version}) in "
                                     f"{res['run']['seconds']} s"] + [f"earlier attempt: {e.splitlines()[0]}" for e in errors])
            errors.append(_failure_text(tool_id, res))
            # keep the failed tool's log for the record, next to the outputs
            commit(raw, ctx.workspace / IL2CPP_DIR / tool_id)
            if dlls.exists():
                import shutil

                shutil.rmtree(dlls, ignore_errors=True)
        missing_only = all("is not installed" in e for e in errors)
        return Result(status="failed", error="\n\n".join(errors), tool=candidates[0],
                      notes=["install a tool: gst tools install " + candidates[0]] if missing_only else
                      [f"no dummy DLLs from {', '.join(candidates)}"])


@register(*ENGINES)
class Il2cppMaps(_Il2cpp):
    name = "code.il2cpp.maps"
    resource = "heavy"
    after = ("code.il2cpp.metadata", "code.il2cpp.dummydll")

    def discover(self, ctx: Context):
        if not _on(ctx, "il2cpp.maps", True):
            return
        header = _header(ctx, inputs(ctx))
        version = md.version_key(header) if header.get("standard") else ""
        tools = [t.id for t in tooling.supporting(version, MAP_TOOLS)] if version else []
        if not tools:
            yield self._unit(ctx, "maps", tool=None)
        for tool_id in tools:
            yield self._unit(ctx, tool_id, tool=tool_id)

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.pipeline.worker import commit, partial_dir, tree_bytes

        header, early = self._check(ctx, unit)
        if early:
            return early
        version = md.version_key(header)
        tool_id = unit.meta.get("tool")
        if not tool_id:
            return Result(status="unsupported", error=tooling.unsupported_reason(version),
                          notes=[f"metadata v{version}: no name-map tool ({', '.join(MAP_TOOLS)})"])
        final = ctx.workspace / IL2CPP_DIR / tool_id
        stamp = _stamp(ctx, unit.meta)
        if not _forced(ctx, self.name) and _manifest_current(final / "MANIFEST.json", stamp):
            return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=tree_bytes(final), tool=tool_id,
                          tool_version=tooling.tool_version(tool_id), notes=["already produced for these inputs"])
        staging = partial_dir(ctx, unit)
        raw = staging / tool_id
        res = run_tool(tool_id, ctx, unit, raw, None)
        if res.get("missing"):
            return Result(status="failed", error=res["error"], tool=tool_id, notes=[f"gst tools install {tool_id}"])
        manifest = _raw_manifest(tool_id, res, version=version, stamp=stamp, ctx=ctx, role="maps")
        write_json(raw / "MANIFEST.json", manifest)
        commit(raw, final)
        if not res["ok"]:
            status = "unsupported" if res.get("facts", {}).get("unsupported_version") else "failed"
            return Result(status=status, error=_failure_text(tool_id, res), tool=tool_id,
                          tool_version=manifest["tool_version"], outputs=[final.relative_to(ctx.workspace).as_posix()])
        return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=tree_bytes(final), tool=tool_id,
                      tool_version=manifest["tool_version"],
                      notes=[f"{tool_id}: {', '.join(manifest['outputs'])} in {res['run']['seconds']} s"])


@register(*ENGINES)
class Il2cppDecompile(_Il2cpp):
    name = "code.il2cpp.decompile"
    resource = "heavy"
    after = ("code.il2cpp.dummydll",)

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst import index
        from gst.common import GsError
        from gst.engines.il2cpp import decompile
        from gst.pipeline.worker import tree_bytes

        header, early = self._check(ctx, unit)
        if early:
            return early
        dummy = ctx.workspace / DUMMY_DIR
        if not tooling.files_under(dummy, "*.dll"):
            version = md.version_key(header)
            if not tooling.supporting(version):
                return Result(status="unsupported", error=tooling.unsupported_reason(version))
            return Result(status="failed", error=f"no dummy DLLs in {dummy}: code.il2cpp.dummydll did not produce them "
                                                 "(gst jobs list --extractor code.il2cpp.dummydll)")
        try:
            result = decompile.decompile(ctx.workspace, dummy_dir=dummy, everything=_on(ctx, "il2cpp.all"),
                                         timeout=_timeout(ctx))
        except GsError as exc:
            return Result(status="failed", error=str(exc), tool="ilspycmd")
        out = ctx.workspace / decompile.OUT_DIR
        files = sum(v.get("files", 0) for v in result["assemblies"].values())
        summary = index.build(ctx.workspace)
        addresses = decompile.build_addresses(ctx.workspace)
        failed = result["failed"]
        notes = [f"{len(result['assemblies'])} assemblies, {files} C# files ({len(result['skipped'])} framework/third-party "
                 "skipped; --opt il2cpp.all=1 includes them)",
                 f"index: {summary.get('files')} files, {summary.get('symbols')} symbols",
                 f"il2cpp_addresses: {addresses.get('rows')} attributed declarations, {addresses.get('with_rva')} with an RVA"]
        if failed:
            notes.append(f"ilspycmd failed on: {', '.join(failed)}")
        from gst import tools

        status = "failed" if failed and len(failed) == len(result["assemblies"]) else "done"
        error = None
        if failed:
            error = "\n".join(f"{name}: exit {result['assemblies'][name].get('exit_code')} (see decomp/cs-il2cpp/{name}.log)"
                              for name in failed)
        outputs = [out, ctx.workspace / decompile.ADDRESSES_TSV]
        return Result(outputs=[p.relative_to(ctx.workspace).as_posix() for p in outputs if p.exists()],
                      out_bytes=tree_bytes(out), status=status, error=error, tool="ilspycmd",
                      tool_version=tools.installed_version(tools.get("ilspycmd")) or "", notes=notes)


SEQUENCE = (Il2cppMetadata, Il2cppDummyDll, Il2cppMaps, Il2cppDecompile)


def run_all(ws: Path, game: dict, options: dict | None = None, *, maps: bool = False) -> dict[str, list[dict]]:
    """The IL2CPP extractors in order, in this process (``gst decompile il2cpp``): ``{name: [result per unit]}``.

    A step that does not finish ``done`` stops the ones that need it (no dummy DLLs, no decompile)."""
    from gst.pipeline.worker import context_for

    out: dict[str, list[dict]] = {}
    for cls in SEQUENCE:
        if cls is Il2cppMaps and not maps:
            continue
        ext = cls()
        ctx = context_for(ws, game, options)
        if not ext.applies(ctx):
            out[cls.name] = [{"unit": None, "status": "skipped", "error": "no GameAssembly + global-metadata.dat"}]
            break
        results = []
        for unit in ext.discover(ctx):
            res = ext.run(unit, ctx)
            results.append({"unit": unit.id, "status": res.status, "error": res.error, "notes": res.notes,
                            "outputs": res.outputs, "tool": res.tool, "tool_version": res.tool_version})
        out[cls.name] = results
        if cls in (Il2cppMetadata, Il2cppDummyDll) and any(r["status"] != "done" for r in results):
            break
    return out
