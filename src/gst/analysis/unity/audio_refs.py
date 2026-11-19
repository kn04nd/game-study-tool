"""``gst analyze audio_refs``: who references each Unity AudioClip (components, ScriptableObjects, containers).

Port of ``repo/scan_audio_refs.py`` (typetree paths, Mono) and ``safari/scan_refs.py`` (binary PPtr scan, IL2CPP),
merged: every MonoBehaviour/AudioSource is pre-filtered on its raw bytes (any 4-aligned ``int32 file id`` + ``int64
path id`` pair naming an AudioClip of the inventory), then read with its typetree when the chain has one (field path
like ``Sounds[]``), else reported by byte offset (``@0x2c``). The owner GameObject, its root and the script class
come from raw PPtrs and builtin typetrees of GameObject/Transform (no MonoBehaviour typetree needed).

Mixer routing: every ``AudioMixerGroupController`` gets its path (``<mixer>/<group>/<child>``, the master group
named after its mixer, ``data.mixer_groups``), and each reference gets the group it plays through
(``mixer_group``): an AudioSource's ``m_OutputAudioMixerGroup``; for a MonoBehaviour, the AudioMixerGroup or
AudioSource PPtr of its own typetree in the clip field's struct or an enclosing one (``Sound.Source`` next to
``Sound.Sounds[]``; ``mixer_via: field``), else the AudioSource(s) of its GameObject when they share one group
(``mixer_via: gameobject``).

``depth=2`` (default): ScriptableObjects that hold clips (sound presets, containers) become targets of a second
pass, so ``SoundEvent -> SoundContainer -> AudioClip`` style chains resolve (``data.holder_refs``).

Options: ``file`` (regex on install relpaths), ``depth`` (1 or 2), ``types`` (component types scanned besides
MonoBehaviour, default ``AudioSource``).
"""

from __future__ import annotations

import collections
import re
from array import array

from gst.analysis import Report, UNITY, analysis, counted, md_table
from gst.analysis.unity import UnityFile, basename, builtin, ptr


def row_key(row: dict) -> str:
    return f"{(row.get('cab') or basename(row['file'])).lower()}|{row['path_id']}"


def fid_map(sf, target_files: set[str]) -> dict[int, str]:
    """``file id -> lower file name`` for this serialized file and its externals that hold targets."""
    out = {}
    me = basename(str(sf.name)).lower()
    if me in target_files:
        out[0] = me
    for i, ext in enumerate(getattr(sf, "externals", None) or [], 1):
        name = basename(str(getattr(ext, "path", ""))).lower()
        if name in target_files:
            out[i] = name
    return out


