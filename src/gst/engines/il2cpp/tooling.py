"""Shared plumbing of the IL2CPP tool wrappers: which catalog tool supports a metadata version, and how a tool runs.

Tool choice: each IL2CPP tool in ``data/catalog.toml`` lists ``metadata_versions`` (``"39"``, ``"24-29.1"``,
``"104-106"``); :func:`supporting` returns the catalog tools whose list covers a version, in the preference order
of the caller.

Running (:func:`run_tool`): no timeout by default (``timeout=None``), the working directory is a gst-made empty
folder inside the workspace (Cpp2IL and Il2CppInspector load plugins from their working directory; a folder we
created holds none), stdin is closed, stdout+stderr go to a log file in the workspace and the full text is kept.
Tools never get a path inside the game install as an output.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

PREFERENCE = ("cpp2il", "il2cppdumper", "il2cppinspector")  # dummy DLL source order (Cpp2IL output feeds TypeTreeGenerator)


def _vtuple(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", str(text))) or (0,)


def covers(spec: str, version: str) -> bool:
    """Does a ``metadata_versions`` entry (``"39"``, ``"24-29.1"``) cover ``version`` (``"39"``, ``"24.2"``)?

    A bare major in the version (``"24"``) matches any sub-version range that includes that major, and a bare major
    in the spec (``"39"``) matches its sub-versions (``"39.0"``)."""
    v = _vtuple(version)
    if "-" in spec:
        lo, hi = (_vtuple(p) for p in spec.split("-", 1))
        if len(v) == 1:
            return lo[0] <= v[0] <= hi[0]
        hi_cmp = hi if len(hi) > 1 else hi + (999,)
        return lo <= v <= hi_cmp
    s = _vtuple(spec)
    return v[:len(s)] == s if len(s) <= len(v) else s[:len(v)] == v


def supports(tool, version: str) -> bool:
    return any(covers(spec, version) for spec in (getattr(tool, "metadata_versions", None) or []))


def supporting(version: str, order: tuple[str, ...] = PREFERENCE) -> list:
    """Catalog tools (``gst.tools.Tool``) that support this metadata version, in ``order``."""
    from gst import tools

    catalog = tools.load_catalog()
    return [catalog[t] for t in order if t in catalog and supports(catalog[t], version)]


def support_table(order: tuple[str, ...] = PREFERENCE) -> dict[str, list[str]]:
    from gst import tools

    catalog = tools.load_catalog()
    return {t: list(catalog[t].metadata_versions) for t in order if t in catalog}


def unsupported_reason(version: str) -> str:
    table = ", ".join(f"{t} {'/'.join(v)}" for t, v in support_table().items())
    return (f"IL2CPP metadata version {version}: no tool in the gst catalog supports it ({table}); "
            "the typetree chain falls back to TypeTreeGenerator il2cpp, then to MonoBehaviour headers, and REA "
            "(inspect-il2cpp-metadata) is the remaining code evidence")


def tool_version(tool_id: str) -> str:
    from gst import tools

    try:
        return tools.installed_version(tools.get(tool_id)) or tools.get(tool_id).version
    except Exception:
        return ""


def run_tool(cmd: list[str], *, cwd: Path, log: Path, timeout: float | None = None, env: dict | None = None) -> dict:
    """Run a tool to completion: ``{exit_code, seconds, output, command, timed_out}`` (output = full text, also in
    ``log``). ``timeout`` only from an explicit option (``gst study --timeout``)."""
    cwd.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    full_env = {**os.environ, "NO_COLOR": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1", **(env or {})}
    timed_out = False
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        fh.write(f"$ {subprocess.list2cmdline(cmd)}\n(cwd {cwd})\n\n")
        fh.flush()
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=full_env, creationflags=flags)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()
    seconds = round(time.time() - t0, 1)
    if cwd.is_dir() and not any(cwd.iterdir()):
        cwd.rmdir()
    output = log.read_text(encoding="utf-8", errors="replace")
    if timed_out:
        output += f"\n[gst] stopped after {timeout}s (explicit --timeout)\n"
        log.write_text(output, encoding="utf-8")
    return {"exit_code": proc.returncode, "seconds": seconds, "output": output, "command": cmd, "timed_out": timed_out}


def files_under(folder: Path, pattern: str = "*") -> list[Path]:
    return sorted(p for p in folder.rglob(pattern) if p.is_file()) if folder.is_dir() else []


def bytes_under(folder: Path) -> int:
    return sum(p.stat().st_size for p in files_under(folder))
