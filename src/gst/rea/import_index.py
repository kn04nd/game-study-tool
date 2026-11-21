"""Import REA Evidence into the workspace code index (``index/code.db``), so ``gst q callers/callees`` also work
where the decompiled source has no call edges (IL2CPP, or a module the decompiler could not read).

Tables (rebuilt from every Evidence file under ``dumps/rea/`` on each import; one file in memory at a time):

- ``rea_calls(caller, callee, kind, assembly, evidence, caller_type, caller_method, callee_type, callee_method,
  il_offset, artifact_sha256)``: managed call sites (``call``/``callvirt``/``newobj``/``ldftn``) from
  ``inspect_managed_members``, or ``calls`` edges of ``project_managed_application_graph`` for an artifact without a
  members record (``kind = "graph"``).
- ``rea_fields(type, field, field_type, assembly, evidence, flags, artifact_sha256, source)``: managed field
  declarations, and IL2CPP fields with the type the binary renders (``source = "il2cpp"``).
- ``rea_methods(type, method, assembly, rva, address, state, token, evidence, artifact_sha256, source)``: managed
  method RVAs and IL2CPP method addresses (``state`` = code | no-body | unknown).
- ``rea_field_accesses(method, field, field_type, kind, assembly, evidence)``: managed ``ldfld``/``stfld``/... sites.
- ``rea_sources(evidence, operation, artifact, artifact_sha256, size, mtime_ns, rows)``.

``evidence`` is the Evidence file relative to the workspace. A record whose (operation, artifact SHA-256) was
already imported from an earlier file is skipped (``dumps/rea/evidence/`` first, then older manual bundles).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from gst.rea import evidence

REA_DIR = Path("dumps") / "rea"
SKIP_SUFFIXES = (".request.json", ".run.json", ".error.json")
IMPORT_SUMMARY = "import.json"  # dumps/rea/import.json: what rea.import writes, never one of its inputs

SCHEMA = """
DROP TABLE IF EXISTS rea_calls;
DROP TABLE IF EXISTS rea_fields;
DROP TABLE IF EXISTS rea_methods;
DROP TABLE IF EXISTS rea_field_accesses;
DROP TABLE IF EXISTS rea_sources;
CREATE TABLE rea_calls (caller TEXT, callee TEXT, kind TEXT, assembly TEXT, evidence TEXT, caller_type TEXT,
    caller_method TEXT, callee_type TEXT, callee_method TEXT, il_offset INTEGER, artifact_sha256 TEXT);
CREATE TABLE rea_fields (type TEXT, field TEXT, field_type TEXT, assembly TEXT, evidence TEXT, flags INTEGER,
    artifact_sha256 TEXT, source TEXT);
CREATE TABLE rea_methods (type TEXT, method TEXT, assembly TEXT, rva TEXT, address TEXT, state TEXT, token TEXT,
    evidence TEXT, artifact_sha256 TEXT, source TEXT);
