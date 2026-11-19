"""gst recon: list installed games, fingerprint one (engine, protections, verdict) or the whole library."""

from __future__ import annotations

import sys

from gst import config, guard, workspace
from gst.common import GsError, emit, slugify


def register(sub, parent) -> None:
    p = sub.add_parser("recon", parents=[parent], help="engine, runtime, protections and verdict of a game",
                       description=__doc__)
    p.add_argument("game", nargs="?", help="name, AppID or folder (default: the current workspace)")
    p.add_argument("--list", action="store_true", help="list installed games (Steam, Epic, GOG, Xbox)")
    p.add_argument("--library", action="store_true", help="recon every installed game into the registry")
    p.add_argument("--deep", action="store_true", help="also run Detect It Easy on the main executables")
    p.set_defaults(func=cmd_recon)


def cmd_recon(args) -> int:
    from gst.recon import scan, stores

    if args.list:
        games = stores.all_games()
        rows = sorted(games, key=lambda g: (g["name"] or "").lower())
        text = "\n".join(f"{g['store']:<6} {str(g.get('appid') or ''):>8}  {g['name']}  ->  {g['path']}" for g in rows)
        emit(rows, args.json, text or "no installs found; pass a folder: gst recon <path>")
        return 0
    if args.library:
        return _library(args)
    if args.game:
        game = stores.resolve_game(args.game)
        ws = None
        entry = guard.registry_games().get(slugify(game.get("name") or ""), {})
        if entry.get("workspace"):
            ws = workspace.Path(entry["workspace"])
    else:
        ws, meta = workspace.current()
        game = {"name": meta["name"], "store": meta.get("store"), "appid": meta.get("appid"),
                "buildid": meta.get("buildid"), "path": meta["install_dir"]}
    report = scan.run_recon(game, deep=args.deep)
    if ws:
        scan.write_outputs(report, ws / "recon")
        meta = workspace.load(ws)
        meta.update(engine=workspace.engine_meta(report), verdict=report["verdict"], protections=report["protections"],
                    protected_modules=report["protected_modules"], recon_at=report["scanned_at"],
                    middleware=workspace.middleware_meta(report))
        workspace.save(ws, meta)
        workspace.journal(ws, f"`gst recon`: {report['engine']['label']}, verdict `{report['verdict']}`")
        guard.save_registry_entry(meta["slug"], workspace.registry_entry(meta, report, ws))
    else:
        slug = slugify(game.get("name") or "")
        guard.save_registry_entry(slug, workspace.registry_entry({**game, "install_dir": game["path"]}, report, None))
    emit(report, args.json, scan.render_markdown(report))
    return 0


def _library(args) -> int:
    from gst.recon import scan, stores

    games = stores.all_games()
    if not games:
        raise GsError("no installed games found")
    rows = []
    for i, game in enumerate(sorted(games, key=lambda g: (g["name"] or "").lower()), 1):
        sys.stderr.write(f"gst: [{i}/{len(games)}] {game['name']}\n")
        try:
            report = scan.run_recon(game, deep=args.deep)
        except (OSError, GsError) as exc:
            rows.append({"name": game["name"], "error": str(exc)})
            continue
        slug = slugify(game.get("name") or "")
        existing = guard.registry_games().get(slug, {})
        entry = workspace.registry_entry({**game, "install_dir": game["path"], "keys": existing.get("keys", [])}, report, None)
        if existing.get("workspace"):
            entry["workspace"] = existing["workspace"]
            entry["sandbox_dir"] = existing.get("sandbox_dir")
        guard.save_registry_entry(slug, entry)
        rows.append({"name": game["name"], "appid": game.get("appid"), "engine": report["engine"]["label"],
                     "verdict": report["verdict"],
                     "protections": sorted({f"{p['kind']}:{p['name']}" for p in report["protections"]})})
    lines = [f"{'game':<38} {'engine':<34} verdict"]
    for r in rows:
        if "error" in r:
            lines.append(f"{r['name'][:38]:<38} ERROR {r['error']}")
        else:
            lines.append(f"{r['name'][:38]:<38} {r['engine'][:34]:<34} {r['verdict']}")
    lines.append(f"\nregistry: {config.gs_home() / 'registry.json'}")
    emit(rows, args.json, "\n".join(lines))
    return 0
