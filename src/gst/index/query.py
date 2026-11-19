"""Queries behind `gst q`: definitions, fuzzy symbols, references, callers/callees, source slices,
outlines, type hierarchy, string literals, regex grep and statistics.

Results are capped and compact; each hit carries `path:line` so it can be cited as evidence.
References are matched by name (decompiled code has no reliable types): hits whose qualifier
matches the queried type rank first.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path

from gst.common import GsError

TYPE_KINDS = ("class", "struct", "interface", "enum", "record", "delegate", "namespace")  # namespace: native code
KIND_RANK = {k: i for i, k in enumerate(TYPE_KINDS + ("method", "ctor", "property", "field", "event", "indexer",
                                                       "operator", "enum_member", "dtor", "function"))}


def _row(r: sqlite3.Row, **extra) -> dict:
    d = {k: r[k] for k in r.keys()}
    d.update(extra)
    return d


def _split(query: str) -> tuple[str, str]:
    """`Ns.Type.Member` (or C++ `Ns::Type::Member`, IL2CPP `Ns.Type$$Member`) -> (`Ns.Type`, `Member`); `Member` ->
    (``, `Member`). Generic args and parens dropped."""
    q = re.sub(r"\(.*$", "", query.strip()).replace("::", ".").replace("$$", ".")
    q = re.sub(r"<[^<>]*>", "", q)
    head, _, tail = q.rpartition(".")
    return head, tail


def defs(con: sqlite3.Connection, query: str, *, kind: str | None = None, limit: int | None = None) -> list[dict]:
    """Symbols named ``query`` (a simple name, `Type.Member` or a full `Ns.Type.Member`)."""
    head, name = _split(query)
    rows = con.execute(
        "SELECT s.*, f.path FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.name = ? COLLATE NOCASE"
        + (" AND s.kind = ?" if kind else "") + " LIMIT 5000", (name, kind) if kind else (name,)).fetchall()
    want = f"{head}.{name}".lower() if head else None
    out = []
    for r in rows:
        q = r["qname"].lower()
        if want and not (q == want or q.endswith("." + want)):
            continue
        exact = want is not None and q == want
        out.append(_row(r, _score=(0 if exact else 1, KIND_RANK.get(r["kind"], 99), len(r["qname"]))))
    out.sort(key=lambda d: d.pop("_score"))
    return out[:limit]


def symbols(con: sqlite3.Connection, text: str, *, kind: str | None = None, limit: int | None = None) -> list[dict]:
    """Fuzzy: qualified names containing ``text`` (trigram index; shorter texts use LIKE)."""
    if len(text) >= 3:
        sql = ("SELECT s.*, f.path FROM symbols_fts JOIN symbols s ON s.id = symbols_fts.rowid JOIN files f ON f.id = s.file_id "
               "WHERE symbols_fts MATCH ?" + (" AND s.kind = ?" if kind else "") + " LIMIT ?")
        args = [json.dumps(text)] + ([kind] if kind else []) + [-1 if limit is None else limit * 20]
    else:
        sql = ("SELECT s.*, f.path FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.qname LIKE ?"
               + (" AND s.kind = ?" if kind else "") + " LIMIT ?")
        args = [f"%{text}%"] + ([kind] if kind else []) + [-1 if limit is None else limit * 20]
    rows = [_row(r) for r in con.execute(sql, args)]
    low = text.lower()
    rows.sort(key=lambda d: (d["name"].lower() != low, not d["name"].lower().startswith(low),
                             KIND_RANK.get(d["kind"], 99), len(d["qname"])))
    return rows[:limit]


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def references(con: sqlite3.Connection, query: str, *, kinds: tuple[str, ...] = (), limit: int | None = None) -> list[dict]:
    """Uses of a name: calls, `new` and other recorded references, with the enclosing symbol.

    With a qualifier (`Type.Member`), only uses written as `Type.Member` or made from inside `Type` are
    returned; when there is none, every use of the bare name comes back marked `by_name`.
    """
    head, name = _split(query)
    sql = ("SELECT r.kind, r.target, r.line, f.path, s.qname AS owner FROM refs r JOIN files f ON f.id = r.file_id "
           "LEFT JOIN symbols s ON s.id = r.symbol_id WHERE r.name = ? COLLATE NOCASE")
    args: list = [name]
    if kinds:
        sql += f" AND r.kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    if head:
        type_name = head.rsplit(".", 1)[-1]
        qualified = [_row(r, by_name=False) for r in con.execute(
            sql + " AND (r.target LIKE ? ESCAPE '\\' OR s.qname LIKE ? ESCAPE '\\') LIMIT 20000",
            args + [f"%{_like(type_name)}.%", f"{_like(head)}.%"])]
        if qualified:
            qualified.sort(key=lambda d: (d["path"], d["line"]))
            return qualified[:limit]
    rows = [_row(r, by_name=True) for r in con.execute(sql + " LIMIT ?", args + [-1 if limit is None else max(limit, 200)])]
    rows.sort(key=lambda d: (d["path"], d["line"]))
    return rows[:limit]


def callers(con: sqlite3.Connection, query: str, *, limit: int | None = None) -> list[dict]:
    """Symbols that call ``query`` (or construct it, for a type), grouped, with the first call site."""
    found = defs(con, query, limit=5)
    if any(d["kind"] in TYPE_KINDS for d in found):
        kinds: tuple[str, ...] = ("new",)
    elif found:
        kinds = ("call",)
    else:  # not in the index (e.g. an engine or library type): both uses
        kinds = ("call", "new")
    hits = references(con, query, kinds=kinds, limit=5000)
    grouped: dict[str, dict] = {}
    for h in hits:
        key = h["owner"] or f"{h['path']} (top level)"
        g = grouped.setdefault(key, {"caller": key, "calls": 0, "path": h["path"], "line": h["line"], "target": h["target"],
                                     "by_name": h.get("by_name", False), "source": "index"})
        g["calls"] += 1
    out = list(grouped.values())
    known = {_member_key(g["caller"]) for g in out}
    out += [r for r in rea_callers(con, query) if _member_key(r["caller"]) not in known]
    return out[:limit]


# --- REA call graph: rea_calls, imported from dumps/rea by gst.rea.import_index ------------------------------

def _has_rea(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rea_calls'").fetchone() is not None


def _member_key(qname: str) -> str:
    """``Ns.Outer/Inner.Method`` -> ``inner.method``: index and REA spell owners differently."""
    from gst.rea.evidence import short_type, split_member

    head, name = split_member(re.sub(r"\(.*$", "", qname))
    return f"{short_type(head)}.{name}".lower() if head else name.lower()


def _type_matches(full: str, head: str) -> bool:
    """Does REA's ``Ns.Outer/Inner`` match the queried ``Type`` / ``Ns.Type`` / ``Outer.Inner``?"""
    t = full.replace("/", ".").replace("+", ".").lower()
    h = head.lower()
    return t == h or t.endswith("." + h)


