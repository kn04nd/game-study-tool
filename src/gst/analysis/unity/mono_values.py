"""``gst analyze mono_values [--script X ...]``: serialized field values of MonoBehaviours, per script class.

Port of ``repo/prefab_values.py`` + ``fix_scripts.py``: the script of each MonoBehaviour comes from its raw
``m_Script`` PPtr (offset 16/20 of the object's bytes, resolved through the MonoScript catalog) *before* any
typetree is read, so only the requested classes are parsed; then the typetree comes from the generator chain
(Managed DLLs, BackUp folder, IL2CPP metadata, dummy DLLs) and is read from the object's own bytes.

Outputs: ``analysis/mono_values.json`` (per script: objects, ScriptableObjects vs components, files, field
summary - kinds, ranges, small value histograms) and ``analysis/mono_values/<Script>.jsonl`` with every
object's fields (``{"file", "cab", "path_id", "gameobject", "name", "fields"}``; PPtrs as ``{"ref", "file"}``).
Options: ``script`` (default: every script), ``file`` (regex on install relpaths), ``values=0`` (summary only).
Files are chosen from the inventory summaries (``scripts`` per file), opened one at a time.
"""

from __future__ import annotations

import collections
import json
import os
import re
import shutil

from gst.analysis import Report, UNITY, analysis, md_table
from gst.analysis.unity import UnityFile, basename
from gst.analysis.unity.fields import FieldStats, describe

FLUSH_BYTES = 32 << 20  # buffered JSON lines (all scripts) before they are appended to their files


class ValueWriter:
    """Appends one JSON line per object to ``<dir>/<Script>.jsonl``. Lines are buffered per script and flushed
    together once ``FLUSH_BYTES`` are pending (one open per script per flush: hundreds of scripts interleave in one
    file, and reopening a file per object is slow on Windows)."""

    def __init__(self, folder):
        self.folder = folder
        self.buffers: dict[str, list[str]] = {}
        self.pending = 0
        self.paths: dict[str, str] = {}
        self.counts: collections.Counter = collections.Counter()

    def write(self, script: str, row: dict) -> None:
        line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
        self.buffers.setdefault(script, []).append(line)
        self.pending += len(line)
        self.counts[script] += 1
        if self.pending >= FLUSH_BYTES:
            self.flush()

    def flush(self) -> None:
        from gst.assets import safe_name

        if self.buffers:
            os.makedirs(self.folder, exist_ok=True)
        for script, lines in self.buffers.items():
            path = self.paths.setdefault(script, os.path.join(self.folder, f"{safe_name(script or '_unknown', 120)}.jsonl"))
            with open(path, "a", encoding="utf-8", newline="\n", errors="backslashreplace") as fh:
                fh.writelines(lines)
        self.buffers.clear()
        self.pending = 0

    def close(self) -> None:
        self.flush()


class ScriptAgg:
    def __init__(self):
        self.stats = FieldStats()
        self.files: collections.Counter = collections.Counter()
        self.scriptable = 0
        self.components = 0
        self.namespace = ""
        self.assembly = ""

    def add(self, file: str, tree: dict, is_component: bool) -> None:
        self.stats.add(tree)
        self.files[file] += 1
        if is_component:
            self.components += 1
        else:
            self.scriptable += 1


def clean(value):
    from gst.engines.unity.export.mono import _clean

    return _clean(value)


def collect(objects, *, script_of, read, gameobject_name, wanted: set[str] | None, file: str, cab: str | None,
            aggs: dict[str, ScriptAgg], untyped: collections.Counter, writer: ValueWriter | None) -> int:
    """Feed the MonoBehaviours of one serialized file into the aggregators. ``script_of(obj)`` -> ScriptInfo-like
    (``class_name``, ``namespace``, ``assembly``) from raw bytes; ``read(obj, info)`` -> typetree dict or None."""
    n = 0
    for obj in objects:
        info = script_of(obj)
        script = getattr(info, "class_name", "") if info else ""
        if wanted is not None and script not in wanted:
            continue  # raw-byte prefilter: no typetree read for other classes
        tree = read(obj, info)
        if tree is None:
            untyped[script or "?"] += 1
            continue
        go_pid = (tree.get("m_GameObject") or {}).get("m_PathID", 0) if isinstance(tree.get("m_GameObject"), dict) else 0
        agg = aggs.get(script)
        if agg is None:
            agg = aggs[script] = ScriptAgg()
            agg.namespace, agg.assembly = getattr(info, "namespace", ""), getattr(info, "assembly", "")
        agg.add(file, tree, bool(go_pid))
        n += 1
        if writer is not None:
            fields = {k: v for k, v in tree.items() if k not in ("m_GameObject", "m_Enabled", "m_Script", "m_Name",
                                                                  "m_EditorHideFlags", "m_EditorClassIdentifier")}
            row = {"file": file, **({"cab": cab} if cab else {}), "path_id": obj.path_id,
                   "gameobject": gameobject_name(go_pid) if go_pid else None, "name": tree.get("m_Name") or None,
                   "fields": clean(fields)}
            writer.write(script or "_unknown", row)
    return n


