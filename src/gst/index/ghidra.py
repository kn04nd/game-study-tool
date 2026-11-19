"""Ghidra pseudo-C written by GsExport.java (`gst decompile native`).

The C is not parsed: each `X.c` has an `X.c.map.json` sidecar with its functions (qualified name, RVA,
signature, line range) and call sites taken from the decompiler's own tokens, so call edges are exact.
String literals are read from the C text. A `.c` without a sidecar yields only its line count.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

STRING = re.compile(r'(?:\bu8|\b[uUL])?"((?:[^"\\\n]|\\.){3,})"')


def extract(rel: str, source: bytes, full: str | None = None) -> dict:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    out = {"lines": len(lines), "has_error": False, "symbols": [], "bases": [], "refs": [], "strings": []}
    sidecar = Path(f"{full}.map.json") if full else None
    if sidecar is None or not sidecar.is_file():
        return out
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    parent = None
    if data.get("namespace"):
        parent = 0
        ns = data["namespace"]
        out["symbols"].append((0, None, data.get("kind") or "namespace", ns.rsplit(".", 1)[-1], ns, "", 1, len(lines), None))
        out["bases"] = [(0, base) for base in data.get("bases") or []]
    ranges = []
    for i, fn in enumerate(data.get("functions", []), 1):
        # the body_hash column holds the address for native code: "rva:0x1a2b0" (module+RVA in FACTS.md)
        out["symbols"].append((i, parent, fn.get("kind") or "function", fn["name"], fn["q"], fn.get("sig") or "",
                               fn["line"], fn["end"], f"rva:{fn['rva']}" if fn.get("rva") else None))
        ranges.append((fn["line"], fn["end"], i))
        for line, target in fn.get("calls", []):
            out["refs"].append(("call", target, target.rsplit(".", 1)[-1], line, i))
    for number, line in enumerate(lines, 1):
        if '"' not in line or line.lstrip().startswith("//"):
            continue
        owner = next((i for start, end, i in ranges if start <= number <= end), None)
        for m in STRING.finditer(line):
            out["strings"].append((m.group(1)[:400], number, owner))
    return out
