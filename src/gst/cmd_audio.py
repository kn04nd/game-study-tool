"""gst audio: how the game's sound is organized and used - inventory of the exported audio, PCM copies for
listening, the check of the study's event catalog (event -> files -> who uses it) and a local page to
browse and listen. Any engine; the audio itself comes from gst assets audio/extract or a format reader."""

from __future__ import annotations

import argparse

from gst import workspace
from gst.common import GsError, emit, human_size

CATALOG_SCHEMA = """Checks assets/audio/catalog.json against assets/audio/files.jsonl (gst audio index) and writes
assets/audio/catalog_summary.json. The catalog is written by the study; one object per event, as a list or
as a dict keyed by name:

  {"name": "TankMoveLoop", "kind": "sound|voice|dialog|music|ui|ambient", "category": "optional label",
   "defined_at": "path:line", "fields": {"Volume": "40", "Priority": "low"},
   "files": [{"name": "vgenlo2a", "file": "assets/audio/.../vgenlo2a.wav"}],   (file: null when missing)
   "used_by": [{"where": "path:line", "block": "Object Tank", "key": "SoundMoveStart"}]}
"""


def register(sub, parent) -> None:
    p = sub.add_parser("audio", parents=[parent], help="audio study: inventory, PCM copies, event catalog check, local listening page",
                       description=__doc__)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")

    ix = s.add_parser("index", parents=[parent], help="inventory assets/audio (or a folder): format, rate, channels, duration -> files.jsonl")
    ix.add_argument("folder", nargs="?", help="folder inside the workspace (default assets/audio)")
    ix.add_argument("--no-probe", action="store_true", help="skip ffprobe for non-WAV durations")
    ix.set_defaults(func=cmd_index)

    pc = s.add_parser("pcm", parents=[parent], help="PCM copies (assets/audio/pcm/) of WAVs a browser cannot play (ADPCM...), with ffmpeg")
    pc.add_argument("--jobs", type=int, default=8, help="parallel ffmpeg processes (default 8)")
    pc.set_defaults(func=cmd_pcm)

    ck = s.add_parser("check", parents=[parent], help="check assets/audio/catalog.json against the files: missing, unreferenced, unused events",
                      description=CATALOG_SCHEMA, formatter_class=argparse.RawDescriptionHelpFormatter)
    ck.set_defaults(func=cmd_check)

    pg = s.add_parser("page", parents=[parent], help="assets/audio/INDEX.html: browse events by category, who uses them, and listen (local only)")
    pg.set_defaults(func=cmd_page)


def cmd_index(args) -> int:
    from gst.assets import audiocat

    ws, _game = workspace.current()
    root = None
    if args.folder:
        root = (ws / args.folder).resolve()
        if not root.is_relative_to(ws.resolve()):
            raise GsError("the folder must be inside the workspace (export the audio there first)")
    s = audiocat.index(ws, root, probe=not args.no_probe)
    workspace.journal(ws, f"gst audio index: {s['files']} files, {s['hours']} h, {s['formats']}")
    text = (f"{s['files']} audio files, {human_size(s['bytes'])}, {s['hours']} h ({s['seconds']} s)\n"
            + ", ".join(f"{k} {v}" for k, v in s["formats"].items()) + f"\nwritten: {s['out']}")
    if s["unplayable"]:
        text += f"\n{s['unplayable']} WAVs are not PCM (a browser will not play them): gst audio pcm"
    text += ("\nnext: write assets/audio/catalog.json (event -> files -> who uses it; study-audio skill), "
             "then gst audio check and gst audio page")
    emit(s, args.json, text)
    return 0


def cmd_pcm(args) -> int:
    from gst.assets import audiocat

    ws, _game = workspace.current()
    r = audiocat.pcm(ws, jobs=args.jobs)
    workspace.journal(ws, f"gst audio pcm: {r['converted']} converted, {r.get('failures', 0)} failed")
    text = f"PCM copies: {r['converted']} converted, {r['skipped']} already there ({r['seconds']} s) -> assets/audio/pcm/"
    if r.get("failures"):
        text += f"\n{r['failures']} failed, e.g. {r['failed'][0]}"
    emit(r, args.json, text)
    return 0


def cmd_check(args) -> int:
    from gst.assets import audiocat

    ws, _game = workspace.current()
    s = audiocat.check(ws)
    workspace.journal(ws, f"gst audio check: {s['events']} events, {s['missing_refs']} missing refs, "
                          f"{s['files_unreferenced']} unreferenced files")
    text = (f"{s['events']} events in {len(s['categories'])} categories; {s['files_referenced']} of {s['files']} files referenced\n"
            f"missing files: {s['missing_refs']} references in {s['missing_events']} events"
            + (f" (e.g. {s['missing_samples'][0]['event']} -> {s['missing_samples'][0]['name']})" if s["missing_samples"] else "")
            + f"\nunreferenced files: {s['files_unreferenced']}; events nobody uses: {s['events_without_users']}"
            "\nwritten: assets/audio/catalog_summary.json")
    emit(s, args.json, text)
    return 0


def cmd_page(args) -> int:
    from gst.assets import audiocat

    ws, game = workspace.current()
    r = audiocat.page(ws, game.get("name") or ws.name)
    workspace.journal(ws, f"gst audio page: {r['items']} items, {r['players']} players")
    text = (f"{r['out']}: {r['items']} items from {r['from']}, {r['players']} players"
            + (f", {r['without_file']} without a file" if r["without_file"] else "")
            + "\nopen it in a browser from disk; it stays in GS_HOME (guardrails rule 1: do not publish it)")
    emit(r, args.json, text)
    return 0
