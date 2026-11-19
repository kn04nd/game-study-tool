"""Parser of Godot text scenes and resources (``.tscn``/``.tres``/``.escn``, format 2 = Godot 3, 3/4 = Godot 4) into
plain Python/JSON. Stdlib only.

File = sections (``[tag key=value ...]``) each followed by ``key = value`` properties::

    [gd_scene load_steps=3 format=3 uid="uid://..."]       header (gd_resource has type="...")
    [ext_resource type="Script" path="res://p.cs" id="1_x"]
    [sub_resource type="Animation" id="Animation_a"]       + its properties
    [node name="Root" type="Node3D"]                       + its properties (parent=, instance=, groups=...)
    [connection signal="pressed" from="B" to="." method="_on"]
    [editable path="Child"]
    [resource]                                            (gd_resource) the main resource's properties

Values (``parse_value``): numbers (``inf``, ``-inf``, ``nan``), strings with escapes (multiline allowed), ``&"name"``
(StringName), ``^"path"`` / ``NodePath("path")``, ``true``/``false``/``null``, arrays ``[...]``, dictionaries
``{k: v}``, typed ``Array[T]([...])`` / ``Dictionary[K, V]({...})``, constructors ``Vector3(1, 2, 3)``,
``Transform3D(...)``, ``Color(...)``, ``Packed*Array(...)``, ``Object(Class, "k": v)`` and the references
``ExtResource("id")`` / ``SubResource("id")`` (Godot 3: ``ExtResource( 1 )``).

Python form: strings, numbers, bools, None, lists, dicts (keys made strings with :func:`key_str`) and the classes
:class:`Ctor` (name + args), :class:`Ref` (ext|sub + id), :class:`NodePath`, :class:`StringName` (and :class:`Bulk`,
a packed array the binary reader ``rsrc.py`` left undecoded).
:func:`to_json` turns any of them into JSON (``{"type": "Vector3", "args": [...]}``, ``{"ref": "ext", "id": ...}``,
``{"type": "NodePath", "path": ...}``; StringName -> plain string), optionally summarising long packed arrays.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any


class TresError(ValueError):
    pass


@dataclass(frozen=True)
class Ctor:
    name: str
    args: tuple

    def nums(self) -> list:
        return [a for a in self.args if isinstance(a, (int, float))]


@dataclass(frozen=True)
class Ref:
    kind: str  # "ext" | "sub"
    id: str


@dataclass(frozen=True)
class Bulk:
    """A packed array the binary reader left undecoded (``rsrc.read(bulk=N)``): its type and value count (the
    length ``Ctor.args`` would have, e.g. 3 per item for PackedVector3Array)."""
    name: str
    count: int


class NodePath(str):
    pass


class StringName(str):
    pass


@dataclass
class Section:
    tag: str
    attrs: dict = field(default_factory=dict)
    props: dict = field(default_factory=dict)
    line: int = 0


_NUM = re.compile(r"[-+]?(?:\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WS = " \t\r\n"
_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "b": "\b", "f": "\f", "a": "\a", "v": "\v",
        "0": "\0"}
_WORDS = {"true": True, "false": False, "null": None, "nil": None, "inf": math.inf, "inf_neg": -math.inf,
          "nan": math.nan}


class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.n = len(text)

    # --- low level
    def err(self, msg: str):
        line = self.s.count("\n", 0, self.i) + 1
        raise TresError(f"line {line}: {msg}")

    def ws(self) -> None:
        s, n = self.s, self.n
        while self.i < n:
            c = s[self.i]
            if c in _WS:
                self.i += 1
            elif c == ";":  # comment to the end of the line
                j = s.find("\n", self.i)
                self.i = n if j < 0 else j + 1
            else:
                break

    def peek(self) -> str:
        return self.s[self.i] if self.i < self.n else ""

    def expect(self, ch: str) -> None:
        self.ws()
        if self.peek() != ch:
            self.err(f"expected {ch!r}, found {self.s[self.i:self.i + 20]!r}")
        self.i += 1

    def string(self) -> str:
        # at the opening quote
        s = self.s
        self.i += 1
        out = []
        start = self.i
        while True:
            j = s.find('"', self.i)
            k = s.find("\\", self.i, j if j >= 0 else self.n)
            if j < 0:
                self.err("unterminated string")
            if k < 0:
                out.append(s[start:j])
                self.i = j + 1
                return "".join(out)
            out.append(s[start:k])
            c = s[k + 1:k + 2]
            if c == "u" or c == "U":
                width = 4 if c == "u" else 6
                hexs = s[k + 2:k + 2 + width]
                try:
                    out.append(chr(int(hexs, 16)))
                except ValueError:
                    out.append("\\" + c + hexs)
                self.i = k + 2 + width
            else:
                out.append(_ESC.get(c, c))
                self.i = k + 2
            start = self.i

    # --- values
    def value(self) -> Any:
        self.ws()
        c = self.peek()
        if c == '"':
            return self.string()
        if c == "&" and self.s[self.i + 1:self.i + 2] == '"':
            self.i += 1
            return StringName(self.string())
        if c == "^" and self.s[self.i + 1:self.i + 2] == '"':
            self.i += 1
            return NodePath(self.string())
        if c == "[":
            return self.array()
        if c == "{":
            return self.dict()
        m = _NUM.match(self.s, self.i)
        if m and (c not in "+-" or m.end() > self.i + 1):
            self.i = m.end()
            t = m.group(0)
            if any(ch in t for ch in ".eE"):
                return float(t)
            return int(t)
        if c == "-" and self.s.startswith("-inf", self.i):
            self.i += 4
            return -math.inf
        m = _IDENT.match(self.s, self.i)
        if not m:
            self.err(f"unexpected {self.s[self.i:self.i + 20]!r}")
        name = m.group(0)
        self.i = m.end()
        self.ws()
        nxt = self.peek()
        if nxt == "[" and name in ("Array", "Dictionary"):  # typed container: Array[T]([...])
            self.i += 1
            self.items("]")
            self.expect("(")
            self.ws()
            inner = self.value() if self.peek() != ")" else ([] if name == "Array" else {})
            self.expect(")")
            return inner
        if nxt == "(":
            self.i += 1
            args = self.ctor_args()
            if name in ("ExtResource", "SubResource"):
                rid = args[0] if args else ""
                return Ref("ext" if name == "ExtResource" else "sub", str(rid))
            if name == "NodePath":
                return NodePath(args[0] if args else "")
            if name == "StringName":
                return StringName(args[0] if args else "")
            if name in ("Array",) and len(args) == 1 and isinstance(args[0], list):
                return args[0]
            if name in ("Dictionary",) and len(args) == 1 and isinstance(args[0], dict):
                return args[0]
            return Ctor(name, tuple(args))
        if name in _WORDS:
            return _WORDS[name]
        return Ctor(name, ())  # a bare identifier (Godot 3 enum-ish words); kept with its name

    def ctor_args(self) -> list:
        args: list = []
        self.ws()
        if self.peek() == ")":
            self.i += 1
            return args
        while True:
            self.ws()
            v = self.value()
            self.ws()
            if self.peek() == ":":  # Object(Class, "prop": value, ...)
                self.i += 1
                v = (key_str(v), self.value())
                self.ws()
            args.append(v)
            c = self.peek()
            if c == ",":
                self.i += 1
                self.ws()
                if self.peek() == ")":
                    self.i += 1
                    return args
                continue
            if c == ")":
                self.i += 1
                return args
            self.err(f"expected ',' or ')' in arguments, found {self.s[self.i:self.i + 20]!r}")

    def items(self, close: str) -> list:
        out = []
        self.ws()
        if self.peek() == close:
            self.i += 1
            return out
        while True:
            out.append(self.value())
            self.ws()
            c = self.peek()
            if c == ",":
                self.i += 1
                self.ws()
                if self.peek() == close:
                    self.i += 1
                    return out
                continue
            if c == close:
                self.i += 1
                return out
            self.err(f"expected ',' or {close!r}, found {self.s[self.i:self.i + 20]!r}")

    def array(self) -> list:
        self.i += 1
        return self.items("]")

    def dict(self) -> dict:
        self.i += 1
        out: dict = {}
        self.ws()
        if self.peek() == "}":
            self.i += 1
            return out
        while True:
            k = self.value()
            self.expect(":")
            out[key_str(k)] = self.value()
            self.ws()
            c = self.peek()
            if c == ",":
                self.i += 1
                self.ws()
                if self.peek() == "}":
                    self.i += 1
                    return out
                continue
            if c == "}":
                self.i += 1
                return out
            self.err(f"expected ',' or '}}' in dictionary, found {self.s[self.i:self.i + 20]!r}")

    # --- file structure
    def section_head(self) -> Section:
        line = self.s.count("\n", 0, self.i) + 1
        self.i += 1  # '['
        self.ws()
        m = _IDENT.match(self.s, self.i)
        if not m:
            self.err("section without a tag")
        sec = Section(tag=m.group(0), line=line)
        self.i = m.end()
        while True:
            self.ws()
            c = self.peek()
            if c == "]":
                self.i += 1
                return sec
            m = _IDENT.match(self.s, self.i)
            if not m:
                self.err(f"bad section attribute at {self.s[self.i:self.i + 20]!r}")
            self.i = m.end()
            self.expect("=")
            sec.attrs[m.group(0)] = self.value()

    def prop_key(self) -> str:
        if self.peek() == '"':
            key = self.string()
        else:
            j = self.i
            s, n = self.s, self.n
            while j < n and s[j] not in "=\n":
                j += 1
            key = s[self.i:j].strip()
            self.i = j
        self.expect("=")
        return key

    def document(self) -> list[Section]:
        secs: list[Section] = []
        cur: Section | None = None
        top: dict = {}  # properties before any section (should not happen; kept)
        while True:
            self.ws()
            if self.i >= self.n:
                break
            if self.peek() == "[":
                cur = self.section_head()
                secs.append(cur)
                continue
            key = self.prop_key()
            val = self.value()
            (cur.props if cur is not None else top)[key] = val
        if top:
            secs.insert(0, Section(tag="_top", props=top))
        return secs


def key_str(k: Any) -> str:
    """A dictionary key as a JSON-able string (strings stay; refs and constructors get a readable form)."""
    if isinstance(k, str):
        return str(k)
    if isinstance(k, bool) or k is None:
        return str(k).lower() if k is not None else "null"
    if isinstance(k, (int, float)):
        return repr(k)
    if isinstance(k, Ref):
        return f"{'ExtResource' if k.kind == 'ext' else 'SubResource'}({k.id})"
    if isinstance(k, Ctor):
        return f"{k.name}({', '.join(key_str(a) for a in k.args)})"
    return str(k)


def parse_value(text: str) -> Any:
    p = _Parser(text)
    v = p.value()
    p.ws()
    if p.i < p.n:
        p.err(f"trailing text {text[p.i:p.i + 20]!r}")
    return v


def parse_sections(text: str) -> list[Section]:
    if text.startswith("﻿"):
        text = text[1:]
    return _Parser(text).document()


def parse(text: str) -> dict:
    """A whole ``.tscn``/``.tres`` -> ``{kind, header, ext_resources: {id: {type, path, uid}}, sub_resources: {id:
    {type, props}}, sub_order, nodes: [{name, type, parent, path, instance, props, ...}], connections, editable,
    resource}``. ``path`` of a node is relative to the scene root ("." for the root)."""
    secs = parse_sections(text)
    doc: dict = {"kind": None, "header": {}, "ext_resources": {}, "sub_resources": {}, "sub_order": [], "nodes": [],
                 "connections": [], "editable": [], "resource": None}
    for sec in secs:
        t = sec.tag
        if t in ("gd_scene", "gd_resource"):
            doc["kind"] = "scene" if t == "gd_scene" else "resource"
            doc["header"] = dict(sec.attrs)
        elif t == "ext_resource":
            rid = str(sec.attrs.get("id", ""))
            doc["ext_resources"][rid] = {"type": sec.attrs.get("type"), "path": sec.attrs.get("path"),
                                         "uid": sec.attrs.get("uid"), "line": sec.line}
        elif t == "sub_resource":
            rid = str(sec.attrs.get("id", ""))
            doc["sub_resources"][rid] = {"type": sec.attrs.get("type"), "props": sec.props, "line": sec.line}
            doc["sub_order"].append(rid)
        elif t == "node":
            a = sec.attrs
            parent = a.get("parent")
            name = str(a.get("name", ""))
            if parent is None:
                path = "."
            elif parent == ".":
                path = name
            else:
                path = f"{parent}/{name}"
            node = {"name": name, "type": a.get("type"), "parent": parent, "path": path, "line": sec.line,
                    "props": sec.props}
            for k in ("instance", "instance_placeholder", "groups", "owner", "index", "unique_id", "parent_id_path"):
                if k in a:
                    node[k] = a[k]
            doc["nodes"].append(node)
        elif t == "connection":
            doc["connections"].append({**{k: v for k, v in sec.attrs.items()}, "line": sec.line})
        elif t == "editable":
            doc["editable"].append(sec.attrs.get("path"))
        elif t == "resource":
            doc["resource"] = {"type": doc["header"].get("type"), "props": sec.props, "line": sec.line}
    return doc


# --- JSON ---------------------------------------------------------------------------------------

def to_json(v: Any, *, packed_max: int | None = None) -> Any:
    """Python form -> JSON. ``packed_max``: Packed*Array / long numeric lists over this many items become
    ``{"type", "count", "head"}`` (None = everything, the default)."""
    if isinstance(v, StringName):
        return str(v)
    if isinstance(v, NodePath):
        return {"type": "NodePath", "path": str(v)}
    if isinstance(v, Ref):
        return {"ref": v.kind, "id": v.id}
    if isinstance(v, Bulk):
        return {"type": v.name, "count": v.count, "decoded": False}
    if isinstance(v, Ctor):
        if packed_max is not None and v.name.startswith("Packed") and len(v.args) > packed_max:
            return {"type": v.name, "count": len(v.args), "head": [to_json(a) for a in v.args[:8]]}
        args = [list(a) if isinstance(a, tuple) else a for a in v.args]
        return {"type": v.name, "args": [to_json(a, packed_max=packed_max) for a in args]}
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return v
    if isinstance(v, (list, tuple)):
        return [to_json(a, packed_max=packed_max) for a in v]
    if isinstance(v, dict):
        return {str(k): to_json(a, packed_max=packed_max) for k, a in v.items()}
    return v


def short(v: Any, doc: dict | None = None) -> str:
    """A one-line, Godot-like text form of a value (value keys in summaries, .md tables); never truncated here.
    With ``doc``, ``ExtResource`` shows its ``res://`` path."""
    if isinstance(v, StringName):
        s = f'&"{v}"'
    elif isinstance(v, NodePath):
        s = f'NodePath("{v}")'
    elif isinstance(v, Ref):
        path = ref_path(doc, v) if doc is not None else None
        s = f'ExtResource("{path}")' if path else f"{'ExtResource' if v.kind == 'ext' else 'SubResource'}(\"{v.id}\")"
    elif isinstance(v, Ctor):
        s = f"{v.name}({', '.join(short(a, doc) for a in v.args)})"
    elif isinstance(v, Bulk):
        s = f"{v.name}(<{v.count} values>)"
    elif isinstance(v, float):
        s = format(v, ".9g")
    elif isinstance(v, str):
        s = '"' + v.replace("\n", "\\n") + '"'
    elif isinstance(v, bool):
        s = "true" if v else "false"
    elif v is None:
        s = "null"
    elif isinstance(v, list):
        s = "[" + ", ".join(short(a, doc) for a in v) + "]"
    elif isinstance(v, dict):
        s = "{" + ", ".join(f"{k}: {short(a, doc)}" for k, a in v.items()) + "}"
    else:
        s = str(v)
    return s


def ref_path(doc: dict, v: Any) -> str | None:
    """``ExtResource("id")`` -> its ``res://`` path; anything else -> None."""
    if isinstance(v, Ref) and v.kind == "ext":
        e = doc["ext_resources"].get(v.id)
        return e.get("path") if e else None
    return None


def ref_type(doc: dict, v: Any) -> str | None:
    if isinstance(v, Ref):
        table = doc["ext_resources"] if v.kind == "ext" else doc["sub_resources"]
        e = table.get(v.id)
        return e.get("type") if e else None
    return None


def iter_refs(v: Any):
    """Every :class:`Ref` inside a value (arrays, dicts, constructors)."""
    if isinstance(v, Ref):
        yield v
    elif isinstance(v, (list, tuple)):
        for a in v:
            yield from iter_refs(a)
    elif isinstance(v, dict):
        for a in v.values():
            yield from iter_refs(a)
    elif isinstance(v, Ctor):
        for a in v.args:
            yield from iter_refs(a)


def is_binary(head: bytes) -> bool:
    """Binary resource (``.scn``/``.res``: ``RSRC`` or compressed ``RSCC``) rather than text."""
    return head[:4] in (b"RSRC", b"RSCC")