def rea_callers(con: sqlite3.Connection, query: str) -> list[dict]:
    """Callers from ``rea_calls`` (managed call sites, also for IL2CPP games whose code has no source index)."""
    if not _has_rea(con):
        return []
    head, name = _split(query)
    if name in ("ctor", "cctor") and head.endswith("."):
        head, name = head[:-1], "." + name
    rows = con.execute("SELECT caller, callee_type, callee_method, kind, assembly, evidence, il_offset FROM rea_calls "
                       "WHERE callee_method = ? COLLATE NOCASE", (name,)).fetchall()
    if not rows and not head:  # a type: who constructs it
        rows = con.execute("SELECT caller, callee_type, callee_method, kind, assembly, evidence, il_offset FROM rea_calls "
                           "WHERE callee_method = '.ctor' AND kind = 'newobj'").fetchall()
        rows = [r for r in rows if _type_matches(r["callee_type"], name)]
    grouped: dict[str, dict] = {}
    for r in rows:
        if head and not _type_matches(r["callee_type"], head):
            continue
        offset = r["il_offset"]
        g = grouped.setdefault(r["caller"], {
            "caller": r["caller"], "calls": 0, "path": f"rea:{r['assembly'] or r['evidence']}",
            "line": f"IL_{offset:04x}" if isinstance(offset, int) else "-", "target": f"{r['callee_type']}.{r['callee_method']}",
            "by_name": not head, "source": "rea", "evidence": r["evidence"]})
        g["calls"] += 1
    return sorted(grouped.values(), key=lambda g: (-g["calls"], g["caller"]))


def rea_callees(con: sqlite3.Connection, query: str) -> list[dict]:
    """What ``query`` calls according to ``rea_calls``, with the callee's RVA/address from ``rea_methods`` when known."""
    if not _has_rea(con):
        return []
    head, name = _split(query)
    rows = con.execute("SELECT caller_type, callee, callee_type, callee_method, kind, il_offset, evidence FROM rea_calls "
                       "WHERE caller_method = ? COLLATE NOCASE ORDER BY evidence, il_offset", (name,)).fetchall()
    seen, out = set(), []
    for r in rows:
        if head and not _type_matches(r["caller_type"], head):
            continue
        key = (r["kind"], r["callee"])
        if key in seen:
            continue
        seen.add(key)
        offset = r["il_offset"]
        out.append({"kind": "new" if r["kind"] == "newobj" else "call", "target": r["callee"],
                    "line": f"IL_{offset:04x}" if isinstance(offset, int) else "-", "defined_as": [r["callee"]],
                    "definitions": 1, "resolved_by": "rea", "source": "rea", "evidence": r["evidence"]})
    return out


