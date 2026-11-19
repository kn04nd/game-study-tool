"""gst guard: ask the policy whether a write or a command is allowed (same logic as the hooks)."""

from __future__ import annotations

from gst import guard
from gst.common import emit


def register(sub, parent) -> None:
    p = sub.add_parser("guard", parents=[parent], help="check a path or command against the guardrails",
                       description=__doc__)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")
    path = s.add_parser("path", parents=[parent], help="may a file be written here?")
    path.add_argument("path")
    path.add_argument("--content-file", help="file whose text would be written (decompiled-code check)")
    path.set_defaults(func=cmd_path)
    cmd = s.add_parser("cmd", parents=[parent], help="may this shell command run?")
    cmd.add_argument("command", help="the full command line, quoted")
    cmd.add_argument("--agent", help="check as this plugin agent would run it (e.g. recon, read-only)")
    cmd.set_defaults(func=cmd_cmd)


def _report(decision, args) -> int:
    data = {"allowed": decision.allowed, "ask": decision.ask, "reason": decision.reason, "rule": decision.rule}
    if decision.allowed:
        text = "allowed"
    else:
        rule = f" [guardrails rule {decision.rule}]" if decision.rule else ""
        text = f"{'ask' if decision.ask else 'denied'}: {decision.reason}{rule}"
    emit(data, args.json, text)
    return 0 if decision.allowed else 3


def cmd_path(args) -> int:
    content = None
    if args.content_file:
        from gst.common import read_text

        content = read_text(args.content_file)
    return _report(guard.check_write(args.path, content), args)


def cmd_cmd(args) -> int:
    return _report(guard.check_command(args.command, agent_type=args.agent), args)
