"""Claude Code hook entry point: ``gst hook <name>`` with the hook JSON on stdin.

PreToolUse runs before every Bash/PowerShell/Write/Edit call, so this module is the fast path:
it imports only ``os``/``sys`` and the built-in ``_json`` scanner (``json``, ``re`` and ``pathlib``
cost 30-50 ms each on a Defender-scanned Python), screens the payload, and loads
``gst.hooks.handlers`` (full guard, digests, journal) only when there is something to check.
``plugins/gst/hooks/run.sh`` starts it with the base Python and ``-S -E`` using hook.cfg
(see ``ensure_launcher_cfg``); ``gst hook`` through gst.exe is the fallback.

Contract: never block a session by accident (any internal error -> exit 0 without output).
"""

import os
import sys

HOOKS = ("session-start", "compact-digest", "pre-compact", "pre-tool", "post-tool", "session-end", "subagent-stop")
WORKSPACE_ONLY = ("compact-digest", "pre-compact", "session-end", "subagent-stop")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
READ_HINT_BYTES = 400_000
GS_EXE = os.path.join(os.path.expanduser("~"), ".local", "bin", "gst.exe")  # uv tool install target

# Lower-case substrings that some deny/ask rule of gst.guard.check_command needs before it can fire
# (policy.DENY_RULES, runtime tools, writes into installs). A command containing none of them, and
# matching none of the combined checks in screen_command, cannot be denied, so the full guard is
# skipped. tests/unit/test_hooks.py runs a sample of every rule through the screen.
NEEDLES = (
    # policy.DENY_RULES
    "steamless", "scyllahide", "renderdoc", "aes", "forza", "cricodecs", "-process", "quickbms", "uxm",
    "ttd", "stop", "delete", "config", "-service", "taskkill", "dbk", "dbvm", "gdre", "retoc", "fromjson", "pyghidra",
    # output that could land inside a game install
    "repak", "hgpaktool", "rp6_unpackcli", "filediver", "pyinstxtractor", "unrpyc", "ree.unpacker", "rvpacker",
    "undertalemodcli", "tee", "out-file", "-content",
    # policy.RUNTIME_TOOLS (renderdoc and ttd above cover renderdoccmd, qrenderdoc and ttd)
    "x64dbg", "x32dbg", "x96dbg", "frida", "procdump", "cheatengine", "windbg", "cdb", "pixtool", "ngfx",
    "apitrace", "drrun", "drltrace", "drstrace", "pe-sieve", "hollows_hunter", "winapioverride", "tinytracer",
    "presentmon",
)
# policy.KEY_TOOLS not already in NEEDLES; they matter only together with a key flag (-k, --key, --aes-key, uas -a/-A).
KEY_TOOLS = ("uas", "ue4export", "unrpa", "rpatool", "pyinstxtractor", "rustydemon")
GIT_VERBS = ("add", "commit", "init", "stage")
READ_ONLY_AGENTS = ("recon",)  # = gst.guard.READ_ONLY_AGENTS (checked by a test)


# --- payload ---------------------------------------------------------------------------------------

class _ScanContext:
    """Attributes read by _json.make_scanner: the defaults of json.JSONDecoder."""

    strict = True
    object_hook = None
    object_pairs_hook = None
    parse_float = float
    parse_int = int
    parse_constant = {"-Infinity": float("-inf"), "Infinity": float("inf"), "NaN": float("nan")}.__getitem__


def loads(text: str):
    """``json.loads`` for one JSON document, through the C scanner json itself uses (no ``re`` import)."""
    start = len(text) - len(text.lstrip(" \t\r\n"))
    if start == len(text):
        return {}
    try:
        import _json
    except ImportError:  # not CPython: the regular parser
        import json

        return json.loads(text)
    try:
        value, _ = _json.make_scanner(_ScanContext())(text, start)
    except StopIteration:
        raise ValueError("hook input is not JSON") from None
    return value


