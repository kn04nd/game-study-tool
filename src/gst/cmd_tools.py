"""gst tools: list, install and locate third-party tools from the pinned catalog."""

from __future__ import annotations

from gst import tools
from gst.common import GsError, emit

PROFILES = ["core", "unity", "unreal", "jvm-lua-py", "indie", "native", "runtime", "graphics", "formats", "aaa", "all"]


def register(sub, parent) -> None:
    p = sub.add_parser("tools", parents=[parent], help="list, install and locate third-party tools",
                       description=__doc__)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")

    ls = s.add_parser("list", parents=[parent], help="catalog with install state")
    ls.add_argument("--profile", choices=PROFILES, default="all")
    ls.set_defaults(func=cmd_list)

    ins = s.add_parser("install", parents=[parent], help="install tools or whole profiles (e.g. core)")
    ins.add_argument("targets", nargs="+", help="tool ids and/or profile names")
    ins.add_argument("--force", action="store_true", help="reinstall even if present")
    ins.set_defaults(func=cmd_install)

    path = s.add_parser("path", parents=[parent], help="absolute path of a tool executable")
    path.add_argument("tool")
    path.add_argument("--bin", help="a specific executable of the tool")
    path.set_defaults(func=cmd_path)

    info = s.add_parser("info", parents=[parent], help="catalog entry of a tool")
    info.add_argument("tool")
    info.set_defaults(func=cmd_info)


def cmd_list(args) -> int:
    rows = [tools.status(t) for t in tools.profile_tools([args.profile])]
    if args.json:
        emit(rows, True)
        return 0
    lines = [f"{'tool':<14} {'state':<14} {'version':<16} {'wanted':<14} {'kind':<11} profiles"]
    for r in rows:
        lines.append(f"{r['id']:<14} {r['state']:<14} {str(r['version'] or '-'):<16} {r['wanted'] or '-':<14} "
                     f"{r['kind']:<11} {','.join(r['profiles'])}")
    emit(rows, False, "\n".join(lines))
    return 0


def _expand_targets(targets: list[str]) -> list[tools.Tool]:
    catalog = tools.load_catalog()
    chosen: dict[str, tools.Tool] = {}
    for target in targets:
        if target in catalog:
            chosen[target] = catalog[target]
        elif target in PROFILES:
            for t in tools.profile_tools([target]):
                chosen.setdefault(t.id, t)
        else:
            raise GsError(f"unknown tool or profile '{target}'")
    return list(chosen.values())


def cmd_install(args) -> int:
    from gst.tools.installer import install

    results, failures = [], []
    for tool in _expand_targets(args.targets):
        try:
            results.append(install(tool, force=args.force))
        except GsError as exc:
            failures.append({"id": tool.id, "error": str(exc)})
    if args.json:
        emit({"results": results, "failures": failures}, True)
    else:
        for r in results:
            extra = ""
            if r.get("action") == "guided":
                extra = "\n    " + "\n    ".join(r.get("steps", []))
            print(f"{r['id']:<14} {r.get('action', '?'):<10} {r['state']:<14} {r.get('path') or ''}{extra}")
        for f in failures:
            print(f"{f['id']:<14} FAILED     {f['error']}")
    return 1 if failures else 0


def cmd_path(args) -> int:
    tool = tools.get(args.tool)
    path = tools.bin_path(tool, args.bin)
    if path is None:
        raise GsError(f"{tool.id} is not installed (gst tools install {tool.id})", code=1)
    emit({"tool": tool.id, "path": str(path)}, args.json, str(path))
    return 0


def cmd_info(args) -> int:
    tool = tools.get(args.tool)
    data = {k: v for k, v in tool.__dict__.items() if v not in ("", [], None)}
    data["status"] = tools.status(tool)
    emit(data, args.json)
    return 0
