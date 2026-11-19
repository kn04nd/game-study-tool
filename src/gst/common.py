"""Shared helpers: errors, output, text/JSON IO, subprocesses and path normalisation.

Adapted from universal-modder's um/common.py (MIT, see THIRD_PARTY_NOTICES.md).
Imported by the hook path: keep module-level imports light (subprocess/shutil are local).
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

TYPE_CHECKING = False  # typing.TYPE_CHECKING without importing typing
if TYPE_CHECKING:
    import subprocess
    from typing import Any

IS_WINDOWS = os.name == "nt"


class GsError(Exception):
    """User-facing failure: printed as ``gst: <message>`` and mapped to an exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def setup_console() -> None:
    """Force UTF-8 on stdout/stderr (Windows consoles default to a legacy code page)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def emit(data: Any, as_json: bool, text: str | None = None) -> None:
    """Print ``data`` as JSON, or ``text`` (falls back to a plain rendering of ``data``)."""
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")
    else:
        sys.stdout.write((text if text is not None else render(data)) + "\n")


def render(data: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{pad}{key}:")
                lines.append(render(value, indent + 1))
            else:
                lines.append(f"{pad}{key}: {value}")
        return "\n".join(lines)
    if isinstance(data, list):
        return "\n".join(
            f"{pad}- {render(item, indent + 1).lstrip()}" if isinstance(item, (dict, list)) else f"{pad}- {item}"
            for item in data
        )
    return f"{pad}{data}"


def warn(message: str) -> None:
    sys.stderr.write(f"gst: {message}\n")


# --- text / JSON files: always UTF-8 with LF ---------------------------------------------

def read_text(path: str | os.PathLike) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def write_text(path: str | os.PathLike, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


def append_text(path: str | os.PathLike, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


def read_json(path: str | os.PathLike, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: str | os.PathLike, data: Any) -> Path:
    return write_text(path, json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


# --- processes ----------------------------------------------------------------------------

def which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def run(
    cmd: Iterable[str],
    *,
    cwd: str | os.PathLike | None = None,
    timeout: float | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command, capturing text output as UTF-8. Raises GsError on failure when ``check``."""
    import subprocess

    argv = [str(c) for c in cmd]
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            timeout=timeout,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise GsError(f"command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise GsError(f"timed out after {timeout}s: {' '.join(argv[:3])} ...") from exc
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise GsError(f"{Path(argv[0]).name} exited with {proc.returncode}: {tail}")
    return proc


# --- paths --------------------------------------------------------------------------------

def norm_path(path: str | os.PathLike) -> str:
    """Canonical comparable form: forward slashes, lower case, Git-Bash ``/c/x`` -> ``c:/x``.

    Only for comparisons; never use the result to open files.
    """
    s = str(path).strip().strip('"').strip("'").replace("\\", "/")
    if s.startswith("//?/"):
        s = s[4:]
    if len(s) >= 3 and s[0] == "/" and s[1].isalpha() and s[2] == "/":
        s = f"{s[1]}:{s[2:]}"
    elif len(s) == 2 and s[0] == "/" and s[1].isalpha():
        s = f"{s[1]}:/"
    while "//" in s[1:]:
        s = s[0] + s[1:].replace("//", "/")
    if len(s) > 3 and s.endswith("/"):
        s = s.rstrip("/")
    return s.lower()


def is_under(path: str | os.PathLike, root: str | os.PathLike) -> bool:
    """True if ``path`` equals ``root`` or lies inside it (case-insensitive, separator-agnostic)."""
    p, r = norm_path(path), norm_path(root)
    return p == r or p.startswith(r.rstrip("/") + "/")


LONG_PATH_AT = 240  # MAX_PATH is 260 with the terminator; leave room for a sidecar suffix


def long_path(path: str | os.PathLike) -> str:
    """``path`` usable by open/stat/rename beyond MAX_PATH on Windows: absolute, with the ``\\\\?\\``
    prefix when long (the Win32 file APIs accept it even with LongPathsEnabled=0)."""
    s = os.path.abspath(str(path))
    if not IS_WINDOWS or s.startswith("\\\\?\\") or len(s) < LONG_PATH_AT:
        return s
    if s.startswith("\\\\"):  # UNC \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def contained(base: str | os.PathLike, rel: str) -> Path | None:
    """``base / rel`` when ``rel`` (a relative path read from game data: a glTF buffer uri, a ``res://`` path) stays
    inside ``base``; None for absolute, drive-letter, UNC or ``..`` escapes. Never touches a network path: an
    absolute or UNC name is refused before any file-system call."""
    rel = str(rel).replace("\\", "/")
    if not rel or rel.startswith("/") or ":" in rel or "\x00" in rel:
        return None
    root = Path(base)
    target = root / rel
    try:
        resolved_root = root.resolve()
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if resolved != resolved_root and resolved_root not in resolved.parents:
        return None
    return target


# cmd.exe metacharacters. Python runs a .bat/.cmd through cmd.exe even with an argument list, so an argument that
# holds one of these (from a game file name, say) could start another command ("BatBadBut").
BAT_UNSAFE = re.compile(r'[&|<>^%!"\r\n\t]')


def is_batch(exe: str | os.PathLike) -> bool:
    return str(exe).lower().endswith((".bat", ".cmd"))


def check_bat_args(cmd: list[str], what: str | None = None) -> None:
    """Refuse a .bat/.cmd command line whose arguments hold cmd.exe metacharacters."""
    bad = [a for a in cmd[1:] if BAT_UNSAFE.search(str(a))]
    if bad:
        name = what or os.path.basename(str(cmd[0]))
        raise GsError(f"argument unsafe for {name} (cmd.exe metacharacters): {bad[0]!r}; "
                      "use paths without & | < > ^ % ! or quotes")


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def slugify(name: str, max_len: int = 24) -> str:
    """Short ASCII slug for workspace folders (short paths matter with LongPathsEnabled=0)."""
    import re
    import unicodedata

    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "game"