CREATE TABLE rea_field_accesses (method TEXT, field TEXT, field_type TEXT, kind TEXT, assembly TEXT, evidence TEXT);
CREATE TABLE rea_sources (evidence TEXT, operation TEXT, artifact TEXT, artifact_sha256 TEXT, size INTEGER,
    mtime_ns INTEGER, rows INTEGER, skipped TEXT);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS rea_calls_callee ON rea_calls(callee_method COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS rea_calls_caller ON rea_calls(caller_method COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS rea_calls_callee_type ON rea_calls(callee_type COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS rea_fields_field ON rea_fields(field COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS rea_fields_type ON rea_fields(type COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS rea_methods_method ON rea_methods(method COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS rea_methods_type ON rea_methods(type COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS rea_fa_field ON rea_field_accesses(field COLLATE NOCASE);
"""


def evidence_files(ws: Path) -> list[Path]:
    """Evidence JSON under ``dumps/rea``: ``evidence/`` first (sorted), then other files (manual bundles), never
    the request/run/error side files, ``split/`` or the import summary (``IMPORT_SUMMARY``: counting it made
    ``rea.import`` change its own fingerprint and rerun, with every analysis after it, on each ``gst study``)."""
    root = Path(ws) / REA_DIR
    if not root.is_dir():
        return []
    first, rest = [], []
    for p in sorted(root.rglob("*.json")):
        rel = p.relative_to(root).as_posix()
        if (p.name.endswith(SKIP_SUFFIXES) or p.name.startswith(".partial-") or rel.startswith("split/")
                or rel == IMPORT_SUMMARY):
            continue
        (first if rel.startswith("evidence/") else rest).append(p)
    return first + rest


def has_tables(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rea_calls'").fetchone() is not None


def import_workspace(ws: Path) -> dict:
    """(Re)build the ``rea_*`` tables of ``<ws>/index/code.db`` (created when missing) from ``dumps/rea``."""
    from gst import index

    t0 = time.time()
    con = index.connect(Path(ws), write=True)
    try:
        con.executescript(SCHEMA)
        seen: set[tuple[str, str]] = set()
        members_shas: set[str] = set()
        graphs: list[tuple[str, dict]] = []
        summary = {"files": 0, "records": 0, "calls": 0, "fields": 0, "methods": 0, "field_accesses": 0,
                   "skipped_duplicates": 0, "evidence": []}
        for path in evidence_files(ws):
            rel = path.relative_to(ws).as_posix()
            doc = evidence.load(path)
            st = path.stat()
            imported = 0
            skipped = []
            for rec in evidence.records(doc):
                op = evidence.operation(rec)
                art = evidence.artifact(rec)
                if op not in ("inspect_managed_members", "inspect_il2cpp_metadata", "project_managed_application_graph"):
                    continue
                key = (op, art["sha256"] or art["path"])
                if key in seen:
                    skipped.append(f"{op} {art['name']} (already imported)")
                    summary["skipped_duplicates"] += 1
                    continue
                seen.add(key)
                summary["records"] += 1
                if op == "inspect_managed_members":
                    members_shas.add(art["sha256"])
                    imported += _import_members(con, rec, rel, summary)
                elif op == "inspect_il2cpp_metadata":
                    imported += _import_il2cpp(con, rec, rel, summary)
                else:
                    graphs.append((rel, rec))
            con.execute("INSERT INTO rea_sources VALUES (?,?,?,?,?,?,?,?)",
                        (rel, ",".join(sorted({evidence.operation(r) for r in evidence.records(doc)})), "", "",
                         st.st_size, st.st_mtime_ns, imported, "; ".join(skipped)))
            summary["files"] += 1
            summary["evidence"].append(rel)
            del doc
        for rel, rec in graphs:  # graph edges only for artifacts without a (more complete) members record
            root_sha = str(evidence.result(rec).get("root_artifact_sha256") or "")
            if root_sha and root_sha in members_shas:
                continue
            _import_graph(con, rec, rel, summary)
        con.executescript(INDEXES)
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('rea_imported_at', ?)",
                    (time.strftime("%Y-%m-%dT%H:%M:%S"),))
        con.commit()
    finally:
        con.close()
    summary["seconds"] = round(time.time() - t0, 2)
    return summary


def _import_members(con: sqlite3.Connection, rec: dict, rel: str, summary: dict) -> int:
    nr = evidence.result(rec)
    asm = evidence.assembly(rec)
    sha = evidence.artifact(rec)["sha256"]
    by_token = {str(m.get("token")): m for m in nr.get("methods") or [] if m.get("token")}
    rows = []
    for e in nr.get("call_edges") or []:
        m = by_token.get(str(e.get("caller_token")))
        if m:
            ctype, cname = str(m.get("declaring_type") or ""), str(m.get("name") or "")
        else:
            ctype, cname = evidence.split_member(str(e.get("caller") or ""))
        ttype, tname = str(e.get("target_declaring_type") or ""), str(e.get("target_name") or "")
        rows.append((f"{ctype}.{cname}", f"{ttype}.{tname}", str(e.get("opcode") or ""), asm, rel, ctype, cname, ttype,
                     tname, e.get("il_offset"), sha))
    con.executemany("INSERT INTO rea_calls VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    fields = [(str(f.get("declaring_type") or ""), str(f.get("name") or ""), (f.get("signature") or {}).get("field_type"),
               asm, rel, f.get("flags"), sha, "managed") for f in nr.get("fields") or []]
    con.executemany("INSERT INTO rea_fields VALUES (?,?,?,?,?,?,?,?)", fields)
    methods = [(str(m.get("declaring_type") or ""), str(m.get("name") or ""), asm,
                hex(m["rva"]) if isinstance(m.get("rva"), int) else m.get("rva"), None, "il", m.get("token"), rel, sha,
                "managed") for m in nr.get("methods") or []]
    con.executemany("INSERT INTO rea_methods VALUES (?,?,?,?,?,?,?,?,?,?)", methods)
    accesses = []
    for a in nr.get("field_accesses") or []:
        m = by_token.get(str(a.get("method_token")))
        owner = f"{m.get('declaring_type')}.{m.get('name')}" if m else str(a.get("method") or "")
        accesses.append((owner, str(a.get("field_name") or ""), str(a.get("field_declaring_type") or ""),
                         str(a.get("opcode") or ""), asm, rel))
    con.executemany("INSERT INTO rea_field_accesses VALUES (?,?,?,?,?,?)", accesses)
    summary["calls"] += len(rows)
    summary["fields"] += len(fields)
    summary["methods"] += len(methods)
    summary["field_accesses"] += len(accesses)
    return len(rows) + len(fields) + len(methods) + len(accesses)


def _import_il2cpp(con: sqlite3.Connection, rec: dict, rel: str, summary: dict) -> int:
    from gst.rea.split import il2cpp_type_name

    nr = evidence.result(rec)
    sha = evidence.artifact(rec)["sha256"]
    types = nr.get("types") or []
    image_of: dict[int, str] = {}
    for img in nr.get("images") or []:
        start, count = img.get("type_start") or 0, img.get("type_count") or 0
        name = str(img.get("name") or "?")
        name = name[:-4] if name.lower().endswith(".dll") else name
        for i in range(start, start + count):
            image_of[i] = name
    names = [il2cpp_type_name(types, i) for i in range(len(types))]
    methods = nr.get("methods") or []
    addresses = nr.get("method_addresses") or []
    mrows = []
    for i, m in enumerate(methods):
        owner = m.get("declaring_type")
        tname = names[owner] if isinstance(owner, int) and 0 <= owner < len(names) else ""
        addr = addresses[i] if i < len(addresses) and isinstance(addresses[i], dict) else {}
        mrows.append((tname, str(m.get("name") or ""), image_of.get(owner, ""), addr.get("rva"), addr.get("address"),
                      addr.get("state") or ("metadata-only" if not addresses else None), str(i), rel, sha, "il2cpp"))
    con.executemany("INSERT INTO rea_methods VALUES (?,?,?,?,?,?,?,?,?,?)", mrows)
    field_types = nr.get("field_types") or []
    frows = []
    for i, f in enumerate(nr.get("fields") or []):
        owner = f.get("declaring_type")
        tname = names[owner] if isinstance(owner, int) and 0 <= owner < len(names) else ""
        ftype = field_types[i].get("name") if i < len(field_types) and isinstance(field_types[i], dict) else None
        frows.append((tname, str(f.get("name") or ""), ftype, image_of.get(owner, ""), rel, None, sha, "il2cpp"))
    con.executemany("INSERT INTO rea_fields VALUES (?,?,?,?,?,?,?,?)", frows)
    summary["methods"] += len(mrows)
    summary["fields"] += len(frows)
    return len(mrows) + len(frows)


def _import_graph(con: sqlite3.Connection, rec: dict, rel: str, summary: dict) -> int:
    graph = evidence.result(rec).get("graph") or {}
    sha = str(evidence.result(rec).get("root_artifact_sha256") or "")
    label = {}
    for node in graph.get("nodes") or []:
        obs = node.get("observations") or []
        if obs and isinstance(obs[0], dict) and obs[0].get("label"):
            label[node.get("node_id")] = str(obs[0]["label"])
    rows = []
    for edge in graph.get("edges") or []:
        if edge.get("relation") != "calls":
            continue
        src, dst = label.get(edge.get("source_node_id")), label.get(edge.get("target_node_id"))
        if not src or not dst:
            continue
        ctype, cname = evidence.split_member(src)
        ttype, tname = evidence.split_member(dst)
        rows.append((src, dst, "graph", "", rel, ctype, cname, ttype, tname, None, sha))
    con.executemany("INSERT INTO rea_calls VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    summary["calls"] += len(rows)
    return len(rows)


def stats(ws: Path) -> dict:
    """Row counts of the ``rea_*`` tables (empty when they were never imported)."""
    from gst import index

    path = index.db_path(Path(ws))
    if not path.exists():
        return {}
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        if not has_tables(con):
            return {}
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("rea_calls", "rea_fields", "rea_methods", "rea_field_accesses", "rea_sources")}
    finally:
        con.close()


def mtime_key(ws: Path) -> list[tuple[str, int, int]]:
    """(relpath, size, mtime_ns) of every Evidence file: the fingerprint input of the ``rea.import`` job."""
    out = []
    for p in evidence_files(ws):
        st = p.stat()
        out.append((p.relative_to(ws).as_posix(), st.st_size, st.st_mtime_ns))
    return out