CALLABLE = ("method", "ctor", "property", "function", "delegate", "event", "indexer", "operator")


def _type_of_signature(signature: str) -> str:
    """`Dictionary<int, Foo> cache` -> `Dictionary`; `ConduitFlow[] flows` -> `ConduitFlow`."""
    head = signature.rsplit(" ", 1)[0] if " " in signature else signature
    return re.sub(r"[\[\]?*]", "", _split(head + ".x")[0]).rsplit(".", 1)[-1]


def _members_named(con: sqlite3.Connection, type_name: str, name: str, depth: int = 3) -> list[str]:
    """`type_name.name` definitions, walking up the type's bases by name."""
    seen, frontier = set(), [type_name]
    for _ in range(depth):
        found, nxt = [], []
        for t in frontier:
            if t.lower() in seen:
                continue
            seen.add(t.lower())
            found += [r[0] for r in con.execute(
                "SELECT s.qname FROM symbols s JOIN symbols p ON p.id = s.parent_id WHERE s.name = ? COLLATE NOCASE "
                f"AND p.name = ? COLLATE NOCASE AND s.kind IN ({','.join('?' * len(CALLABLE))})", (name, t, *CALLABLE))]
            nxt += [r[0] for r in con.execute("SELECT b.base_name FROM bases b JOIN symbols p ON p.id = b.symbol_id "
                                              "WHERE p.name = ? COLLATE NOCASE", (t,))]
        if found:
            return list(dict.fromkeys(found))
        frontier = nxt
    return []


def resolve_call(con: sqlite3.Connection, owner_id: int | None, target: str, name: str, kind: str) -> tuple[list[str], str]:
    """Best guess at what a call/`new` points to, with how it was resolved:
    `type` (qualifier names a type), `self` (unqualified: the caller's type or its bases), `field-type` (qualifier is
    a field/property of the caller's type), or `name` (every definition with that name). Native code (Ghidra)
    records the callee's full qualified name: an exact match comes first (`exact`)."""
    if "." in target:
        hits = [r[0] for r in con.execute("SELECT qname FROM symbols WHERE qname = ? COLLATE NOCASE LIMIT 5", (target,))]
        if hits:
            return hits, "exact"
    clean = _strip(target)
    if kind == "new":
        hits = [r[0] for r in con.execute(f"SELECT qname FROM symbols WHERE name = ? COLLATE NOCASE AND kind IN "
                                          f"({','.join('?' * len(TYPE_KINDS))})", (name, *TYPE_KINDS))]
        return hits, "type" if hits else "name"
    owner = con.execute("SELECT p.name AS type_name, p.id AS type_id FROM symbols s JOIN symbols p ON p.id = s.parent_id "
                        "WHERE s.id = ?", (owner_id,)).fetchone() if owner_id else None
    qualifier = clean.rpartition(".")[0]
    if qualifier in {"", "this", "base", "self", "super"} and owner:
        hits = _members_named(con, owner["type_name"], name)
        if hits:
            return hits, "self"
    if qualifier and re.fullmatch(r"[A-Za-z_]\w*", qualifier):
        if owner:
            field = con.execute("SELECT signature FROM symbols WHERE parent_id = ? AND name = ? AND kind IN ('field','property')",
                                (owner["type_id"], qualifier)).fetchone()
            if field:
                hits = _members_named(con, _type_of_signature(field[0]), name)
                if hits:
                    return hits, "field-type"
        hits = _members_named(con, qualifier, name)  # static call: Type.Method
        if hits:
            return hits, "type"
    elif qualifier:
        first, _, member = qualifier.partition(".")
        if "." not in member:  # Type.Instance.Method / Singleton<T>.Instance.Method: through the member's type
            sig = con.execute("SELECT s.signature FROM symbols s JOIN symbols p ON p.id = s.parent_id WHERE p.name = ? "
                              "COLLATE NOCASE AND s.name = ? AND s.kind IN ('field','property')", (first, member)).fetchone()
            if sig:
                typ = _type_of_signature(sig[0])
                generic = re.match(rf"\s*{re.escape(first)}\s*<\s*([\w.]+)", target)
                if generic and re.fullmatch(r"T\w{0,2}", typ):  # generic singleton: T -> the type argument
                    typ = generic.group(1).rsplit(".", 1)[-1]
                hits = _members_named(con, typ, name)
                if hits:
                    return hits, "field-type"
        hits = [r[0] for r in con.execute("SELECT qname FROM symbols WHERE name = ? COLLATE NOCASE", (name,))
                if r[0].lower().endswith(clean.lower())]  # Ns.Type.Method or Outer.Inner.Method
        if hits:
            return list(dict.fromkeys(hits)), "type"
    hits = [r[0] for r in con.execute(f"SELECT qname FROM symbols WHERE name = ? COLLATE NOCASE AND kind IN "
                                      f"({','.join('?' * len(CALLABLE))}) LIMIT 50", (name, *CALLABLE))]
    return list(dict.fromkeys(hits)), "name"


