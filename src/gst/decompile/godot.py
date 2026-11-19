"""Godot 3/4 GDScript -> source, with GDRE Tools (gdsdecomp), into ``decomp/godot/``.

Only scripts: `--include` of `.gd`/`.gdc` (GDRE refuses `--scripts-only` together with filters).
C# of Godot .NET games comes from the dotnet backend (ilspycmd, PDB names) to keep one copy of
each type in the index; scenes and resources are `gst assets` work. An encrypted pack is diagnosed,
not opened (guardrail 4).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from gst import policy, tools
from gst.common import GsError, write_json, write_text
from gst.recon.sniff import godot_pck

OUT_DIR = Path("decomp") / "godot"
MAX_CMDLINE = 30_000  # characters per command (CreateProcess fails with WinError 206 near 32k)
SCRIPT_GLOBS = ("*.gd", "*.gdc")
DANGEROUS = ("--pck-create", "--pck-patch", "--compile", "--txt-to-bin", "--patch-translations", "--key")


def gdre() -> Path:
    exe = tools.bin_path("gdre")
    if exe is None:
        raise GsError("GDRE Tools is not installed (gst tools install gdre)")
    return exe


def find_pack(install: Path) -> Path:
    """The game's .pck, or the executable that embeds it."""
    packs = sorted(install.glob("*.pck"), key=lambda p: -p.stat().st_size)
    if packs:
        return packs[0]
    for exe in sorted(install.glob("*.exe"), key=lambda p: -p.stat().st_size):
        info = godot_pck(exe)
        if info and info.get("where") == "embedded in exe":
            return exe
    raise GsError(f"no Godot pack (.pck or embedded) in {install}")


def check_pack(pack: Path) -> dict:
    info = godot_pck(pack) or {}
    if info.get("dir_encrypted"):
        raise GsError(f"{pack.name} has an encrypted file index: only a community-published key with its source "
                      "URL may open it (guardrails.md rule 4); diagnose and record it instead", 3)
    return info


def command(exe: Path, pack: Path, out: Path, includes=SCRIPT_GLOBS, excludes=()) -> list[str]:
    cmd = [str(exe), "--headless", f"--recover={pack}", f"--output={out}"]
    cmd += [f"--include={g}" for g in includes] + [f"--exclude={g}" for g in excludes]
    assert not any(c.startswith(DANGEROUS) for c in cmd)
    return cmd


def cmdline_length(cmd: list[str]) -> int:
    return len(subprocess.list2cmdline(cmd))


def batched_commands(exe: Path, pack: Path, out: Path, includes, excludes=(), *, max_chars: int = MAX_CMDLINE) -> list[list[str]]:
    """GDRE commands that together select ``includes``, none longer than ``max_chars`` (Windows
    command lines stop near 32k characters: WinError 206). The whole project needs no filter at all
    (one call, see ``command``)."""
    base = command(exe, pack, out, includes=(), excludes=excludes)
    room = max_chars - cmdline_length(base)
    batches: list[list[str]] = []
    current: list[str] = []
    used = 0
    for item in includes:
        cost = len(subprocess.list2cmdline([f"--include={item}"])) + 1
        if cost > room:
            raise GsError(f"a single GDRE filter is longer than the command line allows: {item}")
        if current and used + cost > room:
            batches.append(current)
            current, used = [], 0
        current.append(item)
        used += cost
    if current:
        batches.append(current)
    return [command(exe, pack, out, includes=b, excludes=excludes) for b in batches]


def run(cmd: list[str], out: Path, log_name: str, *, timeout: float | None = None) -> tuple[int, str]:
    """Run GDRE to completion (no time limit unless asked: the pipeline watchdog only warns)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
        code, text = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, text = -1, f"timed out after {timeout}s (--timeout)"
    write_text(out.parent / log_name, f"$ {subprocess.list2cmdline(cmd)}\n\n{text}")
    return code, text


def decompile(ws: Path, game: dict) -> dict:
    import shutil

    from gst.decompile.dotnet import sha256_of

    install = Path(game["install_dir"])
    pack = find_pack(install)
    decision = policy.check(game, "decompile", str(pack))  # a pack embedded in a protected exe stays closed
    if not decision.allowed:
        raise GsError(f"{decision.reason} (guardrails.md rule {decision.rule})", 3)
    info = check_pack(pack)
    out = ws / OUT_DIR
    if out.exists():
        shutil.rmtree(out)
    exe = gdre()
    cmd = command(exe, pack, out)
    t0 = time.time()
    code, text = run(cmd, out, "godot.log")
    scripts = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.gd")) if out.exists() else []
    if code != 0 and not scripts:  # a pure C# game has no GDScript: that alone is not a failure
        raise GsError(f"GDRE recovery failed (exit {code}); see decomp/godot.log")
    write_json(out / "MANIFEST.json", {
        "backend": "godot", "tool": "gdre_tools", "tool_version": tools.installed_version(tools.get("gdre")),
        "source": str(pack), "sha256": sha256_of(pack), "godot_version": info.get("version"),
        "game_build": game.get("buildid"), "scripts": len(scripts), "command": cmd,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    return {"out": str(out), "pack": str(pack), "scripts": len(scripts), "examples": scripts[:8],
            "seconds": round(time.time() - t0, 1), "exit_code": code}
