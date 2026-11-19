"""``gst analyze audio_catalog``: the study's sound catalog (event -> files -> who uses it), any engine.

Same schema as ``assets/audio/catalog.json`` (:mod:`gst.assets.audiocat`, read by ``gst audio check`` / ``page``):
``{"name", "kind", "category", "defined_at", "fields", "files": [{"name", "file"}], "used_by": [{"where", "block",
"key"}]}``. Written to ``analysis/audio_catalog.json`` (``data.events``) and published to
``assets/audio/catalog.json`` unless the study already wrote its own there (option ``publish``).

- Unity (port of ``repo/build_audio_catalog.py``): from ``analysis/audio_refs.json``. One event per (component
  class, field, set of clips) with its owners (file, root / GameObject); clip-holding ScriptableObjects get their
  referrers too. Classification: ``category`` = the AudioMixer group the event plays through (``audio_refs``
  ``mixer_group``; the game's own grouping, like the Godot ``bus``) unless it is the game's default group (the one
  most references use), else the owner family; ``kind`` = music,
  ambient, voice, ui, footstep, impact, weapon or sound, from the mixer group name, else the referencing
  component (field name, then class), else the clip names (``fields.kind_by`` says which). Files: the exported clips found by their ``.gs.json`` sidecars (``unity.audio``). Code: the decompiled
  class (``decomp/**/<Class>.cs``) gives ``defined_at`` (field declaration) and the lines that play the field.
  Clips nobody references become ``unreferenced: <name>`` events so the page still lists them.
- Godot (port of ``pratfall/build_audio_catalog.py``): ``.tscn``/``.tres`` of the recovered project: AudioStream
  ``ext_resource``s used by ``AudioStreamPlayer*`` nodes, by resources/sub-resources that hold streams (custom
  AudioClip resources) and by other node properties.
- Any engine: string literals in the decompiled code / scripts naming a clip or stream (``path:line``) are added
  to ``used_by`` with key ``code mention``.

Options: ``publish`` (``auto`` = write assets/audio/catalog.json when absent or written by gst before, ``1`` = always,
``0`` = never), ``code=0`` (skip the source scan).
"""

from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

from gst.analysis import ANY, Report, analysis, counted, md_table

AUDIO_EXT = (".wav", ".ogg", ".mp3", ".flac", ".opus", ".aif", ".aiff", ".m4a")
MARKER = ".catalog-by-gst"
SRC_EXT = (".cs", ".gd", ".lua", ".js", ".ts", ".py", ".java", ".c", ".cpp", ".h")
LITERAL = re.compile(r'"([^"\\\n]{2,200})"')


def kind_of(text: str) -> str:
    s = text.lower()
    if "music" in s or "jingle" in s or "/bgm" in s or "soundtrack" in s:
        return "music"
    if "ambien" in s or "ambient" in s:
        return "ambient"
    if re.search(r"(^|[/_ \-.])(ui|gui|menu|button|click)([/_ \-.]|$)", s):
        return "ui"
    if "voice" in s or "/vo/" in s or "dialog" in s:
        return "voice"
    return "sound"


KIND_WORDS: tuple[tuple[str, frozenset], ...] = (
    ("music", frozenset({"music", "musical", "song", "songs", "jingle", "jingles", "soundtrack", "bgm", "radio",
                         "boombox", "melody", "ost"})),
    ("ambient", frozenset({"ambience", "ambiences", "ambient", "ambiance", "atmosphere", "atmos", "amb"})),
    ("voice", frozenset({"voice", "voices", "vo", "vox", "speech", "dialog", "dialogue", "talk", "talking", "tts",
                         "microphone", "mic", "scream", "shout", "speak", "barks", "bark"})),
    ("ui", frozenset({"ui", "gui", "menu", "menus", "button", "buttons", "click", "hud", "cursor", "notification",
                      "popup", "tooltip", "interface"})),
    ("footstep", frozenset({"footstep", "footsteps", "step", "steps", "walk", "walking", "foot", "feet"})),
    ("impact", frozenset({"impact", "impacts", "hit", "hits", "collision", "collide", "break", "breaks", "smash",
                          "crash", "thud", "bump", "physaudio", "destroy", "shatter"})),
    ("weapon", frozenset({"weapon", "weapons", "gun", "guns", "shoot", "shot", "shots", "fire", "reload", "melee",
                          "swing", "sword", "rifle", "pistol", "bullet", "bullets", "ammo", "grenade", "shotgun",
                          "muzzle", "gunshot"})),
)
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokens(text: str) -> list[str]:
    """``soundHurtVO`` -> ``[sound, hurt, vo]``; ``Material Impacts`` -> ``[material, impacts]`` (digits dropped)."""
    return [t.lower() for t in _CAMEL.findall(text or "") if not t.isdigit()]