def raw_hits(raw: bytes, fids: dict[int, str], pids: set[int], targets: set[str]) -> list[tuple[int, str]]:
    """``(byte offset, target key)`` of every aligned PPtr in ``raw`` that names a target."""
    n = len(raw) // 4 * 4
    if n < 12 or not fids:
        return []
    q0 = array("q")
    q0.frombytes(raw[: (n // 8) * 8])
    q4 = array("q")
    q4.frombytes(raw[4: 4 + ((n - 4) // 8) * 8])
    if not (pids.intersection(q0) or pids.intersection(q4)):
        return []
    ints = array("i")
    ints.frombytes(raw[:n])
    out = []
    for i in range(0, len(ints) - 2):
        f = ints[i]
        if f in fids:
            pid = (ints[i + 1] & 0xFFFFFFFF) | (ints[i + 2] << 32)
            k = f"{fids[f]}|{pid}"
            if k in targets:
                out.append((i * 4, k))
    return out


def walk_pptrs(value, path: str = ""):
    if isinstance(value, dict):
        if "m_FileID" in value and "m_PathID" in value and len(value) == 2:
            yield path, ptr(value)
            return
        for k, v in value.items():
            if k in ("m_GameObject", "m_Script"):
                continue
            yield from walk_pptrs(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for v in value:
            yield from walk_pptrs(v, f"{path}[]")


class Owners:
    """GameObject name and root name from builtin typetrees, cached per serialized file."""

    def __init__(self, sf):
        self.sf = sf
        self.go: dict[int, tuple[str, int]] = {}
        self.tr: dict[int, tuple[int, int]] = {}

    def _go(self, pid: int) -> tuple[str, int]:
        if pid not in self.go:
            name, tpid = "", 0
            obj = self.sf.objects.get(pid)
            tree = builtin(obj) if obj is not None else None
            if tree:
                name = tree.get("m_Name") or ""
                for c in tree.get("m_Component") or []:
                    cp = ptr((c or {}).get("component"))
                    co = self.sf.objects.get(cp[1]) if cp[0] == 0 else None
                    if co is not None and co.type.name in ("Transform", "RectTransform"):
                        tpid = cp[1]
                        break
            self.go[pid] = (name, tpid)
        return self.go[pid]

    def _tr(self, pid: int) -> tuple[int, int]:
        if pid not in self.tr:
            obj = self.sf.objects.get(pid)
            tree = builtin(obj) if obj is not None else None
            self.tr[pid] = (ptr((tree or {}).get("m_Father"))[1], ptr((tree or {}).get("m_GameObject"))[1]) if tree else (0, 0)
        return self.tr[pid]

    def names(self, go_pid: int) -> tuple[str, str]:
        name, tpid = self._go(go_pid)
        root = name
        for _ in range(80):
            if not tpid:
                break
            father, _g = self._tr(tpid)
            if not father:
                break
            _f2, fgo = self._tr(father)
            if not fgo:
                break
            root, tpid = self._go(fgo)[0], father
        return name, root


def group_paths(groups: dict[int, dict], mixers: dict[int, dict]) -> dict[int, dict]:
    """``group pid -> {path, mixer, group}`` of one serialized file's mixers (typetrees of AudioMixerGroupController
    and AudioMixerController). The master group is named after its mixer; groups no mixer reaches keep their name."""
    out: dict[int, dict] = {}
    for mpid in sorted(mixers):
        m = mixers[mpid]
        mname = str(m.get("m_Name") or f"mixer{mpid}")
        stack = [(ptr(m.get("m_MasterGroup"))[1], mname)]
        while stack:
            gpid, path = stack.pop()
            if gpid in out or gpid not in groups:
                continue
            out[gpid] = {"path": path, "mixer": mname, "group": str(groups[gpid].get("m_Name") or "")}
            for c in reversed(groups[gpid].get("m_Children") or []):
                cp = ptr(c)
                if cp[0] == 0 and cp[1] in groups:
                    stack.append((cp[1], path + "/" + str(groups[cp[1]].get("m_Name") or cp[1])))
    for gpid, g in groups.items():
        out.setdefault(gpid, {"path": str(g.get("m_Name") or gpid), "mixer": "", "group": str(g.get("m_Name") or "")})
    return out


def mixer_groups(ctx, fpat=None) -> dict[str, dict]:
    """``group key -> {path, mixer, group}`` for every AudioMixerGroupController of the inventory."""
    from gst.engines.unity import loader

    out: dict[str, dict] = {}
    for rel in ctx.inventory_files(("AudioMixerGroupController",)):
        if fpat and not fpat.search(rel):
            continue
        with UnityFile(ctx, rel) as uf:
            for sf in uf.files():
                groups, mixers = {}, {}
                for obj in list(sf.objects.values()):
                    tn = loader.type_name(obj)
                    if tn in ("AudioMixerGroupController", "AudioMixerController"):
                        tree = builtin(obj)
                        if tree:
                            (groups if tn == "AudioMixerGroupController" else mixers)[obj.path_id] = tree
                me = basename(str(sf.name)).lower()
                for gpid, g in group_paths(groups, mixers).items():
                    out[f"{me}|{gpid}"] = g
    return out


def output_group(tree: dict | None):
    tree = tree or {}
    return tree.get("m_OutputAudioMixerGroup", tree.get("OutputAudioMixerGroup"))


def nearest_group(field: str, candidates: list[tuple[str, str, str]]) -> tuple[str, str] | None:
    """The ``(group key, via)`` of the group/AudioSource PPtr that belongs to the clip field's struct or an enclosing
    one (``jump.Source`` for ``jump.Sounds[]``, a top-level ``mixerGroup`` for any clip), the innermost first.
    A PPtr of a sibling struct (``land.Source``) does not count."""
    clip = field.replace("[]", "").split(".")
    best = None
    for path, key, via in candidates:
        parent = path.replace("[]", "").split(".")[:-1]
        if clip[:len(parent)] == parent and (best is None or len(parent) > best[0]):
            best = (len(parent), key, via)
    return (best[1], best[2]) if best else None


def scan(ctx, targets: dict[str, str], *, types: tuple[str, ...], fpat, pass_name: str,
         groups: dict[str, dict] | None = None) -> tuple[list[dict], dict]:
    """One pass over every file holding MonoBehaviours (and ``types``): references to ``targets`` (key -> name),
    each with the mixer group it plays through when ``groups`` (from :func:`mixer_groups`) resolves one."""
    from gst.engines.unity import loader

    groups = groups or {}
    group_files = {k.split("|", 1)[0] for k in groups}
    target_files = {k.split("|", 1)[0] for k in targets}
    pids = {int(k.split("|", 1)[1]) for k in targets}
    tset = set(targets)
    refs, stats = [], {}
    for rel in ctx.inventory_files(("MonoBehaviour",) + types):
        if fpat and not fpat.search(rel):
            continue
        ctx.log(f"{pass_name}: {rel}")
        scanned = hit_objs = 0
        typed = untyped = false_pos = 0
        with UnityFile(ctx, rel) as uf:
            for sf in uf.files():
                fids = fid_map(sf, target_files)
                if not fids:
                    continue
                gfids = fid_map(sf, group_files) if groups else {}
                go_sources: dict[int, list[int]] = collections.defaultdict(list)
                if gfids:  # AudioSources by GameObject from raw bytes; their typetree only when a reference needs it
                    for obj in list(sf.objects.values()):
                        if loader.type_name(obj) == "AudioSource":
                            go_sources[loader.raw_gameobject_pid(obj) or 0].append(obj.path_id)
                src_cache: dict[int, str | None] = {}

                def source_group(pid: int, _sf=sf, _gf=gfids, _c=src_cache) -> str | None:
                    if pid not in _c:
                        obj = _sf.objects.get(pid)
                        key = None
                        if obj is not None and loader.type_name(obj) == "AudioSource":
                            g = ptr(output_group(builtin(obj)))
                            key = f"{_gf[g[0]]}|{g[1]}" if g[0] in _gf else None
                        _c[pid] = key if key in groups else None
                    return _c[pid]

                def group_of(field: str, tree, tn: str, pid: int, go_pid: int, _gf=gfids, _gs=go_sources):
                    if tn == "AudioSource":
                        g = source_group(pid)
                        return (g, "audiosource") if g else None
                    cands = []
                    for path, (fid, gp) in (walk_pptrs(tree) if tree else ()):
                        if fid in _gf and f"{_gf[fid]}|{gp}" in groups:
                            cands.append((path, f"{_gf[fid]}|{gp}", "field"))
                        elif fid == 0 and source_group(gp):
                            cands.append((path, source_group(gp), "field"))
                    found = nearest_group(field, cands)
                    if found:
                        return found
                    on_go = {source_group(a) for a in _gs.get(go_pid, [])} - {None}
                    return (next(iter(on_go)), "gameobject") if len(on_go) == 1 else None

                owners = Owners(sf)
                for obj in list(sf.objects.values()):
                    tn = loader.type_name(obj)
                    if tn != "MonoBehaviour" and tn not in types:
                        continue
                    scanned += 1
                    try:
                        raw = obj.get_raw_data()
                    except Exception:
                        continue
                    hits = raw_hits(raw, fids, pids, tset)
                    if not hits:
                        continue
                    hit_objs += 1
                    info = uf.script(obj) if tn == "MonoBehaviour" else None
                    cls = (info.class_name if info else "?") if tn == "MonoBehaviour" else tn
                    tree = None
                    try:
                        tree = uf.mono(obj, info) if tn == "MonoBehaviour" else builtin(obj)
                    except Exception:
                        tree = None
                    go_pid = loader.raw_gameobject_pid(obj) or 0
                    if go_pid:
                        go, root = owners.names(go_pid)
                    else:
                        go, root = "", (tree or {}).get("m_Name") or loader.peek_name(obj)
                    base = {"file": rel, "cab": basename(str(sf.name)) if rel.endswith(".bundle") else None,
                            "path_id": obj.path_id, "type": tn, "class": cls, "go": go, "root": root,
                            "scriptable": not go_pid}
                    found = []
                    if tree:
                        for path, (fid, pid) in walk_pptrs(tree):
                            if fid in fids and f"{fids[fid]}|{pid}" in tset:
                                found.append((path, f"{fids[fid]}|{pid}"))
                    new = []
                    if found:
                        typed += 1
                        new = [{**base, "field": path, "target": k, "how": "typetree"} for path, k in found]
                    elif tree:
                        false_pos += 1  # the typetree has no such PPtr: the raw match was data (glyphs, floats...)
                    else:
                        untyped += 1
                        new = [{**base, "field": f"@0x{off:x}", "target": k, "how": "binary"} for off, k in hits]
                    for r in new:
                        g = group_of(r["field"], tree, tn, obj.path_id, go_pid) if groups else None
                        if g:
                            r["mixer_group"], r["mixer_via"] = groups[g[0]]["path"], g[1]
                    refs += new
        stats[rel] = {"scanned": scanned, "objects_with_refs": hit_objs, "typetree": typed, "binary": untyped,
                      "raw_false_positives": false_pos}
    for r in refs:
        if r["cab"] is None:
            r.pop("cab")
    refs.sort(key=lambda r: (r["file"], r["path_id"], r["field"], r["target"]))
    return refs, stats


@analysis("audio_refs", engines=UNITY, needs=("unity.inventory", "unity.install"),
          description="Who references each AudioClip: components, ScriptableObjects and sound containers "
                      "(typetree paths, binary PPtr scan without typetrees), and the mixer group each plays through",
          version=2,
          options={"file": "regex on install relpaths", "depth": "2 = also who references clip-holding "
                   "ScriptableObjects (default 2)", "types": "component types scanned besides MonoBehaviour (default AudioSource)"})
def audio_refs(ctx) -> Report:
    fpat = re.compile(ctx.opt("file"), re.I) if ctx.opt("file") else None
    types = tuple(ctx.opt_list("types") or ["AudioSource"])
    clips = {}
    clip_rows = {}
    for row in ctx.rows(types=("AudioClip",)):
        k = row_key(row)
        clips[k] = row.get("name") or ""
        clip_rows[k] = row
    groups = mixer_groups(ctx, fpat) if clips else {}
    refs, stats = scan(ctx, clips, types=types, fpat=fpat, pass_name="clips", groups=groups) if clips else ([], {})
    holder_refs, hstats = [], {}
    holders = {}
    if int(ctx.opt("depth", 2)) >= 2:
        for r in refs:
            if r["scriptable"] and r["type"] == "MonoBehaviour":
                holders[f"{(r.get('cab') or basename(r['file'])).lower()}|{r['path_id']}"] = f"{r['class']}:{r['root']}"
        if holders:
            holder_refs, hstats = scan(ctx, holders, types=(), fpat=fpat, pass_name="holders", groups=groups)
            holder_refs = [h for h in holder_refs
                           if f"{(h.get('cab') or basename(h['file'])).lower()}|{h['path_id']}" != h["target"]]
    return build_report(clips, clip_rows, refs, holders, holder_refs, stats, hstats, groups)


def build_report(clips: dict, clip_rows: dict, refs: list[dict], holders: dict, holder_refs: list[dict],
                 stats: dict, hstats: dict, groups: dict | None = None) -> Report:
    groups = groups or {}
    by_clip: dict[str, int] = collections.Counter(r["target"] for r in refs)
    clip_list = [{"key": k, "name": clips[k], "file": clip_rows[k]["file"], "path_id": clip_rows[k]["path_id"],
                  **({"cab": clip_rows[k]["cab"]} if clip_rows[k].get("cab") else {}), "refs": by_clip.get(k, 0)}
                 for k in sorted(clips, key=lambda x: (clips[x], x))]
    classes = counted(collections.Counter(r["class"] for r in refs))
    fields = counted(collections.Counter(f"{r['class']}.{r['field']}" for r in refs))
    how = counted(collections.Counter(r["how"] for r in refs))
    summary = {"clips": len(clips), "clips_referenced": len(by_clip), "clips_unreferenced": len(clips) - len(by_clip),
               "refs": len(refs), "components": len({(r["file"], r["path_id"]) for r in refs}), "classes": len(classes),
               "holders": len(holders), "holder_refs": len(holder_refs), "how": how, "mixer_groups": len(groups),
               "refs_with_mixer_group": sum(1 for r in refs if r.get("mixer_group"))}
    by_group = counted(collections.Counter(f"{r['mixer_group']} ({r['mixer_via']})" for r in refs + holder_refs
                                           if r.get("mixer_group")))
    data = {"clips": clip_list, "refs": refs, "holders": dict(sorted(holders.items())), "holder_refs": holder_refs,
            "classes": classes, "fields": fields, "scan": stats, "holder_scan": hstats,
            "mixer_groups": {k: g["path"] for k, g in sorted(groups.items(), key=lambda kv: kv[1]["path"])},
            "refs_by_mixer_group": by_group}
    md = [f"{len(clips)} AudioClips; {len(by_clip)} referenced by {summary['components']} components/ScriptableObjects "
          f"({len(refs)} references: " + ", ".join(f"{k} {v}" for k, v in how.items()) + f"); {summary['clips_unreferenced']} "
          f"never referenced from the scanned files (code-loaded, Resources, unused). {len(holders)} clip-holding "
          f"ScriptableObjects, {len(holder_refs)} references to them.", "",
          "## Referencing classes", "", md_table(["Class", "References"], list(classes.items())[:60]), "",
          f"## Mixer groups ({len(groups)})", "",
          ("Groups: " + ", ".join(f"`{p}`" for p in data["mixer_groups"].values()) + ". " if groups else
           "No AudioMixerGroupController in the inventory. ")
          + f"{summary['refs_with_mixer_group']} of {len(refs)} clip references resolve a group.", "",
          md_table(["Group (how)", "References"], list(by_group.items())), "",
          "## Fields", "", md_table(["Class.field", "References"], list(fields.items())[:80]), "",
          "## Files scanned", "", md_table(["File", "Objects scanned", "Raw matches", "Typetree", "Binary only (no typetree)",
                                            "Raw matches the typetree rejects"],
                                           [(f"`{f}`", s["scanned"], s["objects_with_refs"], s["typetree"], s["binary"],
                                             s.get("raw_false_positives", 0))
                                            for f, s in sorted(stats.items())]), ""]
    if holder_refs:
        hc = counted(collections.Counter(f"{h['class']}.{h['field']} -> {holders.get(h['target'], h['target']).split(':')[0]}"
                                         for h in holder_refs))
        md += ["## References to clip holders (depth 2)", "", md_table(["Referrer.field -> holder class", "References"],
                                                                        list(hc.items())[:60]), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)
