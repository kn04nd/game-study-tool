"""gst study / gst map / gst notes / gst report: extract everything, then from the code index to the final document.

- gst study: resolve/create the workspace, recon, plan every extractor of the engine, run them as resumable jobs.

- gst map: systems of the game from the code index (keywords + usage), notes/MAP.md with a dependency diagram.
- gst notes: new notes from a template, notes/INDEX.md (and GS_HOME/INDEX.md), search, broken citations.
- gst report: ARCHITECTURE.md status (sections 1-2 filled from recon) and --check, which must pass at the end.
"""

from __future__ import annotations

from gst import config, workspace
from gst.common import emit


def _csv(values) -> list[str]:
    return [v.strip() for item in (values or []) for v in str(item).split(",") if v.strip()]


def register(sub, parent) -> None:
    st = sub.add_parser("study", parents=[parent], help="extract everything from a game, as resumable jobs (jobs/, COVERAGE.md)",
                        description="Resolve or create the workspace (recon when missing or when the build changed: a new "
                                    "build gets a sibling workspace <slug>@<buildid>), plan every extractor of the engine, "
                                    "print the estimate and run. No limits on size, count or time; rerun to resume.")
    st.add_argument("target", help="install folder or workspace slug")
    st.add_argument("--assets", choices=("none", "basic", "normal", "full"),
                    help="how much of the assets to study (asked once per workspace, then kept in game.json): none; "
                         "basic = inventory, map list and overview analyses; normal = every export and asset analysis "
                         "except the map/level containers; full = everything. --dry-run shows the time of each")
    st.add_argument("--only", action="append", help="extractors, prefixes (code) or categories to run (comma-separated)")
    st.add_argument("--skip", action="append", help="extractors, prefixes or categories to leave out")
    st.add_argument("--jobs", type=int, help="parallel jobs (default: half the CPUs, at most 4; heavy tools run one at a time)")
    st.add_argument("--retry-failed", action="store_true", help="run failed jobs again")
    st.add_argument("--force", action="append", help="rerun these extractors even when done (comma-separated)")
    st.add_argument("--dry-run", action="store_true",
                    help="plan and estimate only; writes nothing (no workspace, sibling, jobs/plan.json or registry entry)")
    st.add_argument("--timeout", type=float, help="stop a job after N seconds (default: no limit; the watchdog only warns)")
    st.add_argument("--min-free", help="pause (never cut) when free disk drops below this, e.g. 20GB")
    st.add_argument("--opt", action="append", default=[], metavar="KEY=VALUE",
                    help="extractor option, e.g. dotnet.all=1, native.all=1, godot.include=*.tscn, unreal.modes=json")
    st.add_argument("--quiet", action="store_true", help="no progress lines on stderr")
    st.set_defaults(func=cmd_study)

    m = sub.add_parser("map", parents=[parent], help="map of the game's systems from the code index (notes/MAP.md)",
                       description=__doc__)
    m.add_argument("--list", action="store_true", help="print the systems of the last map as JSON (for agents)")
    m.set_defaults(func=cmd_map)

    n = sub.add_parser("notes", parents=[parent], help="study notes: new, templates, index, search, check", description=__doc__)
    n.set_defaults(group_parser=n)
    s = n.add_subparsers(dest="action", metavar="<action>")
    new = s.add_parser("new", parents=[parent], help="notes/<layer>/<topic>.md from the template (--template: pre-filled dossier)")
    new.add_argument("spec", help="<layer>/<topic>, e.g. logic/oxygen or code/save-system; with --template, <topic> alone works")
    new.add_argument("--title")
    new.add_argument("--template", help="note template pre-filled from analysis/*.json (list: gst notes templates)")
    new.add_argument("--system", help="regex: the system of analysis/systems.json the note is about (templates sistema, mecanica...)")
    new.add_argument("--match", help="regex on script/table names for the tunables table (template mecanica...)")
    new.add_argument("--games", help="comma-separated workspace slugs (template comparativo; default: every workspace)")
    new.add_argument("--facet", help="library facet for the comparativo table (rigs, joints, animation, audio, middleware, "
                                     "systems, shaders, dcc; default: the note topic)")
    new.set_defaults(func=cmd_notes_new)
    tp = s.add_parser("templates", parents=[parent], help="note templates for gst notes new --template")
    tp.set_defaults(func=cmd_notes_templates)
    ix = s.add_parser("index", parents=[parent], help="notes/INDEX.md and GS_HOME/INDEX.md")
    ix.set_defaults(func=cmd_notes_index)
    se = s.add_parser("search", parents=[parent], help="regex over notes, FACTS, STUDY and ARCHITECTURE")
    se.add_argument("pattern")
    se.set_defaults(func=cmd_notes_search)
    ck = s.add_parser("check", parents=[parent], help="path:line citations in the notes that point nowhere")
    ck.set_defaults(func=cmd_notes_check)

    r = sub.add_parser("report", parents=[parent], help="ARCHITECTURE.md: status, sections 1-2 from recon, --check",
                       description=__doc__)
    r.add_argument("--check", action="store_true", help="exit 1 unless the document holds (sections, systems, evidence)")
    r.add_argument("--min-systems", type=int, default=5)
    r.set_defaults(func=cmd_report)


