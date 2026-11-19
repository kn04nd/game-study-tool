"""IL2CPP name map: every name with a native address the IL2CPP tools recovered, normalized into
``decomp/il2cpp/names.jsonl`` for Ghidra (``GstIl2cppNames.java``) and anything else that needs
``RVA -> Namespace.Type$$Method``.

Sources (adapters below), each optional:

- ``il2cppdumper``: Il2CppDumper ``script.json`` (ScriptMethod, ScriptString, ScriptMetadata,
  ScriptMetadataMethod, Addresses) at ``decomp/il2cpp/il2cppdumper/script.json``.
- ``inspector``: Il2CppInspector(Redux) JSON metadata (``addressMap``) under ``decomp/il2cpp/il2cppinspector/``
  or ``decomp/il2cpp/inspector/``; its addresses are VAs, the PE image base turns them into RVAs.
- ``cpp2il``: Cpp2IL diffable C# (``--output-as diffable-cs`` with the ``attributeinjector`` processor) or any
  C# written from its dummy DLLs: ``[Address(RVA = "0x..", Offset = "0x..", Length = "0x..")]`` before each
  method; folders ``decomp/il2cpp/cpp2il/`` and ``decomp/il2cpp/cpp2il-diffable/``.
- ``rea``: raw REA ``inspect_il2cpp_metadata`` Evidence (``dumps/rea/evidence/inspect_il2cpp_metadata/*.json``;
  method RVAs only when REA ran with ``binary_path``) and REA ``--ghidra-seeds`` files (``dumps/rea/**/*seeds*.txt``).

File format (docs/INTERFACES.md section 7): line 1 is a header ``{"_header": 1, "format", "game_assembly",
"sources", "counts", "precedence", ...}``; every other line is one address and kind::

    {"rva": int, "name": str, "signature": str|null, "kind": str, "type": str|null, "source": str}

plus, when known, ``ns`` (Ghidra namespace path of a method: namespace parts + type), ``also`` (other names
at the same address: identical code folding), ``value`` (string literal text) and ``size`` (code bytes).
``kind`` is one of :data:`KINDS`; ``function`` is a code address without a name (Il2CppDumper ``Addresses``).
Lines are sorted by ``(rva, kind)``; one line per ``(rva, kind)``: the name of the first source in
:data:`PRECEDENCE` wins and the others are counted as conflicts when they disagree.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator

from gst.common import read_json, write_json

NAMES = Path("decomp") / "il2cpp" / "names.jsonl"
FORMAT = 1
KINDS = ("method", "method_info", "type_info", "string_literal", "field", "metadata", "function")
SOURCES = ("il2cppdumper", "inspector", "cpp2il", "rea")
PRECEDENCE = SOURCES  # Il2CppDumper is the reference naming; REA last (newest reimplementation)
GHIDRA_NAME_MAX = 2000  # Ghidra's own SymbolUtilities.MAX_SYMBOL_NAME_LENGTH, not a gst ceiling

SCRIPT_JSON = Path("decomp") / "il2cpp" / "il2cppdumper" / "script.json"
INSPECTOR_DIRS = (Path("decomp") / "il2cpp" / "il2cppinspector", Path("decomp") / "il2cpp" / "inspector")
CPP2IL_DIRS = (Path("decomp") / "il2cpp" / "cpp2il", Path("decomp") / "il2cpp" / "cpp2il-diffable")
REA_EVIDENCE = Path("dumps") / "rea" / "evidence" / "inspect_il2cpp_metadata"
REA_DIR = Path("dumps") / "rea"
ADDRESSES_TSV = Path("decomp") / "il2cpp" / "addresses.tsv"  # code.il2cpp.*: Cpp2IL [Address] rows
IL2CPP_METADATA = Path("decomp") / "il2cpp" / "metadata.json"  # code.il2cpp.metadata: binary.image_base


# --- names ----------------------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def ghidra_name(name: str) -> str:
    """A symbol name Ghidra accepts: no whitespace or control characters, not empty, within Ghidra's
    2000-character symbol limit (longer names keep their start plus a hash of the whole name)."""
    s = _CTRL.sub("", _WS.sub("_", name.strip()))
    if not s:
        return "_"
    if len(s) > GHIDRA_NAME_MAX:
        s = s[: GHIDRA_NAME_MAX - 17] + "~" + hashlib.blake2b(s.encode("utf-8"), digest_size=8).hexdigest()
    return s


def split_type(type_name: str) -> list[str]:
    """``Ns.Sub.Outer.Inner<A.B, C>`` -> ``[Ns, Sub, Outer, Inner<A.B, C>]`` (dots inside <> or [] stay;
    ``/`` and ``+`` nested-type separators count as dots)."""
    parts, depth, cur = [], 0, []
    for ch in type_name:
        if ch in "<[(":
            depth += 1
        elif ch in ">])":
            depth = max(0, depth - 1)
        if depth == 0 and ch in "./+":
            if cur:
                parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def method_name(type_name: str | None, method: str) -> str:
    """Il2CppDumper style ``Namespace.Type$$Method``."""
    return f"{type_name}$${method}" if type_name else method


def split_method(name: str) -> tuple[str | None, str]:
    """``Ns.Type$$Method`` -> (``Ns.Type``, ``Method``); a name without ``$$`` has no type."""
    head, sep, tail = name.partition("$$")
    return (head, tail) if sep else (None, name)


def namespace_path(type_name: str | None) -> list[str] | None:
    if not type_name:
        return None
    return [ghidra_name(p) for p in split_type(type_name)] or None


def row(rva: int, name: str, kind: str, source: str, *, signature: str | None = None, type_name: str | None = None,
        **extra) -> dict:
    out = {"rva": int(rva), "name": name, "signature": signature, "kind": kind, "type": type_name, "source": source}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


# --- adapters -------------------------------------------------------------------------------------

def from_script_json(path: Path) -> Iterator[dict]:
    """Il2CppDumper ``script.json``: RVAs in ``Address`` fields."""
    data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    for m in data.get("ScriptMethod") or []:
        name = str(m.get("Name") or "")
        if m.get("Address") is None or not name:
            continue
        type_name, _ = split_method(name)
        yield row(m["Address"], name, "method", "il2cppdumper", signature=m.get("Signature"), type_name=type_name)
    for i, s in enumerate(data.get("ScriptString") or []):
        if s.get("Address") is None:
            continue
        yield row(s["Address"], f"StringLiteral_{i + 1}", "string_literal", "il2cppdumper", value=s.get("Value"))
    for m in data.get("ScriptMetadata") or []:
        name = str(m.get("Name") or "")
        if m.get("Address") is None or not name:
            continue
        if name.endswith("_TypeInfo"):
            kind, type_name = "type_info", name[: -len("_TypeInfo")]
        elif name.startswith("Field$"):
            kind, type_name = "field", None
        else:
            kind, type_name = "metadata", None
        yield row(m["Address"], name, kind, "il2cppdumper", signature=m.get("Signature"), type_name=type_name)
    for m in data.get("ScriptMetadataMethod") or []:
        name = str(m.get("Name") or "")
        if m.get("Address") is None or not name:
            continue
        yield row(m["Address"], name, "method_info", "il2cppdumper", method_rva=m.get("MethodAddress"))
    for address in data.get("Addresses") or []:
        yield row(address, "", "function", "il2cppdumper")


def _va(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 16 if str(value).lower().startswith("0x") else 10)
    except ValueError:
        return None


def demangle_nested(name: str) -> list[str]:
    """Components of an Itanium nested name as Il2CppInspector writes them: ``_ZN8TypeInfo6System6ObjectE`` ->
    ``[TypeInfo, System, Object]`` (parameters after the closing ``E`` are ignored; ``C1``/``D1`` end the list)."""
    if not name.startswith("_ZN"):
        return []
    out, i = [], 3
    while i < len(name) and name[i].isdigit():
        j = i
        while j < len(name) and name[j].isdigit():
            j += 1
        n = int(name[i:j])
        out.append(name[j:j + n])
        i = j + n
    return out


def _inspector_type(group: str) -> str | None:
    """``Assembly-CSharp.dll/SafariTV/Gameplay/Player+<Run>d__5`` -> ``SafariTV.Gameplay.Player.<Run>d__5``."""
    if not group or "/" not in group:
        return None
    return group.split("/", 1)[1].replace("/", ".").replace("+", ".")


def _inspector_method(dotnet_sig: str, c_sig: str) -> str | None:
    """Metadata method name from Il2CppInspector's ``dotNetSignature`` (``Void Configure(Transform, ...)``;
    constructors have no return type: ``Player(Int32)``)."""
    if not dotnet_sig:
        return None
    depth, cut = 0, len(dotnet_sig)
    for i, ch in enumerate(dotnet_sig):
        if ch in "[<":
            depth += 1
        elif ch in "]>":
            depth -= 1
        elif ch == "(" and depth == 0:
            cut = i
            break
    head = dotnet_sig[:cut].strip()
    depth, space = 0, -1
    for i, ch in enumerate(head):
        if ch in "[<":
            depth += 1
        elif ch in "]>":
            depth -= 1
        elif ch == " " and depth == 0:
            space = i
    if space < 0:  # no return type: a constructor
        return ".cctor" if "__cctor(" in (c_sig or "") else ".ctor"
    name = head[space + 1:]
    if name.endswith("]") and "[" in name:  # generic method arguments Foo[T]
        name = name[: name.index("[")]
    return _strip_generic_suffix(name)


def from_inspector_json(path: Path, image_base: int) -> Iterator[dict]:
    """Il2CppInspector(Redux) JSON metadata (``addressMap``, VAs: VA - image base = RVA). Method names come from
    ``group`` (type) + ``dotNetSignature`` (method): its ``name`` fields are Itanium-mangled."""
    data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    amap = data.get("addressMap") or {}

    def rva_of(entry) -> int | None:
        va = _va(entry.get("virtualAddress") if isinstance(entry, dict) else entry)
        return None if va is None or va < image_base else va - image_base

    for key in ("methodDefinitions", "constructedGenericMethods"):
        for m in amap.get(key) or []:
            rva = rva_of(m)
            type_name = _inspector_type(str(m.get("group") or ""))
            method = _inspector_method(str(m.get("dotNetSignature") or ""), str(m.get("signature") or ""))
            if rva is None or not type_name or not method:
                continue
            yield row(rva, method_name(type_name, method), "method", "inspector",
                      signature=m.get("dotNetSignature") or m.get("signature"), type_name=type_name)
    for s in amap.get("stringLiterals") or []:
        rva = rva_of(s)
        if rva is not None:
            yield row(rva, str(s.get("name") or f"StringLiteral_{rva:x}"), "string_literal", "inspector", value=s.get("string"))
    for key, kind, suffix in (("typeInfoPointers", "type_info", "_TypeInfo"), ("typeRefPointers", "metadata", "_var")):
        for t in amap.get(key) or []:
            rva, parts = rva_of(t), demangle_nested(str(t.get("name") or ""))
            if rva is None or not (parts[1:] or t.get("name")):
                continue
            type_name = ".".join(parts[1:]) if parts[1:] else None
            yield row(rva, f"{type_name}{suffix}" if type_name else str(t["name"]), kind, "inspector",
                      signature=t.get("type") or t.get("dotNetType"), type_name=type_name)
    for m in amap.get("methodInfoPointers") or []:
        rva, parts = rva_of(m), demangle_nested(str(m.get("name") or ""))
        if rva is None or not (parts[1:] or m.get("name")):
            continue
        method_rva = _va(m.get("methodAddress"))
        yield row(rva, "Method$" + ".".join(parts[1:]) if parts[1:] else str(m["name"]), "method_info", "inspector",
                  signature=m.get("dotNetSignature"),
                  method_rva=method_rva - image_base if method_rva and method_rva >= image_base else None)
    for key in ("fields", "fieldRvas"):
        for f in amap.get(key) or []:
            rva = rva_of(f)
            if rva is not None and f.get("name"):
                yield row(rva, str(f["name"]), "field", "inspector")
    for address in amap.get("functionAddresses") or []:
        rva = rva_of(address)
        if rva:
            yield row(rva, "", "function", "inspector")


_ILSPY_ESCAPE = re.compile(r"_00([0-9A-Fa-f]{2})")
TSV_METHOD_KINDS = {"method", "ctor", "dtor", "operator", "function"}


def from_addresses_tsv(path: Path) -> Iterator[dict]:
    """``decomp/il2cpp/addresses.tsv`` (Cpp2IL ``[Address]`` attributes read back from ``decomp/cs-il2cpp``):
    ``rva va offset length token field_offset kind qname decl path line``; methods, constructors and operators
    (property/event rows do not say which accessor they are and are left to the other sources)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        col = {name: i for i, name in enumerate(header)}
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) < len(header) or not f[col["rva"]] or f[col["kind"]] not in TSV_METHOD_KINDS:
                continue
            qname = _ILSPY_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), f[col["qname"]])
            type_name, _, member = qname.rpartition(".")
            if not type_name:
                continue
            kind = f[col["kind"]]
            if qname.endswith((("..ctor", "..cctor"))):
                type_name = qname.rsplit("..", 1)[0]
            if kind == "ctor":  # qname = Type.TypeName
                member = ".cctor" if re.search(r"\bstatic\b", f[col["decl"]]) else ".ctor"
            elif kind == "dtor":
                member = "Finalize"
            elif kind == "operator":  # qname = Type.operator_declaration: the name is in the declaration
                member = _member(f[col["decl"]], "", None) or member
            length = _va(f[col["length"]]) if f[col["length"]] else None
            yield row(int(f[col["rva"]], 16), method_name(type_name, member), "method", "cpp2il",
                      signature=f[col["decl"]] or None, type_name=type_name, size=length)


