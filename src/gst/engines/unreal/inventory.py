"""Unreal inventory: every packed path with the container it lives in.

Per container: plain legacy pak -> repak (v1-v11) or the stdlib lister (v12, or when repak fails); plain
IoStore -> ``retoc list --path``; ``global.utoc`` -> no assets (its script objects are counted); encrypted ->
the same tools with a published key (``keys``), else diagnosed only. Paths of encrypted containers come from
``Manifest_UFSFiles_*.txt`` (container ``manifest``) when the game ships one. uas lists only what the tools
above could not (container ``uas``).

Outputs: ``assets/inventory.jsonl`` (path, ext, folder, container), ``assets/INVENTORY.md`` and
``assets/unreal/containers.json`` (headers, how each container was listed, why one was not).
"""

from __future__ import annotations

import collections
import json
import re
import time
from pathlib import Path

from gst.assets import assets_dir
from gst.common import GsError, human_size, write_json, write_text
from gst.engines.unreal import keys as keys_mod
from gst.engines.unreal import paks as paks_mod


def _list_container(ws: Path, game: dict, paks: Path, c: dict) -> tuple[list[str], str]:
    """(paths, how) for one container; GsError code 3 = encrypted without a published key."""
    from gst.engines.unreal import repak, retoc

    key = keys_mod.require(ws, game, c)
    if c["kind"] == "pak":
        return repak.list_paths(paks / c["name"], key=key)
    if c["kind"] == "global":
        return [], "global (script objects only)"
    return retoc.list_paths(paks / c["name"], key=key), "retoc" + ("+aes" if key else "")


def list_rows(ws: Path, install: Path, game: dict, paks: Path, items: list[dict], game_enum: str) -> tuple[list[tuple[str, str]], str]:
    """(rows of (path, container), source description); fills each item's ``listed``/``how``/``blocked``."""
    rows: list[tuple[str, str]] = []
    known: set[str] = set()
    unlisted_iostore = False
    for c in items:
        try:
            paths, how = _list_container(ws, game, paks, c)
        except GsError as exc:
            c.update(listed=0, how=None, blocked=str(exc) if exc.code == 3 else None, error=None if exc.code == 3 else str(exc))
            unlisted_iostore = unlisted_iostore or (c["kind"] == "iostore" and exc.code != 3)
            continue
        c.update(listed=len(paths), how=how)
        for p in paths:
            if p not in known:
                known.add(p)
                rows.append((p, c["name"]))
    sources = sorted({c["how"] for c in items if c.get("how")})
    if unlisted_iostore:  # retoc could not read a plain container: uas mounts the folder and lists it
        from gst.engines.unreal import uas

        log = ws / "logs" / "uas-list.log"
        listed, needs = uas.list_paths(paks, game_enum, log=log, rsp=ws / "logs" / "uas-list.rsp")
        rows += [(p, "uas") for p in listed if p not in known]
        known.update(listed)
        sources.append("uas")
    if any(c.get("blocked") for c in items):
        manifest, paths = paks_mod.manifest_paths(install)
        extra = [p for p in paths if p not in known]
        if extra:
            rows += [(p, "manifest") for p in extra]
            sources.append(f"manifest {manifest.name} (contents unreadable: encrypted, rule 4)")
    return rows, ", ".join(sources) or "nothing"


def inventory(ws: Path, install: Path, game: dict) -> dict:
    t0 = time.time()
    paks = paks_mod.paks_dir(install)
    items = paks_mod.containers(paks)
    game_enum = paks_mod.ue_game(paks, game)
    rows, source = list_rows(ws, install, game, paks, items, game_enum)
    encrypted = [c["name"] for c in items if c["encrypted"]]
    blocked = [c["name"] for c in items if c.get("blocked")]
    if not rows:
        raise GsError(f"encrypted containers ({', '.join(encrypted[:4])}) and no Manifest_UFSFiles to list instead; "
                      "only a community-published key with its source URL may open them (guardrails.md rule 4)", 3)
    out = assets_dir(ws)
    out.mkdir(parents=True, exist_ok=True)
    exts, folders = collections.Counter(), collections.Counter()
    with open(out / "inventory.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for path, container in rows:
            parts = path.split("/")
            folder = "/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1])
            ext = Path(path).suffix.lower()
            exts[ext] += 1
            folders[folder] += 1
            fh.write(json.dumps({"path": path, "ext": ext, "folder": folder, "container": container}, ensure_ascii=False) + "\n")
    write_json(out / "unreal" / "containers.json", {"paks": str(paks), "game_enum": game_enum, "containers": items})
    blueprints = sum(1 for p, _ in rows if re.search(r"/BP_[^/]*\.uasset$", p))
    lines = [f"# Unreal asset inventory: {game.get('name')}", "",
             f"- {len(items)} containers in `{paks.relative_to(install).as_posix()}`; listed from {source}; "
             f"{len(rows)} files; engine guess for exports: `{game_enum}` (override with --ue 4.xx/5.x).",
             f"- Blueprints (`BP_*.uasset`): {blueprints}; maps (`.umap`): {exts.get('.umap', 0)}.",
             "- Export: `gst assets extract --name <regex on the path> [--types json,textures,audio,models,raw] [--bytecode]`; "
             "IoStore to legacy: `gst assets to-legacy --filter <substring>`.", "",
             "| Container | Kind | Size | Version | Encrypted | Signed | Listed | How |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {c['name']} | {c['kind']} | {human_size(c['size'])} | {c['version'] or '-'} | {'yes' if c['encrypted'] else 'no'} | "
              f"{'yes' if c['signed'] else 'no'} | {c.get('listed', 0)} | {c.get('how') or ('blocked (rule 4)' if c.get('blocked') else c.get('error') or '-')} |"
              for c in items]
    lines += ["", "| Extension | Files |", "| --- | --- |"] + [f"| `{e or '-'}` | {n} |" for e, n in exts.most_common(15)]
    lines += ["", "## Top folders", "", "| Folder | Files |", "| --- | --- |"] + [f"| `{f}` | {n} |" for f, n in folders.most_common(20)]
    write_text(out / "INVENTORY.md", "\n".join(lines) + "\n")
    return {"containers": len(items), "encrypted": encrypted, "blocked": blocked, "files": len(rows), "source": source,
            "game_enum": game_enum, "blueprints": blueprints, "types": dict(exts.most_common(12)),
            "seconds": round(time.time() - t0, 1)}


def matching(ws: Path, install: Path, game: dict, name: str) -> list[dict]:
    """Inventory rows whose packed path matches ``name`` (regex, case-insensitive)."""
    inventory_file = assets_dir(ws) / "inventory.jsonl"
    if not inventory_file.exists():
        inventory(ws, install, game)
    rx = re.compile(name, re.I)
    with open(inventory_file, encoding="utf-8") as fh:
        return [row for row in map(json.loads, fh) if rx.search(row["path"])]


def search(ws: Path, install: Path, game: dict, name: str, limit: int | None = None) -> tuple[list[str], int]:
    """Packed paths matching ``name``: (paths, or the first ``limit`` when asked; total matches)."""
    rows = matching(ws, install, game, name)
    return [r["path"] for r in (rows if limit is None else rows[:limit])], len(rows)


def rows_by_container(ws: Path) -> dict[str, list[str]]:
    inv = assets_dir(ws) / "inventory.jsonl"
    out: dict[str, list[str]] = collections.defaultdict(list)
    if inv.exists():
        with open(inv, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    out[r.get("container", "")].append(r["path"])
    return out
