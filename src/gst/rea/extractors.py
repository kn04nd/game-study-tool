"""REA as ``gst study`` jobs. Static only: every job reads the install, writes under ``dumps/rea/``.

- ``rea.managed_members``: ``inspect_managed_members`` per managed assembly (game assemblies first, third-party
  after; ``System.*``/``Unity*``/``Mono.*``/``mscorlib``/``netstandard``/``Microsoft.*`` only with
  ``--opt rea.framework=1``), then the per-namespace split.
- ``rea.managed_graph``: ``project_managed_application_graph`` over each members Evidence (in-module calls and field
  accesses as an application graph).
- ``rea.il2cpp_metadata``: ``inspect_il2cpp_metadata`` with ``binary_path`` = GameAssembly (method RVAs, field and
  parameter types) and the caps raised to 2 GiB. An REA without ``--max-output-bytes`` (6.9.0 upstream) whose
  report passes 64 MiB fails; the job is then ``unsupported`` with REA's own reason and falls back to a
  metadata-only run (names, no addresses). Contract for code.il2cpp.native: the raw Evidence is
  ``dumps/rea/evidence/inspect_il2cpp_metadata/<GameAssembly|global-metadata>.json``.
- ``rea.artifact``: ``inspect_artifact`` of a Godot ``.pck`` and a cross-check of REA's entry list against gst's own
  reading of the PCK directory (``dumps/rea/artifact/<pck>.crosscheck.json``); ``--opt rea.extract=1`` also runs
  ``extract_artifact`` into ``dumps/rea/extract/<pck>/``.
- ``rea.import``: after ``code.index``, (re)builds the ``rea_*`` tables of ``index/code.db`` from every Evidence file
  (``gst q callers/callees`` read them).

Fingerprints include the REA version and whether it takes the IL2CPP caps, so installing a patched REA reruns
``rea.il2cpp_metadata``.
"""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path

from gst import policy
from gst.common import GsError, long_path, write_json
from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.registry import register

FRAMEWORK = re.compile(r"^(?:System|Unity|UnityEngine|Mono|Microsoft|mscorlib|netstandard|Godot(?:Sharp|Plugins)?)(?:\.|$)",
                       re.I)
# Managed code shows up in Unity Mono, Godot .NET, XNA/FNA and in IL2CPP installs that still ship managed assemblies;
# ``applies`` (managed assemblies found) decides, like ``code.dotnet``. Unreal ships none, so it never applies there.
MANAGED_ENGINES = ("*",)


def _opt(ctx: Context, key: str) -> bool:
    return str(ctx.options.get(key, "")).lower() in {"1", "true", "yes", "on"}


def _rel(ctx: Context, path: Path | str) -> str:
    return Path(path).resolve().relative_to(ctx.install_dir.resolve()).as_posix()


def _ws_rel(ctx: Context, path: str | Path) -> str:
    return Path(path).resolve().relative_to(Path(ctx.workspace).resolve()).as_posix()


def _blocked(ctx: Context, target: str) -> str | None:
    decision = policy.check(ctx.game, "decompile", target)
    return None if decision.allowed else f"{decision.reason} (guardrails.md rule {decision.rule})"


def _rea(ctx: Context):
    from gst.rea import runner

    return runner.find()


def _fingerprint(ctx: Context, relpaths: list[str], extra: str) -> str:
    from gst.pipeline.jobs import fingerprint_of, stat_inputs

    return fingerprint_of(stat_inputs(ctx.install_dir, relpaths), ctx.game.get("buildid"), extra)


_TAGS: dict[str, str] = {}


def _rea_tag(ctx: Context) -> str:
    """REA identity that changes the output: version + IL2CPP caps support (computed once per process and REA)."""
    from gst.rea import runner

    rea = runner.find()
    if rea is None:
        return "none"
    if rea.path not in _TAGS:
        _TAGS[rea.path] = f"{rea.version}|caps={int(runner.il2cpp_caps(rea))}"
    return _TAGS[rea.path]


def _missing() -> Result:
    return Result(status="failed", tool="rea",
                  error="REA is not installed (gst tools install rea; it needs Node.js 22.23+/24.22+/26.12+ on PATH)")