def _strip(target: str) -> str:
    """Call target without generic arguments or `new X()` prefixes: `Singleton<A>.Instance.Do` -> `Singleton.Instance.Do`."""
    from gst.index.csharp import _strip_generics

    return re.sub(r"^new\s+\S+\(\)\.", "", _strip_generics(target).replace("?.", ".")).strip()


def callees(con: sqlite3.Connection, query: str, *, limit: int | None = None) -> list[dict]:
    """What the symbol(s) named ``query`` call or construct, each with its most likely definition(s)."""
    targets = [d for d in defs(con, query, limit=10) if d["kind"] not in TYPE_KINDS] or defs(con, query, limit=3)
    if not targets:  # not in the source index (IL2CPP, unreadable module): the REA call graph, when imported
        return rea_callees(con, query)[:limit]
    ids = [d["id"] for d in targets]
    if any(d["kind"] in TYPE_KINDS for d in targets):  # a type: everything its members call
        ids += [r[0] for r in con.execute(f"SELECT id FROM symbols WHERE parent_id IN ({','.join('?' * len(ids))})", ids)]
    rows = con.execute(f"SELECT kind, target, name, line, symbol_id FROM refs WHERE symbol_id IN ({','.join('?' * len(ids))})"
                       " AND kind IN ('call', 'new') ORDER BY line", ids).fetchall()
    seen, out = set(), []
    for r in rows:
        key = (r["kind"], r["target"])
        if key in seen:
            continue
        seen.add(key)
        hits, how = resolve_call(con, r["symbol_id"], r["target"], r["name"], r["kind"])
        out.append({"kind": r["kind"], "target": r["target"], "line": r["line"], "defined_as": hits[:3],
                    "definitions": len(hits), "resolved_by": how})
    if not out:  # defined in the index but without call edges there (e.g. IL2CPP stubs): the REA call graph
        out = rea_callees(con, query)
    return out[:limit]


def owner_at(con: sqlite3.Connection, path: str, line: int) -> dict | None:
    row = con.execute("SELECT s.* , f.path FROM symbols s JOIN files f ON f.id = s.file_id WHERE f.path = ? AND s.line <= ? "
                      "AND s.end_line >= ? ORDER BY (s.end_line - s.line) LIMIT 1", (path, line, line)).fetchone()
    return _row(row) if row else None


def show(con: sqlite3.Connection, ws: Path, query: str, *, max_lines: int | None = None, context: int = 0) -> dict:
    """Source of a symbol (`Ns.Type.Method`) or of the symbol around `path:line`."""
    m = re.fullmatch(r"(.+?):(\d+)", query.strip())
    if m and (ws / m.group(1)).is_file():
        target = owner_at(con, m.group(1).replace("\\", "/"), int(m.group(2)))
        if target is None:
            line = int(m.group(2))
            target = {"path": m.group(1), "line": max(1, line - 20), "end_line": line + 20, "qname": None, "kind": "lines"}
        others = []
    else:
        found = defs(con, query, limit=20)
        if not found:
            raise GsError(f"no symbol named {query} (try gst q sym {query})")
        target, others = found[0], found[1:]
    lines = (ws / target["path"]).read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, target["line"] - context)
    end = min(len(lines), target["end_line"] + context)
    truncated = max_lines is not None and end - start + 1 > max_lines
    if truncated:
        end = start + max_lines - 1
    body = "\n".join(f"{n:>6}  {lines[n - 1]}" for n in range(start, end + 1))
    return {"qname": target.get("qname"), "kind": target.get("kind"), "path": target["path"], "start": start, "end": end,
            "truncated": truncated, "full_end": target["end_line"], "text": body,
            "others": [f"{o['kind']} {o['qname']} {o['path']}:{o['line']}" for o in others[:8]]}


