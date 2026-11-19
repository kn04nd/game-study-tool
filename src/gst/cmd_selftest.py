"""gst selftest: is this installation healthy? The environment always; --tools runs the pipelines live on the
sample game (in GS_HOME/_selftest-sample); --games checks the real studies read-only."""

from __future__ import annotations

from gst.common import emit

ICON = {"ok": "ok  ", "warn": "warn", "skip": "skip", "fail": "FAIL"}


def register(sub, parent) -> None:
    p = sub.add_parser("selftest", parents=[parent], help="health check: environment, tools (live on the sample game), games",
                       description=__doc__)
    p.add_argument("--tools", action="store_true", help="tool states and live runs on the sample game (minutes)")
    p.add_argument("--games", help="comma-separated games to check read-only, e.g. oni,pz,drg (names, slugs or AppIDs)")
    p.set_defaults(func=cmd_selftest)


def cmd_selftest(args) -> int:
    import sys

    from gst import selftest

    report = selftest.Report()
    selftest.environment(report)
    if args.tools:
        sys.stderr.write("gst: live runs on the sample game take a few minutes...\n")
        selftest.tool_states(report)
        selftest.sample(report)
    if args.games:
        selftest.games(report, [g.strip() for g in args.games.split(",") if g.strip()])
    lines = [f"{ICON[r['status']]}  {r['group']:<7} {r['name']:<32} {r['seconds']:>6.1f} s  {r['detail'][:150]}"
             for r in report.rows]
    lines.append(f"{len(report.rows)} checks, {report.failed} failed")
    emit({"checks": report.rows, "failed": report.failed}, args.json, "\n".join(lines))
    return 1 if report.failed else 0
