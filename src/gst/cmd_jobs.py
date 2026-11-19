"""gst jobs list|show|retry|reset: the jobs of `gst study` (jobs/jobs.db)."""

from __future__ import annotations

from gst import workspace
from gst.common import GsError, emit, human_size


def register(sub, parent) -> None:
    j = sub.add_parser("jobs", parents=[parent], help="jobs of gst study: list, show, retry, reset", description=__doc__)
    j.set_defaults(group_parser=j)
    s = j.add_subparsers(dest="action", metavar="<action>")
    ls = s.add_parser("list", parents=[parent], help="jobs with status, extractor, unit, duration")
    ls.add_argument("--status", help="pending|running|done|failed|unsupported|blocked-policy|paused-disk|skipped")
    ls.add_argument("--extractor", help="extractor name or prefix (code = code.*)")
    ls.add_argument("--slug", help="workspace under GS_HOME (default: the current one)")
    ls.set_defaults(func=cmd_list)
    sh = s.add_parser("show", parents=[parent], help="one job in full: inputs, outputs, the whole error")
    sh.add_argument("id", type=int)
    sh.add_argument("--slug")
    sh.set_defaults(func=cmd_show)
    rt = s.add_parser("retry", parents=[parent], help="put failed jobs (or the given ids) back to pending")
    rt.add_argument("ids", nargs="*", type=int)
    rt.add_argument("--extractor")
    rt.add_argument("--slug")
    rt.set_defaults(func=cmd_retry)
    rs = s.add_parser("reset", parents=[parent], help="put jobs back to pending (ids, --extractor or --status; --all for every job)")
    rs.add_argument("ids", nargs="*", type=int)
    rs.add_argument("--extractor")
    rs.add_argument("--status")
    rs.add_argument("--all", action="store_true")
    rs.add_argument("--slug")
    rs.set_defaults(func=cmd_reset)


def _db(slug: str | None):
    from gst.pipeline.jobs import JobsDB, db_path

    ws, _game = workspace.current(slug=slug)
    path = db_path(ws)
    if not path.exists():
        raise GsError(f"no jobs yet in {ws} (gst study {ws.name})")
    return ws, JobsDB(path)


def cmd_list(args) -> int:
    _ws, db = _db(args.slug)
    with db:
        rows = db.rows(status=args.status, extractor=args.extractor)
    for r in rows:
        r.pop("inputs", None)
    text = "\n".join(f"{r['id']:>6} {r['status']:<14} {r['extractor']:<18} {human_size(r['size'] or 0):>9} "
                     f"{r['duration_s'] if r['duration_s'] is not None else '-':>8}s  {r['unit_id']}" for r in rows)
    emit(rows, args.json, text or "no jobs match")
    return 0


def cmd_show(args) -> int:
    _ws, db = _db(args.slug)
    with db:
        row = db.get(args.id)
    if row is None:
        raise GsError(f"no job {args.id}")
    keys = ("id", "extractor", "unit_id", "status", "attempts", "category", "resource", "size", "buildid", "fingerprint",
            "started_at", "finished_at", "duration_s", "tool", "tool_version", "out_bytes")
    lines = [f"{k}: {row.get(k)}" for k in keys]
    lines += ["inputs:"] + [f"  {i[0]}  {i[1]} B  mtime_ns {i[2]}" for i in row.get("inputs") or []]
    lines += ["outputs:"] + [f"  {o}" for o in row.get("outputs") or []]
    lines += ["notes:"] + [f"  {n}" for n in row.get("notes") or []]
    if row.get("error"):
        lines += ["error:", row["error"]]
    emit(row, args.json, "\n".join(lines))
    return 0


def cmd_retry(args) -> int:
    _ws, db = _db(args.slug)
    with db:
        n = db.reset(ids=args.ids or None, extractor=args.extractor, status=None if args.ids else "failed")
    emit({"pending": n}, args.json, f"{n} jobs back to pending (gst study runs them)")
    return 0


def cmd_reset(args) -> int:
    if not (args.ids or args.extractor or args.status or args.all):
        raise GsError("name what to reset: job ids, --extractor, --status, or --all")
    _ws, db = _db(args.slug)
    with db:
        n = db.reset(ids=args.ids or None, extractor=args.extractor, status=args.status)
    emit({"pending": n}, args.json, f"{n} jobs back to pending (gst study runs them)")
    return 0
