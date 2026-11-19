"""gst config: show and change gst settings (GS_HOME, tools dir, online metadata...)."""

from __future__ import annotations

from gst import config
from gst.common import GsError, emit


def register(sub, parent) -> None:
    p = sub.add_parser("config", parents=[parent], help="show or change gst settings",
                       description=__doc__)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")
    show = s.add_parser("show", parents=[parent], help="effective settings and where they come from")
    show.set_defaults(func=cmd_show)
    setp = s.add_parser("set", parents=[parent], help="persist a setting in config.toml")
    setp.add_argument("key", choices=sorted(config.KEYS))
    setp.add_argument("value")
    setp.set_defaults(func=cmd_set)
    unset = s.add_parser("unset", parents=[parent], help="remove a persisted setting")
    unset.add_argument("key", choices=sorted(config.KEYS))
    unset.set_defaults(func=cmd_unset)


def cmd_show(args) -> int:
    stored = config.load_config()
    data = {
        "config_file": str(config.effective_config_path()),
        "own_config_file": str(config.config_path()),
        "legacy_tools_dir": str(config.legacy_tools_dir() or ""),
        "gs_home": str(config.gs_home()),
        "tools_dir": str(config.tools_dir()),
        "state_dir": str(config.state_dir()),
        "online_metadata": config.online_allowed(),
        "focus": config.focus(),
        "stored": {k: ("***" if "token" in k else v) for k, v in stored.items()},
    }
    emit(data, args.json)
    return 0


def cmd_set(args) -> int:
    values = config.load_config()
    if args.key == "gs_home":
        from gst.guard import git_root

        root = git_root(args.value)
        if root is not None:
            raise GsError(f"GS_HOME must be outside any git repository (found {root})")
    values[args.key] = args.value
    path = config.save_config(values)
    emit({"saved": args.key, "file": str(path)}, args.json, f"saved {args.key} in {path}")
    return 0


def cmd_unset(args) -> int:
    values = config.load_config()
    values.pop(args.key, None)
    config.save_config(values)
    emit({"removed": args.key}, args.json, f"removed {args.key}")
    return 0
