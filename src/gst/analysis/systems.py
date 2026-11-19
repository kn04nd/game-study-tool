"""``gst analyze systems``: candidate gameplay systems -> ``analysis/systems.json`` + ``.md``.

Three sources, each optional except the code index:

1. Code index (``index/code.db``): the ``gst map`` keyword map (``mapping.compute``, overridable in
   ``notes/map-keywords.json``): types per system ranked by use, calls between systems.
2. Prefabs (``analysis/prefabs.json``, Unity): which MonoBehaviour classes sit on how many prefabs. A script on many
   prefabs is a system that matters in content, even when the code calls it little; scripts on prefabs that no
   keyword assigns are listed apart (candidates for ``notes/map-keywords.json``).
3. REA (``dumps/rea/**/*.json`` evidence with ``call_edges``, one record or a bundle, once per artifact): caller
   type -> target type edges, folded into system -> system edges (works for IL2CPP too, where the code index may be
   thin). With a code index, only callers that are the game's own types count (third-party assemblies inspected by
   ``rea.managed_members`` would otherwise land in systems by keyword).

Each system gets a score (code uses + prefab usage + REA edges) to order the study; nothing here is a verified
fact - the notes and ``ARCHITECTURE.md`` confirm them.
"""

from __future__ import annotations

import collections
import time
from pathlib import Path

from gst.analysis import ANY, Report, analysis, counted, md_table


def rea_edges(rea_dir: Path, ctx=None) -> tuple[collections.Counter, int, list[str]]:
    """``(caller type, target type) -> calls`` from every REA Evidence JSON with ``call_edges`` (one file at a time).

    Reads both shapes: a bundle (``{"records": [...]}``, the manual studies' exports) and one record (``rea <cmd>
    --json``, what ``gst study`` writes to ``dumps/rea/evidence/<op>/<name>.json``). Side files (``*.request.json``,
    ``*.run.json``, ``*.error.json``, ``split/``) are skipped, and an artifact already counted from an earlier file
    (``evidence/`` first, then the rest) is not counted twice."""
    from gst.rea import evidence
    from gst.rea.import_index import evidence_files

    rea_dir = Path(rea_dir)
    paths = evidence_files(rea_dir.parent.parent) if rea_dir.name == "rea" and rea_dir.parent.name == "dumps" else \
        sorted(p for p in rea_dir.rglob("*.json") if not p.name.endswith((".request.json", ".run.json", ".error.json")))
    edges: collections.Counter = collections.Counter()
    files = []
    seen: set[str] = set()
    n = 0
    for p in paths:
        doc = evidence.load(p)
        found = False
        for rec in evidence.records(doc):
            nr = evidence.result(rec)
            if not nr.get("call_edges"):
                continue
            art = evidence.artifact(rec)
            key = art["sha256"] or art["path"]
            if key and key in seen:
                continue
            seen.add(key)
            by_token = {str(m.get("token")): str(m.get("declaring_type") or "") for m in nr.get("methods") or []
                        if m.get("token")}
            for e in nr["call_edges"]:
                src = by_token.get(str(e.get("caller_token"))) or evidence.split_member(str(e.get("caller") or ""))[0]
                dst = str(e.get("target_declaring_type") or "")
                if src and dst:
                    edges[(_short(src), _short(dst))] += 1
                    n += 1
                    found = True
        del doc
        if found:
            files.append(p.as_posix())
            if ctx is not None:
                ctx.use(f"rea:{p.name}", p)
    return edges, n, files


def _short(type_name: str) -> str:
    """``Ns.Outer/Inner`1`` -> ``Inner``: the index keys types by simple name."""
    from gst.rea.evidence import short_type

    return short_type(type_name)


@analysis("systems", engines=(ANY,), needs=("code.index",), uses=("analysis:prefabs", "dumps.rea"),
          description="Candidate gameplay systems: code index keyword map + scripts on prefabs + REA call graph",
          version=2, options={})