def kind_from(texts) -> str | None:
    """The first kind whose words appear among the tokens of ``texts`` (in :data:`KIND_WORDS` order)."""
    toks = set()
    for t in texts:
        toks.update(tokens(t))
    for kind, words in KIND_WORDS:
        if toks & words:
            return kind
    return None


def classify(mixer: str | None, cls: str, field: str, clip_names: list[str]) -> tuple[str, str]:
    """``(kind, how)``: from the mixer group, else the field name, else the class, else the clip names."""
    for seg in reversed((mixer or "").split("/")):  # the deepest group that names a kind
        k = kind_from([seg])
        if k:
            return k, "mixer"
    for how, texts in (("component", [field.rsplit(".", 1)[-1] if field else ""]), ("component", [field, cls]),
                       ("clip name", clip_names)):
        k = kind_from(texts)
        if k:
            return k, how
    return "sound", "default"


def owner_family(root: str, cls: str) -> str:
    if " - " in root:
        return root.split(" - ", 1)[0].strip().lower()
    return cls.lower()


def norm_field(cls: str, field: str) -> str:
    f = re.sub(r"(\[\])+$", "", field)
    if cls == "AudioSource" and f == "m_Resource":
        return "m_audioClip"
    parts = f.split(".")
    if len(parts) >= 2 and re.search(r"(?i)(sound|clip)s?$", parts[-1]) and parts[-1].endswith("s"):
        f = ".".join(parts[:-1])
    return f


def unique_names(events: list[dict]) -> None:
    seen: collections.Counter = collections.Counter()
    for e in events:
        seen[e["name"]] += 1
        if seen[e["name"]] > 1:
            e["name"] = f"{e['name']} #{seen[e['name']]}"


# --- code ---------------------------------------------------------------------------------------------

class Sources:
    """Decompiled / script sources of the workspace: class files by name and a one-pass literal index."""

    def __init__(self, ws: Path, roots: list[Path]):
        self.ws = ws
        self.by_stem: dict[str, Path] = {}
        self.files: list[Path] = []
        for root in roots:
            if not root.is_dir():
                continue
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for f in sorted(files):
                    if f.endswith(SRC_EXT):
                        p = Path(dirpath) / f
                        self.files.append(p)
                        self.by_stem.setdefault(Path(f).stem, p)
        self._lines: dict[Path, list[str]] = {}

    def rel(self, p: Path) -> str:
        try:
            return p.relative_to(self.ws).as_posix()
        except ValueError:
            return p.as_posix()

    def lines(self, p: Path) -> list[str]:
        if p not in self._lines:
            try:
                self._lines[p] = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            except OSError:
                self._lines[p] = []
        return self._lines[p]

    def field_refs(self, cls: str, field: str) -> tuple[str | None, list[dict]]:
        """``defined_at`` (declaration of the field's leaf in ``<cls>``) and the lines that play it."""
        p = self.by_stem.get(cls)
        if p is None:
            return None, []
        leaf = re.sub(r"\[.*", "", field.split(".")[-1])
        if not leaf or leaf.startswith("@"):
            return f"{self.rel(p)}", []
        decl, plays = None, []
        word = re.compile(r"\b" + re.escape(leaf) + r"\b")
        play = re.compile(r"\b" + re.escape(leaf) + r"(\[[^\]]*\])?\.(Play|PlayLoop|PlayOneShot|Stop)\w*\(|\.clip\s*=\s*"
                          + re.escape(leaf) + r"|PlayOneShot\(\s*" + re.escape(leaf))
        for i, s in enumerate(self.lines(p), 1):
            if word.search(s):
                if decl is None and re.search(r"(public|private|protected|internal|\[SerializeField\]|export)", s):
                    decl = i
                if play.search(s):
                    plays.append(i)
        used = [{"where": f"{self.rel(p)}:{ln}", "block": cls, "key": f"{leaf}.Play*"} for ln in plays[:6]]
        return (f"{self.rel(p)}:{decl}" if decl else self.rel(p)), used

    def mentions(self, names: set[str]) -> dict[str, list[dict]]:
        """String literals equal to a clip name or ending in ``/<name>`` / ``<name>.<ext>`` -> code locations."""
        lower = {n.lower(): n for n in names if n}
        out: dict[str, list[dict]] = collections.defaultdict(list)
        for p in self.files:
            for i, s in enumerate(self.lines(p), 1):
                if '"' not in s:
                    continue
                for lit in LITERAL.findall(s):
                    base = lit.rsplit("/", 1)[-1]
                    stem = os.path.splitext(base)[0] if base.lower().endswith(AUDIO_EXT) else base
                    hit = lower.get(stem.lower())
                    if hit and len(out[hit]) < 8:
                        out[hit].append({"where": f"{self.rel(p)}:{i}", "block": p.stem, "key": "code mention"})
            self._lines.pop(p, None)
        return out


