"""Audio study helpers for any engine: inventory of the audio already exported into the workspace
(`files.jsonl`), PCM copies of WAVs a browser cannot play (IMA/MS ADPCM...), a check of the study's
event catalog (`catalog.json`: event -> files -> who uses it) and a local listening page (`INDEX.html`).

The catalog is written by the study (each engine names and wires sounds differently: INI events, FMOD
events, Wwise events, AudioClips referenced by prefabs, SoundCues...). Schema, one object per event,
as a list or as a dict keyed by name:

    {"name": "TankMoveLoop", "kind": "sound|voice|music|dialog|ui|ambient|...", "category": "optional label",
     "defined_at": "path:line", "fields": {"Volume": "40", ...},
     "files": [{"name": "vgenlo2a", "file": "assets/audio/.../vgenlo2a.wav" | null}],
     "used_by": [{"where": "path:line", "block": "Object Tank", "key": "SoundMoveStart"}]}
"""

from __future__ import annotations

import collections
import json
import os
import shutil
import struct
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gst.common import GsError, read_json, write_json, write_text

AUDIO_FILES = {".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac", ".wma", ".aif", ".aiff"}
BROWSER_OK = {".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac"}
WAV_TAGS = {1: "PCM", 2: "MS-ADPCM", 3: "IEEE-float", 6: "A-law", 7: "u-law", 0x11: "IMA-ADPCM", 0x55: "MP3",
            0x165: "XMA", 0x166: "XMA2", 0xFFFE: "extensible"}
DERIVED = {"pcm"}  # folders gst writes itself; never inventoried as game audio


def audio_dir(ws: Path) -> Path:
    return ws / "assets" / "audio"


def wav_info(head: bytes) -> dict:
    """Format fields of a RIFF/WAVE header (the first few KB are enough)."""
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return {"fmt": "not-riff"}
    p, info = 12, {}
    while p + 8 <= len(head):
        cid, size = head[p:p + 4], struct.unpack_from("<I", head, p + 4)[0]
        if cid == b"fmt " and p + 24 <= len(head):
            tag, ch, rate, byterate, _align, bits = struct.unpack_from("<HHIIHH", head, p + 8)
            info.update(fmt=WAV_TAGS.get(tag, hex(tag)), channels=ch, rate=rate, bits=bits, byterate=byterate)
        elif cid == b"data":
            info["data_bytes"] = size
            if info.get("byterate"):
                info["seconds"] = round(size / info["byterate"], 3)
            break
        p += 8 + size + (size & 1)
    return info


def _ffprobe() -> Path | None:
    from gst import tools

    try:
        return tools.bin_path("ffmpeg", "ffprobe.exe") or (Path(shutil.which("ffprobe")) if shutil.which("ffprobe") else None)
    except Exception:  # catalog lookup problems must not stop an inventory
        return Path(shutil.which("ffprobe")) if shutil.which("ffprobe") else None


def _ffmpeg() -> Path:
    from gst import tools

    exe = tools.bin_path("ffmpeg") or (Path(shutil.which("ffmpeg")) if shutil.which("ffmpeg") else None)
    if exe is None:
        raise GsError("ffmpeg is not installed (gst tools install ffmpeg)")
    return exe