def systems(ctx) -> Report:
    from gst import mapping

    t0 = time.time()
    m = mapping.compute(ctx.workspace, with_members=True)
    kw = ctx.workspace / "notes" / "map-keywords.json"
    if kw.is_file():
        ctx.use("notes/map-keywords.json", kw)
    system_of: dict[str, str | None] = m.pop("system_of")
    inbound: dict[str, int] = m.pop("inbound")
    type_info: dict[str, dict] = m.pop("type_info")
    patterns = mapping._systems(ctx.workspace)

    pref = ctx.load("prefabs")
    scripts: dict[str, dict] = (pref or {}).get("data", {}).get("scripts", {}) if pref else {}
    rea_dir = ctx.workspace / "dumps" / "rea"
    edges, n_edges, rea_files = rea_edges(rea_dir, ctx) if rea_dir.is_dir() else (collections.Counter(), 0, [])

    def system_for(name: str) -> str | None:
        if name in system_of:
            return system_of[name]
        return mapping.assign(name, patterns)

    by_sys: dict[str, dict] = {}
    for s in m["systems"]:
        by_sys[s["label"]] = {**s, "prefab_scripts": [], "prefabs": 0, "rea_out": {}, "rea_calls": 0}
    unassigned_scripts = []
    for script, use in scripts.items():
        label = system_for(script)
        entry = {"script": script, "prefabs": use["prefabs"], "components": use["components"],
                 "in_code": script in type_info}
        if script in type_info:
            entry.update(path=type_info[script]["path"], line=type_info[script]["line"])
        if label is None or label not in by_sys:
            if label is None:
                unassigned_scripts.append(entry)
                continue
            by_sys[label] = {"label": label, "types": 0, "files": 0, "modules": {}, "top": [], "uses": {},
                             "prefab_scripts": [], "prefabs": 0, "rea_out": {}, "rea_calls": 0}
        by_sys[label]["prefab_scripts"].append(entry)
        by_sys[label]["prefabs"] += use["prefabs"]
    rea_sys: collections.Counter = collections.Counter()
    # REA: whole-assembly Evidence (every call of Assembly-CSharp and of the third-party assemblies) would swamp the
    # score, so a caller must be one of the game's own types (the code index) when the index has types (IL2CPP with a
    # thin index keeps every edge), and a call counts for its system only when it lands on a game system.
    own_types = set(type_info)
    n_game = 0
    for (a, b), n in edges.items():
        if own_types and a not in own_types:
            continue
        sa, sb = system_for(a), system_for(b)
        if sa and sb and sa != sb:
            rea_sys[(sa, sb)] += n
        if sa in by_sys and sb:
            by_sys[sa]["rea_calls"] += n
            n_game += n
    for (a, b), n in rea_sys.items():
        if a in by_sys:
            by_sys[a]["rea_out"][b] = by_sys[a]["rea_out"].get(b, 0) + n
    out = []
    for label, s in by_sys.items():
        code_refs = sum(t["refs"] for t in s.get("top", []))
        s["prefab_scripts"].sort(key=lambda e: (-e["prefabs"], e["script"]))
        s["rea_out"] = counted(s["rea_out"])
        s["score"] = code_refs + s["prefabs"] + s["rea_calls"]
        s["code_refs"] = code_refs
        out.append(s)
    out.sort(key=lambda s: (-s["score"], s["label"]))
    unassigned_scripts.sort(key=lambda e: (-e["prefabs"], e["script"]))
    summary = {"systems": len(out), "types": m["types"], "unassigned_types": m["unassigned"],
               "prefab_scripts": len(scripts), "unassigned_prefab_scripts": len(unassigned_scripts),
               "rea_edges": n_edges, "rea_edges_between_systems": n_game}
    data = {"systems": out, "unassigned_types_top": m["unassigned_top"], "unassigned_prefab_scripts": unassigned_scripts,
            "rea": {"files": rea_files, "edges": n_edges,
                    "system_edges": [{"from": a, "to": b, "calls": n} for (a, b), n in
                                     sorted(rea_sys.items(), key=lambda kv: (-kv[1], kv[0]))]},
            "sources": {"code_index": True, "prefabs": pref is not None, "rea": bool(rea_files)},
            "seconds_map": round(time.time() - t0, 2)}
    return Report(data=data, markdown=markdown(out, unassigned_scripts, rea_sys, m, pref is not None, bool(rea_files)),
                  summary=summary)


def markdown(out: list[dict], unassigned: list[dict], rea_sys, m: dict, have_prefabs: bool, have_rea: bool) -> str:
    md = [f"{len(out)} candidate systems over {m['types']} types ({m['unassigned']} types without a system). Sources: code "
          f"index{', prefabs' if have_prefabs else ''}{', REA call graph' if have_rea else ''}. Score = uses in code (top "
          "types) + prefabs carrying the system's scripts + REA calls from the game's own types into game systems. "
          "Keywords: `notes/map-keywords.json`.", "",
          md_table(["System", "Score", "Types", "Code uses", "Prefabs", "REA calls", "Main types", "Scripts on most prefabs"],
                   [(s["label"], s["score"], s["types"], s["code_refs"], s["prefabs"], s["rea_calls"],
                     ", ".join(f"`{t['name']}`" for t in s.get("top", [])[:4]) or "-",
                     ", ".join(f"`{e['script']}` ({e['prefabs']})" for e in s["prefab_scripts"][:4]) or "-") for s in out]), ""]
    edges = sorted(((a["label"], b, c) for a in out for b, c in a.get("uses", {}).items()), key=lambda e: (-e[2], e[0], e[1]))[:24]
    if edges:
        used = sorted({a for a, _b, _c in edges} | {b for _a, b, _c in edges})
        node = {label: f"s{i}" for i, label in enumerate(used)}
        md += ["## Dependencies (calls in the code index)", "", "```mermaid", "graph LR"]
        md += [f'    {node[label]}["{label}"]' for label in used]
        md += [f"    {node[a]} -->|{c}| {node[b]}" for a, b, c in edges]
        md += ["```", ""]
    if rea_sys:
        md += ["## REA call graph between systems", "", md_table(["From", "To", "Calls"],
                                                                 [(a, b, n) for (a, b), n in sorted(rea_sys.items(), key=lambda kv: (-kv[1], kv[0]))[:40]]), ""]
    if unassigned:
        md += [f"## Scripts on prefabs without a system ({len(unassigned)})", "",
               "Add keywords for them in `notes/map-keywords.json` (`{\"System\": \"regex\"}`) and run `gst analyze systems` again.", "",
               md_table(["Script", "Prefabs", "Components", "Code"],
                        [(f"`{e['script']}`", e["prefabs"], e["components"],
                          f"`{e['path']}:{e['line']}`" if e.get("path") else "-") for e in unassigned[:60]]), ""]
    return "\n".join(md)
