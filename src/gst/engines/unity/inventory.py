"""Per-file object table of a Unity build, without parsing the objects.

One serialized file or bundle at a time: the object table (type, path id, byte size) comes from the
file's metadata; the name is peeked (a few bytes per object); a MonoBehaviour's script comes from its
raw ``m_Script`` PPtr.

Format (docs/INTERFACES.md, section 5), all under ``assets/unity/_inventory/``:

- ``<relpath with __>.jsonl.gz``: gzip'd JSONL, streamed. Line 1 is the header
  ``{"_header": 1, "file": <relpath>, "kind"}``; a ``{"_cab": <name>}`` line opens the rows of each CAB
  of a bundle; rows are ``{"type", "class_id", "name", "path_id", "size"}`` (+ ``container``,
  ``script`` when known). ``file``/``cab`` are not repeated per row (a PEAK map bundle holds ~21 M objects).
- ``<relpath with __>.summary.json``: counts and bytes per type, scripts, externals, CABs.
- ``INDEX.json``: every file -> objects, types, bytes, path of its table (no merged all-objects file).
  ``assets/INVENTORY.md`` is the human summary.

Readers go through :func:`iter_rows` (streams the tables back as full rows with ``file``/``cab``).
"""

from __future__ import annotations

import collections
import gzip
import json
import re
import os
import time
from pathlib import Path

from gst.common import human_size, write_text
from gst.engines.unity import loader
from gst.engines.unity.scripts import ScriptIndex, script_name

INVENTORY_DIR = "_inventory"
INDEX = "INDEX.json"
GZ_LEVEL = 4  # speed over the last % of size
RESOURCES_MAP = "_resources_container.jsonl"


def safe_rel(relpath: str) -> str:
    return relpath.replace("\\", "/").replace("/", "__")


def _atomic_open(dest: Path, *, gz: bool = False):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.partial-{os.getpid()}")  # unique: parallel workers never share a temp file
    if gz:
        return tmp, gzip.open(tmp, "wt", encoding="utf-8", newline="\n", compresslevel=GZ_LEVEL)
    return tmp, open(tmp, "w", encoding="utf-8", newline="\n")


def table_path(out_dir: Path, relpath: str) -> Path:
    return Path(out_dir) / f"{safe_rel(relpath)}.jsonl.gz"


def bundle_container(sf) -> dict[tuple[str, int], str]:
    """``(cab name lower, path_id) -> container path`` from the AssetBundle object(s) of one CAB."""
    out: dict[tuple[str, int], str] = {}
    for obj in list(sf.objects.values()):
        if loader.type_name(obj) != "AssetBundle":
            continue
        try:
            tree = obj.read_typetree(check_read=False)
        except Exception:
            continue
        for pair in tree.get("m_Container", []):
            try:
                path, info = pair if isinstance(pair, (list, tuple)) else (pair.get("first"), pair.get("second"))
                ptr = info["asset"]
                fid, pid = loader.fix_pptr(int(ptr["m_FileID"]), int(ptr["m_PathID"]))
            except Exception:
                continue
            out[((loader.external_name(sf, fid) or sf.name).lower(), pid)] = path
    return out


def resource_manager_rows(env):
    """``Resources/`` paths from a ResourceManager (globalgamemanagers): (path, external file, path_id)."""
    for obj in loader.iter_objects(env):
        if loader.type_name(obj) != "ResourceManager":
            continue
        try:  # only m_Container (first field): m_DependentAssets holds millions of PPtrs (GBs as dicts)
            from UnityPy.helpers.TypeTreeNode import TypeTreeNode

            node = obj._get_typetree_node()
            if node.m_Children and node.m_Children[0].m_Name == "m_Container":
                node = TypeTreeNode(node.m_Level, node.m_Type, node.m_Name, node.m_ByteSize, node.m_Version,
                                    node.m_Children[:1], node.m_TypeFlags, node.m_VariableCount, node.m_Index,
                                    node.m_MetaFlag, node.m_RefTypeHash)
            tree = obj.read_typetree(nodes=node, check_read=False)
        except Exception:
            continue
        sf = obj.assets_file
        for pair in tree.get("m_Container", []):
            try:
                path, ptr = pair if isinstance(pair, (list, tuple)) else (pair.get("first"), pair.get("second"))
                fid, pid = loader.fix_pptr(int(ptr["m_FileID"]), int(ptr["m_PathID"]))
            except Exception:
                continue
            yield path, loader.external_name(sf, fid) or sf.name, pid


