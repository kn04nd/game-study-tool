"""Run REA's JSON CLI as a static tool: one request file in, one Evidence file out.

Every call writes, under ``<ws>/dumps/rea/evidence/<op>/``:

- ``<name>.request.json``: the request, shaped like the MCP tool arguments (for commands that take an
  ``inputJson`` this file *is* the input REA reads);
- ``<name>.json``: REA's raw ``--json`` output (one Evidence record) when the command succeeds;
- ``<name>.error.json`` + ``<name>.stderr.txt``: the error JSON and the full stderr when it fails (never cut; an
  older ``<name>.json`` from a successful run is kept, a later success removes the error files);
- ``<name>.run.json``: argv, exit code, seconds, peak working set, output bytes, REA version, raised caps.

Policy: static only. :data:`STATIC_COMMANDS` is an allow-list; Frida, debugger, capture, adb and every other
runtime or attach command is refused before anything starts (guardrails.md: no runtime/attach here). Paths go to
REA as ``D:/...`` (UNC paths are refused: some providers reject them). No timeout; caps that REA lets a caller
raise go to :data:`RAISED_CAP_BYTES` (2 GiB) when the installed REA advertises the option.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from gst.common import GsError, long_path, write_json

EVIDENCE_DIR = Path("dumps") / "rea" / "evidence"
RAISED_CAP_BYTES = 2 * 1024 * 1024 * 1024
ENV_EXE = "GST_REA_EXE"  # a specific rea entry (rea.cmd, rea.mjs or, in tests, a Python script)

# Static, file-reading commands gst may run. Anything else (frida, debug-with-*, capture-*, observe-*, adb, browser,
# electron, environment-serve...) is refused.
STATIC_COMMANDS = frozenset({
    "inspect-managed-artifact", "inspect-managed-members", "inspect-managed-native-boundaries",
    "compare-managed-members", "project-managed-application-graph",
    "inspect-il2cpp-metadata",
    "inspect-artifact", "extract-artifact",
    "inspect-pe-imports", "inspect-native-exports", "inspect-binary-hardening", "inspect-pe-resources",
    "inspect-analysis-view", "inspect-evidence-bundle",
})
RUNTIME_MARKERS = ("frida", "instrument", "attach", "capture", "observe", "debug", "adb", "gdb", "rizin",
                   "browser", "electron", "process", "start-", "trace", "build-computer", "serve")

# Caps a caller may raise, by command: CLI option -> what it bounds. Added only when ``--help`` lists the option.
CAP_OPTIONS: dict[str, tuple[str, ...]] = {
    "inspect-managed-artifact": ("--max-snapshot-bytes", "--max-metadata-representation-bytes"),
    "inspect-managed-members": ("--max-snapshot-bytes", "--max-metadata-representation-bytes"),
    "inspect-managed-native-boundaries": ("--max-snapshot-bytes", "--max-metadata-representation-bytes"),
    "compare-managed-members": ("--max-snapshot-bytes", "--max-metadata-representation-bytes"),
    "inspect-il2cpp-metadata": ("--max-input-bytes", "--max-output-bytes"),  # REA built with patches/rea/ (docs/tools/rea.md)
}
# Node's single-string ceiling bounds REA's "represented bytes" fields (buffer.constants.MAX_STRING_LENGTH).
NODE_MAX_STRING = 536_870_888
# JSON-valued cap options: the Godot directory representation (at most Node's string ceiling) and the aggregate
# archive work, raised for every archive reader REA runs on a pck.
CAP_JSON_OPTIONS: dict[str, tuple[str, dict]] = {
    "inspect-artifact": ("--artifact-limits", {"godot": {"directory_bytes": NODE_MAX_STRING},
                                               "expanded_bytes": RAISED_CAP_BYTES}),
    "extract-artifact": ("--artifact-limits", {"godot": {"directory_bytes": NODE_MAX_STRING},
                                               "expanded_bytes": RAISED_CAP_BYTES}),
}


@dataclass(frozen=True)
class Rea:
    """A runnable REA: ``argv`` prefix (``node rea.mjs`` when the package is found, else the shim), its version."""

    argv: tuple[str, ...]
    path: str
    version: str


@dataclass
class RunResult:
    op: str
    name: str
    status: str                      # done | failed
    evidence: str | None = None      # path of <name>.json (absolute)
    request: str | None = None
    error: str | None = None         # full error text (error JSON + stderr), never cut
    error_json: dict | None = None
    stderr: str | None = None        # path of <name>.stderr.txt on failure
    returncode: int | None = None
    seconds: float = 0.0
    peak_rss: int | None = None      # bytes (peak working set of the REA process, Windows)
    out_bytes: int = 0
    argv: list[str] = field(default_factory=list)
    rea_version: str = ""
    caps: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "done"


# --- locating REA ---------------------------------------------------------------------------------

_VERSIONS: dict[str, str] = {}
_HELP: dict[tuple[str, str], str] = {}


def _entry(exe: Path) -> tuple[str, ...]:
    """``node <pkg>/scripts/rea.mjs`` for an npm shim (avoids cmd.exe quoting), the script itself for .py/.mjs."""
    suffix = exe.suffix.lower()
    if suffix == ".py":
        return (sys.executable, "-X", "utf8", str(exe))
    node = shutil.which("node")
    if suffix in {".mjs", ".js"}:
        return (node or "node", str(exe))
    for pkg in (exe.parent.parent / "rea-agents", exe.parent / "node_modules" / "rea-agents"):
        manifest = pkg / "package.json"
        if not manifest.is_file() or node is None:
            continue
        try:
            bins = json.loads(manifest.read_text(encoding="utf-8")).get("bin")
        except (OSError, ValueError):
            continue
        script = bins.get("rea") if isinstance(bins, dict) else bins
        if isinstance(script, str) and (pkg / script).is_file():
            return (node, str(pkg / script))
    return (str(exe),)


def find(exe: str | os.PathLike | None = None) -> Rea | None:
    """The REA to run: ``exe``, else ``GST_REA_EXE``, else the catalog tool ``rea`` (gst tools dir, then PATH)."""
    chosen = exe or os.environ.get(ENV_EXE)
    if chosen:
        path = Path(chosen)
    else:
        from gst import tools

        try:
            path = tools.bin_path(tools.get("rea"))
        except GsError:
            path = None
        if path is None:
            return None
    argv = _entry(path)
    return Rea(argv=argv, path=str(path), version=version_of(argv))


def version_of(argv: tuple[str, ...]) -> str:
    """The package.json version next to ``scripts/rea.mjs`` (instant), else ``rea --version``."""
    key = "\0".join(argv)
    if key not in _VERSIONS:
        import re

        manifest = Path(argv[-1]).parent.parent / "package.json"
        if len(argv) == 2 and Path(argv[-1]).name == "rea.mjs" and manifest.is_file():
            try:
                _VERSIONS[key] = str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "")
                if _VERSIONS[key]:
                    return _VERSIONS[key]
            except (OSError, ValueError):
                pass
        try:
            proc = subprocess.run([*argv, "--version"], capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", stdin=subprocess.DEVNULL, timeout=None)
            match = re.search(r"(\d+\.\d+\.\d+(?:[-+][\w.]+)?)", proc.stdout + proc.stderr)
            _VERSIONS[key] = match.group(1) if match else ""
        except OSError:
            _VERSIONS[key] = ""
    return _VERSIONS[key]


def _help_cache_file() -> Path:
    from gst import config

    return config.tools_dir() / "rea" / "help-cache.json"


def help_text(rea: Rea, command: str) -> str:
    """``rea <command> --help``, cached in memory and on disk (``<tools_dir>/rea/help-cache.json``, keyed by the REA
    entry and its mtime): every study job is a fresh process and Node takes seconds to start."""
    key = ("\0".join(rea.argv), command)
    if key in _HELP:
        return _HELP[key]
    try:
        stamp = os.stat(rea.argv[-1]).st_mtime_ns
    except OSError:
        stamp = 0
    disk_key = f"{key[0]}\0{stamp}\0{command}"
    cache_file = _help_cache_file()
    try:
        disk = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.is_file() else {}
    except (OSError, ValueError):
        disk = {}
    if disk_key in disk:
        _HELP[key] = str(disk[disk_key])
        return _HELP[key]
    try:
        proc = subprocess.run([*rea.argv, command, "--help"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=None)
        _HELP[key] = proc.stdout + proc.stderr
    except OSError:
        return ""
    if proc.returncode == 0 and _HELP[key]:
        disk[disk_key] = _HELP[key]
        try:
            write_json(cache_file, disk)
        except OSError:
            pass
    return _HELP[key]


def supports(rea: Rea, command: str, option: str) -> bool:
    """Does this REA's ``<command> --help`` list ``option``? (REA built with patches/rea/ adds the IL2CPP caps)."""
    return option in help_text(rea, command)