def _split(ctx: Context, evidence_path: str) -> dict:
    from gst.rea import split

    return split.split_file(evidence_path, Path(ctx.workspace) / split.SPLIT_DIR)


# --- managed --------------------------------------------------------------------------------------

DUMMY_DLLS = Path("decomp") / "il2cpp" / "dummydll"  # code.il2cpp.dummydll: Cpp2IL dummy assemblies of an IL2CPP game


def managed_assemblies(ctx: Context) -> list[dict]:
    """Managed assemblies of the install: game first, third-party next, framework last (stable within each). An
    IL2CPP install has none: then the dummy assemblies code.il2cpp.dummydll wrote to ``decomp/il2cpp/dummydll/`` (signatures and
    constructor chains only, no method bodies) are used, with ``workspace = True``."""
    from gst.decompile import dotnet

    found = dotnet.find_assemblies(ctx.install_dir)
    if not found:
        dummy = Path(ctx.workspace) / DUMMY_DLLS
        if dummy.is_dir():
            for dll in sorted(dummy.glob("*.dll")):
                found.append({"name": dll.stem, "path": str(dll), "size": dll.stat().st_size, "pdb": False,
                              "game": not dotnet.NOT_GAME.match(dll.stem), "workspace": True})
    for a in found:
        a["framework"] = bool(FRAMEWORK.match(a["name"]))
    return sorted(found, key=lambda a: (0 if a["game"] else 2 if a["framework"] else 1, a["name"].lower()))


def _assembly_unit(ctx: Context, extractor: str, a: dict) -> Unit:
    """A unit per assembly; dummy DLLs live in the workspace, so their inputs are fingerprinted there."""
    meta = {"name": a["name"], "game": a["game"], "framework": a["framework"]}
    if a.get("workspace"):
        from gst.pipeline.jobs import fingerprint_of, stat_inputs

        rel = Path(a["path"]).resolve().relative_to(Path(ctx.workspace).resolve()).as_posix()
        fp = fingerprint_of(stat_inputs(Path(ctx.workspace), [rel]), ctx.game.get("buildid"), f"{extractor}|{_rea_tag(ctx)}")
        return Unit(id=f"workspace:{rel}", extractor=extractor, relpaths=[], size=a["size"], fingerprint=fp,
                    meta={**meta, "workspace_path": rel})
    rel = _rel(ctx, a["path"])
    return Unit(id=rel, extractor=extractor, relpaths=[rel], size=a["size"],
                fingerprint=_fingerprint(ctx, [rel], f"{extractor}|{_rea_tag(ctx)}"), meta=meta)


def _assembly_path(ctx: Context, unit: Unit) -> Path:
    if unit.meta.get("workspace_path"):
        return Path(ctx.workspace) / unit.meta["workspace_path"]
    return ctx.install_dir / unit.relpaths[0]


@register(*MANAGED_ENGINES)
class ReaManagedMembers(Extractor):
    name = "rea.managed_members"
    category = "code"
    resource = "heavy"  # REA holds the whole member inventory in memory (~1 GB for REPO's Assembly-CSharp)
    stage = 32
    after = ("code.il2cpp.dummydll",)  # the Cpp2IL dummy assemblies (IL2CPP); ignored where that extractor is absent

    def applies(self, ctx: Context) -> bool:
        return bool(managed_assemblies(ctx))

    def discover(self, ctx: Context):
        for a in managed_assemblies(ctx):
            yield _assembly_unit(ctx, self.name, a)

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.rea import runner

        meta = unit.meta
        if meta.get("framework") and not _opt(ctx, "rea.framework"):
            return Result(status="skipped", notes=[f"framework assembly {meta['name']} (gst study --opt rea.framework=1 "
                                                   "includes it)"])
        dll = _assembly_path(ctx, unit)
        reason = _blocked(ctx, str(dll))
        if reason:
            return Result(status="blocked-policy", error=reason)
        rea = _rea(ctx)
        if rea is None:
            return _missing()
        res = runner.run(ctx.workspace, "inspect_managed_members", meta["name"], path=dll, rea=rea)
        if not res.ok:
            return Result(status="failed", error=res.error, tool="rea", tool_version=rea.version)
        summary = _split(ctx, res.evidence)
        return Result(outputs=[_ws_rel(ctx, res.evidence)], out_bytes=res.out_bytes, tool="rea", tool_version=rea.version,
                      notes=[f"{meta['name']}: {res.out_bytes} bytes of Evidence in {res.seconds} s (peak "
                             f"{(res.peak_rss or 0) // (1 << 20)} MB); split {summary['types']} types / "
                             f"{summary['namespaces']} namespaces"])


