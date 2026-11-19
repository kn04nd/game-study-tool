"""gst new / status / migrate / workspace / refresh: create a study workspace, show where it stands, upgrade gs (v1) ones."""

from __future__ import annotations

import re
from pathlib import Path

from gst import workspace
from gst.common import emit, human_size, read_text


def register(sub, parent) -> None:
    p = sub.add_parser("new", parents=[parent], help="create the study workspace for a game (runs recon)",
                       description="Create GS_HOME/<slug> with templates, settings and recon for a game you own.")
    p.add_argument("game", help="name (fuzzy), Steam AppID or install folder")
    p.add_argument("--slug", help="workspace folder name (default: short slug of the game name)")
    p.add_argument("--deep", action="store_true", help="also run Detect It Easy on the main executables")
    p.set_defaults(func=cmd_new)

    s = sub.add_parser("status", parents=[parent], help="summary of the current study workspace")
    s.add_argument("slug", nargs="?", help="workspace under GS_HOME (default: the one around the cwd, else GST_GAME)")
    s.add_argument("--short", action="store_true", help="a few lines (used in skill context and hooks)")
    s.add_argument("--watch", action="store_true", help="follow jobs/STATUS.md while a gst study runs")
    s.set_defaults(func=cmd_status)

    m = sub.add_parser("migrate", parents=[parent], help="upgrade gs (v1) workspaces to gst v2 (additive; --dry-run first)",
                       description="Additive upgrade of gs workspaces: adds jobs/ and analysis/, imports the existing "
                                   "outputs (recon/, decomp/*, index/*, assets/*, dumps/*, traces/*) as done(legacy) jobs "
                                   "in jobs/jobs.db, writes jobs/COVERAGE.md and marks game.json (gst_workspace 2). "
                                   "Nothing is moved or deleted and the legacy gs keeps reading the workspace; a second "
                                   "run with nothing new writes nothing. Archives (.zip) and folders without game.json "
                                   "are ignored; empty <slug>@<buildid> siblings are reported and left alone.")
    m.add_argument("slug", nargs="?", help="workspace under GS_HOME (default: the current one)")
    m.add_argument("--all", action="store_true", help="every workspace under GS_HOME (including _selftest-*)")
    m.add_argument("--dry-run", action="store_true",
                   help="read-only: what would be imported, problems and status per workspace (writes nothing)")
    m.add_argument("--report", metavar="FILE", help="also write the result to FILE (Markdown; JSON when FILE ends in .json)")
    m.set_defaults(func=cmd_migrate)

    w = sub.add_parser("workspace", parents=[parent], help="list the workspaces under GS_HOME (version, engine, migration)",
                       description="Read-only list of the study workspaces under GS_HOME: gs v1 or gst v2, migrated or "
                                   "not, engine, build, install found, registry entry; and what is not a workspace.")
    w.set_defaults(func=cmd_workspace_list)

    r = sub.add_parser("refresh", parents=[parent], help="update a workspace's toolkit files after a toolkit update",
                       description="Merge the current toolkit settings into .claude/settings.json (your own rules stay) "
                                   "and replace .claude/rules/*. Study files are never touched.")
    r.add_argument("--all", action="store_true", help="every workspace in the registry, not just the current one")
    r.add_argument("--claude-md", action="store_true", help="also re-render CLAUDE.md from the template (backup: CLAUDE.md.bak)")
    r.set_defaults(func=cmd_refresh)


def cmd_new(args) -> int:
    from gst.recon import scan, stores

    game = stores.resolve_game(args.game)
    report = scan.run_recon(game, deep=args.deep)
    ws = workspace.create(game, report, args.slug)
    scan.write_outputs(report, ws / "recon")
    data = {"workspace": str(ws), "engine": report["engine"]["label"], "verdict": report["verdict"]}
    text = (f"workspace: {ws}\nengine:    {report['engine']['label']}\nverdict:   {report['verdict']} "
            f"({report['verdict_text']})\nrecon:     {ws / 'recon' / 'RECON.md'}\n\n"
            f"next: {workspace.TRUST_HINT}\n      then ask: analise este jogo")
    emit(data, args.json, text)
    return 0