def dumps(obj) -> str:
    """ASCII JSON for hook output (dict/list/str/int/float/bool/None), without importing json."""
    try:
        from _json import encode_basestring_ascii as enc
    except ImportError:
        import json

        return json.dumps(obj)

    def walk(o) -> str:
        if isinstance(o, str):
            return enc(o)
        if o is True or o is False or o is None:
            return {True: "true", False: "false", None: "null"}[o]
        if isinstance(o, (int, float)):
            return repr(o)
        if isinstance(o, dict):
            return "{" + ", ".join(f"{enc(str(k))}: {walk(v)}" for k, v in o.items()) + "}"
        if isinstance(o, (list, tuple)):
            return "[" + ", ".join(walk(v) for v in o) + "]"
        raise TypeError(f"not JSON serializable: {type(o).__name__}")

    return walk(obj)


def read_payload() -> dict:
    stream = sys.stdin
    if stream is None or stream.isatty():
        return {}
    raw = stream.buffer.read() if hasattr(stream, "buffer") else stream.read()
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw.lstrip("﻿")
    data = loads(text)
    return data if isinstance(data, dict) else {}


# --- fast screens ----------------------------------------------------------------------------------

def find_workspace(start: str) -> str | None:
    """Folder holding a gst ``game.json`` at or above ``start`` (os.path twin of gst.config.find_workspace)."""
    here = os.path.abspath(start)
    while True:
        marker = os.path.join(here, "game.json")
        if os.path.isfile(marker):
            try:
                with open(marker, "rb") as fh:
                    data = loads(fh.read().decode("utf-8-sig", errors="replace"))
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict) and data.get("gs_workspace"):
                return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def _redirect_target(low: str, j: int) -> str:
    n = len(low)
    while j < n and low[j] in " \t\r\n\x0b\x0c":
        j += 1
    if j < n and low[j] in "\"'":
        end = low.find(low[j], j + 1)
        return low[j + 1:end if end != -1 else n]
    k = j
    while k < n and low[k] not in " \t\r\n;|&<>)":
        k += 1
    return low[j:k]


def _absolute_redirect(low: str) -> bool:
    """True if some ``>``/``>>`` writes to an absolute path (the only kind guard can map to an install)."""
    i = low.find(">")
    while i != -1:
        j = i + 1
        if low.startswith(">", j):
            j += 1
        if not low.startswith("&", j):  # 2>&1 and friends
            target = _redirect_target(low, j).strip(" \t\r\n\x0b\x0c\"'")  # as guard + norm_path see it
            if target and (target[1:2] == ":" or (target[0] in "/\\" and target != "/dev/null")):
                return True
        i = low.find(">", j)
    return False


def screen_command(command: str) -> bool:
    """Must the full guard look at this shell command? A cheap over-approximation of check_command."""
    if not command.isascii():
        return True  # re.IGNORECASE equates e.g. "ı" with "i"; let the real regexes decide
    low = command.lower()
    if any(needle in low for needle in NEEDLES):
        return True
    if "git" in low and any(verb in low for verb in GIT_VERBS):
        return True
    if ("-k" in low or "-a" in low) and any(name in low for name in KEY_TOOLS):
        return True
    if ">" not in low:
        return False
    for continuation in ("\\\r\n", "\\\n", "`\r\n", "`\n", "^\r\n", "^\n"):  # = guard._CONTINUATION
        low = low.replace(continuation, " ")
    return _absolute_redirect(low)


def ps_call(exe: str) -> str:
    """PowerShell call-operator form of an executable path (single quotes, where '' escapes ')."""
    return "& '" + exe.replace("'", "''") + "'"


def rewrite_gs(command: str, exe: str) -> str:
    """PowerShell can't find a bare ``gst`` (plugin bin/ and CLAUDE_ENV_FILE are Bash-only): turn a ``gst``
    that starts the command or a ``;`` segment into ``& '<exe>'``. Same matches as the regex
    ``(^|;\\s*)gst(?=\\s|$)``, which tests/unit/test_hooks.py checks."""
    parts = command.split(";")
    last = len(parts) - 1
    call = ps_call(exe)
    for i, part in enumerate(parts):
        body = part if i == 0 else part.lstrip()
        if body.startswith("gst") and ((len(body) > 3 and body[3].isspace()) or (len(body) == 3 and i == last)):
            parts[i] = part[:len(part) - len(body)] + call + body[3:]
    return ";".join(parts)


def gs_on_path() -> bool:
    """Is gst.exe's folder on PATH? Then PowerShell resolves a bare ``gst`` (true after VS Code restarts)."""
    target = os.path.normcase(os.path.dirname(GS_EXE))
    return any(os.path.normcase(os.path.normpath(p.strip('"'))) == target
               for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip())