@register(*MANAGED_ENGINES)
class ReaManagedGraph(Extractor):
    name = "rea.managed_graph"
    category = "code"
    resource = "heavy"
    stage = 32
    after = ("rea.managed_members",)

    def applies(self, ctx: Context) -> bool:
        return bool(managed_assemblies(ctx))

    def discover(self, ctx: Context):
        for a in managed_assemblies(ctx):
            yield _assembly_unit(ctx, self.name, a)

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.rea import runner

        meta = unit.meta
        members = runner.evidence_dir(ctx.workspace, "inspect_managed_members") / f"{runner.safe_name(meta['name'])}.json"
        if not members.is_file():
            why = ("framework assembly (rea.framework=1)" if meta.get("framework") and not _opt(ctx, "rea.framework")
                   else "no inspect_managed_members Evidence (rea.managed_members failed or did not run)")
            return Result(status="skipped", notes=[f"{meta['name']}: {why}"])
        rea = _rea(ctx)
        if rea is None:
            return _missing()
        out = runner.evidence_dir(ctx.workspace, "project_managed_application_graph")
        os.makedirs(long_path(out), exist_ok=True)
        request = out / f"{runner.safe_name(meta['name'])}.request.json"
        _write_inline_request(request, "managed_members", members)
        res = runner.run(ctx.workspace, "project_managed_application_graph", meta["name"], request_file=request, rea=rea)
        # the request inlined the whole members Evidence: keep a small stub pointing at it instead of a second copy
        write_json(request, {"managed_members": {"inlined_from": _ws_rel(ctx, members)}})
        if not res.ok:
            return Result(status="failed", error=res.error, tool="rea", tool_version=rea.version)
        return Result(outputs=[_ws_rel(ctx, res.evidence)], out_bytes=res.out_bytes, tool="rea", tool_version=rea.version,
                      notes=[f"{meta['name']}: graph {res.out_bytes} bytes in {res.seconds} s"])


def _write_inline_request(path: Path, role: str, evidence_file: Path) -> None:
    """``{"<role>": <Evidence>}`` streamed byte for byte (a 200 MB Evidence is never parsed here)."""
    with open(long_path(path), "wb") as out, open(long_path(evidence_file), "rb") as src:
        out.write(b'{"' + role.encode("ascii") + b'":')
        while chunk := src.read(1 << 20):
            out.write(chunk)
        out.write(b"}")


# --- IL2CPP ---------------------------------------------------------------------------------------

IL2CPP_BINARIES = ("GameAssembly.dll", "GameAssembly.so", "libil2cpp.so", "GameAssembly.dylib")


def il2cpp_inputs(install: Path) -> tuple[Path, Path | None] | None:
    """(global-metadata.dat, GameAssembly) of an IL2CPP install, or None."""
    metas = sorted(install.glob("*_Data/il2cpp_data/Metadata/global-metadata.dat"))
    if not metas:
        return None
    binary = next((install / b for b in IL2CPP_BINARIES if (install / b).is_file()), None)
    return metas[0], binary