def _probe_seconds(probe: Path | None, path: Path) -> float | None:
    if probe is None:
        return None
    proc = subprocess.run([str(probe), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    try:
        return round(float(proc.stdout.strip()), 3)
    except ValueError:
        return None


def _rel(ws: Path, p: Path) -> str:
    return p.relative_to(ws).as_posix() if p.is_relative_to(ws) else p.as_posix()


def index(ws: Path, root: Path | None = None, *, probe: bool = True) -> dict:
    """Inventory every audio file under `root` (default assets/audio) into assets/audio/files.jsonl.
    Keys already present for a file (provenance written by a custom extractor: archive, path, offset,
    effective...) are kept; format fields are refreshed."""
    t0 = time.time()
    root = root or audio_dir(ws)
    if not root.is_dir():
        raise GsError(f"no audio folder at {root}: export the game's audio first (gst assets audio, gst assets extract, "
                      "or a reader for the game's containers writing into assets/audio/)")
    out = audio_dir(ws) / "files.jsonl"
    old = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                old[row.get("file")] = row
    prober = _ffprobe() if probe else None
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_FILES:
            continue
        if DERIVED & {part.lower() for part in path.relative_to(root).parts[:-1]}:
            continue
        rel = _rel(ws, path)
        row = dict(old.get(rel, {}))
        row.update(file=rel, size=path.stat().st_size, ext=path.suffix.lower())
        if row["ext"] == ".wav":
            with path.open("rb") as fh:
                row.update(wav_info(fh.read(65536)))
        else:
            row["fmt"] = row["ext"].lstrip(".")
            if prober and "seconds" not in row:
                seconds = _probe_seconds(prober, path)
                if seconds is not None:
                    row["seconds"] = seconds
        pcm = path_for_pcm(ws, rel)
        if pcm.exists():
            row["pcm"] = _rel(ws, pcm)
        rows.append(row)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    formats = collections.Counter(r.get("fmt", "?") for r in rows)
    folders = collections.Counter(str(Path(r["file"]).parent.as_posix()) for r in rows)
    return {"files": len(rows), "bytes": sum(r["size"] for r in rows),
            "hours": round(sum(r.get("seconds") or 0 for r in rows) / 3600, 2), "formats": dict(formats.most_common()),
            "folders": dict(folders.most_common(12)), "unplayable": sum(1 for r in rows if needs_pcm(r) and not r.get("pcm")),
            "out": _rel(ws, out), "seconds": round(time.time() - t0, 1)}


def load_files(ws: Path) -> list[dict]:
    path = audio_dir(ws) / "files.jsonl"
    if not path.exists():
        raise GsError("no assets/audio/files.jsonl yet: run gst audio index")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def needs_pcm(row: dict) -> bool:
    """A WAV a browser cannot play, among the files the game actually uses (shadowed copies are skipped)."""
    return row.get("ext") == ".wav" and row.get("effective", True) and row.get("fmt") not in ("PCM", "IEEE-float", "not-riff")


def path_for_pcm(ws: Path, rel: str) -> Path:
    """assets/audio/<x>/a.wav -> assets/audio/pcm/<x>/a.wav (other roots keep their path under pcm/)."""
    base = audio_dir(ws)
    src = ws / rel
    sub = src.relative_to(base) if src.is_relative_to(base) else Path(*Path(rel).parts[1:])
    return base / "pcm" / sub


def pcm(ws: Path, *, jobs: int = 8) -> dict:
    """16-bit PCM copies of the WAVs a browser cannot play; the originals stay untouched."""
    t0 = time.time()
    rows = load_files(ws)
    todo = [r for r in rows if needs_pcm(r)]
    if not todo:
        return {"converted": 0, "skipped": 0, "failed": [], "seconds": 0.0}
    exe = _ffmpeg()

    def one(row: dict) -> str | None:
        dst = path_for_pcm(ws, row["file"])
        if dst.exists():
            return "skip"
        dst.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([str(exe), "-v", "error", "-y", "-i", str(ws / row["file"]), "-c:a", "pcm_s16le", str(dst)],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return None if proc.returncode == 0 and dst.exists() else f"{row['file']}: {proc.stderr.strip()[-160:]}"

    with ThreadPoolExecutor(max(1, jobs)) as ex:
        results = list(ex.map(one, todo))
    failed = [r for r in results if r and r != "skip"]
    for row in todo:
        dst = path_for_pcm(ws, row["file"])
        if dst.exists():
            row["pcm"] = _rel(ws, dst)
    with (audio_dir(ws) / "files.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"converted": sum(1 for r in results if r is None), "skipped": results.count("skip"), "failed": failed,
            "failures": len(failed), "seconds": round(time.time() - t0, 1)}


def load_catalog(ws: Path) -> list[dict]:
    path = audio_dir(ws) / "catalog.json"
    data = read_json(path, None)
    if data is None:
        return []
    events = list(data.values()) if isinstance(data, dict) else list(data)
    for ev in events:
        if not isinstance(ev, dict) or not ev.get("name"):
            raise GsError(f"{path}: every event needs a 'name' (see gst audio check --help for the schema)")
        ev.setdefault("files", [])
        ev.setdefault("used_by", [])
        ev.setdefault("fields", {})
        if "defined_at" not in ev and ev.get("ini"):
            ev["defined_at"] = ev["ini"]
    return events


def category(ev: dict) -> str:
    """The study's own category if given, else a guess from the kind, the name and who uses it."""
    if ev.get("category"):
        return ev["category"]
    name, kind = ev["name"].lower(), str(ev.get("kind", "")).lower()
    keys = " ".join(str(u.get("key", "")) for u in ev["used_by"]).lower()
    fields = " ".join(str(v) for v in ev["fields"].values()).lower()
    if kind == "music" or "music" in name or "track" in kind:
        return "Música"
    if kind in ("dialog", "streaming", "speech") or "dialog" in name:
        return "Falas e diálogos"
    if kind == "voice" or "voice" in keys or "voice" in name or " voice" in fields:
        return "Vozes de personagens/unidades"
    if kind == "ambient" or name.startswith("amb") or "ambient" in keys:
        return "Ambiente"
    if any(w in name or w in keys for w in ("weapon", "fire", "shot", "gun")):
        return "Armas e disparos"
    if any(w in name for w in ("explo", "debris", "die", "death")):
        return "Explosões e mortes"
    if any(w in name or w in keys for w in ("move", "step", "foot", "engine", "locomot")):
        return "Movimento"
    if kind == "ui" or name.startswith(("ui", "gui", "menu", "button")):
        return "Interface"
    return "Outros efeitos"


def check(ws: Path) -> dict:
    """Cross-check catalog.json with files.jsonl: references to files that do not exist, files no event
    references, events nobody uses; writes assets/audio/catalog_summary.json."""
    files = load_files(ws)
    events = load_catalog(ws)
    if not events:
        raise GsError("no assets/audio/catalog.json yet: map each sound event to its files and to who uses it "
                      "(see the study-audio skill), then run gst audio check")
    present = {r["file"] for r in files}
    effective = {r["file"] for r in files if r.get("effective", True)}
    referenced, missing = set(), []
    for ev in events:
        for f in ev["files"]:
            if f.get("file") and (f["file"] in present or (ws / f["file"]).exists()):
                referenced.add(f["file"])
            else:
                missing.append({"event": ev["name"], "name": f.get("name"), "file": f.get("file")})
    unreferenced = sorted(effective - referenced)
    unused = [ev["name"] for ev in events if not ev["used_by"]]
    cats = collections.Counter(category(ev) for ev in events)
    keys = collections.Counter(u.get("key") or "?" for ev in events for u in ev["used_by"])
    summary = {"events": len(events), "categories": dict(cats.most_common()), "files": len(files),
               "files_referenced": len(referenced), "files_unreferenced": len(unreferenced),
               "unreferenced_by_folder": dict(collections.Counter(str(Path(u).parent.as_posix()) for u in unreferenced).most_common(10)),
               "missing_refs": len(missing), "missing_events": len({m["event"] for m in missing}), "missing_samples": missing,
               "events_without_users": len(unused), "unused_samples": unused, "usage_keys": dict(keys.most_common(30)),
               "most_used": [[len(ev["used_by"]), ev["name"]] for ev in sorted(events, key=lambda e: -len(e["used_by"]))[:15]]}
    write_json(audio_dir(ws) / "catalog_summary.json", summary)
    return summary


PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Áudio – __TITLE__</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#fff;--fg:#1d1d1f;--mut:#666;--line:#e3e3e3;--acc:#0b63c5;--chip:#f1f3f5;--bad:#c0392b}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#eee;--mut:#9a9a9a;--line:#2b2b2b;--acc:#6aa8ff;--chip:#222;--bad:#ff7b6b}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;padding:16px;max-width:1200px}
h1{font-size:20px;margin:0 0 4px}.mut{color:var(--mut)}
input{width:100%;padding:8px;font-size:15px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);box-sizing:border-box}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.tab{padding:4px 10px;border-radius:14px;background:var(--chip);cursor:pointer;border:1px solid var(--line)}
.tab.on{background:var(--acc);color:#fff}.ev{border-top:1px solid var(--line);padding:10px 0}.name{font-weight:600;word-break:break-all}
.fd{font:12px ui-monospace,monospace;color:var(--mut);word-break:break-word}.files{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.file{background:var(--chip);border-radius:6px;padding:4px 6px;font-size:12px;max-width:100%}audio{height:28px;display:block;max-width:260px}
.miss{color:var(--bad)}details{font-size:12px;color:var(--mut)}
</style></head><body>
<h1>Áudio de __TITLE__</h1>
<div class="mut">Gerado por <code>gst audio page</code> a partir de <code>__SOURCE__</code>. Página local de estudo: fica no GS_HOME, não publique.</div>
<p><input id="q" placeholder="buscar evento, arquivo, unidade, mapa..."></p>
<div class="tabs" id="tabs"></div><div id="n" class="mut"></div><div id="list"></div>
<script>
const D=__DATA__;const C=__CATS__;let cur="Todos";
const tabs=document.getElementById('tabs');
["Todos",...Object.keys(C)].forEach(c=>{const t=document.createElement('span');t.className='tab'+(c==cur?' on':'');
 t.textContent=c+" ("+(c=="Todos"?D.length:C[c])+")";t.onclick=()=>{cur=c;[...tabs.children].forEach(x=>x.classList.remove('on'));t.classList.add('on');draw()};tabs.appendChild(t)});
const esc=s=>String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));
function draw(){const q=document.getElementById('q').value.toLowerCase();
 const R=D.filter(r=>(cur=="Todos"||r.c==cur)&&(!q||JSON.stringify(r).toLowerCase().includes(q)));
 document.getElementById('n').textContent=R.length+" itens"+(R.length>300?" (mostrando 300; refine a busca)":"");
 document.getElementById('list').innerHTML=R.slice(0,300).map(r=>`<div class="ev"><span class="name">${esc(r.e)}</span> <span class="mut">· ${esc(r.c)}${r.d?' · '+esc(r.d):''}</span>
 <div class="fd">${Object.entries(r.fd||{}).map(([k,v])=>esc(k)+'='+esc(v)).join(' · ')}</div>
 <div class="files">${r.fs.map(f=>f.src?`<div class="file">${esc(f.n)} <span class="mut">${f.s?Number(f.s).toFixed(1)+'s':''} ${esc(f.f||'')}</span><audio controls preload="none" src="${encodeURI(f.src)}"></audio></div>`:`<div class="file miss">${esc(f.n)} (arquivo ausente)</div>`).join('')}</div>
 ${r.u===null?'':(r.u.length?`<details><summary>usado por ${r.nu}</summary>${r.u.map(esc).join('<br>')}${r.nu>r.u.length?'<br>…':''}</details>`:'<details><summary>sem uso encontrado</summary>pode ser chamado direto pelo código ou ser sobra</details>')}</div>`).join('');}
document.getElementById('q').oninput=draw;draw();
</script></body></html>"""


def page(ws: Path, title: str) -> dict:
    """assets/audio/INDEX.html: the catalog's events (or, without a catalog, every file by folder) with players."""
    base = audio_dir(ws)
    rows_by_file = {r["file"]: r for r in load_files(ws)}

    def src(rel: str | None) -> str | None:
        if not rel:
            return None
        row = rows_by_file.get(rel, {})
        target = ws / (row.get("pcm") or rel)
        return os.path.relpath(target, base).replace(os.sep, "/") if target.exists() else None

    events = load_catalog(ws)
    items, cats = [], collections.Counter()
    if events:
        for ev in sorted(events, key=lambda e: e["name"].lower()):
            c = category(ev)
            cats[c] += 1
            users = []
            for u in ev["used_by"]:
                label = f"{u.get('block') or u.get('where') or '?'} ({u.get('key') or '-'})"
                if label not in users:
                    users.append(label)
            files = [{"n": f.get("name") or Path(f.get("file") or "?").name, "src": src(f.get("file")),
                      "s": rows_by_file.get(f.get("file") or "", {}).get("seconds"),
                      "f": rows_by_file.get(f.get("file") or "", {}).get("fmt")} for f in ev["files"]]
            items.append({"e": ev["name"], "c": c, "d": ev.get("defined_at"), "fd": ev["fields"], "fs": files,
                          "u": users[:12], "nu": len(users)})
        source = "assets/audio/catalog.json"
    else:
        for rel, row in sorted(rows_by_file.items()):
            c = str(Path(rel).parent.as_posix())
            cats[c] += 1
            items.append({"e": Path(rel).name, "c": c, "d": None, "fd": {}, "u": None, "nu": 0,
                          "fs": [{"n": Path(rel).name, "src": src(rel), "s": row.get("seconds"), "f": row.get("fmt")}]})
        source = "assets/audio/files.jsonl"
    links = [f["src"] for it in items for f in it["fs"] if f["src"]]
    html = (PAGE.replace("__TITLE__", _html_escape(title)).replace("__SOURCE__", source)
            .replace("__DATA__", json.dumps(items, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__CATS__", json.dumps(dict(cats.most_common()), ensure_ascii=False)))
    out = write_text(base / "INDEX.html", html)
    missing_players = sum(1 for it in items for f in it["fs"] if not f["src"])
    return {"out": _rel(ws, out), "items": len(items), "players": len(links), "without_file": missing_players,
            "categories": dict(cats.most_common()), "from": source}


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