def inventory_file(install: Path, relpath: str, out_dir: Path, *, catalog=None, env=None) -> dict:
    """Write ``<out_dir>/<safe>.jsonl.gz`` and ``.summary.json`` for one file; return the summary."""
    t0 = time.time()
    install = Path(install)
    own_env = env is None
    env = env or loader.open_env(install / relpath)
    if catalog is not None:
        catalog.pin(relpath, env)
    scripts = ScriptIndex(catalog) if catalog is not None else None
    counts, sizes, script_counts = collections.Counter(), collections.Counter(), collections.Counter()
    containers: dict[tuple[str, int], str] = {}
    version = ""
    dest = table_path(out_dir, relpath)
    tmp, fh = _atomic_open(dest, gz=True)
    externals: set[str] = set()
    cabs = []
    n = 0
    try:
        with fh:
            fh.write(json.dumps({"_header": 1, "file": relpath, "kind": loader.classify(install / relpath)},
                                ensure_ascii=False) + "\n")
            for sf in loader.serialized_files(env, release=True):
                v = str(getattr(sf, "unity_version", "") or "")
                if v and not v.startswith("0.0"):
                    version = version or v
                containers.update(bundle_container(sf))
                cab = sf.name if sf.name != Path(relpath).name else ""
                if cab:
                    cabs.append(cab)
                    fh.write(json.dumps({"_cab": cab}, ensure_ascii=False) + "\n")
                for ext in getattr(sf, "externals", []):
                    externals.add(os.path.basename(str(ext.path).replace("\\", "/")))
                for obj in list(sf.objects.values()):
                    t = loader.type_name(obj)
                    row = {"type": t, "class_id": int(getattr(obj, "class_id", -1)), "name": loader.peek_name(obj),
                           "path_id": obj.path_id, "size": obj.byte_size}
                    c = containers.get((sf.name.lower(), obj.path_id))
                    if c:
                        row["container"] = c
                    if t == "MonoBehaviour" and scripts is not None:
                        s = script_name(obj, scripts)
                        if s:
                            row["script"] = s
                            script_counts[s] += 1
                    counts[t] += 1
                    sizes[t] += obj.byte_size
                    n += 1
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, dest)
        if counts.get("ResourceManager"):  # only the file that holds it (parallel units must not overwrite the map)
            rm = Path(out_dir) / RESOURCES_MAP
            rtmp, rfh = _atomic_open(rm)
            with rfh:
                for path, fname, pid in resource_manager_rows(env):
                    rfh.write(json.dumps({"container": path, "file": fname, "path_id": pid}, ensure_ascii=False) + "\n")
            os.replace(rtmp, rm)
        summary = {"file": relpath, "kind": "bundle" if cabs else "serialized", "cabs": cabs,
                   "unity_version": version or loader.unity_version(env), "objects": n, "types": dict(counts.most_common()),
                   "bytes": dict(sizes), "scripts": dict(script_counts.most_common()), "externals": sorted(externals),
                   "input_bytes": (install / relpath).stat().st_size, "table": dest.name,
                   "table_bytes": dest.stat().st_size, "seconds": round(time.time() - t0, 2)}
        sdest = Path(out_dir) / f"{safe_rel(relpath)}.summary.json"
        stmp, sfh = _atomic_open(sdest)
        with sfh:
            json.dump(summary, sfh, ensure_ascii=False, indent=1)
        os.replace(stmp, sdest)
        return summary
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        if catalog is not None:
            catalog.unpin(relpath)
        if own_env:
            loader.close_env(env)


def summaries(out_dir: Path):
    for p in sorted(Path(out_dir).glob("*.summary.json")):
        try:
            yield json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue


