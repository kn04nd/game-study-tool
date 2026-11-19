"""MonoBehaviour data -> JSON through the typetree chain, raw-byte prefilter first.

- ScriptableObjects (no GameObject): one JSON each, ``MonoBehaviour/<source>/<Script>/<name>.json``.
- Components (on a GameObject): grouped per script, ``MonoBehaviour/<source>/_components/<Script>.jsonl``
  (one ``{"path_id", "gameobject", "fields"}`` per line) - hundreds of thousands of files otherwise.
- Without a typetree source, or for a script the generator does not know, the object is counted in
  ``_untyped.json`` (script, count) instead of being read: no blind ``read_typetree``.

Localization (Unity Localization package): ``StringTable`` + ``SharedTableData`` -> one JSON per table
with ``{key: text}`` resolved through the shared table ids.
"""

from __future__ import annotations

import collections
import json
import os

from gst.engines.unity import loader, typetree
from gst.engines.unity.export import to_jsonable


def _clean(value):
    """PPtr dicts -> compact refs, everything JSON-safe."""
    if isinstance(value, dict):
        if set(value) == {"m_FileID", "m_PathID"}:
            return None if not value["m_PathID"] else {"ref": value["m_PathID"], "file": value["m_FileID"]}
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return to_jsonable(value)


class MonoCollector:
    def __init__(self, s, *, wanted: set[str] | None = None):
        self.s = s
        self.wanted = wanted
        self.groups: dict[str, list] = collections.defaultdict(list)  # script -> component lines (tmp files)
        self.handles: dict[str, tuple] = {}
        self.untyped: collections.Counter = collections.Counter()
        self.done = 0

    def _handle(self, script: str):
        if script not in self.handles:
            w = self.s.w
            final = w.target("MonoBehaviour", script, ".jsonl", script, "_components")
            w.partial.mkdir(parents=True, exist_ok=True)
            tmp = w.partial / f"{os.getpid()}_{id(self)}_{len(self.handles)}.jsonl"
            self.handles[script] = (final, tmp, open(tmp, "w", encoding="utf-8", newline="\n"), [0])
        return self.handles[script]

    def add(self, obj) -> list:
        s = self.s
        info = s.scripts.script_of(obj)
        script = info.class_name if info else ""
        if self.wanted is not None and script not in self.wanted:
            return []
        gen, _src = s.generator()
        tree = typetree.read_mono(obj, gen, info)
        if tree is None:
            self.untyped[script or "?"] += 1
            return []
        go_pid = (tree.get("m_GameObject") or {}).get("m_PathID", 0)
        fields = {k: v for k, v in tree.items() if k not in ("m_GameObject", "m_Enabled", "m_Script", "m_Name",
                                                              "m_EditorHideFlags", "m_EditorClassIdentifier")}
        if not go_pid:  # ScriptableObject
            name = tree.get("m_Name") or f"{script}_{obj.path_id}"
            path = s.w.target("MonoBehaviour", name, ".json", obj.path_id, script or "_unknown")
            s.w.json(path, {"script": info.fullname if info else "", "assembly": info.assembly if info else "",
                            "name": tree.get("m_Name"), "path_id": obj.path_id, "fields": _clean(fields)},
                     meta=s.meta(obj, tree.get("m_Name", ""), script=script, typetree=_src))
            self.done += 1
            return [path]
        go = s.catalog.resolve(obj.assets_file, 0, go_pid)
        _final, _tmp, fh, n = self._handle(script or "_unknown")
        fh.write(json.dumps({"path_id": obj.path_id, "gameobject": loader.peek_name(go) if go is not None else None,
                             "gameobject_path_id": go_pid, "fields": _clean(fields)}, ensure_ascii=False) + "\n")
        n[0] += 1
        self.done += 1
        return []

    def finish(self) -> list:
        s = self.s
        out = []
        for script, (final, tmp, fh, n) in self.handles.items():
            fh.close()
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, final)
            from gst.assets import write_sidecar

            write_sidecar(final, {**s.w.base_meta, "res_path": f"{s.relpath}#{script}", "type": "MonoBehaviour",
                                  "name": script, "count": n[0], "script": script})
            s.w.outputs.append(final.relative_to(s.w.workspace).as_posix())
            s.w.out_bytes += final.stat().st_size
            out.append(final)
        self.handles.clear()
        if self.untyped:
            path = s.w.target("MonoBehaviour", "_untyped", ".json", "untyped")
            s.w.json(path, {"reason": f"no typetree for these scripts (source: {s.generator()[1]})",
                            "scripts": dict(self.untyped.most_common())},
                     meta={"res_path": f"{s.relpath}#_untyped", "type": "MonoBehaviour"})
            out.append(path)
            s.notes.append(f"{sum(self.untyped.values())} MonoBehaviours without typetree "
                           f"({len(self.untyped)} scripts), see _untyped.json")
        return out


LOCALIZATION_SCRIPTS = {"StringTable", "SharedTableData", "AssetTable", "LocalizationSettings", "Locale"}


def export_localization(s, objs) -> list:
    """StringTables of one unit, keys resolved through their SharedTableData (possibly in another file)."""
    gen, src = s.generator()
    tables, shared = [], {}
    out = []
    for obj in objs:
        info = s.scripts.script_of(obj)
        if not info or info.class_name not in LOCALIZATION_SCRIPTS:
            continue
        tree = typetree.read_mono(obj, gen, info)
        if tree is None:
            continue
        if info.class_name == "SharedTableData":
            shared[(id(obj.assets_file), obj.path_id)] = tree
        if info.class_name == "StringTable":
            tables.append((obj, tree))
        if info.class_name in ("Locale", "LocalizationSettings", "SharedTableData", "AssetTable"):
            name = tree.get("m_Name") or f"{info.class_name}_{obj.path_id}"
            path = s.w.target("Localization", name, ".json", obj.path_id, info.class_name)
            s.w.json(path, _clean(tree), meta=s.meta(obj, tree.get("m_Name", ""), script=info.class_name, typetree=src))
            out.append(path)
    for obj, tree in tables:
        ref = tree.get("m_SharedData") or {}
        sh = None
        reader = s.catalog.resolve(obj.assets_file, ref.get("m_FileID", 0), ref.get("m_PathID", 0))
        if reader is not None:
            sh = shared.get((id(reader.assets_file), reader.path_id))
            if sh is None:
                info = s.scripts.script_of(reader)
                sh = typetree.read_mono(reader, gen, info)
        keys = {e.get("m_Id"): e.get("m_Key") for e in (sh or {}).get("m_Entries", []) or []}
        entries = {}
        for e in tree.get("m_TableData", []) or []:
            key = keys.get(e.get("m_Id")) or str(e.get("m_Id"))
            entries[key] = e.get("m_Localized")
        locale = (tree.get("m_LocaleId") or {}).get("m_Code", "")
        coll = (sh or {}).get("m_TableCollectionName", "")
        name = tree.get("m_Name") or f"{coll}_{locale}"
        path = s.w.target("Localization", name, ".json", obj.path_id, "StringTable")
        s.w.json(path, {"collection": coll, "locale": locale, "entries": entries, "keys_resolved": bool(keys)},
                 meta=s.meta(obj, name, locale=locale, count=len(entries), typetree=src))
        out.append(path)
    return out
