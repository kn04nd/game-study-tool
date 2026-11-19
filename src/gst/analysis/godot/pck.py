"""``gst analyze godot_pck``: the directory of every Godot pack of the install (stdlib reader,
``gst.engines.godot.pck``): pack format, Godot version, flags (encrypted directory / files: reported, never
decrypted), files with offset/size/md5, totals per extension and top folder, and a cross-check with the GDRE
inventory (``assets/inventory.jsonl``) when present. Reads the install read-only; ``md5=1`` re-hashes every file's
bytes and compares them with the directory (slow on big packs; default: first and last file only)."""

from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path

from gst.analysis import Report, analysis, counted, md_table


def _md5_of(path: Path, offset: int, size: int) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        fh.seek(offset)
        left = size
        while left > 0:
            chunk = fh.read(min(left, 1 << 20))
            if not chunk:
                break
            h.update(chunk)
            left -= len(chunk)
    return h.hexdigest()


@analysis("godot_pck", engines=("godot",), needs=("godot.pack",), version=1, category="data",
          description="Godot PCK directory (stdlib) and cross-check with the GDRE inventory",
          options={"md5": "1 = hash every file and compare with the directory md5 (default: first and last file)"})
def godot_pck(ctx) -> Report:
    from gst.engines.godot import pck

    packs = pck.find(ctx.install_dir)
    all_md5 = str(ctx.opt("md5", "0")) == "1"
    out_packs, notes = [], []
    every_path: list[str] = []
    for p in packs:
        ctx.use(f"install:{p.name}", p)
        try:
            info = pck.read(p)
        except (pck.PckError, OSError) as exc:
            out_packs.append({"container": p.name, "error": str(exc)})
            notes.append(f"{p.name}: {exc}")
            continue
        files = info.pop("files")
        exts, folders = collections.Counter(), collections.Counter()
        ext_bytes: collections.Counter = collections.Counter()
        for e in files:
            rel = e["path"].split("://", 1)[-1]
            ext = Path(rel).suffix.lower()
            exts[ext] += 1
            ext_bytes[ext] += e["size"]
            folders[rel.split("/", 1)[0] if "/" in rel else ""] += 1
        checked = files if all_md5 else ([files[0], files[-1]] if len(files) > 1 else files)
        bad = []
        for e in checked:
            if e["encrypted"] or e["removal"]:
                continue
            if _md5_of(p, e["offset"], e["size"]) != e["md5"] and e["md5"] != "0" * 32:
                bad.append(e["path"])
        info.update(by_ext=counted(exts), bytes_by_ext=counted(ext_bytes), by_folder=counted(folders),
                    md5_checked=len(checked), md5_mismatch=bad)
        if info.get("dir_encrypted"):
            notes.append(f"{p.name}: encrypted directory (only {info['file_count']} entries known); a key is needed "
                         "and only a community-published one may be used (guardrails rule 4)")
        elif info.get("encrypted_files"):
            notes.append(f"{p.name}: {info['encrypted_files']} encrypted files (listed, not opened)")
        ctx.write_side_json({"container": p.name, "files": files}, f"{p.name}.files.json")
        every_path += [e["path"] for e in files if not e["removal"]]
        out_packs.append(info)
    inv = ctx.workspace / "assets" / "inventory.jsonl"
    cross = None
    if inv.is_file():
        ctx.use("gdre inventory", inv)
        paths = []
        with open(inv, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    if "path" in row:
                        paths.append(row["path"])
        cross = pck.cross_check([{"path": x, "removal": False} for x in every_path], paths)
        if not cross["match"]:
            notes.append(f"pck vs GDRE inventory differ: {len(cross['only_pck'])} only in the pck, "
                         f"{len(cross['only_inventory'])} only in the inventory")
    total = sum(i.get("file_count") or 0 for i in out_packs)
    summary = {"packs": len(out_packs), "files": total, "bytes": sum(i.get("bytes") or 0 for i in out_packs),
               "encrypted_dirs": sum(1 for i in out_packs if i.get("dir_encrypted")),
               "encrypted_files": sum(i.get("encrypted_files") or 0 for i in out_packs),
               "md5_mismatch": sum(len(i.get("md5_mismatch") or []) for i in out_packs)}
    if cross is not None:
        summary["inventory_match"] = cross["match"]
    md = []
    for i in out_packs:
        if "error" in i:
            md += [f"## {i['container']}", "", f"Error: {i['error']}", ""]
            continue
        md += [f"## {i['container']} ({i['where']})", "",
               f"- Pack format {i['pack_format']}, Godot {i['version']}, flags {i['flags']} (encrypted directory: "
               f"{i['dir_encrypted']}, relative file base: {i['rel_filebase']}, sparse: {i['sparse_bundle']})",
               f"- {i['file_count']} files, {i.get('bytes')} bytes; encrypted files {i.get('encrypted_files')}; "
               f"md5 checked {i['md5_checked']}, mismatches {len(i['md5_mismatch'])}",
               f"- Full list: `analysis/godot_pck/{i['container']}.files.json`", "",
               md_table(["Extension", "Files", "Bytes"], [(k or "-", v, i["bytes_by_ext"].get(k)) for k, v in i["by_ext"].items()]),
               ""]
    if cross is not None:
        md += ["## Cross-check with the GDRE inventory", "",
               f"pck {cross['pck']}, inventory {cross['inventory']}, common {cross['common']}, match {cross['match']}."]
    return Report(data={"packs": out_packs, "cross_check": cross}, markdown="\n".join(md), summary=summary, notes=notes)