# --- Unity --------------------------------------------------------------------------------------------

def clip_exports(ws: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Exported AudioClips from their sidecars: ``file|pid -> workspace path`` and ``name -> [paths]``."""
    by_key, by_name = {}, collections.defaultdict(list)
    root = ws / "assets"
    if not root.is_dir():
        return by_key, by_name
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("_inventory", "_thumbs", "pcm")]
        for f in files:
            if not f.endswith(".gs.json"):
                continue
            p = Path(dirpath) / f
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if j.get("type") != "AudioClip" or not j.get("file"):
                continue
            target = (p.parent / j["file"])
            rel = target.relative_to(ws).as_posix()
            src = os.path.basename(str(j.get("source", "")).replace("\\", "/")).lower()
            for k in {f"{src}|{j.get('path_id')}", f"{str(j.get('cab') or '').lower()}|{j.get('path_id')}"}:
                if not k.startswith("|"):
                    by_key[k] = rel
            by_name[str(j.get("name") or "")].append(rel)
    return by_key, by_name


def unity_events(refs_doc: dict, exports: tuple[dict, dict], src: Sources | None) -> list[dict]:
    by_key, by_name = exports
    data = refs_doc["data"]
    clips = {c["key"]: c for c in data["clips"]}

    def file_of(k: str) -> dict:
        c = clips.get(k, {"name": k})
        f = by_key.get(k)
        if f is None and len(by_name.get(c["name"], [])) == 1:
            f = by_name[c["name"]][0]
        return {"name": c["name"], "file": f}

    comps: dict[tuple, dict] = {}
    for r in data["refs"]:
        field = norm_field(r["class"], r["field"])
        e = comps.setdefault(((r["class"], field), r["file"], r.get("cab"), r["path_id"]),
                             {"clips": set(), "root": r["root"] or r["go"], "go": r["go"], "mixer": collections.Counter()})
        e["clips"].add(r["target"])
        if r.get("mixer_group"):
            e["mixer"][r["mixer_group"]] += 1
    # the group most references play through is the game's default ("Sound Effects"): it says nothing about the
    # sound, so those events keep their owner family as category
    used_groups = collections.Counter(r["mixer_group"] for r in data["refs"] if r.get("mixer_group"))
    default_group = used_groups.most_common(1)[0][0] if used_groups else None
    holder_users: dict[str, list[dict]] = collections.defaultdict(list)
    for h in data.get("holder_refs", []):
        holder_users[h["target"]].append(h)
    groups: dict[tuple, dict] = {}
    for (k, fl, cab, pid), e in comps.items():
        g = groups.setdefault((k, tuple(sorted(e["clips"]))), {"owners": collections.Counter(), "holders": [],
                                                                "mixer": collections.Counter()})
        g["owners"][(fl, e["root"], e["go"])] += 1
        g["mixer"].update(e["mixer"])
        hk = f"{(cab or os.path.basename(fl)).lower()}|{pid}"
        if hk in holder_users:
            g["holders"].extend(holder_users[hk])
    by_cf: dict[tuple, list] = collections.defaultdict(list)
    for (k, cs), g in groups.items():
        by_cf[k].append((cs, g))
    events, used = [], set()
    for (cls, field) in sorted(by_cf):
        lst = sorted(by_cf[(cls, field)], key=lambda x: x[0])
        defined, plays = (src.field_refs(cls, field) if src else (None, []))
        for cs, g in lst:
            files = [file_of(c) for c in cs]
            used.update(cs)
            names = [f["name"] for f in files]
            label = os.path.commonprefix(names).strip(" -_0123456789") or names[0]
            name = f"{cls}.{field}" + (f": {label}" if len(lst) > 1 else "")
            used_by = list(plays)
            for (fl, root, go), n in sorted(g["owners"].items(), key=lambda kv: (-kv[1], kv[0]))[:12]:
                used_by.append({"where": fl, "block": f"{root} / {go} [{cls}]" if go else f"{root} [{cls}]", "key": field,
                                **({"count": n} if n > 1 else {})})
            for h in sorted(g["holders"], key=lambda h: (h["file"], h["path_id"], h["field"]))[:12]:
                hf = norm_field(h["class"], h["field"])
                used_by.append({"where": h["file"], "block": f"{h['root']} / {h['go']} [{h['class']}]", "key": hf})
                if src:
                    used_by.extend(src.field_refs(h["class"], hf)[1][:3])
            root0 = next(iter(g["owners"]))[1]
            mixer = sorted(g["mixer"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if g["mixer"] else None
            kind, how = classify(mixer, cls, field, names)
            events.append({"name": name, "kind": kind,
                           "category": mixer if mixer and mixer != default_group else owner_family(root0 or "", cls),
                           "defined_at": defined or "",
                           "fields": {"clips": len(files), "owners": sum(g["owners"].values()), "component": cls, "field": field,
                                      "kind_by": how, **({"mixer_group": mixer} if mixer else {})},
                           "files": files, "used_by": used_by})
    for k in sorted(set(clips) - used, key=lambda x: (clips[x]["name"], x)):
        f = file_of(k)
        kind, how = classify(None, "", "", [f["name"]])
        events.append({"name": f"unreferenced: {f['name']}", "kind": kind, "category": "unreferenced",
                       "defined_at": f["file"] or clips[k]["file"], "fields": {"kind_by": how}, "files": [f], "used_by": []})
    return events


# --- Godot --------------------------------------------------------------------------------------------

SEC = re.compile(r'^\[(\w+)\s*(.*)\]\s*$')
ATTR = re.compile(r'(\w+)=("[^"]*"|\S+)')
ER = re.compile(r'ExtResource\(\s*"?([^")]+)"?\s*\)')
SR = re.compile(r'SubResource\(\s*"?([^")]+)"?\s*\)')
PLAYER_FIELDS = ("volume_db", "bus", "autoplay", "pitch_scale", "max_distance", "unit_size", "max_polyphony")


def parse_godot(text: str):
    ext, secs, cur = {}, [], None
    for n, line in enumerate(text.splitlines(), 1):
        m = SEC.match(line)
        if m:
            attrs = {k: v.strip('"') for k, v in ATTR.findall(m.group(2))}
            if m.group(1) == "ext_resource":
                ext[attrs.get("id")] = attrs
                cur = None
            else:
                cur = {"kind": m.group(1), "attrs": attrs, "line": n, "props": {}}
                secs.append(cur)
            continue
        if cur is not None and " = " in line and not line.startswith(("[", ";")):
            k, v = line.split(" = ", 1)
            cur["props"].setdefault(k.strip(), (v, n))
    return ext, secs


def node_path(attrs: dict) -> str:
    name, parent = attrs.get("name", ""), attrs.get("parent")
    if parent is None:
        return name
    return name if parent == "." else f"{parent}/{name}"


def godot_project(ws: Path) -> Path | None:
    root = ws / "assets" / "godot"
    if not root.is_dir():
        return None
    found = sorted(root.rglob("project.godot"), key=lambda p: (len(p.parts), str(p)))
    return found[0].parent if found else None


def godot_events(ws: Path, proj: Path) -> list[dict]:
    events: dict[str, dict] = {}

    def add(name, streams, fields, defined, cls, user):
        e = events.setdefault(name, {"name": name, "streams": list(streams), "fields": dict(fields), "defined_at": defined,
                                     "cls": cls, "used_by": []})
        if user:
            e["used_by"].append(user)

    for f in sorted(proj.rglob("*")):
        if f.suffix not in (".tscn", ".tres") or ".godot" in f.parts:
            continue
        rel = f.relative_to(ws).as_posix()
        stem = f.stem
        ext, secs = parse_godot(f.read_text(encoding="utf-8", errors="replace"))
        aud = {i: a for i, a in ext.items()
               if a.get("type", "").startswith("AudioStream") and a.get("path", "").lower().endswith(AUDIO_EXT)}
        if not aud:
            continue
        subs = {}
        for s in secs:
            if s["kind"] not in ("sub_resource", "resource"):
                continue
            streams, fields, cls = [], {}, None
            sc = s["props"].get("script")
            if sc:
                m = ER.search(sc[0])
                cls = Path(ext[m.group(1)]["path"]).stem if m and m.group(1) in ext else None
            for k, (v, _ln) in s["props"].items():
                ids = [i for i in ER.findall(v) if i in aud]
                if ids:
                    streams += [aud[i]["path"] for i in ids]
                elif k != "script" and not k.startswith("metadata") and len(v) < 60:
                    fields[k] = v
            if streams:
                subs[s["attrs"].get("id", "resource")] = {"streams": streams, "fields": fields, "cls": cls, "line": s["line"], "sec": s}
        used_subs = set()
        for s in secs:
            if s["kind"] != "node":
                continue
            np_, ntype = node_path(s["attrs"]), s["attrs"].get("type", "")
            for k, (v, ln) in s["props"].items():
                srs = SR.findall(v)
                for sid in srs:
                    if sid in subs:
                        used_subs.add(sid)
                        sub = subs[sid]
                        name = f"{stem}/{np_}.{k}" if len(srs) == 1 and v.strip().startswith("SubResource") else f"{stem}/{np_}.{k}#{sid}"
                        add(name, sub["streams"], sub["fields"], f"{rel}:{sub['line']}", sub["cls"],
                            {"where": f"{rel}:{ln}", "block": f"{np_} ({ntype})", "key": k})
                ids = [i for i in ER.findall(v) if i in aud]
                if ids:
                    fields = {x: y[0] for x, y in s["props"].items() if x in PLAYER_FIELDS} if ntype.startswith("AudioStreamPlayer") else {}
                    add(f"{stem}/{np_}.{k}", [aud[i]["path"] for i in ids], fields, f"{rel}:{ln}", ntype,
                        {"where": f"{rel}:{ln}", "block": np_, "key": k})
        for sid, sub in subs.items():
            if sid in used_subs:
                continue
            users = [{"where": f"{rel}:{ln}", "block": s["attrs"].get("id") or node_path(s["attrs"]), "key": k}
                     for s in secs if s is not sub["sec"] for k, (v, ln) in s["props"].items() if sid in SR.findall(v)]
            if sub["sec"]["kind"] == "resource" or users:
                name = f"{stem}:{sid}" + (f" [{sub['cls']}]" if sub["cls"] else "")
                add(name, sub["streams"], sub["fields"], f"{rel}:{sub['line']}", sub["cls"], None)
                events[name]["used_by"] += users or [{"where": f"{rel}:{sub['line']}", "block": "[resource]",
                                                      "key": "(data resource loaded by path)"}]
    out = []
    for e in events.values():
        seen, files = set(), []
        for p in e["streams"]:
            if p in seen:
                continue
            seen.add(p)
            local = proj / p[len("res://"):] if p.startswith("res://") else None
            files.append({"name": Path(p).name, "file": local.relative_to(ws).as_posix() if local and local.is_file() else None})
        folders = sorted({Path(p).parent.name for p in e["streams"]})
        out.append({"name": e["name"], "kind": kind_of(" ".join(e["streams"])), "category": "/".join(folders[:2]),
                    "defined_at": e["defined_at"], "fields": {"class": e["cls"] or "", **e["fields"]}, "files": files,
                    "used_by": e["used_by"]})
    return sorted(out, key=lambda e: e["name"])


# --- the analysis -------------------------------------------------------------------------------------

def publish(ws: Path, events: list[dict], mode: str) -> str:
    d = ws / "assets" / "audio"
    cat, marker = d / "catalog.json", d / MARKER
    if mode in ("0", "false", "no"):
        return "not published (publish=0)"
    if cat.exists() and not marker.exists() and mode not in ("1", "true", "yes", "force"):
        return "kept the study's own assets/audio/catalog.json (publish=1 to overwrite)"
    d.mkdir(parents=True, exist_ok=True)
    from gst.analysis import _atomic_write

    _atomic_write(cat, json.dumps(events, ensure_ascii=False, indent=1) + "\n")
    marker.write_text("assets/audio/catalog.json written by gst analyze audio_catalog\n", encoding="utf-8")
    return "assets/audio/catalog.json"


@analysis("audio_catalog", engines=(ANY,), uses=("analysis:audio_refs",),
          description="Sound catalog: event -> clips/streams -> who uses it (components, scenes, code path:line)", version=2,
          options={"publish": "auto (default) | 1 | 0: write assets/audio/catalog.json",
                   "code": "0 = skip the scan of decompiled code / scripts for clip names"})
def audio_catalog(ctx) -> Report:
    ws = ctx.workspace
    proj = godot_project(ws)
    roots = [ws / "decomp"] + ([proj] if proj else [])
    src = Sources(ws, roots) if str(ctx.opt("code", "1")) not in ("0", "false", "no") else None
    events: list[dict] = []
    source = None
    refs = ctx.load("audio_refs")
    if refs is not None:
        source = "unity"
        exports = clip_exports(ws)
        events += unity_events(refs, exports, src)
    elif proj is not None:
        source = "godot"
        ctx.use("godot project", proj)
        events += godot_events(ws, proj)
    if src is not None and events:
        if src.files:
            ctx.use("code", ws / "decomp")
        names = {Path(f["name"]).stem for e in events for f in e["files"] if f.get("name")}
        names |= {f["name"] for e in events for f in e["files"] if f.get("name")}
        mentions = src.mentions(names)
        for e in events:
            seen = {u["where"] for u in e["used_by"]}
            for f in e["files"]:
                for key in {f.get("name") or "", Path(f.get("name") or "").stem}:
                    for m in mentions.get(key, []):
                        if m["where"] not in seen:
                            e["used_by"].append(m)
                            seen.add(m["where"])
    unique_names(events)
    where = publish(ws, events, str(ctx.opt("publish", "auto")).lower()) if events else "nothing to publish"
    return build_report(events, ws, source, where)


def build_report(events: list[dict], ws: Path, source: str | None, published: str) -> Report:
    files = [f for e in events for f in e["files"]]
    missing = [f for f in files if f.get("file") and not (ws / f["file"]).exists()]
    no_export = [f for f in files if not f.get("file")]
    referenced = {f["file"] for f in files if f.get("file")}
    unused = [e["name"] for e in events if not e["used_by"]]
    code = sum(1 for e in events for u in e["used_by"] if re.search(r":\d+$", u.get("where", "")) and
               not u["where"].endswith((".tscn", ".tres")) and ".tscn:" not in u["where"] and ".tres:" not in u["where"])
    cats = counted(collections.Counter(e["category"] or "?" for e in events))
    kinds = counted(collections.Counter(e["kind"] for e in events))
    kind_clips = {k: len({f.get("file") or f.get("name") for e in events if e["kind"] == k for f in e["files"]}) for k in kinds}
    kind_by = counted(collections.Counter((e.get("fields") or {}).get("kind_by", "-") for e in events))
    summary = {"source": source, "events": len(events), "files_referenced": len(referenced), "missing_files": len(missing),
               "without_export": len(no_export), "events_without_users": len(unused), "code_locations": code,
               "kinds": kinds, "kind_by": kind_by, "published": published}
    data = {"events": events, "categories": cats, "missing": missing[:200], "without_export": no_export[:200]}
    md = [f"{len(events)} events from {source or 'nothing (no analysis/audio_refs.json, no Godot project)'}; "
          f"{len(referenced)} distinct files, {len(missing)} missing on disk, {len(no_export)} without an exported file; "
          f"{len(unused)} events nobody uses; {code} code locations. Published: {published}.", "",
          "Check and listen: `gst audio index` (inventory of the exported files), `gst audio check`, `gst audio page`.", "",
          "## Kinds", "", "Classified by: " + ", ".join(f"{k} {v}" for k, v in kind_by.items())
          + " (mixer = AudioMixer group name, component = field/class name, clip name = keywords of the clips).", "",
          md_table(["Kind", "Events", "Distinct clips"], [(k, v, kind_clips[k]) for k, v in kinds.items()]), "",
          "## Categories", "", md_table(["Category", "Events"], list(cats.items())[:60]), "",
          "## Events with the most files", "",
          md_table(["Event", "Kind", "Files", "Used by", "Defined at"],
                   [(f"`{e['name']}`", e["kind"], len(e["files"]), len(e["used_by"]), e["defined_at"] or "-")
                    for e in sorted(events, key=lambda e: (-len(e["files"]), e["name"]))[:40]]), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)
