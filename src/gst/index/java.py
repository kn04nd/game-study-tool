"""Java extractor for the code index (tree-sitter-java). Same output shape as `gst.index.csharp.extract`."""

from __future__ import annotations

import hashlib
from bisect import bisect_right

from gst.index.csharp import _last_identifier

_PARSER = None
_QUERY = None

TYPE_NODES = {"class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum",
              "record_declaration": "record", "annotation_type_declaration": "interface"}
MEMBER_NODES = {"method_declaration": "method", "constructor_declaration": "ctor",
                "compact_constructor_declaration": "ctor", "annotation_type_element_declaration": "method"}
FIELD_NODES = {"field_declaration": "field", "constant_declaration": "field"}
REF_QUERY = """
(method_invocation) @call
(object_creation_expression type: (_) @new)
(string_literal) @string
"""


def _setup():
    global _PARSER, _QUERY
    if _PARSER is None:
        import tree_sitter as ts
        import tree_sitter_java as tsj

        lang = ts.Language(tsj.language())
        _PARSER = ts.Parser(lang)
        _QUERY = ts.QueryCursor(ts.Query(lang, REF_QUERY))
    return _PARSER, _QUERY


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _call_target(node) -> str:
    obj, name = node.child_by_field_name("object"), node.child_by_field_name("name")
    return f"{_text(obj)}.{_text(name)}" if obj is not None else _text(name)


def extract(rel_path: str, source: bytes) -> dict:
    parser, cursor = _setup()
    tree = parser.parse(source)
    symbols, bases, spans = [], [], []

    def add(node, kind, name, container, parent, signature):
        local = len(symbols)
        qname = f"{container}.{name}" if container else name
        body = source[node.start_byte:node.end_byte]
        symbols.append((local, parent, kind, name, qname, signature, node.start_point[0] + 1, node.end_point[0] + 1,
                        hashlib.sha1(body).hexdigest()[:16]))
        spans.append((node.start_byte, node.end_byte, local))
        return local, qname

    package = ""
    for child in tree.root_node.children:
        if child.type == "package_declaration":
            package = next((_text(c) for c in child.named_children if c.type in {"scoped_identifier", "identifier"}), "")

    def walk(node, container, parent):
        for child in node.named_children:
            ctype = child.type
            if ctype in TYPE_NODES:
                name = _text(child.child_by_field_name("name"))
                local, qname = add(child, TYPE_NODES[ctype], name, container, parent, name)
                sup = child.child_by_field_name("superclass")
                if sup is not None:
                    bases.extend((local, _text(n)) for n in sup.named_children)
                for field in ("interfaces", "extends_interfaces"):
                    node_i = child.child_by_field_name(field)
                    if node_i is None:
                        node_i = next((c for c in child.named_children if c.type in {"super_interfaces", "extends_interfaces"}), None)
                    if node_i is not None:
                        for type_list in node_i.named_children:
                            bases.extend((local, _text(t)) for t in (type_list.named_children if type_list.type == "type_list" else [type_list]))
                        break
                body = child.child_by_field_name("body")
                if body is not None:
                    walk(body, qname, local)
            elif ctype in MEMBER_NODES:
                name = _text(child.child_by_field_name("name")) or container.rsplit(".", 1)[-1]
                params = _text(child.child_by_field_name("parameters"))
                typ = _text(child.child_by_field_name("type"))
                sig = f"{name}{params}" if MEMBER_NODES[ctype] == "ctor" else f"{typ} {name}{params}".strip()
                add(child, MEMBER_NODES[ctype], name, container, parent, sig)
            elif ctype in FIELD_NODES:
                typ = _text(child.child_by_field_name("type"))
                for decl in (c for c in child.named_children if c.type == "variable_declarator"):
                    name = _text(decl.child_by_field_name("name"))
                    add(child, "field", name, container, parent, f"{typ} {name}")
            elif ctype == "enum_constant":
                add(child, "enum_member", _text(child.child_by_field_name("name")), container, parent,
                    _text(child.child_by_field_name("name")))
            elif ctype in {"enum_body", "enum_body_declarations", "class_body", "interface_body"}:
                walk(child, container, parent)

    walk(tree.root_node, package, None)
    spans.sort(key=lambda s: (s[0], -s[1]))
    starts = [s[0] for s in spans]

    def owner(pos: int):
        i = bisect_right(starts, pos) - 1
        while i >= 0:
            if pos < spans[i][1]:
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
                elif kind == "call":
                    target = " ".join(_call_target(n).split())[:200]
                    refs.append(("call", target, _text(n.child_by_field_name("name")), line, who))
                else:
                    target = " ".join(_text(n).split())[:200]
                    refs.append(("new", target, _last_identifier(target), line, who))
    return {"symbols": symbols, "bases": bases, "refs": refs, "strings": strings,
            "lines": source.count(b"\n") + 1, "has_error": tree.root_node.has_error}