def outline(con: sqlite3.Connection, query: str, *, limit: int | None = None) -> list[dict]:
    """Members of a type or namespace, or the symbols of a file (`decomp/...cs`, `.c`, `.java`...)."""
    from gst.index import EXTRACTORS

    if query.replace("\\", "/").lower().endswith(tuple(EXTRACTORS)):
        rows = con.execute("SELECT s.*, f.path FROM symbols s JOIN files f ON f.id = s.file_id WHERE f.path = ? ORDER BY s.line",
                           (query.replace("\\", "/"),)).fetchall()
        return [_row(r) for r in rows][:limit]
    types = [d for d in defs(con, query, limit=10) if d["kind"] in TYPE_KINDS]
    if not types:
        raise GsError(f"no type named {query} (gst q sym {query} --kind class)")
    t = types[0]
    rows = con.execute("SELECT s.*, f.path FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.parent_id = ? ORDER BY s.line",
                       (t["id"],)).fetchall()
    return [t] + [_row(r) for r in rows][:limit]


def hierarchy(con: sqlite3.Connection, query: str, *, depth: int = 3, limit: int | None = None) -> dict:
    """Bases of a type and the types deriving from it (by name, `depth` levels down)."""
    types = [d for d in defs(con, query, limit=10) if d["kind"] in TYPE_KINDS]
    if not types:
        raise GsError(f"no type named {query}")
    t = types[0]
    bases = [r[0] for r in con.execute("SELECT base FROM bases WHERE symbol_id = ?", (t["id"],))]
    derived, frontier, seen = [], [t["name"]], {t["qname"]}
    for level in range(1, depth + 1):
        nxt = []
        for name in frontier:
            for r in con.execute("SELECT s.qname, s.name, s.kind, f.path, s.line FROM bases b JOIN symbols s ON s.id = b.symbol_id "
                                 "JOIN files f ON f.id = s.file_id WHERE b.base_name = ? COLLATE NOCASE", (name,)):
                if r["qname"] not in seen and (limit is None or len(derived) < limit):
                    seen.add(r["qname"])
                    derived.append(_row(r, level=level))
                    nxt.append(r["name"])
        frontier = nxt
    return {"type": t, "bases": bases, "derived": derived}


def strings(con: sqlite3.Connection, text: str, *, limit: int | None = None) -> list[dict]:
    if len(text) >= 3:
        sql = ("SELECT st.value, st.line, f.path, s.qname AS owner FROM strings_fts JOIN strings st ON st.id = strings_fts.rowid "
               "JOIN files f ON f.id = st.file_id LEFT JOIN symbols s ON s.id = st.symbol_id WHERE strings_fts MATCH ? LIMIT ?")
        args = [json.dumps(text), -1 if limit is None else limit]
    else:
        sql = ("SELECT st.value, st.line, f.path, s.qname AS owner FROM strings st JOIN files f ON f.id = st.file_id "
               "LEFT JOIN symbols s ON s.id = st.symbol_id WHERE st.value LIKE ? LIMIT ?")
        args = [f"%{text}%", -1 if limit is None else limit]
    return [_row(r) for r in con.execute(sql, args)]


def grep(con: sqlite3.Connection | None, ws: Path, pattern: str, *, glob: str | None = None, limit: int | None = None,
         ignore_case: bool = False) -> list[dict]:
    """Regex search over decomp/ with ripgrep; each hit names its enclosing symbol when indexed."""
    from gst import tools

    rg = tools.bin_path("ripgrep")
    if rg is None:
        raise GsError("ripgrep is not installed (gst tools install ripgrep)")
    cmd = [str(rg), "--json", "-n", "--no-ignore-vcs", "--max-count", "20", "-e", pattern]
    if ignore_case:
        cmd.append("-i")
    if glob:
        cmd += ["-g", glob]
    cmd.append(str(ws / "decomp"))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL)
    if proc.returncode not in (0, 1):
        raise GsError(f"rg failed: {proc.stderr.strip()[-400:]}")
    hits = []
    for raw in proc.stdout.splitlines():
        event = json.loads(raw)
        if event.get("type") != "match":
            continue
        data = event["data"]
        path = Path(data["path"]["text"]).resolve().relative_to(ws.resolve()).as_posix()
        line = data["line_number"]
        owner = owner_at(con, path, line) if con is not None else None
        hits.append({"path": path, "line": line, "text": data["lines"]["text"].strip()[:200],
                     "owner": owner["qname"] if owner else None})
        if limit is not None and len(hits) >= limit:
            break
    return hits
