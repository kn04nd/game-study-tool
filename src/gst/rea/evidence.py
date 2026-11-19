"""Read REA Evidence: one record (``rea <cmd> --json``) or a bundle (``{"records": [...]}``, the MCP export and
``--evidence_output`` format). Both shapes are accepted everywhere in gst (split, import, ``analyze systems``)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

from gst.common import long_path


def load(path: str | os.PathLike) -> dict | list | None:
    """Parse one Evidence JSON file (None when it is not JSON)."""
    try:
        with open(long_path(path), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def records(doc) -> Iterator[dict]:
    """Evidence records of a bundle, or the document itself when it is one record."""
    if isinstance(doc, dict):
        if isinstance(doc.get("records"), list):
            for rec in doc["records"]:
                if isinstance(rec, dict):
                    yield rec
        elif "normalized_result" in doc:
            yield doc
    elif isinstance(doc, list):
        for item in doc:
            yield from records(item)


def result(rec: dict) -> dict:
    nr = rec.get("normalized_result")
    return nr if isinstance(nr, dict) else {}


def artifact(rec: dict) -> dict:
    """``{"path", "sha256", "name"}`` of the record's primary artifact."""
    nr = result(rec)
    art = nr.get("artifact") if isinstance(nr.get("artifact"), dict) else {}
    subject = rec.get("subject") if isinstance(rec.get("subject"), dict) else {}
    path = str(art.get("path") or subject.get("local_path") or "")
    sha = str(art.get("sha256") or (subject.get("digest") or {}).get("sha256") or "")
    name = str(subject.get("name") or Path(path.replace("\\", "/")).name or "")
    return {"path": path, "sha256": sha, "name": name}


def assembly(rec: dict) -> str:
    """Managed assembly name (``Assembly-CSharp``), else the artifact file name without extension."""
    nr = result(rec)
    for key in ("assembly", "module"):
        value = nr.get(key)
        if isinstance(value, dict) and value.get("name"):
            name = str(value["name"])
            return name[:-4] if name.lower().endswith((".dll", ".exe")) else name
    name = artifact(rec)["name"]
    return name.rsplit(".", 1)[0] if "." in name else name


def operation(rec: dict) -> str:
    return str(rec.get("operation") or "")


def split_member(full: str) -> tuple[str, str]:
    """``Ns.Type.Method`` -> (``Ns.Type``, ``Method``), keeping ``.ctor``/``.cctor`` and nested ``Outer/Inner``."""
    for special in ("..ctor", "..cctor"):
        if full.endswith(special):
            return full[: -len(special)], special[1:]
    head, _, tail = full.rpartition(".")
    return head, tail


def short_type(type_name: str) -> str:
    """``Ns.Outer/Inner`1`` -> ``Inner``: the code index keys types by simple name."""
    t = type_name.replace("/", ".").replace("+", ".").split("`", 1)[0].split("<", 1)[0]
    return t.rsplit(".", 1)[-1]