def cmd_study(args) -> int:
    from gst.common import GsError
    from gst.pipeline import budget, plan, study

    options: dict = {"only": _csv(args.only), "skip": _csv(args.skip), "force": _csv(args.force),
                     "retry_failed": args.retry_failed, "assets": args.assets}
    for item in args.opt:
        key, sep, value = item.partition("=")
        if not sep:
            raise GsError(f"--opt takes KEY=VALUE, got {item!r}")
        options[key.strip()] = value.strip()
    min_free = budget.parse_size(args.min_free)
    from gst.pipeline import capacity

    try:
        result = study.run(args.target, options=options, dry_run=args.dry_run, jobs=args.jobs, timeout=args.timeout,
                           min_free=min_free, quiet=args.quiet or args.json)
    except study.AssetLevelNeeded as exc:
        emit({"workspace": str(exc.workspace), "error": "asset level needed", "levels": exc.estimates}, args.json, str(exc))
        return 2
    if args.dry_run:
        would = result.get("would_create") or {}
        head = f"workspace: {result['workspace']}"
        if would.get("create"):
            head += (" (does not exist: a real run creates it"
                     + (f" as a sibling of {would['sibling_of']}, new build" if would.get("sibling_of") else "") + ")")
        elif would.get("recon"):
            head += " (no recon yet: this plan used an in-memory recon; a real run writes it)"
        emit(result, args.json, head + "\n" + plan.render(result["plan"]) + "\n" + capacity.render_levels(result["levels"])
             + (f"\nchosen for this workspace: --assets {result['assets']}" if result.get("assets") else
                "\nno asset level chosen yet: a real run needs --assets <level>")
             + "\n(dry run: nothing ran, nothing was written)")
        return 0
    run = result["run"]
    totals = ", ".join(f"{k} {v}" for k, v in sorted(run["totals"].items()))
    text = (f"workspace: {result['workspace']}\nran {run['started']} jobs in {run['seconds']} s; jobs: {totals}\n"
            f"coverage: {result['coverage']}\nlog: {result['log']}")
    if run["paused"]:
        text += "\npaused: free disk below --min-free; free space and rerun gst study to resume"
    emit(result, args.json, text)
    return 1 if run["totals"].get("failed") else 0


def cmd_map(args) -> int:
    from gst import mapping

    ws, _game = workspace.current()
    if args.list:
        print(mapping.systems_json(ws))
        return 0
    result = mapping.build(ws)
    workspace.journal(ws, f"gst map: {len(result['systems'])} systems over {result['types']} types")
    lines = [f"{len(result['systems'])} systems over {result['types']} types ({result['unassigned']} unassigned), "
             f"{result['seconds']} s"]
    lines += [f"  {s['label']:<26} {s['types']:>5} types  " + ", ".join(t["name"] for t in s["top"][:4]) for s in result["systems"]]
    lines.append("written: notes/MAP.md, index/map.json")
    emit(result, args.json, "\n".join(lines))
    return 0


