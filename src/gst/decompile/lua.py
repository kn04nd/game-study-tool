"""Lua scripts -> ``decomp/lua/<path in the install>``, one unit per script folder (``code.lua`` in ``gst study``;
``gst decompile lua`` runs the same extractor in this process).

Plain-text Lua is copied as a snapshot of this build (the index and `gst q` then cover it, and a later
build can be compared). Lua 5.x bytecode goes through unluac; when unluac fails (custom compilers
such as Kahlua's), the partial output is kept as `<name>.partial.lua` and the header is diagnosed.
LuaJIT bytecode is diagnosed only (no decompiler pinned yet).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from gst import policy, tools
from gst.common import GsError, long_path, write_text

OUT_DIR = Path("decomp") / "lua"
SKIP_DIRS = re.compile(r"(?:^|/)(?:jre\w*|jdk\w*|steamapps|\.git|__MACOSX)(?:/|$)", re.I)
UNIT_DEPTH = 3  # one unit per folder this deep (media/lua/client) with its subfolders; shallower: its own files
BYTECODE_EXT = {".luac", ".lbc", ".lub", ".lc", ".lua", ".out", ".bytes"}


def header(path: Path) -> dict | None:
    """Lua/LuaJIT bytecode header facts, or None for anything else."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return None
    if head[:4] == b"\x1bLua" and len(head) >= 12:
        version = f"{head[4] >> 4}.{head[4] & 0xF}"
        info = {"kind": "lua", "version": version, "format": head[5]}
        if head[4] == 0x51:  # 5.1: endianness, int, size_t, instruction, number sizes, integral flag
            info.update({"big_endian": head[6] == 0, "sizeof_int": head[7], "sizeof_size_t": head[8],
                         "sizeof_instruction": head[9], "sizeof_number": head[10], "integral": bool(head[11])})
        return info
    if head[:3] == b"\x1bLJ":
        return {"kind": "luajit", "version": f"LJ{head[3]}"}
    return None


def scan(install: Path) -> tuple[list[Path], list[Path]]:
    """(plain-text .lua files, compiled chunks) under the install."""
    plain, compiled = [], []
    for dirpath, dirs, files in os.walk(install):
        rel = Path(dirpath).relative_to(install).as_posix()
        if rel != "." and SKIP_DIRS.search(rel):
            dirs[:] = []
            continue
        for name in files:
            path = Path(dirpath) / name
            ext = path.suffix.lower()
            if ext not in BYTECODE_EXT:
                continue
            info = header(path)
            if info:
                compiled.append(path)
            elif ext == ".lua":
                plain.append(path)
    return plain, compiled


def units(install: Path) -> list[dict]:
    """One unit per script folder: a file's folder cut at ``UNIT_DEPTH`` levels. A unit at that depth owns its
    whole subtree (``recursive``); a shallower one owns only the files directly in it."""
    install = Path(install)
    plain, compiled = scan(install)
    groups: dict[str, dict] = {}
    for kind, paths in (("plain", plain), ("bytecode", compiled)):
        for path in paths:
            rel = path.relative_to(install).as_posix()
            folders = rel.split("/")[:-1]
            unit = "/".join(folders[:UNIT_DEPTH]) or "."
            group = groups.setdefault(unit, {"id": unit, "recursive": len(folders) >= UNIT_DEPTH, "plain": [],
                                             "bytecode": [], "size": 0})
            group[kind].append(rel)
            group["size"] += path.stat().st_size
    for group in groups.values():
        group["plain"].sort()
        group["bytecode"].sort()
    return [groups[k] for k in sorted(groups)]


def _inside(unit: str, rel: str) -> str:
    """``rel`` (install-relative) as a path inside the unit's output folder."""
    return rel if unit == "." else rel[len(unit) + 1:]


