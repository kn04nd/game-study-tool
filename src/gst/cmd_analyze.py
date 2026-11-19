"""gst analyze: analyses over what a study extracted -> analysis/<name>.json (full data) + analysis/<name>.md (tables).

    gst analyze --list                      registered analyses, engines, requirements (and whether they hold here)
    gst analyze prefabs [--marker EnemyParent]
    gst analyze mono_values --script CosmeticAsset --script EnemyParent
    gst analyze --all [--game slug]         every analysis that applies to the game, in dependency order

Options reach an analysis as ``--opt key=value`` (repeatable) or the shared flags below; ``gst study`` runs
the same analyses as jobs (stage 60) with ``--opt <name>.<key>=<value>``.
"""

from __future__ import annotations

from gst import workspace
from gst.common import GsError, emit


def register(sub, parent) -> None:
    a = sub.add_parser("analyze", parents=[parent], help="analyses over the extracted data: analysis/<name>.json + .md",
                       description=__doc__)
    a.add_argument("name", nargs="?", help="analysis to run (see --list)")
    a.add_argument("--all", action="store_true", help="every analysis that applies to the game, in dependency order")
    a.add_argument("--list", action="store_true", help="list the analyses and their requirements")
    a.add_argument("--game", help="workspace slug under GS_HOME (default: the current workspace)")
    a.add_argument("--opt", action="append", default=[], metavar="KEY=VALUE", help="analysis option (repeatable)")
    a.add_argument("--script", action="append", help="MonoBehaviour class name(s) (mono_values; repeatable or comma-separated)")
    a.add_argument("--marker", action="append", help="MonoBehaviour class name(s) that mark a prefab root (prefabs)")
    a.add_argument("--file", help="regex on install relpaths of the Unity files to read")
    a.add_argument("--name-filter", dest="name_filter", help="regex on object/prefab names")
    a.set_defaults(func=cmd_analyze)


def _options(args) -> dict:
    options: dict = {}
    for item in args.opt:
        key, sep, value = item.partition("=")
        if not sep:
            raise GsError(f"--opt takes KEY=VALUE, got {item!r}")
        options[key.strip()] = value.strip()
    for key in ("script", "marker"):
        if getattr(args, key):
            options[key] = getattr(args, key)
    if args.file:
        options["file"] = args.file
    if args.name_filter:
        options["name"] = args.name_filter
    return options


def cmd_analyze(args) -> int:
    from gst import analysis

    if args.list:
        ws, game = workspace.current(required=False, slug=args.game)
        engine = ((game or {}).get("engine") or {}).get("key")
        rows = []
        for spec in analysis.ordered(list(analysis.all_specs().values())):
            row = {"name": spec.name, "description": spec.description, "engines": list(spec.engines),
                   "needs": list(spec.needs), "uses": list(spec.uses), "options": spec.options, "version": spec.version,
                   "module": spec.module}
            if ws is not None:
                row["applies"] = spec.applies_to(engine)
                row["missing"] = [n for n in spec.needs if not analysis.check_need(ws, game, n)[0]]
                row["done"] = (ws / analysis.ANALYSIS_DIR / f"{spec.name}.json").is_file()
            rows.append(row)
        lines = []
        for r in rows:
            state = ""
            if "applies" in r:
                state = ("n/a  " if not r["applies"] else "ready" if not r["missing"] else "needs") + ("*" if r.get("done") else " ")
            lines.append(f"{state:<7}{r['name']:<16} {r['description']}"
                         + (f"  [needs {', '.join(r['needs'])}]" if r["needs"] else "")
                         + (f"  missing: {', '.join(r['missing'])}" if r.get("missing") else ""))
        if ws is not None:
            lines.append(f"(workspace {ws.name}, engine {engine}; * = analysis/<name>.json exists)")
        emit(rows, args.json, "\n".join(lines) or "no analyses registered")
        return 0
    if not args.name and not args.all:
        raise GsError("name an analysis, or --all, or --list")
    ws, game = workspace.current(slug=args.game)
    options = _options(args)
    say = (lambda msg: None) if args.json else (lambda msg: __import__("sys").stderr.write(f"gst: {msg}\n"))
    if args.all:
        results = analysis.run_all(ws, game, options, log=say)
    else:
        results = [analysis.run(ws, game, args.name, options, log=say)]
    lines = []
    for r in results:
        if r["status"] == "done":
            summ = ", ".join(f"{k} {v}" for k, v in (r.get("summary") or {}).items())
            lines.append(f"{r['name']}: done in {r['seconds']} s -> {r['json']}, {r['md']}" + (f" ({summ})" if summ else ""))
        else:
            reason = (r.get("reason") or "").strip()
            lines.append(f"{r['name']}: {r['status']}: " + (reason if r["status"] != "failed" else reason.splitlines()[-1]))
    done = [r["name"] for r in results if r["status"] == "done"]
    if done:
        workspace.journal(ws, "gst analyze: " + ", ".join(done))
    emit(results if args.all else results[0], args.json, "\n".join(lines))
    return 1 if any(r["status"] == "failed" for r in results) else 0