def cmd_refresh(args) -> int:
    if args.all:
        from gst.guard import registry_games

        targets = [Path(g["workspace"]) for g in registry_games().values()
                   if g.get("workspace") and (Path(g["workspace"]) / "game.json").is_file()]
    else:
        targets = [workspace.current()[0]]
    results = {str(ws): workspace.refresh(ws, claude_md=args.claude_md) for ws in targets}
    text = "\n".join(f"{ws}: {', '.join(changed) or 'up to date'}" for ws, changed in results.items())
    emit(results, args.json, text or "no workspaces in the registry")
    return 0


def _count(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(1 for p in folder.rglob("*") if p.is_file())


def cmd_migrate(args) -> int:
    import time

    from gst import config
    from gst.common import write_json, write_text
    from gst.compat import legacy, migrate

    home = config.gs_home()
    ignored: list[dict] = []
    if args.all:
        targets, ignored = legacy.scan_home(home)
    else:
        targets = [workspace.current(slug=args.slug)[0]]
    plans = []
    for ws in targets:
        plan = legacy.plan_migration(ws, home)
        written = None if args.dry_run else migrate.apply(plan)
        plans.append({**plan.as_dict(), **({"written": written} if written is not None else {})})
    totals: dict[str, int] = {}
    for p in plans:
        totals[p["status"]] = totals.get(p["status"], 0) + 1
    result = {"dry_run": bool(args.dry_run), "home": str(home), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "plans": plans, "ignored": ignored, "totals": totals}
    if args.report:
        out = Path(args.report)
        result["report"] = str(out)
        if out.suffix.lower() == ".json":
            write_json(out, result)
        else:
            write_text(out, migrate.report_markdown(result))
    emit(result, args.json, _migrate_text(result))
    return 1 if totals.get("error") else 0


def _migrate_text(result: dict) -> str:
    lines = []
    verb = "would import" if result["dry_run"] else "imported"
    for p in result["plans"]:
        head = f"{p['slug']:<22} v{p['version']} {str(p['engine'] or '?'):<13} {p['status']}"
        imp = [u for u in p["units"] if u["action"] != "kept"]
        if p["status"] in ("would-migrate", "migrated"):
            parts = []
            if p["create_dirs"]:
                parts.append("create " + ", ".join(d + "/" for d in p["create_dirs"]))
            if imp:
                parts.append(f"{verb} {len(imp)} outputs ({p['import_files']} files, {human_size(p['import_bytes'])}) "
                             "as done(legacy)")
            if p["mark_game_json"]:
                parts.append("mark game.json v2")
            head += ": " + "; ".join(parts)
        lines.append(head)
        if result["dry_run"]:
            lines += [f"    {u['action']:<7} {u['relpath']:<40} {u['category']:<15} {u['files']:>7} files "
                      f"{human_size(u['bytes']):>9}" for u in imp]
        lines += [f"    note: {n}" for n in p["notes"]]
        lines += [f"    problem: {pr}" for pr in p["problems"]]
    if result["ignored"]:
        lines.append("ignored: " + "; ".join(f"{i['name']}: {i['reason']}" for i in result["ignored"]))
    totals = ", ".join(f"{v} {k}" for k, v in sorted(result["totals"].items()))
    lines.append(f"{len(result['plans'])} workspaces under {result['home']}: {totals or 'none'}"
                 + ("  (dry run: nothing written)" if result["dry_run"] else ""))
    if result.get("report"):
        lines.append(f"report: {result['report']}")
    return "\n".join(lines)


def cmd_workspace_list(args) -> int:
    from gst import config
    from gst.compat import legacy

    home = config.gs_home()
    targets, ignored = legacy.scan_home(home)
    registry = legacy.registry_slugs(home)
    rows = []
    for ws in targets:
        found = legacy.open_workspace(ws)
        if found is None:
            rows.append({"folder": ws.name, "path": str(ws), "error": "game.json is not a valid gs/gst marker"})
            continue
        game = found.game
        install = game.get("install_dir")
        rows.append({
            "folder": ws.name, "path": str(ws), "slug": found.slug, "name": game.get("name"), "version": found.version,
            "migrated": "gst_migrated" in game, "engine": (game.get("engine") or {}).get("key"),
            "buildid": game.get("buildid"), "phase": game.get("phase"),
            "install_found": bool(install) and Path(install).is_dir(),
            "registry": ws.name in registry, "stray_of": legacy.stray_of(ws, game, home),
            "jobs_db": (ws / "jobs" / "jobs.db").is_file(),
        })
    lines = [f"{'folder':<22} {'ver':<4} {'engine':<13} {'build':<10} {'install':<8} {'registry':<8} notes"]
    for r in rows:
        if r.get("error"):
            lines.append(f"{r['folder']:<22} ERROR {r['error']}")
            continue
        notes = []
        if r["stray_of"]:
            notes.append(f"empty sibling of {r['stray_of']} (gst migrate --dry-run explains)")
        elif r["version"] < 2:
            notes.append("not migrated (gst migrate)")
        ver = f"v{r['version']}" + ("m" if r["migrated"] else "")
        lines.append(f"{r['folder']:<22} {ver:<4} {str(r['engine'] or '?'):<13} {str(r['buildid'] or '-')[:10]:<10} "
                     f"{'yes' if r['install_found'] else 'MISSING':<8} {'yes' if r['registry'] else 'no':<8} "
                     + "; ".join(notes))
    if ignored:
        lines.append("not workspaces: " + "; ".join(f"{i['name']}: {i['reason']}" for i in ignored))
    lines.append(f"{len(rows)} workspaces under {home} (v2m = migrated from gs v1)")
    emit({"home": str(home), "workspaces": rows, "ignored": ignored}, args.json, "\n".join(lines))
    return 0


def cmd_status(args) -> int:
    if getattr(args, "watch", False):
        from gst.pipeline.progress import watch

        ws, _game = workspace.current(slug=args.slug)
        watch(ws)
        return 0
    ws, game = workspace.current(required=not args.short or bool(args.slug), slug=args.slug)
    if ws is None:  # --short feeds skill context (a failing command aborts the skill): never fail
        from gst import config

        emit({"workspace": None, "gs_home": str(config.gs_home())}, args.json,
             f"no study workspace here; studies live in {config.gs_home()} (gst new <game> creates one)")
        return 0
    facts = ws / "FACTS.md"
    fact_rows = sum(1 for line in read_text(facts).splitlines() if line.startswith("|")) - 2 if facts.exists() else 0
    study = read_text(ws / "STUDY.md") if (ws / "STUDY.md").exists() else ""
    sessions = re.findall(r"^### (.+)$", study, re.M)
    data = {
        "workspace": str(ws), "name": game.get("name"), "engine": game.get("engine", {}).get("label"),
        "engine_version": game.get("engine", {}).get("version"), "verdict": game.get("verdict"),
        "phase": game.get("phase"), "facts": max(fact_rows, 0),
        "files": {d: _count(ws / d) for d in ("decomp", "assets", "dumps", "traces", "notes")},
        "last_session": sessions[-1] if sessions else None,
        "architecture_bytes": (ws / "ARCHITECTURE.md").stat().st_size if (ws / "ARCHITECTURE.md").exists() else 0,
        "claude_trusted": workspace.claude_trusted(ws),
    }
    if args.short:
        text = (f"{data['name']} | {data['engine']} {data['engine_version'] or ''} | verdict {data['verdict']} | "
                f"phase {data['phase']} | facts {data['facts']} | notes {data['files']['notes']}").replace("  ", " ")
    else:
        text = "\n".join([
            f"{data['name']}  ({ws})",
            f"  engine:  {data['engine']} {data['engine_version'] or ''}",
            f"  verdict: {data['verdict']}   phase: {data['phase']}   facts: {data['facts']}",
            "  files:   " + ", ".join(f"{k} {v}" for k, v in data["files"].items()),
            f"  last session: {data['last_session']}   ARCHITECTURE.md: {human_size(data['architecture_bytes'])}",
        ] + ([f"  Claude Code: {workspace.TRUST_HINT}"] if data["claude_trusted"] is False else []))
    emit(data, args.json, text)
    return 0