@register("unity-il2cpp")
class ReaIl2cppMetadata(Extractor):
    name = "rea.il2cpp_metadata"
    category = "code"
    resource = "heavy"  # ~1.1 GB peak for a 32 MB metadata + 160 MB GameAssembly
    stage = 32

    def applies(self, ctx: Context) -> bool:
        return il2cpp_inputs(ctx.install_dir) is not None

    def discover(self, ctx: Context):
        found = il2cpp_inputs(ctx.install_dir)
        if not found:
            return
        meta, binary = found
        relpaths = [_rel(ctx, meta)] + ([_rel(ctx, binary)] if binary else [])
        size = sum(os.path.getsize(ctx.install_dir / r) for r in relpaths)
        yield Unit(id="il2cpp", extractor=self.name, relpaths=relpaths, size=size,
                   fingerprint=_fingerprint(ctx, relpaths, f"rea.il2cpp_metadata|{_rea_tag(ctx)}|"
                                                           f"binary={ctx.options.get('rea.binary', '1')}"),
                   meta={"binary": bool(binary)})

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.rea import runner

        metadata = ctx.install_dir / unit.relpaths[0]
        binary = ctx.install_dir / unit.relpaths[1] if len(unit.relpaths) > 1 else None
        if str(ctx.options.get("rea.binary", "1")).lower() in {"0", "false", "no", "off"}:
            binary = None
        reason = _blocked(ctx, str(metadata))
        if reason:
            return Result(status="blocked-policy", error=reason)
        rea = _rea(ctx)
        if rea is None:
            return _missing()
        notes, outputs, status, error = [], [], "done", None
        if binary is not None:
            name = binary.stem  # GameAssembly
            res = runner.run(ctx.workspace, "inspect_il2cpp_metadata", name, path=metadata,
                             options={"--binary": runner.rea_path(binary)}, rea=rea)
            if res.ok:
                outputs.append(_ws_rel(ctx, res.evidence))
                notes.append(_il2cpp_note(res, binary=True, caps=runner.il2cpp_caps(rea)))
                out = runner.evidence_dir(ctx.workspace, "inspect_il2cpp_metadata")
                for suffix in (".json", ".request.json", ".run.json", ".error.json", ".stderr.txt"):
                    # a metadata-only fallback of an earlier, unpatched REA is superseded by the linked report
                    (out / f"{runner.safe_name(metadata.stem)}{suffix}").unlink(missing_ok=True)
                _split(ctx, res.evidence)
                return Result(outputs=outputs, out_bytes=res.out_bytes, tool="rea", tool_version=rea.version, notes=notes)
            if runner.error_code(res) == "resource_constraint" and not runner.il2cpp_caps(rea):
                status = "unsupported"
                error = (f"REA {rea.version} has a fixed 64 MiB IL2CPP report allowance and the report with {binary.name} "
                         f"exceeds it (build REA with patches/rea/, see docs/tools/rea.md, and install it with GST_REA_SPEC=<tarball>):\n"
                         f"{res.error}")
                notes.append("fell back to a metadata-only run (no method addresses, no field/parameter types)")
            else:
                return Result(status="failed", error=res.error, tool="rea", tool_version=rea.version)
        res = runner.run(ctx.workspace, "inspect_il2cpp_metadata", metadata.stem, path=metadata, rea=rea)
        if not res.ok:
            return Result(status="failed", error=(error + "\n\n" if error else "") + str(res.error), tool="rea",
                          tool_version=rea.version, notes=notes)
        outputs.append(_ws_rel(ctx, res.evidence))
        notes.append(_il2cpp_note(res, binary=False, caps=runner.il2cpp_caps(rea)))
        _split(ctx, res.evidence)
        return Result(status=status, error=error, outputs=outputs, out_bytes=res.out_bytes, tool="rea",
                      tool_version=rea.version, notes=notes)


def _il2cpp_note(res, *, binary: bool, caps: bool) -> str:
    return (f"{'metadata + binary' if binary else 'metadata only'}: {res.out_bytes} bytes of Evidence in {res.seconds} s, "
            f"peak {(res.peak_rss or 0) // (1 << 20)} MB, caps {'raised to 2 GiB' if caps else 'REA defaults'}")


# --- Godot PCK ------------------------------------------------------------------------------------

