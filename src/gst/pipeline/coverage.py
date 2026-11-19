"""jobs/COVERAGE.md per category: discovered, extracted, failed, unsupported, blocked, each with reason and
next step.

Extractors that produce several categories in one job (GDRE recovering a whole Godot project) report
the files per category in ``Result.notes`` as ``count <category> <n>`` lines; they show up as extra
"files out" in that category.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from gst.common import human_size, write_text
from gst.extractors.base import CATEGORIES

LABELS = {
    "code": "Code", "texture": "Textures", "mesh": "Meshes", "audio": "Audio", "animation": "Animations",
    "prefab_scene": "Prefabs / scenes", "material_shader": "Materials / shaders", "data": "Data / ScriptableObjects",
    "localization": "Localization", "font": "Fonts", "video": "Video", "map": "Maps / levels",
    "config_save": "Configs / saves", "other": "Other",
}
NEXT = {
    "failed": "read the error (`gst jobs show <id>`), fix or install the tool, then `gst study --retry-failed`",
    "unsupported": "no backend for this format/version yet: `gst fmt id` on a sample; a reader in notes/formats/",
    "blocked-policy": "guardrails: only a community-published key (with source URL) or an `offline-ok` sandbox opens it",
    "paused-disk": "free disk space (or lower --min-free), then `gst study` resumes these",
    "pending": "`gst study` runs them (resumes where it stopped)",
    "running": "in progress (or the study died: the next `gst study` puts them back to pending)",
    "skipped": "skipped on purpose (see the reason); `gst study --force <extractor>` with the option to include them",
}
COUNT_NOTE = re.compile(r"^count (\w+) (\d+)$")


def first_line(text: str | None) -> str:
    """Reason column: the first non-empty line of the (full, stored) error or note."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return "-"


def build(ws: Path, db) -> dict:
    rows = db.rows()
    cats: dict[str, dict] = {c: {"units": 0, "in_bytes": 0, "out_bytes": 0, "status": {}, "files_out": 0, "issues": [],
                                 "extractors": set()} for c in CATEGORIES}
    for r in rows:
        c = cats.setdefault(r.get("category") or "other", {"units": 0, "in_bytes": 0, "out_bytes": 0, "status": {},
                                                             "files_out": 0, "issues": [], "extractors": set()})
        c["units"] += 1
        c["in_bytes"] += r.get("size") or 0
        c["out_bytes"] += r.get("out_bytes") or 0
        c["status"][r["status"]] = c["status"].get(r["status"], 0) + 1
        c["extractors"].add(r["extractor"])
        if r["status"] == "done":
            meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
            legacy_files = meta.get("files") if meta.get("legacy") else None  # gst migrate: one folder = N files
            c["files_out"] += legacy_files if isinstance(legacy_files, int) else len(r.get("outputs") or [])
        for note in r.get("notes") or []:
            m = COUNT_NOTE.match(str(note))
            if m and r["status"] == "done":
                other = cats.setdefault(m.group(1), {"units": 0, "in_bytes": 0, "out_bytes": 0, "status": {}, "files_out": 0,
                                                     "issues": [], "extractors": set()})
                other["files_out"] += int(m.group(2))
                other["extractors"].add(r["extractor"])
        if r["status"] not in ("done",):
            reason = first_line(r.get("error")) if r.get("error") else first_line("\n".join(map(str, r.get("notes") or [])))
            c["issues"].append({"id": r["id"], "extractor": r["extractor"], "unit": r["unit_id"], "status": r["status"],
                                "reason": reason})
    lines = ["# Coverage", "", f"- Updated: {time.strftime('%Y-%m-%d %H:%M:%S')} (`gst coverage` rewrites this file from jobs/jobs.db)",
             f"- Jobs: {len(rows)}; units are files, assemblies, containers or folders, depending on the extractor.", "",
             "| Category | Units | Input | Done | Failed | Unsupported | Blocked | Skipped | Pending | Files out | Output |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for key in list(CATEGORIES) + [k for k in cats if k not in CATEGORIES]:
        c = cats[key]
        s = c["status"]
        lines.append(f"| {LABELS.get(key, key)} | {c['units']} | {human_size(c['in_bytes'])} | {s.get('done', 0)} | "
                     f"{s.get('failed', 0)} | {s.get('unsupported', 0)} | {s.get('blocked-policy', 0)} | {s.get('skipped', 0)} | "
                     f"{s.get('pending', 0) + s.get('running', 0) + s.get('paused-disk', 0)} | {c['files_out']} | "
                     f"{human_size(c['out_bytes'])} |")
    lines.append("")
    for key in list(CATEGORIES) + [k for k in cats if k not in CATEGORIES]:
        c = cats[key]
        lines += [f"## {LABELS.get(key, key)}", ""]
        if not c["units"] and not c["files_out"]:
            lines += ["Nothing discovered for this category by the extractors of this engine (no unit, no output).",
                      "Next step: check `recon/RECON.md` and `gst fmt scan` for data the pipeline does not know yet.", ""]
            continue
        ex = ", ".join(f"`{e}`" for e in sorted(c["extractors"]))
        lines.append(f"- Extractors: {ex}")
        lines.append(f"- Discovered {c['units']} units ({human_size(c['in_bytes'])}); extracted {c['status'].get('done', 0)}; "
                     f"files out {c['files_out']} ({human_size(c['out_bytes'])}).")
        by_status: dict[str, list] = {}
        for issue in c["issues"]:
            by_status.setdefault(issue["status"], []).append(issue)
        for status, issues in sorted(by_status.items()):
            lines += ["", f"### {status} ({len(issues)})", "", f"Next step: {NEXT.get(status, '-')}", "",
                      "| Job | Extractor | Unit | Reason |", "| --- | --- | --- | --- |"]
            lines += [f"| {i['id']} | {i['extractor']} | `{i['unit']}` | {i['reason'].replace('|', '/')} |" for i in issues]
        lines.append("")
    out = write_text(ws / "jobs" / "COVERAGE.md", "\n".join(lines) + "\n")
    summary = {k: {"units": v["units"], "files_out": v["files_out"], **v["status"]} for k, v in cats.items()
               if v["units"] or v["files_out"]}
    return {"file": str(out), "categories": summary, "jobs": len(rows)}