# Cpp2IL diffable C# (and C# decompiled from its dummy DLLs) ----------------------------------------

ADDRESS = re.compile(r'\[Address\(RVA\s*=\s*"0x([0-9A-Fa-f]+)"(?:[^\]]*?Length\s*=\s*"0x([0-9A-Fa-f]+)")?')
NAMESPACE = re.compile(r"^\s*namespace\s+([\w.@`]+)\s*(;|\{|$)")
TYPE_DECL = re.compile(r"^(\s*)(?:\[[^\]]*\]\s*)*(?:(?:public|private|protected|internal|static|sealed|abstract|"
                       r"readonly|unsafe|partial|ref|new|file)\s+)*(class|struct|interface|enum|record(?:\s+struct)?)\s+"
                       r"((?:<[^>]*>)?[^\s<:{]*(?:<[^>]*>)?)")  # <Run>d__5, <>c, Foo, Foo<A, B>
ACCESSOR = re.compile(r"^\s*(?:(?:public|private|protected|internal)\s+)*(get|set|init|add|remove)\b")
OPERATORS = {
    "+": "op_Addition", "-": "op_Subtraction", "*": "op_Multiply", "/": "op_Division", "%": "op_Modulus",
    "==": "op_Equality", "!=": "op_Inequality", "<": "op_LessThan", ">": "op_GreaterThan", "<=": "op_LessThanOrEqual",
    ">=": "op_GreaterThanOrEqual", "!": "op_LogicalNot", "~": "op_OnesComplement", "++": "op_Increment",
    "--": "op_Decrement", "&": "op_BitwiseAnd", "|": "op_BitwiseOr", "^": "op_ExclusiveOr", "<<": "op_LeftShift",
    ">>": "op_RightShift", ">>>": "op_UnsignedRightShift", "true": "op_True", "false": "op_False",
}
_ACCESSOR_PREFIX = {"get": "get_", "set": "set_", "init": "set_", "add": "add_", "remove": "remove_"}