def summary_for(out_dir: Path, relpath: str) -> dict | None:
    p = Path(out_dir) / f"{safe_rel(relpath)}.summary.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def resources_map(out_dir: Path) -> dict[tuple[str, int], str]:
    """``(file basename lower, path_id) -> Resources/ path`` from globalgamemanagers' ResourceManager."""
    res_map: dict[tuple[str, int], str] = {}
    rm = Path(out_dir) / RESOURCES_MAP
    if rm.is_file():
        with open(rm, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                    res_map[(d["file"].lower(), int(d["path_id"]))] = d["container"]
                except (ValueError, KeyError):
                    continue
    return res_map


def tables(out_dir: Path) -> list[Path]:
    """Per-file tables: ``.jsonl.gz`` (format 2) and legacy uncompressed ``.jsonl``."""
    out_dir = Path(out_dir)
    return sorted(out_dir.glob("*.jsonl.gz")) + sorted(p for p in out_dir.glob("*.jsonl") if p.name != RESOURCES_MAP)


def iter_rows(out_dir: Path, *, files=None, types=None, name: str | None = None):
    """Stream inventory rows (dicts with ``file``, ``container`` and, in bundles, ``cab``) from the
    per-file tables. ``files``: list of relpaths or a regex string; ``types``: type names; ``name``:
    regex on object names. Never loads a whole table."""
    res_map = resources_map(out_dir)
    pat = re.compile(name, re.I) if name else None
    fpat = re.compile(files, re.I) if isinstance(files, str) else None
    wanted_files = set(files) if files is not None and not isinstance(files, str) else None
    wanted_types = set(types) if types else None
    needles = tuple(f'"type": "{t}"' for t in wanted_types) if wanted_types else ()
    for table in tables(out_dir):
        opener = gzip.open if table.suffix == ".gz" else open
        with opener(table, "rt", encoding="utf-8") as fh:
            file, cab = "", ""
            for line in fh:
                if line.startswith('{"_'):
                    row = json.loads(line)
                    if "_header" in row:
                        file = row.get("file", "")
                        if (wanted_files is not None and file not in wanted_files) or (fpat and not fpat.search(file)):
                            break
                    elif "_cab" in row:
                        cab = row["_cab"]
                    continue
                if needles and not any(n in line for n in needles):
                    continue  # cheap prefilter on the raw line
                row = json.loads(line)
                if "file" not in row:
                    row["file"] = file
                elif (wanted_files is not None and row["file"] not in wanted_files) or (fpat and not fpat.search(row["file"])):
                    continue  # legacy table (file on every row)
                if pat and not pat.search(row.get("name") or ""):
                    continue
                if cab:
                    row.setdefault("cab", cab)
                if not row.get("container") and res_map:
                    c = res_map.get((os.path.basename(row["file"]).lower(), row.get("path_id")))
                    if c:
                        row["container"] = c
                row.setdefault("container", "")
                yield row


def monoscripts(out_dir: Path) -> dict[tuple[str, int], str]:
    """``(file basename lower, path_id) -> class name`` of every MonoScript (the old ``mono_name`` lookup)."""
    return {(os.path.basename(r["file"]).lower(), r["path_id"]): r["name"]
            for r in iter_rows(out_dir, types=("MonoScript",))}


def merge(out_dir: Path, dest_index: Path | None = None, dest_md: Path | None = None, game_name: str = "") -> dict:
    """Write ``INDEX.json`` (per file: objects, types, bytes, table path) and ``INVENTORY.md``.
    No merged all-objects file: :func:`iter_rows` streams the per-file tables."""
    out_dir = Path(out_dir)
    dest_index = Path(dest_index) if dest_index else out_dir / INDEX
    dest_md = Path(dest_md) if dest_md else out_dir.parent.parent / "INVENTORY.md"
    counts, sizes = collections.Counter(), collections.Counter()
    files: dict[str, dict] = {}
    version = ""
    for s in summaries(out_dir):
        version = version or s.get("unity_version", "")
        counts.update(s.get("types", {}))
        sizes.update(s.get("bytes", {}))
        table = s.get("table") or f"{safe_rel(s['file'])}.jsonl"
        files[s["file"]] = {"objects": s.get("objects", 0), "types": s.get("types", {}), "bytes": s.get("bytes", {}),
                            "input_bytes": s.get("input_bytes", 0), "kind": s.get("kind"), "cabs": s.get("cabs", []),
                            "table": table, "table_bytes": s.get("table_bytes")}
    index = {"format": 2, "unity_version": version, "objects": sum(counts.values()), "files": files,
             "types": dict(counts.most_common()), "bytes": dict(sizes),
             "reader": "gst.engines.unity.inventory.iter_rows(<this folder>)"}
    tmp, fh = _atomic_open(dest_index)
    with fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, dest_index)
    largest = sorted(files.items(), key=lambda kv: -kv[1]["input_bytes"])
    lines = [f"# Unity asset inventory: {game_name}".rstrip(), "",
             f"- Unity {version or '?'}; {sum(counts.values())} objects in {len(files)} data files.",
             "- Per file: `unity/_inventory/<file>.jsonl.gz` (header line with the file, a `_cab` line per CAB, then "
             "type, class_id, name, path_id, size, container, script); index: `unity/_inventory/INDEX.json`; "
             "read with `gst.engines.unity.inventory.iter_rows`.", "",
             "| Type | Objects | Size |", "| --- | --- | --- |"]
    lines += [f"| {t} | {n} | {human_size(sizes[t])} |" for t, n in counts.most_common()]
    lines += ["", "## Files", "", "| File | Objects | Size on disk | Table |", "| --- | --- | --- | --- |"]
    lines += [f"| `{f}` | {r['objects']} | {human_size(r['input_bytes'])} | {human_size(r['table_bytes'] or 0)} |"
              for f, r in largest]
    write_text(dest_md, "\n".join(lines) + "\n")
    return {"files": len(files), "objects": sum(counts.values()), "types": len(counts), "unity_version": version}
