"""Dummy DLLs -> C# stubs with ilspycmd (``decomp/cs-il2cpp/<Assembly>/``), then the code index and the address table.

The dummy DLLs (Cpp2IL, or Il2CppDumper/Il2CppInspector when Cpp2IL cannot read the metadata) have every type,
field, property and method signature, the attributes the game declared, and the IL2CPP facts as attributes:
``[Address(RVA = "0x977490", Offset = "0x976490", Length = "0x5")]`` on methods, ``[Token(Token = "0x6008963")]``
everywhere and ``[FieldOffset(Offset = "0x20")]`` on fields. Method bodies are stubs (``return null;``): the index
gives definitions, fields, signatures and type hierarchies, not call graphs (those come from Ghidra/REA).

Assemblies: the game's own by default (same rule as ``gst decompile dotnet``: framework, engine and well-known
third-party assemblies are skipped), every one with ``everything=True`` (``--all`` / ``--opt il2cpp.all=1``).
Resumable: an assembly whose DLL sha256 did not change keeps its output (``MANIFEST.json``).

Address table (:func:`build_addresses`): every ``[Address]``/``[Token]``/``[FieldOffset]`` attribute of
``decomp/cs-il2cpp`` joined to the indexed symbol that carries it, in ``index/code.db`` table ``il2cpp_addresses``
and in ``decomp/il2cpp/addresses.tsv`` (``rva va offset length token field_offset kind qname decl path line``).
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gst.common import GsError, read_json, write_json

OUT_DIR = Path("decomp") / "cs-il2cpp"
DUMMY_DIR = Path("decomp") / "il2cpp" / "dummydll"
ADDRESSES_TSV = Path("decomp") / "il2cpp" / "addresses.tsv"
ALWAYS_SKIP = {"__Generated"}  # Cpp2IL/Inspector synthetic assembly (attribute types they inject)


def assemblies(dummy_dir: Path) -> list[dict]:
    from gst.decompile.dotnet import NOT_GAME

    out = []
    for p in sorted(Path(dummy_dir).glob("*.dll")):
        game = not NOT_GAME.match(p.stem) and p.stem not in ALWAYS_SKIP
        out.append({"name": p.stem, "path": str(p), "size": p.stat().st_size, "game": game, "pdb": False})
    return out


def decompile(ws: Path, *, dummy_dir: Path | None = None, names: list[str] | None = None, everything: bool = False,
              jobs: int | None = None, force: bool = False, timeout: float | None = None) -> dict:
    """ilspycmd over the dummy DLLs into ``decomp/cs-il2cpp``; returns ``{out, assemblies, skipped, failed}``."""
    from gst import tools
    from gst.decompile import dotnet

    ws = Path(ws)
    dummy_dir = Path(dummy_dir) if dummy_dir else ws / DUMMY_DIR
    found = assemblies(dummy_dir)
    if not found:
        raise GsError(f"no dummy DLLs in {dummy_dir} (gst study --only code.il2cpp, or gst decompile il2cpp)")
    exe = tools.bin_path(tools.get("ilspycmd"))
    if exe is None:
        raise GsError("ilspycmd is not installed (gst tools install ilspycmd)")
    chosen = dotnet.select(found, names, everything)
    out_root = ws / OUT_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / "MANIFEST.json"
    previous = (read_json(manifest_path, {}) or {}).get("assemblies", {})
    results, todo = {}, []
    for a in chosen:
        a["sha256"] = dotnet.sha256_of(Path(a["path"]))
        prev = previous.get(a["name"])
        if not force and prev and prev.get("sha256") == a["sha256"] and (out_root / a["name"]).is_dir():
            results[a["name"]] = {**prev, "reused": True}
        else:
            todo.append(a)
    for a in todo:
        sys.stderr.write(f"gst: decompiling dummy {a['name']} ({a['size'] // 1024} KB)...\n")
    workers = jobs or max(1, min(4, (os.cpu_count() or 2) // 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for a, res in zip(todo, pool.map(lambda x: dotnet._run_one(exe, x, out_root, timeout), todo)):
            results[a["name"]] = {**res, "sha256": a["sha256"], "reused": False}
    dummy_manifest = read_json(dummy_dir / "MANIFEST.json", {}) or {}
    write_json(manifest_path, {
        "backend": "il2cpp", "tool": "ilspycmd", "tool_version": tools.installed_version(tools.get("ilspycmd")),
        "source": str(dummy_dir), "dummy_source": dummy_manifest.get("tool"), "dummy_tool_version": dummy_manifest.get("tool_version"),
        "metadata_version": dummy_manifest.get("metadata_version"), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "assemblies": {**previous, **{k: {kk: vv for kk, vv in v.items() if kk != "reused"} for k, v in results.items()}}})
    return {"out": str(out_root), "assemblies": results, "skipped": [a["name"] for a in found if a not in chosen],
            "failed": [k for k, v in results.items() if v.get("exit_code") not in (0, None) and not v.get("files")]}


# --- [Address] / [Token] / [FieldOffset] table -----------------------------------------------------------------------

ATTR = re.compile(r"^\s*\[(Address|Token|FieldOffset)\((.*)\)\]\s*$")
KV = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
ATTR_LINE = re.compile(r"^\s*\[")
COLUMNS = ("rva", "va", "offset", "length", "token", "field_offset", "kind", "qname", "decl", "path", "line")
TABLE = """
DROP TABLE IF EXISTS il2cpp_addresses;
CREATE TABLE il2cpp_addresses (symbol_id INTEGER, file_id INTEGER, path TEXT, line INTEGER, qname TEXT, kind TEXT,
    decl TEXT, rva TEXT, va TEXT, offset TEXT, length TEXT, token TEXT, field_offset TEXT);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS il2cpp_addr_rva ON il2cpp_addresses(rva);
