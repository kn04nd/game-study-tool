"""GDScript extractor for the code index. GDScript is line-oriented and scoped by indentation, so
regular expressions plus an indentation stack are enough (no grammar to pin). Same output shape as
`gst.index.csharp.extract`.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from pathlib import PurePosixPath

ANNOTATIONS = r"(?:@\w+(?:\([^)]*\))?\s+)*"
CLASS_NAME = re.compile(rf"^{ANNOTATIONS}class_name\s+(\w+)(?:\s+extends\s+([\w.\"/:]+))?")
EXTENDS = re.compile(r"^extends\s+([\w.\"/:]+)")
INNER_CLASS = re.compile(r"^class\s+(\w+)(?:\s+extends\s+([\w.\"/:]+))?\s*:")
FUNC = re.compile(r"^(?:static\s+)?func\s+(\w+)\s*(\([^)]*\))?\s*(?:->\s*([^:]+?))?\s*:")
SIGNAL = re.compile(r"^signal\s+(\w+)(\([^)]*\))?")
VAR = re.compile(rf"^{ANNOTATIONS}(?:static\s+)?(var|const)\s+(\w+)(?:\s*:\s*([^=:]+?))?\s*(?:[:=]|$)")
ENUM = re.compile(r"^enum\s+(\w+)\s*\{")
CALL = re.compile(r"(?<![\w.$])((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*)\s*\(")
NEW = re.compile(r"\b([A-Za-z_]\w*)\.new\s*\(")
STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'')
NOT_CALLS = {"if", "elif", "while", "for", "match", "func", "return", "assert", "await", "not", "and", "or", "in",
             "is", "as", "signal", "var", "const", "class", "static", "preload", "load", "super", "print", "range",
             "len", "str", "int", "float", "bool", "typeof", "Vector2", "Vector3", "Color", "Array", "Dictionary"}


def _base(value: str) -> str:
    """`Node2D` stays; `"res://scripts/base_enemy.gd"` -> `base_enemy` (scripts extend files by path)."""
    value = value.strip().strip('"\'')
    return PurePosixPath(value).stem if "/" in value or value.endswith(".gd") else value


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip("\t "))


def _code(line: str) -> str:
    """The line without a trailing comment (strings are left as they are; good enough for symbols)."""
    return line.split("#", 1)[0] if "#" in line and '"' not in line and "'" not in line else line


def extract(rel_path: str, source: bytes) -> dict:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    symbols: list[tuple] = []
    bases: list[tuple] = []
    script_name = PurePosixPath(rel_path).stem
    root_local = 0
    symbols.append((0, None, "class", script_name, script_name, script_name, 1, max(1, len(lines)), ""))

    def add(parent, kind, name, container, signature, line, end_line, body=""):
        local = len(symbols)
        qname = f"{container}.{name}" if container else name
        symbols.append((local, parent, kind, name, qname, signature, line, end_line,
                        hashlib.sha1(body.encode("utf-8", "surrogatepass")).hexdigest()[:16] if body else ""))
        return local, qname

    def block_end(start: int, indent: int) -> int:
        """Last line (1-based) of the block opened at index ``start`` with indentation ``indent``."""
        end = start
        for j in range(start + 1, len(lines)):
            stripped = lines[j].strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _indent(lines[j]) <= indent:
                break
            end = j
        return end + 1

    stack: list[tuple[int, int, str]] = [(-1, root_local, script_name)]  # (indent, local id, qname)
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent(raw)
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent_indent, parent, container = stack[-1]
        code = _code(stripped)
        if m := CLASS_NAME.match(code):
            name = m.group(1)
            symbols[0] = (0, None, "class", name, name, name, 1, max(1, len(lines)), "")
            stack[0] = (-1, 0, name)
            if m.group(2):
                bases.append((0, _base(m.group(2))))
            continue
        if indent == 0 and (m := EXTENDS.match(code)):
            bases.append((0, _base(m.group(1))))
            continue
        if m := INNER_CLASS.match(code):
            end = block_end(i, indent)
            local, qname = add(parent, "class", m.group(1), container, m.group(1), i + 1, end)
            if m.group(2):
                bases.append((local, _base(m.group(2))))
            stack.append((indent, local, qname))
            continue
        if m := FUNC.match(code):
            end = block_end(i, indent)
            ret = f" -> {m.group(3).strip()}" if m.group(3) else ""
            body = "\n".join(lines[i:end])
            local, qname = add(parent, "method", m.group(1), container, f"func {m.group(1)}{m.group(2) or '()'}{ret}",
                               i + 1, end, body)
            stack.append((indent, local, qname))
            continue
        if m := SIGNAL.match(code):
            add(parent, "event", m.group(1), container, f"signal {m.group(1)}{m.group(2) or ''}", i + 1, i + 1)
        elif m := ENUM.match(code):
            add(parent, "enum", m.group(1), container, f"enum {m.group(1)}", i + 1, block_end(i, indent))
        elif parent_indent < 0 or symbols[parent][2] == "class":
            if m := VAR.match(code):  # class members only, not locals inside functions
                kind = "field" if m.group(1) == "var" else "const"
                typ = (m.group(3) or "").strip()
                add(parent, kind, m.group(2), container, f"{typ} {m.group(2)}".strip(), i + 1, i + 1)

    spans = sorted(((s[6], s[7], s[0]) for s in symbols[1:]), key=lambda s: (s[0], -s[1]))
    starts = [s[0] for s in spans]

    def owner(line_no: int):
        k = bisect_right(starts, line_no) - 1
        while k >= 0:
            if line_no <= spans[k][1]:
                return spans[k][2]
            k -= 1
        return 0

    refs: list[tuple] = []
    strings: list[tuple] = []
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or FUNC.match(stripped) or stripped.startswith(("class ", "signal ")):
            continue
        who = owner(i + 1)
        for m in STRING.finditer(raw):
            if len(m.group(0)) > 2:
                strings.append((m.group(0)[:500], i + 1, who))
        scrubbed = STRING.sub('""', raw)
        news = set()
        for m in NEW.finditer(scrubbed):
            refs.append(("new", f"{m.group(1)}.new", m.group(1), i + 1, who))
            news.add(m.start(1))
        for m in CALL.finditer(scrubbed):
            target = m.group(1)
            name = target.rsplit(".", 1)[-1]
            if name in NOT_CALLS or name == "new" or m.start(1) in news:
                continue
            refs.append(("call", target, name, i + 1, who))
    return {"symbols": symbols, "bases": bases, "refs": refs, "strings": strings,
            "lines": len(lines), "has_error": False}