def _strip_generic_suffix(name: str) -> str:
    """``Foo<T, U>`` -> ``Foo`` (only a trailing balanced <...>; ``<Run>d__5`` stays)."""
    if not name.endswith(">"):
        return name
    depth = 0
    for i in range(len(name) - 1, -1, -1):
        depth += {">": 1, "<": -1}.get(name[i], 0)
        if depth == 0:
            return name[:i] if i > 0 else name
    return name


def _decl_name(head: str) -> str:
    """Member name from the text before its parameter list: the last token, with a trailing <...> kept
    whole even when it holds spaces (``Foo<A, B>``)."""
    head = head.rstrip()
    if head.endswith(">"):
        depth = 0
        for i in range(len(head) - 1, -1, -1):
            depth += {">": 1, "<": -1}.get(head[i], 0)
            if depth == 0:
                start = head.rfind(" ", 0, i) + 1
                return head[start:]
    return head.rsplit(None, 1)[-1] if head.split() else head


def _member(line: str, type_simple: str, prop: str | None) -> str | None:
    """Metadata method name of the declaration line after an [Address] attribute."""
    text = re.sub(r"//.*$", "", line).strip()
    acc = ACCESSOR.match(text)
    if acc and "(" not in text.split("{", 1)[0]:
        if not prop:
            return None
        iface, _, short = prop.rpartition(".")  # explicit interface property: I.Current -> I.get_Current
        return f"{iface}.{_ACCESSOR_PREFIX[acc.group(1)]}{short}" if iface else _ACCESSOR_PREFIX[acc.group(1)] + short
    if "(" not in text:
        return None
    head = text.split("(", 1)[0]
    if " operator " in f" {head} ":
        op = head.split("operator", 1)[1].strip()
        if re.search(r"\bimplicit\b", head):
            return "op_Implicit"
        if re.search(r"\bexplicit\b", head):
            return "op_Explicit"
        return OPERATORS.get(op, f"op_{op}")
    name = _decl_name(head)
    bare = _strip_generic_suffix(name)
    if name.startswith("~"):
        return "Finalize"
    if re.sub(r"`\d+$", "", bare) == re.sub(r"`\d+$", "", type_simple):  # Cpp2IL: `public List`1() { }` in `class List`
        return ".cctor" if re.search(r"\bstatic\b", head) else ".ctor"
    return _strip_generic_suffix(name)