CREATE INDEX IF NOT EXISTS il2cpp_addr_qname ON il2cpp_addresses(qname COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS il2cpp_addr_symbol ON il2cpp_addresses(symbol_id);
"""


def scan_attributes(text: str) -> list[dict]:
    """Attribute blocks of a C# file: one dict per declaration that carries Address/Token/FieldOffset.

    ``line`` = the declaration line (first line after the attribute block), ``decl`` = its text."""
    lines = text.splitlines()
    out: list[dict] = []
    block: dict = {}
    for i, line in enumerate(lines):
        m = ATTR.match(line)
        if m:
            kv = dict(KV.findall(m.group(2)))
            if m.group(1) == "Address":
                block.update(rva=kv.get("RVA"), va=kv.get("VA"), offset=kv.get("Offset"), length=kv.get("Length"))
            elif m.group(1) == "Token":
                block["token"] = kv.get("Token")
            else:
                block["field_offset"] = kv.get("Offset")
            continue
        if ATTR_LINE.match(line) or not line.strip():
            continue  # other attributes of the same declaration
        if block:
            out.append({**block, "line": i + 1, "decl": line.strip()})
            block = {}
    return out


def _innermost(symbols: list[tuple], line: int) -> tuple | None:
    best = None
    for sym in symbols:  # (id, kind, qname, line, end_line)
        if sym[3] <= line <= sym[4] and (best is None or sym[4] - sym[3] < best[4] - best[3]):
            best = sym
    return best


def build_addresses(ws: Path) -> dict:
    """(Re)build ``il2cpp_addresses`` in ``index/code.db`` and ``decomp/il2cpp/addresses.tsv`` from ``decomp/cs-il2cpp``."""
    from gst import index

    ws = Path(ws)
    db = index.db_path(ws)
    if not db.exists():
        return {"rows": 0, "files": 0, "reason": "no code index"}
    con = sqlite3.connect(db)
    try:
        con.executescript(TABLE)
        files = con.execute("SELECT id, path FROM files WHERE path LIKE ? ORDER BY path",
                            (OUT_DIR.as_posix() + "/%",)).fetchall()
        tsv = ws / ADDRESSES_TSV
        tsv.parent.mkdir(parents=True, exist_ok=True)
        tmp = tsv.with_name(f"{tsv.name}.partial-{os.getpid()}")
        rows = methods = 0
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\t".join(COLUMNS) + "\n")
            for file_id, rel in files:
                try:
                    text = (ws / rel).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                found = scan_attributes(text)
                if not found:
                    continue
                symbols = con.execute("SELECT id, kind, qname, line, end_line FROM symbols WHERE file_id = ?",
                                      (file_id,)).fetchall()
                batch = []
                for a in found:
                    sym = _innermost(symbols, a["line"])
                    rec = {"symbol_id": sym[0] if sym else None, "file_id": file_id, "path": rel, "line": a["line"],
                           "qname": sym[2] if sym else "", "kind": sym[1] if sym else "", "decl": a["decl"],
                           **{k: a.get(k) for k in ("rva", "va", "offset", "length", "token", "field_offset")}}
                    batch.append(rec)
                    methods += bool(a.get("rva"))
                    fh.write("\t".join("" if rec.get(c) is None else str(rec[c]).replace("\t", " ") for c in COLUMNS) + "\n")
                con.executemany("INSERT INTO il2cpp_addresses(symbol_id, file_id, path, line, qname, kind, decl, rva, va, offset,"
                                " length, token, field_offset) VALUES (:symbol_id, :file_id, :path, :line, :qname, :kind,"
                                " :decl, :rva, :va, :offset, :length, :token, :field_offset)", batch)
                rows += len(batch)
        os.replace(tmp, tsv)
        con.executescript(INDEXES)
        con.commit()
    finally:
        con.close()
    return {"rows": rows, "with_rva": methods, "files": len(files), "tsv": str(tsv)}