def read_pck_directory(path: Path) -> dict:
    """gst's own reading of a Godot PCK directory (format 1-3; a pck appended to an exe too): entries with path,
    offset (absolute in the file), size, md5. Refuses an encrypted directory (no key handling here)."""
    with open(long_path(path), "rb") as fh:
        start = 0
        head = fh.read(4)
        if head != b"GDPC":
            fh.seek(-12, os.SEEK_END)
            tail = fh.read(12)
            if tail[8:] != b"GDPC":
                raise GsError(f"{path.name}: no GDPC header")
            size = struct.unpack_from("<Q", tail, 0)[0]
            start = fh.seek(-12 - size, os.SEEK_END)
            if fh.read(4) != b"GDPC":
                raise GsError(f"{path.name}: no embedded GDPC header")
        fmt, major, minor, patch = struct.unpack("<4I", fh.read(16))
        flags, file_base, dir_offset = 0, 0, None
        if fmt >= 2:
            flags, file_base = struct.unpack("<IQ", fh.read(12))
            if fmt >= 3:
                (dir_offset,) = struct.unpack("<Q", fh.read(8))
        if flags & 1:
            raise GsError(f"{path.name}: encrypted PCK directory (guardrails.md rule 4: no key, not opened)")
        fh.read(16 * 4)
        if dir_offset is not None:
            fh.seek(start + dir_offset)
        base = start + file_base if (flags & 2) else file_base
        (count,) = struct.unpack("<I", fh.read(4))
        entries = []
        for _ in range(count):
            (n,) = struct.unpack("<I", fh.read(4))
            raw = fh.read(n)
            name = raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")
            offset, size = struct.unpack("<QQ", fh.read(16))
            md5 = fh.read(16).hex()
            entry_flags = struct.unpack("<I", fh.read(4))[0] if fmt >= 2 else 0
            entries.append({"path": name[6:] if name.startswith("res://") else name, "offset": base + offset,
                            "size": size, "md5": md5, "encrypted": bool(entry_flags & 1)})
    return {"format": fmt, "engine": f"{major}.{minor}.{patch}", "flags": flags, "pack_start": start,
            "file_base": file_base, "directory_offset": dir_offset, "entries": entries}


def crosscheck_pck(rea_evidence: dict, own: dict) -> dict:
    """REA's PCK occurrences vs gst's directory: same paths, sizes and absolute offsets?"""
    from gst.rea import evidence

    occ = []
    for rec in evidence.records(rea_evidence):
        nr = evidence.result(rec)
        for step in nr.get("substeps") or []:
            sub = step.get("evidence") or {}
            occ += evidence.result(sub).get("occurrences") or []
        occ += nr.get("occurrences") or []
    rea_files = {}
    for o in occ:
        if o.get("parent_occurrence_id") and o.get("entry_kind") == "file":
            loc = o.get("source_location") or {}
            rea_files[str(o.get("logical_path"))] = {"size": o.get("declared_size"), "offset": loc.get("offset"),
                                                     "hash_status": o.get("hash_status")}
    mine = {e["path"]: e for e in own["entries"]}
    both = sorted(set(rea_files) & set(mine))
    size_diff = [p for p in both if rea_files[p]["size"] != mine[p]["size"]]
    offset_diff = [p for p in both if rea_files[p]["offset"] is not None and rea_files[p]["offset"] != mine[p]["offset"]]
    unverified = sorted(p for p, v in rea_files.items() if v["hash_status"] not in ("verified", None))
    return {"rea_entries": len(rea_files), "gst_entries": len(mine), "matched": len(both),
            "only_rea": sorted(set(rea_files) - set(mine)), "only_gst": sorted(set(mine) - set(rea_files)),
            "size_mismatch": size_diff, "offset_mismatch": offset_diff, "hash_not_verified": unverified,
            "agree": not size_diff and not offset_diff and set(rea_files) == set(mine)}