def parse_cpp2il_cs(text: str) -> Iterator[tuple[int, int | None, str, str, str]]:
    """(rva, length, type full name, method name, declaration) for every [Address] method of one C# file."""
    lines = text.splitlines()
    namespace = ""
    types: list[tuple[int, str]] = []          # (indent, simple name) of the enclosing types
    last_decl: dict[int, str] = {}             # indent -> last member declaration line (property/event/indexer)
    pending: tuple[int, int | None] | None = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        lead = raw[: len(raw) - len(raw.lstrip())]
        indent = lead.count("\t") + lead.count(" ") // 4
        ns = NAMESPACE.match(raw)
        if ns and not types:
            namespace = ns.group(1)
            continue
        m = ADDRESS.search(stripped)
        if m and stripped.startswith("["):
            pending = (int(m.group(1), 16), int(m.group(2), 16) if m.group(2) else None)
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            continue  # other attributes between [Address] and the declaration
        td = TYPE_DECL.match(raw)
        if td and td.group(3) and "(" not in raw.split(td.group(3), 1)[0]:
            while types and types[-1][0] >= indent:
                types.pop()
            types.append((indent, td.group(3).rstrip("{").strip()))
            pending = None
            continue
        while types and types[-1][0] >= indent and stripped not in ("{", "}"):
            types.pop()  # a member at the indent of a closed type
        if pending is not None:
            rva, length = pending
            pending = None
            if not types:
                continue
            type_simple = _strip_generic_suffix(types[-1][1])
            prop_line = last_decl.get(indent - 1)
            prop = None
            if prop_line is not None:
                head = re.sub(r"//.*$", "", prop_line).strip()
                if re.search(r"\bthis\s*\[", head):
                    prop = "Item"
                else:
                    prop = _decl_name(head.split("=", 1)[0])
            name = _member(stripped, type_simple, prop)
            if not name:
                continue
            full_type = ".".join(([namespace] if namespace else []) + [t for _, t in types])
            decl = re.sub(r"\s*\{\s*\}\s*(//.*)?$", "", stripped).strip()
            yield rva, length, full_type, name, decl
            continue
        if stripped not in ("{", "}") and not stripped.startswith(("//", "[")):
            last_decl[indent] = stripped


