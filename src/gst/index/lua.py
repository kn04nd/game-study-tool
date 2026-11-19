"""Lua extractor for the code index (tree-sitter-lua). Same output shape as `gst.index.csharp.extract`.

Lua has no classes, so common idioms are mapped onto them: `X = Base:derive("X")` (Project Zomboid,
many ISUI-style codebases), `X = class(Base)` / `Class(Base)`, and `X = {}` tables; `function X:m()` and
`X.m = function` become members `X.m`. Calls are recorded by name (`obj:Say` -> `Say`).
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_right

_PARSER = None
_QUERY = None
REF_QUERY = """
(function_call name: (_) @call)
(string) @string
"""
CLASS_CALL = re.compile(r"^(?:([\w.]+):derive|(?:class|Class|newclass)\s*)\s*\(\s*(?:\"[^\"]*\"|'[^']*'|([\w.]+))?")


def _setup():
    global _PARSER, _QUERY
    if _PARSER is None:
        import tree_sitter as ts
        import tree_sitter_lua as tsl

        lang = ts.Language(tsl.language())
        _PARSER = ts.Parser(lang)
        _QUERY = ts.QueryCursor(ts.Query(lang, REF_QUERY))
    return _PARSER, _QUERY


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _last(expr: str) -> str:
    return re.split(r"[.:]", expr.strip())[-1].strip()


def extract(rel_path: str, source: bytes) -> dict:
    parser, cursor = _setup()
    tree = parser.parse(source)
    symbols, bases, spans = [], [], []
    tables: dict[str, int] = {}

    def add(node, kind, name, qname, parent, signature):
        local = len(symbols)
        body = source[node.start_byte:node.end_byte]
        symbols.append((local, parent, kind, name, qname, signature, node.start_point[0] + 1, node.end_point[0] + 1,
                        hashlib.sha1(body).hexdigest()[:16]))
        spans.append((node.start_byte, node.end_byte, local))
        return local

    def table_symbol(name: str, node, kind: str = "table") -> int:
        if name not in tables:
            tables[name] = add(node, kind, name.rsplit(".", 1)[-1], name, None, name)
        return tables[name]

    def function(node, name_node, value=None):
        target = value if value is not None else node
        params = _text(target.child_by_field_name("parameters"))
        if name_node.type in {"dot_index_expression", "method_index_expression"}:
            owner = _text(name_node.child_by_field_name("table"))
            member = _text(name_node.child_by_field_name("field") or name_node.child_by_field_name("method"))
            parent = table_symbol(owner, node)
            kind = "method" if name_node.type == "method_index_expression" else "function"
            add(target, kind, member, f"{owner}.{member}", parent, f"function {owner}{':' if kind == 'method' else '.'}{member}{params}")
        else:
            name = _text(name_node)
            add(target, "function", name, name, None, f"function {name}{params}")

    for node in tree.root_node.named_children:
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                function(node, name_node)
        elif node.type == "assignment_statement":
            names = next((c for c in node.named_children if c.type == "variable_list"), None)
            values = next((c for c in node.named_children if c.type == "expression_list"), None)
            if names is None or values is None:
                continue
            for name_node, value in zip(names.named_children, values.named_children):
                name = _text(name_node)
                if value.type == "function_definition":
                    function(node, name_node, value)
                elif value.type == "table_constructor" and re.fullmatch(r"[\w.]+", name):
                    table_symbol(name, node)
                elif value.type == "function_call" and re.fullmatch(r"[\w.]+", name):
                    m = CLASS_CALL.match(_text(value))
                    if m:
                        local = table_symbol(name, node, "class")
                        base = m.group(1) or m.group(2)
                        if base:
                            bases.append((local, base))
        elif node.type == "variable_declaration":  # local X = {} / local function handled as function_declaration
            for child in node.named_children:
                if child.type == "function_declaration":
                    function(child, child.child_by_field_name("name"))

    spans.sort(key=lambda s: (s[0], -s[1]))
    starts = [s[0] for s in spans]

    def owner(pos: int):
        i = bisect_right(starts, pos) - 1
        while i >= 0:
            if pos < spans[i][1] and symbols[spans[i][2]][2] in {"function", "method"}:
                return spans[i][2]
            i -= 1
        return None

    refs, strings = [], []
    for _, caps in cursor.matches(tree.root_node):
        for kind, nodes in caps.items():
            for n in nodes:
                line, who = n.start_point[0] + 1, owner(n.start_byte)
                if kind == "string":
                    value = _text(n)
                    if len(value) > 2:
                        strings.append((value[:500], line, who))
                else:
                    target = " ".join(_text(n).split())[:200].replace(":", ".")
                    refs.append(("call", target, _last(target), line, who))
    return {"symbols": symbols, "bases": bases, "refs": refs, "strings": strings,
            "lines": source.count(b"\n") + 1, "has_error": tree.root_node.has_error}
