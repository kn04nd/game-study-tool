"""gst mcp: MCP servers for the current study workspace - verified templates only, over stdio or bound to
127.0.0.1 (guardrails rule 9). `add` writes .mcp.json and enables the server with its read-only tools
pre-approved; `check` audits .mcp.json; `tools`/`call` prove a server answers.
"""

from __future__ import annotations

import json

from gst import workspace
from gst.common import GsError, emit


def register(sub, parent) -> None:
    p = sub.add_parser("mcp", parents=[parent], help="MCP servers for the workspace: verified templates, stdio/127.0.0.1 only",
                       description=__doc__)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")
    ls = s.add_parser("list", parents=[parent], help="templates and the servers configured here")
    ls.set_defaults(func=cmd_list)
    add = s.add_parser("add", parents=[parent], help="configure a server from its template")
    add.add_argument("name")
    add.add_argument("--module", help="ghidra: which Ghidra project (module) when there are several")
    add.set_defaults(func=cmd_add)
    rm = s.add_parser("remove", parents=[parent], help="remove a configured server")
    rm.add_argument("name")
    rm.set_defaults(func=cmd_remove)
    ck = s.add_parser("check", parents=[parent], help="audit .mcp.json: local transports, no rejected servers")
    ck.set_defaults(func=cmd_check)
    tl = s.add_parser("tools", parents=[parent], help="start a configured server and list its tools")
    tl.add_argument("name")
    tl.set_defaults(func=cmd_tools)
    call = s.add_parser("call", parents=[parent], help="start a configured server and call one tool")
    call.add_argument("name")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}", help="JSON object with the tool's arguments")
    call.add_argument("--chars", type=int, default=None, help="characters of the answer printed (default: the whole answer)")
    call.set_defaults(func=cmd_call)


def cmd_list(args) -> int:
    from gst import mcp, tools

    ws, _game = workspace.current()
    rows = [{"name": n, "title": t["title"], "tool": t["tool"],
             "installed": tools.bin_path(t["tool"]) is not None, "configured": n in mcp.configured(ws)}
            for n, t in mcp.TEMPLATES.items()]
    extra = [n for n in mcp.configured(ws) if n not in mcp.TEMPLATES]
    text = "\n".join(f"{r['name']:<10} {'configured' if r['configured'] else 'available ' if r['installed'] else 'not installed'}"
                     f"  {r['title']}" for r in rows)
    if extra:
        text += "\nconfigured without a gst template: " + ", ".join(extra) + " (gst mcp check audits them)"
    emit({"templates": rows, "other": extra}, args.json, text)
    return 0


def cmd_add(args) -> int:
    from gst import mcp

    ws, game = workspace.current()
    result = mcp.add(ws, game, args.name, module=args.module)
    workspace.journal(ws, f"gst mcp add {args.name}")
    emit(result, args.json, f"{args.name}: written to .mcp.json and enabled; read-only tools pre-approved\n"
                            f"  {result['config']['command']} {' '.join(result['config']['args'])}\n"
                            "restart Claude Code in this folder to load it; gst mcp tools/call checks it now")
    return 0


def cmd_remove(args) -> int:
    from gst import mcp

    ws, _game = workspace.current()
    found = mcp.remove(ws, args.name)
    emit({"removed": found}, args.json, f"{args.name} removed" if found else f"{args.name} was not configured")
    return 0


def cmd_check(args) -> int:
    from gst import mcp
    from gst.common import read_text

    ws, _game = workspace.current()
    path = ws / ".mcp.json"
    problems = mcp.audit_text(read_text(path)) if path.exists() else []
    emit({"problems": problems}, args.json, "\n".join(f"- {p}" for p in problems) or ".mcp.json is local-only (rule 9)")
    return 1 if problems else 0


def _config(name: str) -> dict:
    from gst import mcp

    ws, _game = workspace.current()
    config = mcp.configured(ws).get(name)
    if config is None:
        raise GsError(f"{name} is not configured here (gst mcp add {name})")
    problems = mcp.audit_text(json.dumps({"mcpServers": {name: config}}))
    if problems:
        raise GsError("; ".join(problems), 3)
    return config


def cmd_tools(args) -> int:
    from gst.mcp_client import Session

    with Session(_config(args.name)) as session:
        listed = session.tools()
    emit(listed, args.json, "\n".join(f"{t['name']:<32} {(t.get('description') or '').strip().splitlines()[0][:90] if t.get('description') else ''}"
                                      for t in listed))
    return 0


def cmd_call(args) -> int:
    from gst.mcp_client import Session, text_of

    try:
        arguments = json.loads(args.arguments)
    except ValueError as exc:
        raise GsError(f"arguments must be a JSON object: {exc}") from exc
    with Session(_config(args.name)) as session:
        result = session.call(args.tool, arguments)
    text = text_of(result)
    emit(result, args.json, (text if args.chars is None else text[:args.chars] + ("\n..." if len(text) > args.chars else "")))
    return 1 if result.get("isError") else 0
