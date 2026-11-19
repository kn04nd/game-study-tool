"""Runtime observation (`gst trace`): the game's processes, read-only memory scans, hardware write
watchpoints, folder snapshots. No elevation and no code injection: the Win32 APIs a debugger uses.

Every live action goes through `target()`: the workspace's verdict must allow runtime work
(guardrails rule 2/3: offline-ok only) and the process must run from the game's install or sandbox.
"""

from __future__ import annotations

import bisect
import ctypes
import json
from pathlib import Path

from gst import policy
from gst.common import GsError, is_under


def _image_path(pid: int) -> str | None:
    from gst.runtime import win

    handle = win.OpenProcess(win.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = win.wt.DWORD(len(buf))
        return buf.value if win.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else None
    finally:
        win.CloseHandle(handle)


def processes() -> list[dict]:
    """Every process this user can see: pid, name, image path (None when access is denied)."""
    from gst.runtime import win

    snap = win.CreateToolhelp32Snapshot(win.TH32CS_SNAPPROCESS, 0)
    if snap in (None, win.wt.HANDLE(-1).value):
        raise GsError(f"process snapshot failed: {win.last_error()}")
    out = []
    try:
        entry = win.PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = win.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append({"pid": entry.th32ProcessID, "name": entry.szExeFile, "parent": entry.th32ParentProcessID,
                        "path": _image_path(entry.th32ProcessID) if entry.th32ProcessID else None})
            ok = win.Process32NextW(snap, ctypes.byref(entry))
    finally:
        win.CloseHandle(snap)
    return out


def game_roots(game: dict) -> list[str]:
    roots = [game.get("install_dir")]
    sandbox = game.get("sandbox_dir")
    if sandbox and Path(sandbox).is_dir():
        roots.append(sandbox)
    return [r for r in roots if r]


def game_processes(game: dict) -> list[dict]:
    roots = game_roots(game)
    return [p for p in processes() if p["path"] and any(is_under(p["path"], root) for root in roots)]


def target(game: dict, pid: int | None) -> dict:
    """The game process a live action may touch, after the policy and ownership checks."""
    decision = policy.check(game, "runtime")
    if not decision.allowed:
        raise GsError(f"{decision.reason} (guardrails.md rule {decision.rule})", 3)
    running = game_processes(game)
    if pid is None:
        if not running:
            raise GsError(f"{game.get('name')} is not running (start it, offline/solo, then gst trace ps)")
        if len(running) > 1:
            raise GsError("several game processes: pass --pid (" + ", ".join(f"{p['pid']} {p['name']}" for p in running) + ")")
        return running[0]
    for p in running:
        if p["pid"] == pid:
            return p
    raise GsError(f"pid {pid} is not a process of {game.get('name')} (its image must sit under the install or the "
                  "sandbox): runtime tools touch only the studied game", 3)


def modules(handle) -> list[dict]:
    """Loaded modules of an open process: base, size, path (sorted by base)."""
    from gst.runtime import win

    arr = (win.wt.HMODULE * 2048)()
    needed = win.wt.DWORD()
    if not win.EnumProcessModulesEx(handle, arr, ctypes.sizeof(arr), ctypes.byref(needed), win.LIST_MODULES_ALL):
        return []
    out = []
    for hmod in arr[:min(len(arr), needed.value // ctypes.sizeof(win.wt.HMODULE))]:
        info = win.MODULEINFO()
        name = ctypes.create_unicode_buffer(32768)
        if win.GetModuleInformation(handle, hmod, ctypes.byref(info), ctypes.sizeof(info)):
            win.GetModuleFileNameExW(handle, hmod, name, len(name))
            out.append({"base": info.lpBaseOfDll or 0, "size": info.SizeOfImage, "path": name.value,
                        "name": Path(name.value).name})
    return sorted(out, key=lambda m: m["base"])


class Symbolizer:
    """address -> module+RVA -> function, through the Ghidra exports in decomp/ghidra/<module>/functions.jsonl."""

    def __init__(self, ws: Path | None, mods: list[dict]):
        self.ws = ws
        self.mods = mods
        self.bases = [m["base"] for m in mods]
        self._tables: dict[str, tuple[list[int], list[dict]] | None] = {}

    def _stale(self, module: dict) -> bool:
        """The export was made from another build of this module (its RVAs would name the wrong functions)."""
        from gst.common import read_json
        from gst.decompile.dotnet import sha256_of

        meta = read_json(self.ws / "decomp" / "ghidra" / module["name"] / "meta.json", {}) or {}
        try:
            return bool(meta.get("sha256")) and meta["sha256"] != sha256_of(Path(module["path"]))
        except OSError:
            return False

    def _table(self, module: dict):
        name = module["name"]
        if name not in self._tables:
            path = self.ws / "decomp" / "ghidra" / name / "functions.jsonl" if self.ws else None
            if path is None or not path.is_file():
                self._tables[name] = None
            elif self._stale(module):
                self._tables[name] = "stale"
            else:
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                rows = sorted(({**r, "start": int(r["rva"], 16)} for r in rows), key=lambda r: r["start"])
                self._tables[name] = ([r["start"] for r in rows], rows)
        return self._tables[name]

    def module_at(self, address: int) -> dict | None:
        i = bisect.bisect_right(self.bases, address) - 1
        if i >= 0 and address < self.mods[i]["base"] + self.mods[i]["size"]:
            return self.mods[i]
        return None

    def describe(self, address: int, *, inside_previous: bool = False) -> dict:
        """`inside_previous`: the address follows the instruction of interest (data breakpoints stop after
        the write), so look up address - 1."""
        mod = self.module_at(address)
        if mod is None:
            return {"address": hex(address), "where": "outside any module (JIT code, heap or stack)"}
        rva = address - mod["base"]
        out = {"address": hex(address), "module": mod["name"], "rva": hex(rva), "where": f"{mod['name']}+{rva:#x}"}
        table = self._table(mod)
        if table is None:
            out["hint"] = f"gst decompile native {mod['name']} names the function"
            return out
        if table == "stale":
            out["hint"] = f"the Ghidra export is from another build of {mod['name']}: gst decompile native {mod['name']}"
            return out
        starts, rows = table
        probe = rva - 1 if inside_previous else rva
        i = bisect.bisect_right(starts, probe) - 1
        if i >= 0 and probe < rows[i]["start"] + max(1, int(rows[i].get("size") or 1)):
            fn = rows[i]
            out.update(function=fn["q"], function_rva=fn["rva"], offset_in_function=hex(probe - fn["start"]),
                       file=fn.get("file"), line=fn.get("line"))
        return out