@register("godot")
class ReaArtifact(Extractor):
    name = "rea.artifact"
    category = "data"
    resource = "heavy"
    stage = 32

    def applies(self, ctx: Context) -> bool:
        try:
            from gst.decompile import godot

            godot.find_pack(ctx.install_dir)
            return True
        except GsError:
            return False

    def discover(self, ctx: Context):
        from gst.decompile import godot

        pack = godot.find_pack(ctx.install_dir)
        rel = _rel(ctx, pack)
        yield Unit(id=rel, extractor=self.name, relpaths=[rel], size=pack.stat().st_size,
                   fingerprint=_fingerprint(ctx, [rel], f"rea.artifact|{_rea_tag(ctx)}|extract="
                                                        f"{int(_opt(ctx, 'rea.extract'))}"))

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.rea import evidence, runner

        pack = ctx.install_dir / unit.relpaths[0]
        reason = _blocked(ctx, str(pack))
        if reason:
            return Result(status="blocked-policy", error=reason)
        rea = _rea(ctx)
        if rea is None:
            return _missing()
        res = runner.run(ctx.workspace, "inspect_artifact", pack.name, path=pack, rea=rea)
        if not res.ok:
            return Result(status="failed", error=res.error, tool="rea", tool_version=rea.version)
        outputs = [_ws_rel(ctx, res.evidence)]
        notes = [f"inspect_artifact {pack.name}: {res.out_bytes} bytes in {res.seconds} s"]
        try:
            own = read_pck_directory(pack)
        except GsError as exc:
            return Result(status="unsupported", error=str(exc), outputs=outputs, tool="rea", tool_version=rea.version,
                          notes=notes)
        check = crosscheck_pck(evidence.load(res.evidence), own)
        check_path = Path(ctx.workspace) / "dumps" / "rea" / "artifact" / f"{pack.name}.crosscheck.json"
        write_json(check_path, {"pack": unit.relpaths[0], "format": own["format"], "engine": own["engine"], **check})
        outputs.append(_ws_rel(ctx, check_path))
        notes.append(f"PCK cross-check: REA {check['rea_entries']} entries, gst {check['gst_entries']}, matched "
                     f"{check['matched']}, size mismatches {len(check['size_mismatch'])}, offset mismatches "
                     f"{len(check['offset_mismatch'])} -> {'agree' if check['agree'] else 'DIFFER'}")
        if _opt(ctx, "rea.extract"):
            target = Path(ctx.workspace) / "dumps" / "rea" / "extract" / pack.name
            if target.exists():
                from gst.pipeline.worker import rmtree_force

                rmtree_force(long_path(target))
            target.parent.mkdir(parents=True, exist_ok=True)  # REA wants an absent target in an existing parent
            ext = runner.run(ctx.workspace, "extract_artifact", f"{pack.name}.extract", path=pack, rea=rea,
                             options={"--output-root": runner.rea_path(target)})
            if ext.ok:
                outputs.append(_ws_rel(ctx, ext.evidence))
            else:
                notes.append("extract_artifact failed (see the error file next to the Evidence)")
        return Result(outputs=outputs, out_bytes=res.out_bytes, tool="rea", tool_version=rea.version, notes=notes)


# --- import into code.db --------------------------------------------------------------------------

@register("*")
class ReaImport(Extractor):
    name = "rea.import"
    category = "code"
    resource = "cpu"
    stage = 34
    after = ("rea.managed_members", "rea.managed_graph", "rea.il2cpp_metadata", "rea.artifact", "code.index")
    after_earlier_stages = True  # code.index may rebuild code.db; the rea_* tables are rebuilt after it

    def discover(self, ctx: Context):
        from gst.pipeline.jobs import fingerprint_of
        from gst.rea.import_index import mtime_key

        yield Unit(id="code.db", extractor=self.name, relpaths=[], size=0,
                   fingerprint=fingerprint_of([list(k) for k in mtime_key(ctx.workspace)], ctx.game.get("buildid"),
                                              "rea.import"))

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.rea import import_index

        if not import_index.evidence_files(ctx.workspace):
            return Result(status="skipped", notes=["no REA Evidence in dumps/rea"])
        summary = import_index.import_workspace(ctx.workspace)
        write_json(Path(ctx.workspace) / import_index.REA_DIR / import_index.IMPORT_SUMMARY, summary)
        db = Path(ctx.workspace) / "index" / "code.db"
        return Result(outputs=["index/code.db"], out_bytes=db.stat().st_size if db.exists() else 0, tool="gst rea import",
                      notes=[f"rea_calls {summary['calls']}, rea_fields {summary['fields']}, rea_methods "
                             f"{summary['methods']}, rea_field_accesses {summary['field_accesses']} from "
                             f"{summary['files']} files ({summary['skipped_duplicates']} duplicate records skipped)"])
