"""gst - Game Study Toolkit command line.

Each command group is a module exposing ``register(subparsers, parent)``; heavy imports stay
inside the command functions so ``gst --help`` and the hooks start fast.
Dispatch pattern adapted from universal-modder's um/cli.py (MIT).
"""

from __future__ import annotations

import argparse
import importlib
import sys

from gst import __version__
from gst.common import GsError, setup_console

# Order = order shown in `gst --help`.
GROUPS = [
    "gst.cmd_workspace",
    "gst.cmd_recon",
    "gst.cmd_decompile",
    "gst.cmd_index",
    "gst.cmd_fmt",
    "gst.cmd_trace",
    "gst.cmd_gfx",
    "gst.cmd_study",
    "gst.cmd_analyze",
    "gst.cmd_jobs",
    "gst.cmd_coverage",
    "gst.cmd_mcp",
    "gst.cmd_selftest",
    "gst.cmd_assets",
    "gst.cmd_audio",
    "gst.cmd_maps",
    "gst.cmd_lib",
    "gst.doctor",
    "gst.cmd_tools",
    "gst.cmd_config",
    "gst.cmd_guard",
    "gst.cmd_setup",
]


def build_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--json", action="store_true", help="machine-readable JSON output")
    parser = argparse.ArgumentParser(
        prog="gst",
        description="game-study-tool (gst): study games you own through reverse engineering - extract everything, resumable.",
        epilog="Guardrails: plugins/gst/guardrails.md. Every group has --help.",
    )
    parser.add_argument("--version", action="version", version=f"gst {__version__}")
    sub = parser.add_subparsers(dest="group", metavar="<command>")
    for name in GROUPS:
        importlib.import_module(name).register(sub, parent)
    return parser


def command_names() -> set[str]:
    """Top-level commands of this toolkit version (docs and recon steps only name what exists)."""
    parser = build_parser()
    return set(next(a for a in parser._actions if a.dest == "group").choices)


def main(argv: list[str] | None = None) -> int:
    setup_console()
    args_list = sys.argv[1:] if argv is None else argv
    if len(args_list) == 2 and args_list[0] == "hook":
        # Claude Code hooks through gst.exe (run.sh's fallback): no parser, no command modules.
        # Then record hook.cfg so run.sh takes its direct-Python fast path next time.
        from gst.hooks import ensure_launcher_cfg, main as hook_main

        code = hook_main(args_list[1])
        try:
            ensure_launcher_cfg()
        except Exception:  # a hook never fails on bookkeeping
            pass
        return code
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        target = getattr(args, "group_parser", None) or parser
        target.print_help()
        return 0 if args.group is None else 2
    try:
        result = func(args)
    except GsError as exc:
        sys.stderr.write(f"gst: {exc}\n")
        return exc.code
    except KeyboardInterrupt:
        sys.stderr.write("gst: interrupted\n")
        return 130
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