def _unluac(java: Path, unluac: Path, src: Path, dest: Path, partial: Path) -> tuple[bool, str]:
    proc = subprocess.run([str(java), "-jar", str(unluac), str(src)], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL)
    ok = proc.returncode == 0 and bool(proc.stdout.strip())
    err = (proc.stderr or "").strip()
    if proc.stdout.strip():
        first = (err.splitlines() or ["?"])[0]
        note = "" if ok else f"-- gst: unluac stopped with an error; output below is PARTIAL.\n-- {first}\n\n"
        write_text(dest if ok else partial, note + proc.stdout)
    return ok, err


def run_unit(install: Path, game: dict, unit: dict, out: Path) -> dict:
    """Copy one unit's plain scripts and decompile (or diagnose) its bytecode into ``out``, with paths relative to
    the unit folder. Plain scripts are files on disk; bytecode is decompiled only when the policy allows it."""
    install = Path(install)
    out.mkdir(parents=True, exist_ok=True)
    base = unit["id"]
    plain = set(unit["plain"])
    for rel in unit["plain"]:
        dest = out / _inside(base, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(long_path(install / rel), long_path(dest))
    diagnosed = []
    java = unluac = None
    if unit["bytecode"]:
        java, unluac = tools.bin_path("temurin21"), tools.bin_path("unluac")
    for rel in unit["bytecode"]:
        src = install / rel
        info = header(src) or {}
        entry = {"file": rel, **info}
        twin = Path(rel).with_suffix(".lua").as_posix()
        if twin != rel and (twin in plain or ((install / twin).is_file() and header(install / twin) is None)):
            entry["source_twin"] = twin
        inner = Path(_inside(base, rel))
        # never overwrite the plain twin or the chunk's own name: <stem>.decompiled.lua then
        clash = inner.suffix.lower() == ".lua" or "source_twin" in entry
        dest = out / inner.with_name(inner.stem + (".decompiled.lua" if clash else ".lua"))
        partial = out / inner.with_name(inner.stem + ".partial.lua")
        decision = policy.check(game, "decompile", str(src))
        if not decision.allowed:
            entry.update({"decompiled": False, "blocked": True,
                          "error": f"{decision.reason} (guardrails.md rule {decision.rule})"})
        elif info.get("kind") == "lua" and java and unluac:
            dest.parent.mkdir(parents=True, exist_ok=True)
            ok, err = _unluac(java, unluac, src, dest, partial)
            entry.update({"decompiled": ok, "error": None if ok else ((err.splitlines() or ["no output"])[0]),
                          "stderr": err or None})
            if ok:
                entry["output"] = dest.relative_to(out).as_posix()
            elif partial.exists():
                entry["partial"] = partial.relative_to(out).as_posix()
        else:
            entry.update({"decompiled": False, "error": "no decompiler for this bytecode in this version"
                          if info.get("kind") == "luajit" else "unluac not installed (gst tools install unluac)"})
        diagnosed.append(entry)
    return {"unit": base, "recursive": unit["recursive"], "plain_files": len(unit["plain"]), "bytecode": diagnosed}


def decompile(ws: Path, game: dict, *, force: bool = False) -> dict:
    """``gst decompile lua``: the ``code.lua`` extractor in this process (same units, outputs and manifest as
    ``gst study``); a unit whose recorded fingerprint did not change is reused unless ``force``."""
    from gst.extractors import javalua

    t0 = time.time()
    rows = javalua.run_inprocess(ws, game, javalua.LuaCode, {}, force=force)
    if not rows:
        raise GsError(f"no Lua scripts or bytecode found under {game['install_dir']}")
    facts = [r["facts"] for r in rows if r.get("facts")]
    return {"out": str(ws / OUT_DIR), "copied": sum(f.get("plain_files", 0) for f in facts),
            "bytecode": [b for f in facts for b in f.get("bytecode", [])], "units": len(rows),
            "reused": sum(1 for r in rows if r["reused"]), "seconds": round(time.time() - t0, 1)}