def il2cpp_caps(rea: Rea) -> bool:
    """True when ``inspect-il2cpp-metadata`` takes ``--max-output-bytes`` (the gst fork or a later upstream)."""
    return supports(rea, "inspect-il2cpp-metadata", "--max-output-bytes")


# --- policy and paths -----------------------------------------------------------------------------

def check_static(command: str) -> None:
    low = command.lower()
    if command not in STATIC_COMMANDS or any(marker in low for marker in RUNTIME_MARKERS):
        raise GsError(f"REA command '{command}' is not a static file analysis allowed by gst "
                      "(no Frida, debugger, capture, adb or other runtime tools)", code=3)


def rea_path(path: str | os.PathLike) -> str:
    """Absolute ``D:/...`` spelling for REA; UNC (``\\\\server\\share``) is refused."""
    s = str(path)
    if s.startswith("\\\\?\\"):
        s = s[4:]
    s = os.path.abspath(s).replace("\\", "/")
    if s.startswith("//"):
        raise GsError(f"UNC path not accepted by REA providers: {path} (map it to a drive letter)")
    return s


def evidence_dir(ws: Path, op: str) -> Path:
    return Path(ws) / EVIDENCE_DIR / op


def safe_name(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_.+@" else "_" for c in name)
    return keep.strip(".") or "evidence"


# --- running --------------------------------------------------------------------------------------

def _peak_working_set(proc: subprocess.Popen) -> int | None:
    """PeakWorkingSetSize of a (finished) child on Windows, read from its still-open process handle."""
    if os.name != "nt" or not hasattr(proc, "_handle"):
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    try:
        psapi = ctypes.WinDLL("psapi")
        ok = psapi.GetProcessMemoryInfo(wintypes.HANDLE(int(proc._handle)), ctypes.byref(counters), counters.cb)
    except (OSError, AttributeError, ValueError):
        return None
    return int(counters.PeakWorkingSetSize) if ok else None


def run(ws: Path, op: str, name: str, *, path: str | os.PathLike | None = None, options: dict | None = None,
        request: dict | None = None, request_file: str | os.PathLike | None = None, raise_caps: bool = True,
        rea: Rea | None = None, env: dict[str, str] | None = None) -> RunResult:
    """Run one REA operation (``op`` = MCP name, e.g. ``inspect_managed_members``; the CLI command is the same name
    with dashes). ``path`` is the positional artifact; ``options`` map CLI options (``{"--binary": "D:/x.dll"}``,
    ``True`` for flags); ``request`` is the JSON input of commands that take ``inputJson`` (written to the request
    file and passed by path), ``request_file`` one already written (e.g. streamed: a 200 MB inlined Evidence).
    Never raises for a failed REA run: see :class:`RunResult`."""
    command = op.replace("_", "-")
    check_static(command)
    rea = rea or find()
    if rea is None:
        raise GsError("REA is not installed (gst tools install rea; Node.js on PATH)")
    out = evidence_dir(ws, op)
    os.makedirs(long_path(out), exist_ok=True)
    name = safe_name(name)
    final, req_file = out / f"{name}.json", out / f"{name}.request.json"
    err_json, err_txt, meta = out / f"{name}.error.json", out / f"{name}.stderr.txt", out / f"{name}.run.json"
    opts: dict = dict(options or {})
    caps = {}
    if raise_caps:
        for option in CAP_OPTIONS.get(command, ()):
            if option not in opts and supports(rea, command, option):
                opts[option] = str(RAISED_CAP_BYTES)
                caps[option] = RAISED_CAP_BYTES
        if command in CAP_JSON_OPTIONS:
            option, value = CAP_JSON_OPTIONS[command]
            if option not in opts and supports(rea, command, option):
                opts[option] = json.dumps(value, separators=(",", ":"))
                caps[option] = value
    argv: list[str] = [*rea.argv, command]
    if request_file is not None:
        req_file = Path(request_file)
        argv.append(rea_path(req_file))
    elif request is not None:
        write_json(req_file, request)
        argv.append(rea_path(req_file))
    else:
        mcp_like = {"operation": op}
        if path is not None:
            mcp_like["path"] = rea_path(path)
        mcp_like.update({k.lstrip("-").replace("-", "_"): v for k, v in opts.items()})
        write_json(req_file, mcp_like)
    if path is not None:
        argv.append(rea_path(path))
    for option, value in opts.items():
        if value is True:
            argv.append(option)
        elif value not in (None, False):
            argv += [option, str(value)]
    argv.append("--json")
    tmp_out = out / f".partial-{os.getpid()}-{name}.json"
    tmp_err = out / f".partial-{os.getpid()}-{name}.stderr"
    t0 = time.time()
    child_env = {**os.environ, **(env or {})}
    with open(long_path(tmp_out), "wb") as fo, open(long_path(tmp_err), "wb") as fe:
        proc = subprocess.Popen(argv, stdout=fo, stderr=fe, stdin=subprocess.DEVNULL, env=child_env)
        try:
            code = proc.wait()
        except BaseException:
            proc.kill()  # the exact child we started
            proc.wait()
            raise
        peak = _peak_working_set(proc)
    seconds = round(time.time() - t0, 2)
    size = os.path.getsize(long_path(tmp_out))
    result = RunResult(op=op, name=name, status="failed", request=str(req_file), returncode=code, seconds=seconds,
                       peak_rss=peak, out_bytes=size, argv=argv, rea_version=rea.version, caps=caps)
    head = b""
    with open(long_path(tmp_out), "rb") as fh:
        head = fh.read(4096).lstrip()
    stderr_text = Path(long_path(tmp_err)).read_text(encoding="utf-8", errors="replace")
    looks_ok = head.startswith(b"{") and b'"evidence_id"' in head[:4096]
    if code == 0 and looks_ok:
        os.replace(long_path(tmp_out), long_path(final))
        for stale in (err_json, err_txt):
            Path(long_path(stale)).unlink(missing_ok=True)
        os.unlink(long_path(tmp_err))
        result.status, result.evidence = "done", str(final)
    else:  # an older <name>.json from a successful run stays: a failure never destroys Evidence
        os.replace(long_path(tmp_out), long_path(err_json))
        os.replace(long_path(tmp_err), long_path(err_txt))
        text = Path(long_path(err_json)).read_text(encoding="utf-8", errors="replace")
        try:
            result.error_json = json.loads(text) if text.strip() else None
        except ValueError:
            result.error_json = None
        result.stderr = str(err_txt)
        result.error = (f"REA {command} exited with {code}\n$ {subprocess.list2cmdline(argv)}\n--- stdout ---\n{text}\n"
                        f"--- stderr ---\n{stderr_text}")
    write_json(meta, {k: v for k, v in asdict(result).items() if k not in {"error", "error_json"}})
    return result


def error_code(result: RunResult) -> str:
    """REA's error ``code`` (``resource_constraint``, ``invalid_request``...) of a failed run, or ``""``."""
    data = result.error_json if isinstance(result.error_json, dict) else {}
    nested = data.get("error") if isinstance(data.get("error"), dict) else {}
    return str(data.get("code") or nested.get("code") or "")
