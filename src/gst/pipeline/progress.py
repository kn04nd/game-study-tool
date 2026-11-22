"""logs/study-<ts>.jsonl (pipeline events) and jobs/STATUS.md (readable progress)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from gst.common import human_size, write_text


class Progress:
    def __init__(self, ws: Path, *, quiet: bool = False):
        self.ws = ws
        self.quiet = quiet
        self.started = time.time()
        self.log_path = ws / "logs" / f"study-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.log_path, "a", encoding="utf-8", newline="\n")

    def event(self, kind: str, **data) -> None:
        row = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": kind, **data}
        self._fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def say(self, text: str) -> None:
        if not self.quiet:
            sys.stderr.write(f"gst: {text}\n")
            sys.stderr.flush()

    def warn(self, text: str, **data) -> None:
        self.event("warning", message=text, **data)
        self.say(f"warning: {text}")

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass


def _hm(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 90:
        return f"{seconds} s"
    h, m = divmod(seconds // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def track_rows(db, planned: set | None, running: list[dict], slots: int) -> list[dict]:
    """Per track (``levels.TRACKS``): jobs of the plan by status, the running extractors and a rough time left
    (``capacity.Model`` on this workspace's history, spread over the slots)."""
    from gst.pipeline import capacity, levels

    cols = "id, extractor, unit_id, category, resource, stage, seq, size, status, duration_s, started_at"
    rows = [dict(r) for r in db.con.execute(f"SELECT {cols} FROM jobs")]
    for r in rows:
        r["size"] = r.get("size") or 0
    model = capacity.Model(rows, [])
    run_ids = {r["id"] for r in running}
    out = []
    for track in levels.TRACKS:
        mine = [r for r in rows if levels.track_of(r["extractor"]) == track
                and (planned is None or (r["extractor"], r["unit_id"]) in planned or r["id"] in run_ids)]
        if not mine:
            continue
        counts: dict[str, int] = {}
        for r in mine:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        left_s, left_n = 0.0, 0
        for r in mine:
            if r["status"] == "pending":
                left_s += model.estimate(r)[0]
                left_n += 1
            elif r["id"] in run_ids:
                left_s += max(model.estimate(r)[0] - capacity._elapsed(r.get("started_at")), 0.0)
                left_n += 1
        busy = sorted({r["extractor"] for r in running if levels.track_of(r["extractor"]) == track})
        finished = sum(n for s, n in counts.items() if s not in ("pending", "running", "paused-disk"))
        out.append({"track": track, "total": len(mine), "finished": finished, "counts": counts, "running": busy,
                    "left_s": round(left_s / max(1, min(slots, left_n or 1)))})
    return out


def track_line(tracks: list[dict]) -> str:
    parts = []
    for t in tracks:
        text = f"{t['track']} {t['finished']}/{t['total']}"
        if t["counts"].get("failed"):
            text += f" ({t['counts']['failed']} failed)"
        if t["finished"] < t["total"]:
            text += f", ~{_hm(t['left_s'])} left"
            if t["running"]:
                text += " (" + ", ".join(t["running"][:3]) + ")"
        else:
            text += " finished"
        parts.append(text)
    return " | ".join(parts)


def status_markdown(ws: Path, db, *, running: list[dict] | None = None, started: float | None = None,
                    tracks: list[dict] | None = None) -> str:
    counts = db.counts()
    totals: dict[str, int] = {}
    for per in counts.values():
        for status, n in per.items():
            totals[status] = totals.get(status, 0) + n
    all_jobs = sum(totals.values())
    finished = sum(totals.get(s, 0) for s in ("done", "failed", "unsupported", "blocked-policy", "skipped"))
    lines = ["# Study status", "", f"- Updated: {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    if started:
        lines.append(f"- Running for: {int(time.time() - started)} s")
    lines += [f"- Jobs: {finished}/{all_jobs} finished; " + ", ".join(f"{s} {n}" for s, n in sorted(totals.items())), ""]
    if tracks:
        lines += ["## By track (this plan)", "", "| Track | Finished | Failed | Running now | Time left (rough) |",
                  "| --- | --- | --- | --- | --- |"]
        lines += [f"| {t['track']} | {t['finished']}/{t['total']} | {t['counts'].get('failed', 0)} | "
                  f"{', '.join(t['running']) or '-'} | {'-' if t['finished'] >= t['total'] else '~' + _hm(t['left_s'])} |"
                  for t in tracks]
        lines.append("")
    if running:
        lines += ["## Running now", "", "| Job | Extractor | Unit | Input | For |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {r['id']} | {r['extractor']} | `{r['unit_id']}` | {human_size(r.get('size') or 0)} | "
                  f"{int(time.time() - r.get('t0', time.time()))} s |" for r in running]
        lines.append("")
    statuses = ["pending", "running", "done", "failed", "unsupported", "blocked-policy", "paused-disk", "skipped"]
    lines += ["## By extractor", "", "| Extractor | " + " | ".join(statuses) + " |", "| --- |" + " --- |" * len(statuses)]
    for name in sorted(counts):
        lines.append(f"| {name} | " + " | ".join(str(counts[name].get(s, 0) or "") for s in statuses) + " |")
    lines += ["", "Details: `gst jobs list`, `gst jobs show <id>`; coverage: `jobs/COVERAGE.md` (`gst coverage`)."]
    return "\n".join(lines) + "\n"


def write_status(ws: Path, db, **kw) -> Path:
    return write_text(ws / "jobs" / "STATUS.md", status_markdown(ws, db, **kw))


def watch(ws: Path, *, interval: float = 2.0, once: bool = False) -> None:
    """Print jobs/STATUS.md whenever it changes, until no study holds the workspace lock (Ctrl+C stops)."""
    from gst.pipeline.jobs import StudyLock

    status = ws / "jobs" / "STATUS.md"
    last = None
    lock = StudyLock(ws)
    while True:
        text = status.read_text(encoding="utf-8") if status.exists() else "no jobs/STATUS.md yet (gst study)\n"
        if text != last:
            sys.stdout.write(("\x1b[2J\x1b[H" if sys.stdout.isatty() else "") + text)
            sys.stdout.flush()
            last = text
        if once or not lock.holder():
            return
        time.sleep(interval)
