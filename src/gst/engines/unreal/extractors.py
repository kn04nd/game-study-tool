"""Unreal 4/5 jobs for ``gst study``:

- ``unreal.inventory`` (stage 40): every packed path with its container (repak / stdlib v10-v12 lister / retoc
  list; Manifest_UFSFiles for encrypted containers), headers in ``assets/unreal/containers.json``.
- ``unreal.iostore`` (stage 45): ``retoc to-legacy`` of the IoStore folder into ``dumps/unreal/legacy/`` (option
  ``unreal.filter`` = comma-separated path substrings; none = everything), then uas JSON on the legacy pak into
  ``assets/unreal/iostore/json/``. The legacy ``.uasset``/``.uexp`` pair also feeds tools that only read legacy paks.
- ``unreal.export`` (json, ``data``), ``unreal.textures`` (``texture``), ``unreal.models`` (``mesh``), ``unreal.audio``
  (``audio``), ``unreal.animations`` (``animation``): one uas run per container and mode, the container's packed
  paths as one prefix-trie regex in a response file (no filter when the container holds every listed path), into
  ``assets/unreal/containers/<container>/<mode>/``. Option ``unreal.modes`` (default ``json,textures,models,audio``);
  ``unreal.name`` = a regex on the packed path that narrows every container (default: everything);
  ``unreal.all_types=1`` = JSON also of the classes uas skips by default (SkeletalMesh, AnimMontage).

Resumable: the unit fingerprint covers the container files, the mode, the filter, and whether a published key or
a ``.usmap`` is present, so adding a key or mappings reruns the blocked units. Encrypted without a published key
and packages that need a missing ``.usmap`` are ``blocked-policy`` with the reason.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.registry import register

DEFAULT_MODES = ("json", "textures", "models", "audio")


def _paks(ctx: Context) -> Path | None:
    from gst.common import GsError
    from gst.engines.unreal import paks

    try:
        return paks.paks_dir(ctx.install_dir)
    except GsError:
        return None


def _rel(ctx: Context, paks: Path) -> str:
    return paks.resolve().relative_to(ctx.install_dir.resolve()).as_posix()


def _access_tag(ctx: Context) -> str:
    """Changes when the user adds a key file or a .usmap (so blocked units rerun)."""
    from gst.engines.unreal import keys, usmap

    found, _ = keys.load(ctx.workspace, ctx.game)
    mapping, _src = usmap.find(ctx.workspace, ctx.game)
    return f"keys={len(found)}|usmap={mapping.name if mapping else '-'}"


def _fingerprint(ctx: Context, relpaths: list[str], extra: str) -> str:
    from gst.pipeline.jobs import fingerprint_of, stat_inputs

    return fingerprint_of(stat_inputs(ctx.install_dir, relpaths), ctx.game.get("buildid"), extra)


def _all_types(ctx: Context) -> bool:
    """Option ``unreal.all_types=1``: JSON exports also hold the classes uas skips by default (SkeletalMesh, AnimMontage)."""
    return str(ctx.options.get("unreal.all_types") or "").lower() in ("1", "true", "yes")


def _modes(ctx: Context) -> list[str]:
    return [m.strip() for m in str(ctx.options.get("unreal.modes") or ",".join(DEFAULT_MODES)).split(",") if m.strip()]


@register("unreal")
class UnrealInventory(Extractor):
    name = "unreal.inventory"
    category = "data"
    resource = "io"
    stage = 40

    def applies(self, ctx: Context) -> bool:
        return _paks(ctx) is not None

    def discover(self, ctx: Context):
        from gst.engines.unreal import paks as paks_mod

        paks = _paks(ctx)
        items = paks_mod.containers(paks)
        rel = _rel(ctx, paks)
        relpaths = [f"{rel}/{c['name']}" for c in items]
        yield Unit(id="containers", extractor=self.name, relpaths=relpaths, size=sum(c["size"] for c in items),
                   fingerprint=_fingerprint(ctx, relpaths, "inventory-v2|" + _access_tag(ctx)))

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.common import GsError
        from gst.engines.unreal import inventory, tool_version

        try:
            s = inventory.inventory(ctx.workspace, ctx.install_dir, ctx.game)
        except GsError as exc:
            return Result(status="blocked-policy" if exc.code == 3 else "failed", error=str(exc), tool="repak/retoc/uas")
        outs = ["assets/inventory.jsonl", "assets/INVENTORY.md", "assets/unreal/containers.json"]
        notes = [f"{s['files']} packed files from {s['source']}"]
        if s["blocked"]:
            notes.append("encrypted without a published key (diagnosed, listed from the manifest when shipped): "
                         + ", ".join(s["blocked"]))
        return Result(outputs=outs, out_bytes=sum((ctx.workspace / o).stat().st_size for o in outs), tool="repak/retoc/uas",
                      tool_version=f"repak {tool_version('repak')}, retoc {tool_version('retoc')}", notes=notes)


@register("unreal")
class UnrealIoStore(Extractor):
    name = "unreal.iostore"
    category = "data"
    resource = "heavy"
    stage = 45
    after = ("unreal.inventory",)

    def applies(self, ctx: Context) -> bool:
        paks = _paks(ctx)
        return paks is not None and any(paks.glob("*.utoc"))

    def discover(self, ctx: Context):
        from gst.engines.unreal import paks as paks_mod

        paks = _paks(ctx)
        rel = _rel(ctx, paks)
        items = [c for c in paks_mod.containers(paks) if c["kind"] in ("iostore", "global")]
        relpaths = [f"{rel}/{c['name']}" for c in items] + [f"{rel}/{c['name'][:-5]}.ucas" for c in items]
        flt = str(ctx.options.get("unreal.filter") or "")
        yield Unit(id="iostore", extractor=self.name, relpaths=relpaths, size=sum(c["size"] for c in items),
                   meta={"filter": flt, "encrypted": [c["name"] for c in items if c["encrypted"]]},
                   fingerprint=_fingerprint(ctx, relpaths, f"iostore-v2|{flt}|all_types={_all_types(ctx)}|"
                                            + _access_tag(ctx)))

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.assets import write_folder_sidecar
        from gst.common import GsError
        from gst.engines.unreal import export, retoc, tool_version, uas, usmap
        from gst.engines.unreal import paks as paks_mod
        from gst.pipeline.worker import commit, partial_dir, tree_bytes

        paks = _paks(ctx)
        items = paks_mod.containers(paks)
        try:
            plan = export.iostore_plan(ctx.workspace, ctx.game, items)
        except GsError as exc:
            return Result(status="blocked-policy" if exc.code == 3 else "failed", error=str(exc), tool="retoc",
                          tool_version=tool_version("retoc") or "")
        filters = [f.strip() for f in unit.meta.get("filter", "").split(",") if f.strip()]
        game_enum = paks_mod.ue_game(paks, ctx.game)
        staging = partial_dir(ctx, unit)
        try:
            source, how = export.mount_folder(paks, plan, staging / "mount")
            conv = retoc.to_legacy(source, staging / "legacy" / "legacy.pak", staging / "retoc.log", filters=filters or None,
                                   version=paks_mod.retoc_version(game_enum), key=plan["key"])
        except GsError as exc:
            return Result(status="blocked-policy" if exc.code == 3 else "failed", error=str(exc), tool="retoc",
                          tool_version=tool_version("retoc") or "")
        if not conv["paks"] or conv["extracted"] == 0:
            return Result(status="unsupported", error=f"retoc converted no package (filter {filters or 'none'}; "
                                                      f"containers {', '.join(plan['convert'])})", tool="retoc",
                          tool_version=tool_version("retoc") or "")
        mapping, _src = usmap.find(ctx.workspace, ctx.game)
        proc = uas.export(staging / "legacy", staging / "json", "json", game_enum, log=staging / "uas.log",
                          rsp=staging / "uas.rsp", usmap=mapping, all_types=_all_types(ctx))
        files = [p for p in (staging / "json").rglob("*.json") if p.name != ".uas-manifest.json"]
        if not files and mapping is None and usmap.needs_mappings(proc.stdout + proc.stderr):
            return Result(status="blocked-policy", error=usmap.NEEDS_REASON, tool="uas")
        legacy_final = ctx.workspace / "dumps" / "unreal" / "legacy" / "study"
        json_final = ctx.workspace / "assets" / "unreal" / "iostore" / "json"
        commit(staging / "legacy", legacy_final)
        if (staging / "json").exists():
            commit(staging / "json", json_final)
            write_folder_sidecar(json_final, {"source": unit.relpaths[0], "res_path": "iostore", "tool": "retoc+uas",
                                              "tool_version": f"retoc {tool_version('retoc')}, uas {tool_version('uas')}",
                                              "build": ctx.game.get("buildid"), "filters": filters, "game": game_enum,
                                              "extractor": self.name, "unit": unit.id, "converted": plan["convert"],
                                              "blocked": plan["blocked"], "input": how})
        outs = [legacy_final.relative_to(ctx.workspace).as_posix()] + (
            [json_final.relative_to(ctx.workspace).as_posix()] if json_final.exists() else [])
        return Result(outputs=outs, out_bytes=sum(tree_bytes(ctx.workspace / o) for o in outs), tool="retoc+uas",
                      tool_version=f"retoc {tool_version('retoc')}, uas {tool_version('uas')}",
                      notes=[f"{conv['extracted']} packages to legacy ({conv['failed']} failed), {len(files)} JSON; "
                             f"filter {', '.join(filters) or 'none'}; input {how}"]
                      + ([f"left out (encrypted without a usable published key): "
                          + ", ".join(b["name"] for b in plan["blocked"])] if plan["blocked"] else []))


class _UnrealModeExport(Extractor):
    """One uas mode over one container."""

    mode = "json"
    resource = "heavy"
    stage = 50
    after = ("unreal.inventory",)

    def applies(self, ctx: Context) -> bool:
        return _paks(ctx) is not None and self.mode in _modes(ctx)

    def discover(self, ctx: Context):
        from gst.engines.unreal import paks as paks_mod

        paks = _paks(ctx)
        rel = _rel(ctx, paks)
        tag = _access_tag(ctx)
        for c in paks_mod.containers(paks):
            if c["kind"] == "global":
                continue  # script objects only, no packages
            rels = [f"{rel}/{c['name']}"] + ([f"{rel}/{c['name'][:-5]}.ucas"] if c["kind"] == "iostore" else [])
            yield Unit(id=c["name"], extractor=self.name, relpaths=rels, size=c["size"],
                       meta={"kind": c["kind"], "encrypted": c["encrypted"], "key_guid": c.get("key_guid")},
                       fingerprint=_fingerprint(ctx, rels, f"{self.mode}-v2|{tag}|{ctx.options.get('unreal.name') or ''}"
                                                + ("|all_types" if self.mode == "json" and _all_types(ctx) else "")))

    def run(self, unit: Unit, ctx: Context) -> Result:
        from gst.assets import write_folder_sidecar
        from gst.engines.unreal import inventory, keys, tool_version, uas, usmap
        from gst.engines.unreal import paks as paks_mod
        from gst.pipeline.worker import commit, partial_dir, tree_bytes

        version = tool_version("uas") or ""
        key_file = None
        if unit.meta.get("encrypted"):
            key, why = keys.key_for(ctx.workspace, ctx.game, unit.meta.get("key_guid"))
            if key is None:
                return Result(status="blocked-policy", error=f"{unit.id} is encrypted: {why}", tool="uas", tool_version=version)
            key_file = keys.key_file(ctx.workspace, key)
        by_container = inventory.rows_by_container(ctx.workspace)
        if not by_container:
            return Result(status="failed", error="assets/inventory.jsonl is missing: unreal.inventory must run first")
        mine = sorted({re.sub(r"\.(uexp|ubulk)$", ".uasset", p) for p in by_container.get(unit.id, []) if p.endswith(uas.EXPORTABLE)})
        name = str(ctx.options.get("unreal.name") or "")
        if name:  # explicit narrowing (a regex on the packed path), recorded in the fingerprint and the sidecar
            rx = re.compile(name, re.I)
            mine = [m for m in mine if rx.search(m)]
        if not mine:
            return Result(status="unsupported", error=f"no exportable packed path of {unit.id} is listed in the inventory "
                                                      "(export it by name with gst assets extract --name)")
        others = sum(1 for c, rows in by_container.items() if c not in (unit.id, "manifest") and rows)
        flt = None if not (others or name) else uas.exact_filter(mine)  # the only readable container: no filter at all
        paks = _paks(ctx)
        game_enum = paks_mod.ue_game(paks, ctx.game)
        mapping, _src = usmap.find(ctx.workspace, ctx.game)
        staging = partial_dir(ctx, unit)
        out = staging / "out"
        proc = uas.export(paks, out, self.mode, game_enum, log=staging / "uas.log", rsp=staging / "uas.rsp", flt=flt,
                          bytecode=self.mode == "json", key_file=key_file, usmap=mapping,
                          all_types=self.mode == "json" and _all_types(ctx))
        log_text = (staging / "uas.log").read_text(encoding="utf-8", errors="replace") if (staging / "uas.log").exists() else ""
        files = [p for p in out.rglob("*") if p.is_file() and p.name != ".uas-manifest.json"] if out.exists() else []
        if not files:
            if mapping is None and usmap.needs_mappings(proc.stdout + proc.stderr + log_text):
                return Result(status="blocked-policy", error=usmap.NEEDS_REASON, tool="uas", tool_version=version)
            if proc.returncode != 0:
                return Result(status="failed", error=f"uas export {self.mode} of {unit.id} failed (exit {proc.returncode})\n"
                                                     f"{proc.stdout}\n{proc.stderr}", tool="uas", tool_version=version)
            return Result(status="skipped", error=f"no {self.mode} asset in {unit.id} ({len(mine)} packages looked at)",
                          tool="uas", tool_version=version)
        final = ctx.workspace / "assets" / "unreal" / "containers" / unit.id / self.mode
        commit(out, final)
        digest = hashlib.sha256(json.dumps(mine).encode("utf-8")).hexdigest()[:16]
        write_folder_sidecar(final, {"source": unit.relpaths[0], "res_path": unit.id, "tool": "uas", "tool_version": version,
                                     "build": ctx.game.get("buildid"), "mode": self.mode, "game": game_enum,
                                     "extractor": self.name, "unit": unit.id, "packages": len(mine), "filter_sha": digest, "name": name or None,
                                     "filtered": flt is not None})
        notes = [f"{len(mine)} packages, {len(files)} files ({self.mode})"]
        if proc.returncode != 0:
            notes.append(f"uas exit {proc.returncode} with partial output; see the job log")
        return Result(outputs=[final.relative_to(ctx.workspace).as_posix()], out_bytes=tree_bytes(final), tool="uas",
                      tool_version=version, notes=notes)


@register("unreal")
class UnrealExport(_UnrealModeExport):
    name = "unreal.export"
    category = "data"
    mode = "json"


@register("unreal")
class UnrealTextures(_UnrealModeExport):
    name = "unreal.textures"
    category = "texture"
    mode = "textures"


@register("unreal")
class UnrealModels(_UnrealModeExport):
    name = "unreal.models"
    category = "mesh"
    mode = "models"


@register("unreal")
class UnrealAudio(_UnrealModeExport):
    name = "unreal.audio"
    category = "audio"
    mode = "audio"


@register("unreal")
class UnrealAnimations(_UnrealModeExport):
    name = "unreal.animations"
    category = "animation"
    mode = "animations"