@analysis("mono_values", engines=UNITY, needs=("unity.inventory", "unity.install"),
          description="MonoBehaviour field values per script class (raw m_Script prefilter, typetree chain)", version=1,
          options={"script": "MonoBehaviour class names (repeatable/comma list; default all)",
                   "file": "regex on install relpaths", "values": "0 = summary only (no per-object JSONL)"})
def mono_values(ctx) -> Report:
    from gst.engines.unity import inventory, loader

    wanted = set(ctx.opt_list("script")) or None
    fpat = re.compile(ctx.opt("file"), re.I) if ctx.opt("file") else None
    write_values = str(ctx.opt("values", "1")) not in ("0", "false", "no")
    side = ctx.side_dir()
    if side.is_dir():
        shutil.rmtree(side, ignore_errors=True)
    writer = ValueWriter(str(side)) if write_values else None
    aggs: dict[str, ScriptAgg] = {}
    untyped: collections.Counter = collections.Counter()
    files = []
    for s in inventory.summaries(ctx.inventory_dir()):
        if "MonoBehaviour" not in s.get("types", {}):
            continue
        if wanted is not None and s.get("scripts") is not None and not (wanted & set(s["scripts"])):
            continue
        if fpat and not fpat.search(s["file"]):
            continue
        files.append(s["file"])
    try:
        for rel in sorted(files):
            ctx.log(f"reading {rel}")
            with UnityFile(ctx, rel) as uf:
                for sf in uf.files():
                    objs = sf.objects
                    cab = sf.name if basename(sf.name) != basename(rel) else None
                    monos = [o for o in list(objs.values()) if loader.type_name(o) == "MonoBehaviour"]
                    collect(monos, script_of=uf.script, read=uf.mono,
                            gameobject_name=lambda pid, _o=objs: loader.peek_name(_o[pid]) if pid in _o else None,
                            wanted=wanted, file=rel, cab=cab, aggs=aggs, untyped=untyped, writer=writer)
    finally:
        if writer is not None:
            writer.close()
    if writer is not None:
        for p in sorted(writer.paths.values()):
            ctx.outputs.append(os.path.relpath(p, ctx.workspace).replace("\\", "/"))
    return build_report(aggs, untyped, wanted, writer, files, ctx.workspace)


def build_report(aggs: dict[str, ScriptAgg], untyped, wanted, writer, files, ws) -> Report:
    scripts = {}
    for name in sorted(aggs, key=lambda n: (-aggs[n].stats.count, n)):
        a = aggs[name]
        entry = {"objects": a.stats.count, "scriptable_objects": a.scriptable, "components": a.components,
                 "namespace": a.namespace, "assembly": a.assembly,
                 "files": dict(sorted(a.files.items())), "fields": a.stats.as_dict()}
        if writer is not None and name in writer.paths:
            entry["values"] = os.path.relpath(writer.paths[name], ws).replace("\\", "/")
        scripts[name or "_unknown"] = entry
    missing = sorted(wanted - set(aggs)) if wanted else []
    summary = {"scripts": len(scripts), "objects": sum(a.stats.count for a in aggs.values()),
               "untyped": sum(untyped.values()), "files": len(files)}
    data = {"requested": sorted(wanted) if wanted else None, "missing": missing, "scripts": scripts,
            "untyped": dict(sorted(untyped.items(), key=lambda kv: (-kv[1], kv[0])))}
    md = [f"{summary['objects']} MonoBehaviours read in {summary['files']} files, {summary['scripts']} script classes; "
          f"{summary['untyped']} without a typetree (see `untyped` in the JSON).", ""]
    if missing:
        md += [f"Requested but not found: {', '.join(f'`{m}`' for m in missing)}.", ""]
    md += ["## Scripts", "", md_table(["Script", "Objects", "ScriptableObjects", "Components", "Files", "Fields", "Values"],
                                      [(f"`{n}`", e["objects"], e["scriptable_objects"], e["components"], len(e["files"]),
                                        len(e["fields"]), f"`{e['values']}`" if e.get("values") else "-")
                                       for n, e in scripts.items()]), ""]
    detail = [n for n in scripts if wanted] or list(scripts)[:10]
    for n in detail:
        e = scripts[n]
        md += [f"## `{n}` ({e['objects']} objects)", "",
               md_table(["Field", "Summary"], [(f"`{f}`", describe(d)) for f, d in e["fields"].items()]), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)