def cmd_notes_new(args) -> int:
    from gst import notes

    ws, game = workspace.current()
    options = {k: getattr(args, k) for k in ("system", "match", "games", "facet") if getattr(args, k, None)}
    path = notes.new(ws, args.spec, args.title, template=args.template, game=game, options=options)
    result = {"note": str(path), "template": args.template}
    text = f"note: {path}"
    if args.template:
        from gst import notetemplates

        result["unfilled"] = notetemplates.note_unfilled(path)
        text += f"\ntemplate {args.template}: {result['unfilled']} sections to fill (<!-- preencher -->)"
    emit(result, args.json, text)
    return 0


def cmd_notes_templates(args) -> int:
    from gst import notetemplates

    rows = notetemplates.read_template_list()
    emit(rows, args.json, "\n".join(f"{r['name']:<30} notes/{r['layer']}/  {r['title']}" for r in rows))
    return 0


def cmd_notes_index(args) -> int:
    from gst import notes

    ws, _game = workspace.current()
    out = notes.index(ws)
    root = notes.study_root_index(config.gs_home())
    emit({"notes": str(out), "studies": str(root)}, args.json, f"written: {out}\nwritten: {root}")
    return 0


def cmd_notes_search(args) -> int:
    from gst import notes

    ws, _game = workspace.current()
    hits = notes.search(ws, args.pattern)
    emit(hits, args.json, "\n".join(f"{h['path']}:{h['line']}  {h['text']}" for h in hits) or "no matches")
    return 0


def cmd_notes_check(args) -> int:
    from pathlib import Path

    from gst import notes
    from gst.common import read_text

    ws, game = workspace.current()
    install = Path(game["install_dir"]) if game.get("install_dir") else None
    from gst.notetemplates import unfilled

    broken, total, todo = [], 0, {}
    for path in notes.all_notes(ws):
        text = read_text(path)
        ok, bad = notes.check_citations(ws, text, install)
        total += len(ok) + len(bad)
        broken += [{**b, "note": path.relative_to(ws).as_posix()} for b in bad]
        if unfilled(text):
            todo[path.relative_to(ws).as_posix()] = unfilled(text)
    emit({"citations": total, "broken": broken, "unfilled": todo}, args.json,
         (f"{total} citations, {len(broken)} broken" + "".join(f"\n  {b['note']}: {b['cite']} ({b['why']})" for b in broken)
          + "".join(f"\n  {n}: {c} sections still to fill (<!-- preencher -->)" for n, c in todo.items())))
    return 1 if broken else 0


def cmd_report(args) -> int:
    from gst import report

    ws, game = workspace.current()
    filled = [] if args.check else report.autofill(ws, game)
    result = report.status(ws, game, min_systems=args.min_systems)
    lines = []
    if filled:
        lines.append("filled from recon: section " + ", ".join(str(n) for n in filled))
    lines.append(" ".join(f"{n}{'+' if s['filled'] else '-'}" for n, s in result["sections"].items()) + "   (+ written, - empty)")
    lines.append(f"systems: {len(result['systems'])} ({', '.join(result['systems']) or '-'}); citations {result['citations']} ok, "
                 f"{len(result['broken'])} broken; diagrams {result['diagrams']}")
    if result["problems"]:
        lines.append(f"{len(result['problems'])} problems:")
        lines += [f"  - {p}" for p in result["problems"][:30]]
    else:
        lines.append("ARCHITECTURE.md holds: every check passes")
    if args.check:
        workspace.journal(ws, f"gst report --check: {'ok' if result['ok'] else str(len(result['problems'])) + ' problems'}")
    emit(result, args.json, "\n".join(lines))
    return 0 if (result["ok"] or not args.check) else 1
