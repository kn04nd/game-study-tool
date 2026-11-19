"""gst coverage: rewrite jobs/COVERAGE.md from jobs/jobs.db (discovered, extracted, failed, unsupported,
blocked per category, with reason and next step)."""

from __future__ import annotations

from gst import workspace
from gst.common import GsError, emit


def register(sub, parent) -> None:
    c = sub.add_parser("coverage", parents=[parent], help="jobs/COVERAGE.md: what gst study extracted, per category",
                       description=__doc__)
    c.add_argument("slug", nargs="?", help="workspace under GS_HOME (default: the current one)")
    c.set_defaults(func=cmd_coverage)


def cmd_coverage(args) -> int:
    from gst.pipeline import coverage
    from gst.pipeline.jobs import JobsDB, db_path

    ws, _game = workspace.current(slug=args.slug)
    if not db_path(ws).exists():
        raise GsError(f"no jobs yet in {ws} (gst study {ws.name})")
    with JobsDB(db_path(ws)) as db:
        result = coverage.build(ws, db)
    lines = [f"{k:<16} units {v['units']:>6}  done {v.get('done', 0):>6}  failed {v.get('failed', 0):>4}  "
             f"files out {v['files_out']}" for k, v in result["categories"].items()]
    emit(result, args.json, "\n".join(lines + [f"written: {result['file']}"]))
    return 0
