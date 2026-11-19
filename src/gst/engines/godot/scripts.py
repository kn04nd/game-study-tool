"""Exported script variables of Godot scripts (the tunables a scene can override), read from the sources a study
already has: GDScript (``@export var x := 1.0``, Godot 3 ``export(float) var x = 1.0``) in the recovered project or
``decomp/godot``, and C# (``[Export] public float X = 1f;`` / ``{ get; set; } = 1f;``) in ``decomp/cs`` (ilspycmd)
or the project. C# base classes are followed by name (``class A : B``), so inherited exports count too.
Stdlib, regex-based: a lookup aid, not a compiler.
"""

from __future__ import annotations

import re
from pathlib import Path

_GD_EXPORT = re.compile(
    r"^[ \t]*(?:@export\w*(?:\([^)\n]*\))?[ \t]+(?:@\w+(?:\([^)\n]*\))?[ \t]+)*|export(?:\([^)\n]*\))?[ \t]+)"
    r"(?:onready[ \t]+)?var[ \t]+(\w+)(?:[ \t]*:[ \t]*([\w\[\], ]+?))?[ \t]*(?::?=[ \t]*([^\n#]+))?[ \t]*(?:#.*)?$",
    re.M)
_GD_EXPORT_LINE = re.compile(r"^[ \t]*@export\w*(?:\([^)\n]*\))?[ \t]*$", re.M)  # annotation alone on its line
_GD_VAR = re.compile(r"[ \t]*var[ \t]+(\w+)(?:[ \t]*:[ \t]*([\w\[\], ]+?))?[ \t]*(?::?=[ \t]*([^\n#]+))?")
_GD_EXTENDS = re.compile(r"^extends[ \t]+(\S+)", re.M)
_GD_CLASS = re.compile(r"^class_name[ \t]+(\w+)", re.M)

_CS_EXPORT = re.compile(
    r"\[Export(?:\([^\]]*\))?\]\s*(?:\[[^\]]*\]\s*)*"
    r"(?:(?:public|private|protected|internal|static|new|readonly|virtual|override|required)\s+)*"
    r"([\w<>\[\],.?]+(?:\s*<[^>]*>)?)\s+(\w+)\s*(\{[^}]*\}\s*)?(?:=\s*([^;]+?))?\s*;?\s*$",
    re.M)
_CS_CLASS = re.compile(r"\b(?:partial\s+)?class\s+(\w+)(?:<[^>]*>)?\s*(?::\s*([\w.<>]+))?")


def gd_exports(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in _GD_EXPORT.finditer(text):
        out[m.group(1)] = {"type": (m.group(2) or "").strip() or None, "default": (m.group(3) or "").strip() or None}
    for m in _GD_EXPORT_LINE.finditer(text):  # "@export" on one line, "var x" on the next
        v = _GD_VAR.match(text, m.end() + 1)
        if v and v.group(1) not in out:
            out[v.group(1)] = {"type": (v.group(2) or "").strip() or None, "default": (v.group(3) or "").strip() or None}
    return out


def cs_exports(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in _CS_EXPORT.finditer(text):
        out[m.group(2)] = {"type": m.group(1), "default": (m.group(4) or "").strip() or None}
    return out


def cs_class(text: str, name: str) -> tuple[str | None, str | None]:
    """(class name, base class name) of the class called ``name`` in a C# file (else the first class)."""
    first = None
    for m in _CS_CLASS.finditer(text):
        if m.group(1) == name:
            return m.group(1), (m.group(2) or "").split(".")[-1] or None
        if first is None:
            first = (m.group(1), (m.group(2) or "").split(".")[-1] or None)
    return first or (None, None)


class ScriptIndex:
    """Finds the source of a ``res://`` script and its exports (with inherited C# exports)."""

    def __init__(self, project: Path | None, roots: list[Path]):
        self.project = Path(project) if project else None
        self.by_stem: dict[str, list[Path]] = {}
        for root in roots:
            if not root or not Path(root).is_dir():
                continue
            for p in Path(root).rglob("*.cs"):
                if ".godot" in p.parts:
                    continue
                self.by_stem.setdefault(p.stem, []).append(p)
        self._cache: dict[str, dict] = {}
        self.decomp_godot = None
        for r in roots:
            if r and Path(r).name == "godot" and Path(r).parent.name == "decomp":
                self.decomp_godot = Path(r)

    def _read(self, p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def source(self, res: str) -> Path | None:
        rel = res[len("res://"):] if res.startswith("res://") else res
        cands = []
        if self.project:
            cands.append(self.project / rel)
        if self.decomp_godot:
            cands.append(self.decomp_godot / rel)
        for c in cands:
            if c.is_file():
                return c
        if rel.endswith(".cs"):
            stem = Path(rel).stem
            found = self.by_stem.get(stem) or []
            return sorted(found, key=lambda p: (len(p.parts), str(p)))[0] if found else None
        if rel.endswith(".gd"):  # GDRE may leave .gdc; decompiled .gd next to it
            for c in cands:
                if c.with_suffix(".gd").is_file():
                    return c.with_suffix(".gd")
        return None

    def exports(self, res: str) -> dict:
        """``{"source": path|None, "language", "class", "base", "exports": {name: {type, default, declared_in}}}``."""
        if res in self._cache:
            return self._cache[res]
        info: dict = {"source": None, "language": "csharp" if res.endswith(".cs") else "gdscript", "class": None,
                      "base": None, "exports": {}}
        self._cache[res] = info
        src = self.source(res)
        if src is None:
            return info
        info["source"] = src
        text = self._read(src)
        if info["language"] == "csharp":
            name, base = cs_class(text, Path(res).stem)
            info.update({"class": name, "base": base})
            own = {k: {**v, "declared_in": name} for k, v in cs_exports(text).items()}
            seen = {name}
            chain = base
            inherited: dict = {}
            while chain and chain not in seen and chain in self.by_stem:
                seen.add(chain)
                btext = self._read(sorted(self.by_stem[chain], key=lambda p: (len(p.parts), str(p)))[0])
                for k, v in cs_exports(btext).items():
                    inherited.setdefault(k, {**v, "declared_in": chain})
                chain = cs_class(btext, chain)[1]
            info["exports"] = {**inherited, **own}
        else:
            m = _GD_CLASS.search(text)
            e = _GD_EXTENDS.search(text)
            info.update({"class": m.group(1) if m else None, "base": e.group(1).strip('"') if e else None})
            info["exports"] = {k: {**v, "declared_in": res} for k, v in gd_exports(text).items()}
            base = info["base"] or ""
            if base.startswith("res://") or base.endswith(".gd"):  # extends "res://base.gd"
                parent = self.exports(base if base.startswith("res://") else "res://" + base)
                info["exports"] = {**parent["exports"], **info["exports"]}
        return info
