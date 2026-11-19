"""Field statistics over many typetree dicts of the same class (mono_values, catalogs).

For each top-level field: kinds seen (number, bool, string, ref, list, object, null), number range, list
lengths, non-null refs and the value histogram while it stays small (``DISTINCT`` distinct values per field
are tracked; past that only the count of distinct values is reported as ``">DISTINCT"`` - the values
themselves are all in the per-object JSONL).
"""

from __future__ import annotations

import collections
from typing import Any

DISTINCT = 64          # values tracked per field for the histogram (memory bound, not an output cut)
SHOW_VALUES = 16       # histogram shown when a field has at most this many distinct values
SKIP = ("m_GameObject", "m_Enabled", "m_Script", "m_EditorHideFlags", "m_EditorClassIdentifier", "m_ObjectHideFlags")


def is_ref(v: Any) -> bool:
    return isinstance(v, dict) and (set(v) == {"m_FileID", "m_PathID"} or set(v) == {"ref", "file"})


def kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if is_ref(v):
        return "ref" if (v.get("m_PathID") or v.get("ref")) else "null"
    if isinstance(v, (list, tuple)):
        return "list"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


class FieldStats:
    def __init__(self):
        self.count = 0
        self.fields: dict[str, dict] = {}

    def add(self, tree: dict) -> None:
        self.count += 1
        for name, v in tree.items():
            if name in SKIP:
                continue
            f = self.fields.get(name)
            if f is None:
                f = self.fields[name] = {"kinds": collections.Counter(), "min": None, "max": None,
                                         "values": collections.Counter(), "overflow": False, "len_min": None,
                                         "len_max": None, "true": 0, "keys": None}
            k = kind(v)
            f["kinds"][k] += 1
            if k == "number":
                f["min"] = v if f["min"] is None else min(f["min"], v)
                f["max"] = v if f["max"] is None else max(f["max"], v)
            elif k == "bool":
                f["true"] += bool(v)
            elif k == "list":
                n = len(v)
                f["len_min"] = n if f["len_min"] is None else min(f["len_min"], n)
                f["len_max"] = n if f["len_max"] is None else max(f["len_max"], n)
            elif k == "object" and f["keys"] is None:
                f["keys"] = sorted(map(str, v))[:40]
            if k in ("number", "string", "bool") and not f["overflow"]:
                key = v if not isinstance(v, float) else round(v, 6)
                if key in f["values"] or len(f["values"]) < DISTINCT:
                    f["values"][key] += 1
                else:
                    f["overflow"] = True

    def as_dict(self) -> dict:
        out = {}
        for name in sorted(self.fields):
            f = self.fields[name]
            d: dict[str, Any] = {"kinds": dict(sorted(f["kinds"].items()))}
            if f["min"] is not None:
                d["min"], d["max"] = f["min"], f["max"]
            if f["kinds"].get("bool"):
                d["true"] = f["true"]
            if f["len_min"] is not None:
                d["len_min"], d["len_max"] = f["len_min"], f["len_max"]
            if f["keys"]:
                d["keys"] = f["keys"]
            if f["values"]:
                d["distinct"] = f">{DISTINCT}" if f["overflow"] else len(f["values"])
                if not f["overflow"] and len(f["values"]) <= SHOW_VALUES:
                    d["values"] = {str(k): n for k, n in sorted(f["values"].items(), key=lambda kv: (-kv[1], str(kv[0])))}
            out[name] = d
        return out


def describe(d: dict) -> str:
    """One-line human description of a field summary (Markdown tables)."""
    kinds = "/".join(d["kinds"])
    parts = [kinds]
    if "min" in d:
        parts.append(f"{d['min']}..{d['max']}")
    if "len_min" in d:
        parts.append(f"len {d['len_min']}..{d['len_max']}")
    if "values" in d:
        parts.append(", ".join(f"{k} ({n})" for k, n in list(d["values"].items())[:6]))
    elif "distinct" in d:
        parts.append(f"{d['distinct']} distinct")
    return "; ".join(parts)