def from_cpp2il(root: Path) -> Iterator[dict]:
    for path in sorted(Path(root).rglob("*.cs")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "[Address(RVA" not in text:
            continue
        for rva, length, type_name, method, decl in parse_cpp2il_cs(text):
            yield row(rva, method_name(type_name, method), "method", "cpp2il", signature=decl, type_name=type_name,
                      size=length)


# REA --------------------------------------------------------------------------------------------------

def _rea_reports(data) -> Iterator[dict]:
    """Complete ``inspect_il2cpp_metadata`` reports inside an Evidence file (or a bare report)."""
    if isinstance(data, dict) and isinstance(data.get("methods"), list) and isinstance(data.get("types"), list):
        yield data
        return
    if isinstance(data, dict) and isinstance(data.get("normalized_result"), dict):
        yield from _rea_reports(data["normalized_result"])
        return
    records = data.get("records") if isinstance(data, dict) else None
    for rec in records or []:
        if isinstance(rec, dict) and rec.get("operation") in (None, "inspect_il2cpp_metadata"):
            yield from _rea_reports(rec.get("normalized_result"))


def rea_type_names(types: list[dict]) -> list[str]:
    """``Namespace.Outer.Inner`` per TypeDefinitionIndex (REA's seed naming)."""
    out: list[str | None] = [None] * len(types)

    def name(i: int) -> str:
        chain, seen, at = [], set(), i
        while at != -1 and at not in seen and 0 <= at < len(types):
            if out[at] is not None:
                chain.append(out[at])
                break
            seen.add(at)
            chain.append(types[at].get("name") or "")
            ns = types[at].get("namespace") or ""
            parent = types[at].get("declaring_type", -1)
            if parent == -1 and ns:
                chain.append(ns)
            at = parent
        return ".".join(reversed(chain))

    for i in range(len(types)):
        out[i] = name(i)
    return out  # type: ignore[return-value]


def from_rea_report(report: dict) -> Iterator[dict]:
    types = report.get("types") or []
    methods = report.get("methods") or []
    addresses = report.get("method_addresses") or []
    if not addresses and not report.get("generic_method_instances"):
        return
    names = rea_type_names(types)
    returns = report.get("return_types") or []
    ptypes = report.get("parameter_types") or []
    params_of: dict[int, list[int]] = defaultdict(list)
    for i, p in enumerate(report.get("parameters") or []):
        if isinstance(p, dict) and p.get("method", -1) >= 0:
            params_of[p["method"]].append(i)
    params = report.get("parameters") or []
    for i, method in enumerate(methods):
        addr = addresses[i] if i < len(addresses) else None
        if not addr or addr.get("state") != "code" or addr.get("rva") is None:
            continue
        owner = method.get("declaring_type", -1)
        type_name = names[owner] if 0 <= owner < len(names) else None
        signature = None
        if i < len(returns) and returns[i].get("name") is not None:
            args = []
            for p in params_of.get(i, []):
                ptype = ptypes[p].get("name") if p < len(ptypes) else None
                args.append(f"{ptype or '?'} {params[p].get('name') or ''}".strip())
            signature = f"{returns[i]['name']} {method.get('name')}({', '.join(args)})"
        yield row(int(addr["rva"], 16), method_name(type_name, method.get("name") or ""), "method", "rea",
                  signature=signature, type_name=type_name)
    for inst in report.get("generic_method_instances") or []:
        if inst.get("state") != "code" or inst.get("rva") is None or not inst.get("name"):
            continue
        type_name, _ = split_method(inst["name"])
        yield row(int(inst["rva"], 16), inst["name"], "method", "rea", type_name=type_name)


def rea_binary_sha256(path: Path) -> str | None:
    data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    for report in _rea_reports(data):
        binary = report.get("binary") or {}
        sha = (binary.get("artifact") or {}).get("sha256")
        if sha:
            return sha
    return None


def from_rea_evidence(path: Path) -> Iterator[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    for report in _rea_reports(data):
        yield from from_rea_report(report)


def from_rea_seeds(path: Path) -> Iterator[dict]:
    """REA ``--ghidra-seeds``: ``0x<address>\\tfunction\\t<name>`` lines, ``# seed_base 0x..`` trailer."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    base = re.search(r"^# seed_base (0x[0-9a-fA-F]+)", text, re.M)
    if not base:
        return
    seed_base = int(base.group(1), 16)
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[1] == "function" and parts[0].startswith("0x"):
            type_name, _ = split_method(parts[2])
            yield row(int(parts[0], 16) - seed_base, parts[2], "method", "rea", type_name=type_name)


# --- merge ----------------------------------------------------------------------------------------

def merge(rows: Iterable[dict], precedence: tuple[str, ...] = PRECEDENCE) -> tuple[list[dict], dict]:
    """One entry per (rva, kind): the best source's first name wins; the other names at the address go in
    ``also``. Returns (entries sorted by (rva, kind), stats)."""
    rank = {s: i for i, s in enumerate(precedence)}
    groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    per_source: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["kind"] not in KINDS or r["rva"] < 0:
            continue
        groups[(r["rva"], r["kind"])].append(r)
        per_source[r["source"]] += 1
    entries, conflicts, folded = [], 0, 0
    conflict_pairs: dict[str, int] = defaultdict(int)
    for key in sorted(groups):
        cands = sorted(groups[key], key=lambda r: rank.get(r["source"], len(rank)))  # stable within a source
        best = dict(cands[0])
        names_by_source: dict[str, list[str]] = defaultdict(list)
        for c in cands:
            if c["name"] and c["name"] not in names_by_source[c["source"]]:
                names_by_source[c["source"]].append(c["name"])
        for source, names in names_by_source.items():
            if source != best["source"] and best["name"] and best["name"] not in names:
                conflicts += 1
                conflict_pairs[f"{best['source']}/{source}"] += 1
        if not best["name"]:  # an unnamed address (kind function) named by a later source
            named = next((c for c in cands if c["name"]), None)
            if named:
                best = dict(named)
        also = []
        for c in cands:
            if c["name"] and c["name"] != best["name"] and c["name"] not in also:
                also.append(c["name"])
        if also:
            folded += 1
            best["also"] = also
        for field in ("signature", "type", "size", "value"):
            if best.get(field) is None:
                other = next((c.get(field) for c in cands if c.get(field) is not None and c["name"] == best["name"]), None)
                if other is not None:
                    best[field] = other
        if best["kind"] == "method" and best["name"]:
            type_name = best.get("type") or split_method(best["name"])[0]
            ns = namespace_path(type_name)
            if ns:
                _, short = split_method(best["name"])
                best["ns"] = ns  # Ghidra namespace path (C# namespace parts + nested types); fn = name inside it
                best["fn"] = ghidra_name(short if best["name"].startswith(f"{type_name}$$") else best["name"])
        best["name"] = ghidra_name(best["name"]) if best["name"] else ""
        entries.append(best)
    by_kind: dict[str, int] = defaultdict(int)
    won: dict[str, int] = defaultdict(int)
    for e in entries:
        by_kind[e["kind"]] += 1
        won[e["source"]] += 1
    stats = {"entries": len(entries), "by_kind": dict(sorted(by_kind.items())), "rows_by_source": dict(sorted(per_source.items())),
             "won_by_source": dict(sorted(won.items())), "conflicts": conflicts, "conflict_pairs": dict(sorted(conflict_pairs.items())),
             "folded": folded}
    return entries, stats


# --- workspace ------------------------------------------------------------------------------------

def game_assembly(install_dir: Path) -> Path | None:
    for name in ("GameAssembly.dll", "GameAssembly.so", "libil2cpp.so", "GameAssembly.dylib"):
        p = Path(install_dir) / name
        if p.is_file():
            return p
    return None


def image_base(path: Path) -> int | None:
    """Preferred image base from the PE header (None for other formats)."""
    try:
        import pefile

        pe = pefile.PE(str(path), fast_load=True)
        try:
            return int(pe.OPTIONAL_HEADER.ImageBase)
        finally:
            pe.close()
    except Exception:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_address_cs(root: Path) -> bool:
    for path in root.rglob("*.cs"):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if "[Address(RVA" in fh.read():
                    return True
        except OSError:
            continue
    return False


def find_sources(ws: Path) -> list[tuple[str, Path]]:
    """(source, path) of every name source present in the workspace, in precedence order."""
    found: list[tuple[str, Path]] = []
    if (ws / SCRIPT_JSON).is_file():
        found.append(("il2cppdumper", ws / SCRIPT_JSON))
    for d in INSPECTOR_DIRS:
        for p in sorted((ws / d).rglob("*.json")) if (ws / d).is_dir() else []:
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    head = fh.read(1 << 16)
            except OSError:
                continue
            if '"addressMap"' in head:
                found.append(("inspector", p))
    for d in CPP2IL_DIRS:
        if (ws / d).is_dir() and _has_address_cs(ws / d):
            found.append(("cpp2il", ws / d))
    if (ws / ADDRESSES_TSV).is_file():
        found.append(("cpp2il", ws / ADDRESSES_TSV))
    if (ws / REA_EVIDENCE).is_dir():
        # REA runner: <name>.json is the Evidence; .request/.run/.error.json sit next to it
        found += [("rea", p) for p in sorted((ws / REA_EVIDENCE).glob("*.json"))
                  if not p.name.endswith((".request.json", ".run.json", ".error.json"))]
    if (ws / REA_DIR).is_dir():
        found += [("rea", p) for p in sorted((ws / REA_DIR).rglob("*seeds*.txt"))]
    rank = {s: i for i, s in enumerate(PRECEDENCE)}
    return sorted(found, key=lambda sp: rank[sp[0]])


def build(ws: Path, install_dir: Path | None = None, *, out: Path | None = None, sources: list[tuple[str, Path]] | None = None,
          log=None) -> dict:
    """Write ``decomp/il2cpp/names.jsonl`` from every source present. Returns the header (with
    ``path``, or ``path = None`` when no source has addresses)."""
    t0 = time.time()
    ws = Path(ws)
    out = Path(out) if out else ws / NAMES
    binary = game_assembly(install_dir) if install_dir else None
    assembly = None
    base = None
    if binary is not None:
        base = image_base(binary)
        if base is None:  # not a PE we can read: the image base code.il2cpp.metadata recorded
            recorded = ((read_json(ws / IL2CPP_METADATA, {}) or {}).get("binary") or {}).get("image_base")
            base = _va(recorded) if recorded is not None else None
        assembly = {"file": binary.name, "size": binary.stat().st_size, "sha256": _sha256(binary),
                    "image_base": f"0x{base:x}" if base is not None else None}
    sources = find_sources(ws) if sources is None else sources
    used: dict[str, list[dict]] = defaultdict(list)
    notes: list[str] = []

    def rows() -> Iterator[dict]:
        for source, path in sources:
            rel = path.relative_to(ws).as_posix() if _under(path, ws) else str(path)
            n = 0
            try:
                if source == "il2cppdumper":
                    it = from_script_json(path)
                elif source == "inspector":
                    if base is None:
                        notes.append(f"{rel}: no PE image base to turn Il2CppInspector VAs into RVAs; skipped")
                        continue
                    it = from_inspector_json(path, base)
                elif source == "cpp2il":
                    it = from_addresses_tsv(path) if path.suffix == ".tsv" else from_cpp2il(path)
                elif path.suffix == ".txt":
                    it = from_rea_seeds(path)
                else:
                    sha = rea_binary_sha256(path)
                    if sha and assembly and sha != assembly["sha256"]:
                        notes.append(f"{rel}: REA linked another GameAssembly build (sha256 {sha}); skipped")
                        continue
                    it = from_rea_evidence(path)
                for r in it:
                    n += 1
                    yield r
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                notes.append(f"{rel}: unreadable ({type(exc).__name__}: {exc})")
            used[source].append({"path": rel, "rows": n})
            if n == 0:
                notes.append(f"{rel}: no names with addresses")

    entries, stats = merge(rows())
    header = {"_header": 1, "format": FORMAT, "game_assembly": assembly, "precedence": list(PRECEDENCE),
              "sources": dict(used), "counts": stats, "notes": notes, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "seconds": round(time.time() - t0, 1)}
    if not entries:
        if out.exists():
            out.unlink()
        return {**header, "path": None}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + f".partial-{os.getpid()}")
    digest = hashlib.sha256()
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(header, ensure_ascii=False) + "\n")
        for e in entries:
            line = json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n"
            digest.update(line.encode("utf-8", errors="surrogatepass"))
            fh.write(line)
    os.replace(tmp, out)
    summary = {**header, "path": out.relative_to(ws).as_posix() if _under(out, ws) else str(out), "entries_sha256": digest.hexdigest()}
    write_json(out.with_suffix(".manifest.json"), summary)
    return summary


def read_header(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            first = json.loads(fh.readline() or "null")
    except (OSError, ValueError):
        return None
    return first if isinstance(first, dict) and first.get("_header") else None


def iter_names(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                d = json.loads(line)
                if not d.get("_header"):
                    yield d


def manifest(ws: Path) -> dict | None:
    return read_json(Path(ws) / NAMES.with_suffix(".manifest.json"), None)


def _under(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False