def gs_call() -> str:
    """How PowerShell reaches gst.exe when it is not on PATH. Forward slashes, the same text the
    workspace allow rule ``PowerShell(<gs_call()> *)`` and the SessionStart context use."""
    return ps_call(GS_EXE.replace("\\", "/"))


def _powershell_rewrite(data: dict, command: str) -> dict | None:
    # Permission rules are evaluated against the rewritten command, so rewrite only when needed.
    if not os.path.isfile(GS_EXE) or gs_on_path():
        return None
    rewritten = rewrite_gs(command, GS_EXE.replace("\\", "/"))
    if rewritten == command:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "defer",
                                   "updatedInput": {**data, "command": rewritten}}}


def _pre_tool_needed(payload: dict) -> bool:
    """Handle what needs no guard here; True when gst.hooks.handlers must look at the call."""
    tool = payload.get("tool_name") or ""
    data = payload.get("tool_input") or {}
    if not isinstance(data, dict):
        return False
    if tool in ("Bash", "PowerShell"):
        command = data.get("command") or ""
        if not isinstance(command, str) or not command.strip():
            return False
        if screen_command(command):
            return True
        if str(payload.get("agent_type") or "").rsplit(":", 1)[-1] in READ_ONLY_AGENTS:
            return True  # guard.read_only_command decides
        if tool == "PowerShell":
            out = _powershell_rewrite(data, command)
            if out:
                sys.stdout.write(dumps(out))
        return False
    if tool in WRITE_TOOLS:
        return True
    if tool == "Read":  # hooks.json only sends Reads under a decomp/ folder
        path = data.get("file_path")
        if not path or data.get("limit"):
            return False
        try:
            return os.stat(path).st_size > READ_HINT_BYTES
        except (OSError, TypeError, ValueError):
            return False
    return False


def _post_tool_needed(payload: dict) -> bool:
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return False
    return find_workspace(payload.get("cwd") or os.getcwd()) is not None


# --- entry -----------------------------------------------------------------------------------------

def main(name: str) -> int:
    if name not in HOOKS:
        sys.stderr.write(f"gst hook: unknown hook '{name}' ({', '.join(HOOKS)})\n")
        return 0
    try:
        payload = read_payload()
        if name == "pre-tool" and not _pre_tool_needed(payload):
            return 0
        if name == "post-tool" and not _post_tool_needed(payload):
            return 0
        if name == "subagent-stop" and not str(payload.get("agent_type") or "").startswith("gst:"):
            return 0  # only the plugin's own agents declare deliverables
        if name in WORKSPACE_ONLY and find_workspace(payload.get("cwd") or os.getcwd()) is None:
            return 0  # every session of every project runs these hooks: quiet and cheap outside studies
        from gst.hooks import handlers

        handlers.HANDLERS[name](payload)
    except Exception as exc:  # never break the session
        if os.environ.get("GS_HOOK_DEBUG"):
            raise
        sys.stderr.write(f"gst hook {name}: {exc.__class__.__name__}: {exc}\n")
    return 0


# --- launcher config for run.sh --------------------------------------------------------------------

def launcher_lines() -> tuple[str, str]:
    """(base interpreter, folder holding the gst package), forward slashes, for run.sh's fast path."""
    exe = getattr(sys, "_base_executable", None) or sys.executable
    src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return exe.replace("\\", "/"), src.replace("\\", "/")


def _launcher_cfg():
    from gst.config import state_dir

    return state_dir() / "hook.cfg"


def ensure_launcher_cfg() -> str | None:
    """Write ``<state_dir>/hook.cfg`` (idempotent); None when this install can't use the fast path."""
    exe, src = launcher_lines()
    if not (os.path.isfile(exe) and os.path.isdir(os.path.join(src, "gst"))):
        return None
    path = _launcher_cfg()
    content = f"{exe}\n{src}\n"
    try:
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    except OSError:
        return None
    return str(path)


def launcher_state() -> dict:
    """run.sh fast path: ok (hook.cfg matches this gst), stale, or missing (hooks then use gst.exe)."""
    exe, src = launcher_lines()
    path = _launcher_cfg()
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = None
    state = "missing" if current is None else ("ok" if current == f"{exe}\n{src}\n" else "stale")
    return {"state": state, "path": str(path), "python": exe, "src": src}
