"""C# extractor for the code index (tree-sitter-c-sharp): symbols, references, inheritance, strings.

Runs in worker processes: `extract(path_text, source_bytes)` returns plain tuples, no database access.
Line numbers are 1-based. Calls are recorded by name (`target` as written, `name` = last identifier);
decompiled code has no reliable type information for a resolver, so queries rank by name and qualifier.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_right

_PARSER = None
_QUERY = None

TYPE_NODES = {
    "class_declaration": "class", "struct_declaration": "struct", "interface_declaration": "interface",
    "enum_declaration": "enum", "record_declaration": "record", "record_struct_declaration": "record",
    "delegate_declaration": "delegate",
}
MEMBER_NODES = {
    "method_declaration": "method", "constructor_declaration": "ctor", "destructor_declaration": "dtor",
    "property_declaration": "property", "indexer_declaration": "indexer", "operator_declaration": "operator",
    "conversion_operator_declaration": "operator", "event_declaration": "event",
    "local_function_statement": "function",
}
FIELD_NODES = {"field_declaration": "field", "event_field_declaration": "event"}
NAMESPACE_NODES = {"namespace_declaration", "file_scoped_namespace_declaration"}

REF_QUERY = """
(invocation_expression function: (_) @call)
(object_creation_expression type: (_) @new)
(string_literal) @string
(verbatim_string_literal) @string
(raw_string_literal) @string
"""


def _setup():
    global _PARSER, _QUERY
    if _PARSER is None:
        import tree_sitter as ts
        import tree_sitter_c_sharp as tscs

        lang = ts.Language(tscs.language())
        _PARSER = ts.Parser(lang)
        _QUERY = ts.QueryCursor(ts.Query(lang, REF_QUERY))
    return _PARSER, _QUERY


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _strip_generics(expr: str) -> str:
    out, depth = [], 0
    for ch in expr:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _last_identifier(expr: str) -> str:
    """`a.b<T>.Call` -> `Call`; `GetComponent<Ns.Type>` -> `GetComponent`; `new X().Save` -> `Save`."""
    tail = _strip_generics(expr).replace("?.", ".").rsplit(".", 1)[-1]
    return tail.strip().lstrip("@")


def _signature(node, kind: str, name: str) -> str:
    if kind in {"field", "event"} and node.type in FIELD_NODES:
        decl = node.child_by_field_name("type") or next((c for c in node.children if c.type == "variable_declaration"), None)
        typ = decl.child_by_field_name("type") if decl is not None and decl.type == "variable_declaration" else decl
        return f"{_text(typ)} {name}".strip()
    params = node.child_by_field_name("parameters")
    returns = node.child_by_field_name("returns") or node.child_by_field_name("type")
    if kind in {"method", "function", "operator", "delegate"}:
        return f"{_text(returns)} {name}{_text(params)}".strip()
    if kind in {"ctor", "dtor"}:
        return f"{name}{_text(params)}"
    if kind in {"property", "indexer", "event"}:
        return f"{_text(returns)} {name}".strip()
    return name


def extract(rel_path: str, source: bytes) -> dict:
    """Symbols, refs, bases and strings of one C# file."""
    parser, cursor = _setup()
    tree = parser.parse(source)
    symbols: list[tuple] = []   # (local_id, parent_local_id, kind, name, qname, signature, line, end_line, body_hash)
    bases: list[tuple] = []     # (local_id, base_text)
    spans: list[tuple] = []     # (start_byte, end_byte, local_id) of members and types, for ref attribution

    def add(node, kind, name, container, parent):
        qname = f"{container}.{name}" if container else name
        body = source[node.start_byte:node.end_byte]
        local = len(symbols)
        symbols.append((local, parent, kind, name, qname, _signature(node, kind, name), node.start_point[0] + 1,
                        node.end_point[0] + 1, hashlib.sha1(body).hexdigest()[:16]))
        spans.append((node.start_byte, node.end_byte, local))
        return local, qname

    def walk(node, container: str, parent):
        for child in node.children:
            ctype = child.type
            if ctype in NAMESPACE_NODES:
                ns = _text(child.child_by_field_name("name"))
                full = f"{container}.{ns}" if container and ns else (ns or container)
                if ctype == "file_scoped_namespace_declaration":
                    container = full  # applies to the rest of the file
                    walk(child, full, parent)
                else:
                    walk(child.child_by_field_name("body") or child, full, parent)
            elif ctype in TYPE_NODES:
                name = _text(child.child_by_field_name("name"))
                local, qname = add(child, TYPE_NODES[ctype], name, container, parent)
                for base_list in (c for c in child.children if c.type == "base_list"):
                    for base in base_list.named_children:
                        bases.append((local, _text(base)))
                body = child.child_by_field_name("body")
                if body is not None:
                    walk(body, qname, local)
            elif ctype in MEMBER_NODES:
                name_node = child.child_by_field_name("name")
                name = _text(name_node) if name_node is not None else ("this" if ctype == "indexer_declaration" else ctype)
                add(child, MEMBER_NODES[ctype], name, container, parent)
            elif ctype in FIELD_NODES:
                for decl in (c for c in child.children if c.type == "variable_declaration"):
                    for var in (v for v in decl.children if v.type == "variable_declarator"):
                        add(child, FIELD_NODES[ctype], _text(var.child_by_field_name("name")), container, parent)
            elif ctype == "enum_member_declaration":
                add(child, "enum_member", _text(child.child_by_field_name("name")), container, parent)
            elif ctype in {"declaration_list", "global_statement"}:
                walk(child, container, parent)

    walk(tree.root_node, "", None)
    spans.sort(key=lambda s: (s[0], -s[1]))
    starts = [s[0] for s in spans]

    def owner(pos: int):
        """Innermost symbol containing ``pos``: spans nest or are disjoint, so walk back from the last start."""
        i = bisect_right(starts, pos) - 1
        while i >= 0:
            if pos < spans[i][1]:
                return spans[i][2]
            i -= 1
        return None

    refs: list[tuple] = []     # (kind, target, name, line, owner_local_id)
    strings: list[tuple] = []  # (value, line, owner_local_id)
    for _, caps in cursor.matches(tree.root_node):
        for kind, nodes in caps.items():
            for n in nodes:
                line, who = n.start_point[0] + 1, owner(n.start_byte)
                if kind == "string":
                    value = _text(n)
                    if len(value) > 2:
                        strings.append((value[:500], line, who))
                else:
                    target = " ".join(_text(n).split())[:200]
                    refs.append((kind, target, _last_identifier(target), line, who))
    return {"symbols": symbols, "bases": bases, "refs": refs, "strings": strings,
            "lines": source.count(b"\n") + 1, "has_error": tree.root_node.has_error}
