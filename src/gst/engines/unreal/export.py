"""Unreal exports for ``gst assets``: uas into ``assets/unreal/<mode>/`` (one run per mode, the union of every
filter used in that folder in one response file), and retoc ``to-legacy`` of IoStore packages into
``dumps/unreal/legacy/`` followed by the same uas path into ``assets/unreal/iostore/<mode>/``.

Encrypted containers need a published key (``keys``); packages with unversioned properties need a published or
user-provided ``.usmap`` (``usmap``). Without them the export stops with code 3 (``blocked-policy``) and the reason.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from gst.assets import FOLDER_SIDECAR, assets_dir
from gst.common import GsError, read_json, write_json
from gst.engines.unreal import keys as keys_mod
from gst.engines.unreal import paks as paks_mod
from gst.engines.unreal import usmap as usmap_mod
from gst.engines.unreal.inventory import matching
from gst.engines.unreal.uas import EXPORTABLE, MODES


def access(ws: Path, game: dict, items: list[dict]) -> tuple[Path | None, Path | None, list[str]]:
    """(uas --aes-file, --usmap, notes) for a folder: the published key of its encrypted containers (uas takes
    one key), and the mappings file when there is one."""
    key_file, notes = None, []
    for c in items:
        if c["encrypted"]:
            key, why = keys_mod.key_for(ws, game, c.get("key_guid"))
            if key is not None:
                key_file = keys_mod.key_file(ws, key)
                notes.append(f"published key from {key['source']}")
                break
            notes.append(f"{c['name']}: {why}")
    usmap, source = usmap_mod.find(ws, game)
    if usmap is not None:
        notes.append(f".usmap {usmap.name} ({source})")
    return key_file, usmap, notes


def _files(out: Path) -> list[Path]:
    return [p for p in out.rglob("*") if p.is_file() and p.name not in {FOLDER_SIDECAR, ".uas-manifest.json"}]


def run_uas(ws: Path, paks: Path, out: Path, mode: str, game_enum: str, flt: str | None, *, bytecode: bool,
            key_file: Path | None, usmap: Path | None, tag: str, all_types: bool = False) -> tuple[int, str]:
    from gst.engines.unreal import uas

    log = ws / "logs" / f"uas-{tag}.log"
    proc = uas.export(paks, out, mode, game_enum, log=log, rsp=ws / "logs" / f"uas-{tag}.rsp", flt=flt, bytecode=bytecode,
                      key_file=key_file, usmap=usmap, all_types=all_types)
    text = proc.stdout + "\n" + proc.stderr
    if log.exists():
        text += "\n" + log.read_text(encoding="utf-8", errors="replace")
    return proc.returncode, text


def extract(ws: Path, install: Path, game: dict, *, name: str, mode: str = "json", limit: int | None = None,
            bytecode: bool = False, ue: str | None = None, all_types: bool = False) -> dict:
    from gst.engines.unreal import tool_version, uas

    if mode not in MODES:
        raise GsError(f"--types for Unreal is one of {', '.join(MODES)}")
    t0 = time.time()
    paks = paks_mod.paks_dir(install)
    items = paks_mod.containers(paks)
    rows = matching(ws, install, game, name)
    key_file, usmap, notes = access(ws, game, items)
    readable = [r for r in rows if r["container"] != "manifest"]
    if rows and not readable or (all(c["encrypted"] for c in items if c["kind"] != "global") and key_file is None):
        raise GsError("these assets sit in containers encrypted with an unpublished key: diagnose only (guardrails.md rule 4); "
                      "the file list is in assets/INVENTORY.md (gst assets list)", 3)
    unique = sorted({re.sub(r"\.(uexp|ubulk)$", ".uasset", r["path"]) for r in readable if r["path"].endswith(EXPORTABLE)})
    if not unique:
        return {"matched": 0, "exported": 0, "seconds": round(time.time() - t0, 1), "out": None}
    limited = limit is not None and len(unique) > limit
    out = assets_dir(ws) / "unreal" / mode
    out.mkdir(parents=True, exist_ok=True)
    side_path = out / FOLDER_SIDECAR
    side = read_json(side_path, {}) or {}
    # uas deletes outputs outside a narrower filter: one run with the union of every filter of this folder
    filters = list(dict.fromkeys(side.get("filters", []) + ([] if limited else [name])))
    exact = list(dict.fromkeys(side.get("exact", []) + (unique[:limit] if limited else [])))
    parts = [uas.union_filter(filters)] if filters else []
    if exact:
        parts.append(uas.exact_filter(exact))
    flt = "|".join(f"(?:{p})" for p in parts)
    bytecode = mode == "json" and (bytecode or bool(side.get("bytecode")))
    all_types = mode == "json" and (all_types or bool(side.get("all_types")))  # sticky, like the filters and bytecode
    game_enum = paks_mod.ue_game(paks, game, ue)
    code, text = run_uas(ws, paks, out, mode, game_enum, flt, bytecode=bytecode, key_file=key_file, usmap=usmap, tag=mode,
                         all_types=all_types)
    files = _files(out)
    if code != 0 and not files:
        if usmap is None and usmap_mod.needs_mappings(text):
            raise GsError(usmap_mod.NEEDS_REASON, 3)
        raise GsError(f"uas export failed (exit {code}); see logs/uas-{mode}.log")
    names = sorted(p.relative_to(out).as_posix() for p in files)
    write_json(side_path, {"source": str(paks), "tool": f"UnrealAssetScout {tool_version('uas') or ''}".strip(), "mode": mode,
                           "game": game_enum, "filters": filters, "exact": exact, "bytecode": bytecode, "all_types": all_types,
                           "count": len(files),
                           "bytes": sum(p.stat().st_size for p in files), "examples": names[:6], "notes": notes,
                           "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return {"matched": len(unique), "exported": len(files), "out": str(out), "game": game_enum,
            "seconds": round(time.time() - t0, 1), "exit_code": code, "limited": limited,
            "selected": min(len(unique), limit) if limited else len(unique)}


def legacy_dir(ws: Path) -> Path:
    return Path(ws) / "dumps" / "unreal" / "legacy"


def iostore_plan(ws: Path, game: dict, items: list[dict]) -> dict:
    """Which IoStore containers retoc can convert: ``{"convert": [names], "blocked": [{name, reason}], "key", "mixed",
    "global"}``.

    retoc takes one AES key per run and opens every ``.utoc`` of a folder input, so: an encrypted ``global.utoc``
    without its published key blocks everything (its script objects resolve every import); containers needing another
    key than the first one found are blocked; when only some containers are convertible (``mixed``), the conversion
    reads a mount folder holding those plus ``global.utoc`` (:func:`mount_folder`). Nothing convertible -> GsError
    code 3 (``blocked-policy``) naming every container and why.
    """
    stores = [c for c in items if c["kind"] == "iostore"]
    if not stores:
        raise GsError("no IoStore containers (.utoc) here: legacy paks go straight to gst assets extract")
    glob = next((c for c in items if c["kind"] == "global"), None)
    key, blocked, convert = None, [], []
    for c in ([glob] if glob else []) + stores:
        if not c["encrypted"]:
            if c is not glob:
                convert.append(c["name"])
            continue
        found, why = keys_mod.key_for(ws, game, c.get("key_guid"))
        if found is not None and (key is None or found["key"] == key["key"]):
            key = found
            if c is not glob:
                convert.append(c["name"])
            continue
        reason = why or "needs a second AES key: retoc takes one key per run (convert with the other key separately)"
        if c is glob:
            raise GsError(f"global.utoc is encrypted: {reason}. Without it no package import resolves; nothing is converted", 3)
        blocked.append({"name": c["name"], "reason": reason})
    if not convert:
        plain = (" The plain global.utoc script objects (native modules and classes) are still read by gst analyze "
                 "unreal_assets.") if glob and not glob["encrypted"] else ""
        reasons = sorted({b["reason"] for b in blocked})
        raise GsError(f"every IoStore container is encrypted without a usable published key ({len(blocked)}: "
                      f"{', '.join(b['name'] for b in blocked)}): {'; '.join(reasons)}.{plain}", 3)
    return {"convert": convert, "blocked": blocked, "key": key, "mixed": bool(blocked),
            "global": glob["name"] if glob else None}


def mount_folder(paks: Path, plan: dict, mount: Path) -> tuple[Path, str]:
    """The retoc input: the Paks folder itself, or (``mixed``) ``mount`` holding only the convertible ``.utoc/.ucas``
    and ``global.utoc/.ucas``, as symbolic links when Windows allows them, else copies. Returns (input, how)."""
    import shutil

    if not plan["mixed"]:
        return paks, "paks folder"
    if mount.exists():
        shutil.rmtree(mount)
    mount.mkdir(parents=True)
    how = "symlinks"
    for name in ([plan["global"]] if plan["global"] else []) + plan["convert"]:
        for src in (paks / name, (paks / name).with_suffix(".ucas")):
            if not src.exists():
                continue
            dst = mount / src.name
            if how == "symlinks":
                try:
                    dst.symlink_to(src)
                    continue
                except OSError:  # no symlink right (Windows without Developer Mode): copy; the install stays untouched
                    how = "copies"
            shutil.copy2(src, dst)
    return mount, how


def to_legacy(ws: Path, install: Path, game: dict, *, filters: list[str] | None, modes: list[str] | None = None,
              bytecode: bool = False, ue: str | None = None, name: str = "legacy", all_types: bool = False) -> dict:
    """retoc to-legacy of the IoStore folder (``filters`` = path substrings, none = everything) into
    ``dumps/unreal/legacy/<name>.pak``, then uas on the legacy paks into ``assets/unreal/iostore/<mode>/``.

    Encrypted containers without a published key are left out (``blocked`` in the result) when others are plain;
    when none is convertible the run stops with code 3 (``blocked-policy``)."""
    from gst.engines.unreal import retoc, tool_version

    t0 = time.time()
    paks = paks_mod.paks_dir(install)
    items = paks_mod.containers(paks)
    plan = iostore_plan(ws, game, items)
    game_enum = paks_mod.ue_game(paks, game, ue)
    out_dir = legacy_dir(ws)
    pak = out_dir / f"{name}.pak"
    for old in out_dir.glob(f"{name}*.pak"):
        old.unlink()
    source, how = mount_folder(paks, plan, Path(ws) / "dumps" / "unreal" / "mount" / name)
    conv = retoc.to_legacy(source, pak, ws / "logs" / f"retoc-{name}.log", filters=filters,
                           version=paks_mod.retoc_version(game_enum), key=plan["key"])
    result = {"legacy": conv, "filters": filters or [], "exports": {}, "converted": plan["convert"],
              "blocked": plan["blocked"], "input": how}
    if not conv["paks"] or conv["extracted"] == 0:
        result.update(seconds=round(time.time() - t0, 1), exported=0)
        return result
    usmap, _src = usmap_mod.find(ws, game)
    for mode in modes or ["json"]:
        out = assets_dir(ws) / "unreal" / "iostore" / mode
        # every legacy pak of the folder in one run: uas deletes outputs of packages missing from a later run
        code, text = run_uas(ws, out_dir, out, mode, game_enum, None, bytecode=bytecode, key_file=None, usmap=usmap,
                             tag=f"iostore-{mode}", all_types=all_types)
        files = _files(out)
        if code != 0 and not files and usmap is None and usmap_mod.needs_mappings(text):
            raise GsError(usmap_mod.NEEDS_REASON, 3)
        write_json(out / FOLDER_SIDECAR, {"source": str(paks), "via": f"retoc to-legacy -> {pak.name}",
                                          "converted": plan["convert"], "blocked": plan["blocked"],
                                          "tool": f"retoc {tool_version('retoc') or ''} + UnrealAssetScout {tool_version('uas') or ''}",
                                          "mode": mode, "game": game_enum, "filters": filters or [], "count": len(files),
                                          "bytes": sum(p.stat().st_size for p in files),
                                          "examples": sorted(p.relative_to(out).as_posix() for p in files)[:6],
                                          "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        result["exports"][mode] = {"exported": len(files), "exit_code": code, "out": str(out)}
    result.update(seconds=round(time.time() - t0, 1), exported=sum(e["exported"] for e in result["exports"].values()))
    return result
