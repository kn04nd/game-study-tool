"""Hook handlers behind ``gst.hooks``: full guard, context digests and the workspace journal.

Loaded only after the fast screen in ``gst.hooks`` decided there is work to do. Only deny/ask
decisions, input rewrites and context are emitted; plain "allow" is never returned, so the normal
permission flow applies.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from gst import hooks

CONTEXT_LIMIT = 1500
FACTS_LIMIT = 4000
NOISY_GS = re.compile(r"^\s*(?:&\s*['\"][^'\"]*gst(?:\.exe|\.cmd)?['\"]|gst(?:\.exe)?)\s+(status|q|guard|tools\s+(?:path|list|info)|doctor|hook|config\s+show)\b")
JOURNAL_CMD = re.compile(
    r"(?:^|[\s;&|/\\'\"])(?:gst(?:\.exe|\.cmd)?\s+(?:new|recon|decompile|assets|fmt|index|map|trace|gfx|sandbox|mcp|report|notes|tools\s+install)\b|"
    r"ilspycmd|analyzeheadless|repak|retoc|uas\b|gdre_tools|undertalemodcli|vineflower|jadx|unluac|cpp2il|il2cppinspector|"
    r"frida-trace|ttd(?:\.exe)?\b|procmon64|procdump64|renderdoccmd|qrenderdoc|imhex|vgmstream-cli|texconv)",
    re.IGNORECASE,
)


def _out(payload: dict) -> None:
    # ASCII-only JSON: correct whatever encoding the hook's stdout ends up with.
    sys.stdout.write(json.dumps(payload))


def _workspace(path: str | None) -> Path | None:
    from gst.config import find_workspace

    try:
        return find_workspace(path or os.getcwd())
    except OSError:
        return None


def _game(ws: Path) -> dict:
    try:
        return json.loads((ws / "game.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _tail_lines(text: str, heading_rx: str, n: int) -> list[str]:
    parts = re.split(heading_rx, text, flags=re.M)
    last = parts[-1] if len(parts) > 1 else ""
    return [ln.strip() for ln in last.splitlines() if ln.strip().startswith("- ")][-n:]


def _section(text: str, title: str) -> list[str]:
    m = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if not m:
        return []
    return [ln.strip() for ln in m.group(1).splitlines() if ln.strip().startswith("- ") and len(ln.strip()) > 2]


# --- SessionStart -----------------------------------------------------------------------------------

def session_start(payload: dict) -> None:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        try:
            current = Path(env_file).read_text(encoding="utf-8") if Path(env_file).exists() else ""
            if "game-study-path" not in current:
                with open(env_file, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write('export PATH="$HOME/.local/bin:$PATH"  # game-study-path\n')
        except OSError:
            pass
    # Bash finds gst through the plugin's bin/; PowerShell only when gst.exe's folder is on PATH.
    ps_gs = hooks.gs_call() if os.path.isfile(hooks.GS_EXE) and not hooks.gs_on_path() else "gst"
    usage = f"`gst` CLI (Bash: `gst <args>`; PowerShell: `{ps_gs} <args>`)"
    ws = _workspace(payload.get("cwd"))
    if ws is None:
        context = (f"Game Study Toolkit available: {usage}. "
                   "To study a game you own: `gst new <game>` then open its workspace.")
        _out({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}})
        return
    game = _game(ws)
    study = (ws / "STUDY.md").read_text(encoding="utf-8", errors="replace") if (ws / "STUDY.md").exists() else ""
    recent = _tail_lines(study, r"^### ", 5)
    questions = _section(study, "Perguntas abertas")[:3]
    from gst import doctor

    try:
        brief = doctor.brief()[1:3]
    except Exception:  # never fail the session on a doctor problem
        brief = []
    engine = game.get("engine", {})
    lines = [
        f"Study workspace: {game.get('name')} ({ws.as_posix()}). Engine: {engine.get('label')} {engine.get('version') or ''}".rstrip()
        + f". Verdict: {game.get('verdict')}. Phase: {game.get('phase')}.",
        f"{usage}. Read CLAUDE.md, recon/RECON.md, FACTS.md before working.",
    ]
    if recent:
        lines.append("Recent journal: " + " | ".join(r[2:][:140] for r in recent))
    if questions:
        lines.append("Open questions: " + " | ".join(q[2:][:120] for q in questions))
    lines += [f"doctor: {b}" for b in brief]
    context = "\n".join(lines)[:CONTEXT_LIMIT]
    out = {"hookEventName": "SessionStart", "additionalContext": context}
    if payload.get("source") in {"startup", "resume"}:
        out["sessionTitle"] = f"gst: {game.get('name')} · {game.get('phase')}"
    _out({"hookSpecificOutput": out})


def compact_digest(payload: dict) -> None:
    ws = _workspace(payload.get("cwd"))
    if ws is None:
        return
    game = _game(ws)
    facts = (ws / "FACTS.md").read_text(encoding="utf-8", errors="replace") if (ws / "FACTS.md").exists() else ""
    auto = ws / "notes" / "_autocaptured.md"
    auto_tail = auto.read_text(encoding="utf-8", errors="replace")[-1000:] if auto.exists() else ""
    study = (ws / "STUDY.md").read_text(encoding="utf-8", errors="replace") if (ws / "STUDY.md").exists() else ""
    recent = _tail_lines(study, r"^### ", 6)
    parts = [f"[gst] Context was compacted. Study: {game.get('name')} | verdict {game.get('verdict')} | phase {game.get('phase')}.",
             "FACTS.md (verified facts):", facts[:FACTS_LIMIT]]
    if recent:
        parts += ["Recent journal:", "\n".join(recent)]
    if auto_tail:
        parts += ["Auto-captured before compaction (UNVERIFIED, check before use):", auto_tail]
    _out({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(parts)}})


# --- PreCompact: checkpoint + auto-capture --------------------------------------------------------

_FACT_RX = re.compile(r"(?:[\w.\-]+\+0x[0-9A-Fa-f]{2,}|\b0x[0-9A-Fa-f]{6,16}\b|\bRVA[: ]+0x[0-9A-Fa-f]+|\boffset\s+0x[0-9A-Fa-f]+)")


def pre_compact(payload: dict) -> None:
    ws = _workspace(payload.get("cwd"))
    if ws is None:
        return
    from gst.workspace import journal

    journal(ws, f"compaction checkpoint ({payload.get('trigger', '?')})")
    transcript = payload.get("transcript_path")
    if not transcript or not Path(transcript).exists():
        return
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    except OSError:
        return
    found: list[str] = []
    for raw in lines:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") or {}
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        texts = [content] if isinstance(content, str) else [c.get("text", "") for c in content or [] if isinstance(c, dict)]
        for text in texts:
            for sentence in re.split(r"(?<=[.!?\n])\s+", text or ""):
                if _FACT_RX.search(sentence) and 12 < len(sentence) < 300:
                    found.append(sentence.strip())
    if not found:
        return
    auto = ws / "notes" / "_autocaptured.md"
    existing = auto.read_text(encoding="utf-8", errors="replace") if auto.exists() else "# Auto-captured (UNVERIFIED)\n"
    new = [f"- {s}" for s in dict.fromkeys(found) if s not in existing][-30:]
    if new:
        with open(auto, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(existing.rstrip("\n") + f"\n\n## {time.strftime('%Y-%m-%d %H:%M')}\n" + "\n".join(new) + "\n")


# --- PreToolUse: guard + PowerShell gst rewrite -------------------------------------------------------

def pre_tool(payload: dict) -> None:
    from gst import guard

    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}
    cwd = payload.get("cwd")
    if tool in {"Bash", "PowerShell"}:
        command = data.get("command", "")
        decision = guard.check_command(command, cwd=cwd, agent_type=payload.get("agent_type"))
        if not decision.allowed:
            _out({"hookSpecificOutput": decision.as_hook()})
            return
        if tool == "PowerShell":
            rewrite = hooks._powershell_rewrite(data, command)
            if rewrite:
                _out(rewrite)
        return
    if tool in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        path = data.get("file_path") or data.get("notebook_path")
        if not path:
            return
        content = data.get("content") or data.get("new_string") or data.get("new_source") or ""
        if tool == "MultiEdit":
            content = "\n".join(e.get("new_string", "") for e in data.get("edits", []))
        ws = _workspace(str(Path(path).parent)) or _workspace(cwd)
        decision = guard.check_write(path, content, workspace=ws, agent_type=payload.get("agent_type"))
        if not decision.allowed:
            _out({"hookSpecificOutput": decision.as_hook()})
        return
    if tool == "Read":
        path = data.get("file_path")
        if not path or data.get("limit"):
            return
        try:
            size = Path(path).stat().st_size
        except OSError:
            return
        ws = _workspace(str(Path(path).parent))
        if ws and size > hooks.READ_HINT_BYTES and "decomp" in Path(path).parts:
            _out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
                  f"{Path(path).name} is {size // 1024} KB of decompiled output: search it first (code index, "
                  "`rg -n`) and Read with offset/limit instead of loading it whole."}})


# --- PostToolUse (async): command log + journal ------------------------------------------------------

def post_tool(payload: dict) -> None:
    tool = payload.get("tool_name", "")
    if tool not in {"Bash", "PowerShell"}:
        return
    ws = _workspace(payload.get("cwd"))
    if ws is None:
        return
    command = (payload.get("tool_input") or {}).get("command", "")
    response = payload.get("tool_response")
    failed = isinstance(response, dict) and (response.get("interrupted") or response.get("is_error"))
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "command": command[:1000],
              "agent": payload.get("agent_type"), "failed": bool(failed)}
    try:
        (ws / "logs").mkdir(exist_ok=True)
        with open(ws / "logs" / "commands.jsonl", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return
    if JOURNAL_CMD.search(command) and not NOISY_GS.search(command):
        from gst.workspace import journal

        short = " ".join(command.split())[:160]
        journal(ws, f"`{short}`" + (" (failed)" if failed else ""))


def session_end(payload: dict) -> None:
    ws = _workspace(payload.get("cwd"))
    if ws is None:
        return
    from gst.workspace import journal

    journal(ws, f"session end ({payload.get('reason', '?')})")


# --- SubagentStop: deliver-first ------------------------------------------------------------------

# A task prompt declares its deliverable(s) with lines `DELIVERABLE: notes/logic/x.md` (paths relative to the
# workspace). The stop is blocked while a declared file is missing, or while a Markdown deliverable has no
# "Não verificado" section; at most MAX_STOP_BLOCKS times per agent, so a stuck agent can still end.
DELIVERABLE_RX = re.compile(r"^[ \t>*-]*DELIVERABLE:\s*`?([^\s`]+)`?", re.M)
# "## Não verificado", "## 12. Não verificado", ARCHITECTURE.md's "## 10. Evidências e o que NÃO foi verificado"
UNVERIFIED_RX = re.compile(r"^#{2,4}[ \t]+[^\n]{0,60}?\bn[ãa]o\s+(?:foi\s+)?verificad", re.M | re.I)
MAX_STOP_BLOCKS = 3


def _first_user_text(transcript: str | None) -> str:
    """Text of the first user message of a transcript JSONL (the subagent's task prompt)."""
    if not transcript:
        return ""
    try:
        with open(transcript, encoding="utf-8", errors="replace") as fh:
            for _ in range(200):
                raw = fh.readline()
                if not raw:
                    break
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                message = entry.get("message") if isinstance(entry, dict) else None
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    return content
                texts = [c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
                if texts:
                    return "\n".join(texts)
    except OSError:
        return ""
    return ""


def deliverables(text: str) -> list[str]:
    """Declared deliverable paths (forward slashes, no leading ./), in order, without duplicates."""
    found = [m.group(1).strip().rstrip(".,;:").replace("\\", "/").removeprefix("./")
             for m in DELIVERABLE_RX.finditer(text or "")]
    return list(dict.fromkeys(p for p in found if p))


def deliverable_problems(ws: Path, paths: list[str]) -> list[str]:
    problems = []
    for rel in paths:
        target = Path(rel) if Path(rel).is_absolute() else ws / rel
        if not target.is_file() or target.stat().st_size == 0:
            problems.append(f"{rel} does not exist yet")
        elif target.suffix.lower() == ".md":
            text = target.read_text(encoding="utf-8", errors="replace")
            m = UNVERIFIED_RX.search(text)
            if not m:
                problems.append(f'{rel} has no "Não verificado" section')
                continue
            body = re.split(r"^#{1,4}\s", text[m.end():].split("\n", 1)[-1], maxsplit=1, flags=re.M)[0]
            body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
            if not any(ln.strip() not in ("", "-", "*") for ln in body.splitlines()):
                problems.append(f'the "Não verificado" section of {rel} is still empty')
    return problems


def _stop_blocks(ws: Path, agent_id: str, bump: bool) -> int:
    """How many times this agent's stop was already blocked (logs/subagent-stop.json); +1 with ``bump``."""
    path = ws / "logs" / "subagent-stop.json"
    try:
        counts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        counts = {}
    if not isinstance(counts, dict):
        counts = {}
    n = int(counts.get(agent_id, 0) or 0)
    if bump:
        counts[agent_id] = n + 1
        try:
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(counts, indent=1), encoding="utf-8", newline="\n")
        except OSError:
            pass
    return n


def subagent_stop(payload: dict) -> None:
    ws = _workspace(payload.get("cwd"))
    if ws is None:
        return
    paths = deliverables(_first_user_text(payload.get("agent_transcript_path")))
    if not paths:
        return
    problems = deliverable_problems(ws, paths)
    if not problems:
        return
    agent = str(payload.get("agent_type") or "?")
    agent_id = str(payload.get("agent_id") or payload.get("agent_transcript_path") or agent)
    if _stop_blocks(ws, agent_id, bump=False) >= MAX_STOP_BLOCKS:
        from gst.workspace import journal

        journal(ws, f"{agent} stopped without its deliverable: {'; '.join(problems)}")
        return
    _stop_blocks(ws, agent_id, bump=True)
    _out({"decision": "block", "reason": (
        "Deliver-first: " + "; ".join(problems) + ". Write the deliverable now from what you already found "
        "(`gst notes new <layer>/<topic> --template <name>` creates it pre-filled), with a \"Não verificado\" "
        "section listing what you could not confirm, then stop.")})


HANDLERS = {
    "session-start": session_start,
    "compact-digest": compact_digest,
    "pre-compact": pre_compact,
    "pre-tool": pre_tool,
    "post-tool": post_tool,
    "session-end": session_end,
    "subagent-stop": subagent_stop,
}
