"""`gst lib compare <facet> <game...>`: one pt-BR Markdown table per facet from library.db, each game's sources cited.

Output: `GS_HOME/_library/compare/<facet>-<key>_<key>....md` = the table (one column per game, one row per
dimension), the sources and highlights per game (`<slug>:<file>` + `ref` inside the JSON), and "Não verificado /
lacunas" (missing analyses, what an engine's analysis does not tell, caveats of the facet). It is the input of the
`comparer` agent and of the `comparativo` note template (`{{auto:library}}`).
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from gst.common import GsError, write_text
from gst.library import connect, facets, library_dir, resolve_games

FACETS = ("rigs", "joints", "animation", "audio", "middleware", "systems", "shaders", "dcc")
ALIASES = {"rig": "rigs", "skeleton": "rigs", "skeletons": "rigs", "ragdoll": "joints", "ragdolls": "joints",
           "physics": "joints", "fisica": "joints", "joint": "joints", "anim": "animation", "animations": "animation",
           "clips": "animation", "sound": "audio", "som": "audio", "net": "middleware", "network": "middleware",
           "rede": "middleware", "voice": "middleware", "system": "systems", "sistemas": "systems", "shader": "shaders",
           "materials": "shaders", "origin": "dcc"}
TITLES = {"rigs": "rigs e esqueletos", "joints": "ragdoll e joints", "animation": "animação", "audio": "áudio",
          "middleware": "middleware (rede, voz, plataforma, SDKs)", "systems": "sistemas do código",
          "shaders": "shaders e materiais", "dcc": "origem DCC dos assets"}
CAVEATS = {
    "rigs": ["Ossos por rig não são a mesma medida em todas as engines: Unity = ossos do SkinnedMeshRenderer (`rig.json` "
             "`data.smr[*].bones`), Godot = juntas por skin dos .glb exportados (`gltf_summary.json`) ou Skeleton3D "
             "salvos em cenas, e o Skeleton3D que a cena de gameplay instancia (cena -> .glb -> cena importada, "
             "`godot_animation.json` `data.characters`); Unreal = ossos do ReferenceSkeleton de cada Skeleton e "
             "SkeletalMesh exportado em JSON (`unreal_animation.json`), os demais pacotes só contados pelo nome.",
             "Um mesmo esqueleto aparece uma vez por malha com skin (roupas e acessórios repetem o rig do personagem)."],
    "joints": ["Unity: joints dos prefabs detalhados (`joints.json`); ragdoll = prefab com uma classe `*Ragdoll*`/PuppetMaster. "
               "Godot: PhysicalBone3D + Joint3D das cenas (`godot_animation.json`). Unreal: PhysicsAsset = ragdoll "
               "(corpos por osso, constraints com limites; `unreal_animation.json`). Ragdoll montado só em código não aparece."],
    "animation": ["Clips Godot embutidos em .glb vêm do `gltf_summary` (fps = 1/menor intervalo entre chaves); "
                  "Animation salva em cena vem do `godot_animation`."],
    "audio": ["Eventos = `audio_catalog.json` (evento -> arquivos -> quem toca); formatos só quando `gst audio index` "
              "gerou `assets/audio/files.jsonl`."],
    "middleware": ["Por nome de arquivo na instalação (recon): presença de SDK não prova uso em jogo."],
    "systems": ["Sistemas = agrupamento por palavras-chave dos nomes de tipo (`gst map`), não uma leitura do código."],
    "shaders": ["Godot: `godot_shaders.json` (.gdshader, Shader embutido e VisualShader); \"assinaturas\" = traços por "
                "palavra-chave (toon, outline, dissolve...), inferência; materiais = ShaderMaterial que apontam para o "
                "shader. Unity: `shaders.json`."],
    "dcc": ["Origem DCC é **inferência** por padrões de nome e transform (`dcc.json` `kind: inference`)."],
}
NONE = "—"
# gaps of other facets a table also uses (the rigs table has a DCC row)
RELATED = {"rigs": ("dcc",)}


def facet_name(name: str) -> str:
    key = ALIASES.get(name.lower(), name.lower())
    if key not in FACETS:
        raise GsError(f"unknown facet '{name}' (facets: {', '.join(FACETS)}; aliases: {', '.join(sorted(ALIASES))})")
    return key


@dataclass
class Column:
    cells: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)      # workspace-relative files used
    details: list[str] = field(default_factory=list)      # per-game highlight lines, each with its ref


def _rows(conn: sqlite3.Connection, table: str, game: str) -> list[sqlite3.Row]:
    return conn.execute(f"SELECT * FROM {table} WHERE game = ? ORDER BY id", (game,)).fetchall()


def _sources(rows) -> list[str]:
    return sorted({r["source"] for r in rows if r["source"]})


def _n(value) -> str:
    return str(int(value)) if float(value).is_integer() else str(round(value, 3))


def _span(values) -> str:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return NONE
    return f"{_n(vals[0])} / {_n(statistics.median(vals))} / {_n(vals[-1])}"


def _top(counter: Counter | dict, n: int = 5, fmt: str = "{k} {v}") -> str:
    items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    if not items:
        return NONE
    return ", ".join(fmt.format(k=k, v=v) for k, v in items[:n]) + (f" (+{len(items) - n})" if len(items) > n else "")


def _cite(g: sqlite3.Row, row) -> str:
    return f"`{g['slug']}:{row['source']}` `{row['ref']}`"


def _counts(conn, g, types) -> tuple[str, list[str]]:
    rows = [r for r in _rows(conn, "asset_counts", g["key"]) if r["type"] in types]
    if not rows:
        return NONE, []
    return ", ".join(f"{r['type']} {r['count']}" for r in rows), _sources(rows)


def _dcc_cell(conn, g) -> tuple[str, list[str]]:
    rows = _rows(conn, "dcc", g["key"])
    if not rows:
        missing = conn.execute("SELECT 1 FROM gaps WHERE game = ? AND analysis = 'dcc'", (g["key"],)).fetchone()
        return ("sem `analysis/dcc.json`" if missing else NONE), []
    present = [r["family"] for r in rows if r["verdict"] == "present"]
    weak = [r["family"] for r in rows if r["verdict"] == "weak"]
    text = ", ".join(present) or "nenhuma forte"
    return text + (f" (fracas: {', '.join(weak)})" if weak else "") + " [I]", _sources(rows)


# --- facets: (conn, game row) -> Column ----------------------------------------------------------------

def col_rigs(conn, g) -> Column:
    c = Column()
    rigs = _rows(conn, "rigs", g["key"])
    kinds = Counter(r["kind"] for r in rigs)
    parts = []
    if kinds.get("unity-smr"):
        parts.append(f"{kinds['unity-smr']} SkinnedMeshRenderer em {len({r['item'] for r in rigs if r['kind'] == 'unity-smr'})} prefabs")
    if kinds.get("gltf-skin"):
        parts.append(f"{kinds['gltf-skin']} skins em {len({r['item'] for r in rigs if r['kind'] == 'gltf-skin'})} .glb")
    if kinds.get("godot-skeleton"):
        parts.append(f"{kinds['godot-skeleton']} Skeleton3D em cenas")
    if kinds.get("unreal-skelmesh"):
        parts.append(f"{kinds['unreal-skelmesh']} SkeletalMesh")
    if kinds.get("unreal-skeleton"):
        parts.append(f"{kinds['unreal-skeleton']} Skeleton (Unreal)")
    if kinds.get("godot-character"):
        parts.append(f"{kinds['godot-character']} esqueletos ligados a cenas de gameplay "
                     f"({len({r['item'] for r in rigs if r['kind'] == 'godot-character'})} cenas)")
    c.cells["malhas com skin"] = "; ".join(parts) or (NONE if not rigs else str(len(rigs)))
    # Godot with scene -> model -> skeleton links: measure the skeletons the scenes use, not every .glb skin
    linked = [r for r in rigs if r["kind"] == "godot-character"]
    measured = [r for r in rigs if r["kind"] != "gltf-skin"] if linked else rigs
    bones = [r["bones"] for r in measured if r["bones"]]
    c.cells["ossos por rig (mín / mediana / máx)"] = _span(bones)
    c.cells["contagens de ossos mais comuns (ossos ×rigs)"] = _top(Counter(bones), 5, "{k} ×{v}")
    deform = [r["deform_bones"] for r in rigs if r["deform_bones"] is not None]
    c.cells["ossos deformantes (máx)"] = str(max(deform)) if deform else NONE
    binds = [r["bindposes"] for r in rigs if r["bindposes"] is not None]
    c.cells["bindposes (mín–máx)"] = f"{min(binds)}–{max(binds)}" if binds else NONE
    infl = [r["max_influences"] for r in rigs if r["max_influences"] is not None]
    c.cells["influências por vértice (máx)"] = str(max(infl)) if infl else NONE
    c.cells["malhas com blendshapes"] = str(sum(1 for r in rigs if r["blendshapes"])) if rigs else NONE
    avatars = _rows(conn, "avatars", g["key"])
    has_avatar = conn.execute("SELECT 1 FROM sources WHERE game = ? AND name = 'avatar'", (g["key"],)).fetchone()
    if avatars or has_avatar:
        hum = [a for a in avatars if a["humanoid"]]
        c.cells["Avatar (humanoid / generic)"] = f"{len(hum)} / {len(avatars) - len(hum)}" + (
            " (" + ", ".join(f"{a['name']} {a['human_bones']} ossos humanos" for a in hum[:3]) + ")" if hum else "")
        c.sources += _sources(avatars) or ["analysis/avatar.json"]
    else:
        c.cells["Avatar (humanoid / generic)"] = NONE
    cons = _rows(conn, "constraints", g["key"])
    c.cells["constraints de rig"] = _top(Counter(r["type"] for r in cons), 5) if cons else NONE
    c.cells["pacotes Unreal (pelo nome)"], src = _counts(conn, g, facets.UNREAL_TYPES["rigs"])
    c.sources += src
    c.cells["origem DCC"], src = _dcc_cell(conn, g)
    c.sources += src + _sources(rigs) + _sources(cons)
    def drive(r) -> int:  # Godot links: how much drives the skeleton (animations, trees, ragdoll bodies)
        try:
            links = json.loads(r["links"] or "{}")
        except ValueError:
            links = {}
        return sum(int(links.get(k) or 0) for k in ("animations", "trees", "ragdoll_bodies"))

    distinct: dict[tuple, list] = {}  # one line per (prefab/file, bones, root): clothes repeat the body's rig
    for r in sorted(measured, key=lambda r: (-drive(r), -(r["bones"] or 0), r["id"])):
        distinct.setdefault((r["item"], r["bones"], r["root_bone"]), []).append(r)
    for (item, n, root), same in list(distinct.items())[:6]:
        r = same[0]
        bones = f"{n} ossos" if n is not None else "ossos não lidos (esqueleto não exportado)"
        links = json.loads(r["links"] or "{}") if r["kind"] == "godot-character" else {}
        chain = (f"; modelo `{r['mesh']}`, {links.get('animations', 0)} animações, {links.get('trees', 0)} AnimationTree, "
                 f"{links.get('ragdoll_bodies', 0)} corpos de ragdoll") if links else ""
        c.details.append(f"`{item}` {r['node'] or ''} — {bones}" + (f", raiz `{root}`" if root else "") + chain
                         + (f", {r['verts']} vértices" if r["verts"] else "")
                         + (f"; {len(same)} malhas com este rig" if len(same) > 1 else "") + f" ({_cite(g, r)}"
                         + (f"; detalhe `{g['slug']}:{r['detail']}`" if r["detail"] else "") + ")")
    return c


def col_joints(conn, g) -> Column:
    c = Column()
    joints = _rows(conn, "joints", g["key"])
    rags = _rows(conn, "ragdolls", g["key"])
    c.cells["joints"] = str(len(joints)) if joints else NONE
    c.cells["joints por tipo"] = _top(Counter(r["type"] for r in joints), 6) if joints else NONE
    c.cells["prefabs/cenas com joints"] = str(len({r["item"] for r in joints})) if joints else NONE
    c.cells["ragdolls"] = (f"{len(rags)}: " + ", ".join(f"`{r['item']}`" for r in rags[:4])
                           + (f" (+{len(rags) - 4})" if len(rags) > 4 else "")) if rags else "0" if joints else NONE
    c.cells["corpos por ragdoll (mín / mediana / máx)"] = _span(r["bodies"] for r in rags)
    c.cells["joints por ragdoll (mín / mediana / máx)"] = _span(r["joints"] for r in rags)
    c.cells["massa total por ragdoll"] = _span(r["mass_total"] for r in rags)
    types = Counter()
    for r in rags:
        types.update(json.loads(r["joint_types"] or "{}"))
    c.cells["tipos de joint no ragdoll"] = _top(types, 4)
    classes = sorted({k for r in rags for k in json.loads(r["classes"] or "[]")})
    c.cells["scripts/classes de ragdoll"] = ", ".join(f"`{k}`" for k in classes[:6]) + (" ..." if len(classes) > 6 else "") if classes else NONE
    springs = []
    for r in joints:
        drives = (json.loads(r["settings"] or "{}").get("drives") or {})
        spring = (drives.get("AngularX") or drives.get("Slerp") or {}).get("spring") if isinstance(drives, dict) else None
        if isinstance(spring, (int, float)) and spring:
            springs.append(spring)
    c.cells["mola angular dos drives (mín / mediana / máx)"] = _span(springs)
    c.cells["pacotes Unreal (pelo nome)"], src = _counts(conn, g, facets.UNREAL_TYPES["joints"])
    c.sources += src + _sources(joints) + _sources(rags)
    for r in rags[:6]:
        c.details.append(f"ragdoll `{r['item']}` — {r['bodies']} corpos, {r['joints']} joints "
                         f"({', '.join(f'{k} {v}' for k, v in json.loads(r['joint_types'] or '{}').items()) or NONE}), "
                         f"massa {r['mass_total']} ({_cite(g, r)}" + (f"; detalhe `{g['slug']}:{r['detail']}`" if r["detail"] else "") + ")")
    return c


def col_animation(conn, g) -> Column:
    c = Column()
    clips = _rows(conn, "clips", g["key"])
    graphs = _rows(conn, "anim_graphs", g["key"])
    kinds = Counter(r["kind"] for r in clips)
    c.cells["clips"] = (f"{len(clips)} (" + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) + ")") if clips else NONE
    lengths = [r["length"] for r in clips if r["length"] is not None]
    c.cells["duração total (s)"] = str(round(sum(lengths), 1)) if lengths else NONE
    c.cells["duração por clip, s (mín / mediana / máx)"] = _span(round(v, 3) for v in lengths)
    c.cells["fps mais comuns"] = _top(Counter(r["fps"] for r in clips if r["fps"]), 3, "{k} ×{v}") if clips else NONE
    c.cells["clips em loop"] = str(sum(1 for r in clips if r["loop"])) if clips else NONE
    c.cells["tipo de clip"] = _top(Counter(r["clip_kind"] for r in clips if r["clip_kind"]), 5) if clips else NONE
    c.cells["root motion"] = str(sum(1 for r in clips if r["root_motion"])) if any(r["kind"] == "unity" for r in clips) else NONE
    ev = [r["events"] for r in clips if r["events"]]
    c.cells["eventos (clips com evento / total)"] = f"{len(ev)} / {sum(ev)}" if clips else NONE
    gk = Counter(r["kind"] for r in graphs)
    c.cells["grafos (controllers / AnimationTrees)"] = ", ".join(f"{k} {v}" for k, v in sorted(gk.items())) if graphs else NONE
    c.cells["estados / transições nos grafos"] = (f"{sum(r['states'] or 0 for r in graphs)} / "
                                                  f"{sum(r['transitions'] or 0 for r in graphs)}") if graphs else NONE
    params = [r["params"] for r in graphs if r["params"] is not None]
    c.cells["parâmetros dos controllers"] = str(sum(params)) if params else NONE
    c.cells["pacotes Unreal (pelo nome)"], src = _counts(conn, g, facets.UNREAL_TYPES["animation"])
    c.sources += src + _sources(clips) + _sources(graphs)
    for r in sorted(graphs, key=lambda r: (-(r["states"] or 0), -(r["transitions"] or 0), r["id"]))[:4]:
        c.details.append(f"grafo `{r['name']}` ({r['kind']}) — {r['states']} estados, {r['transitions']} transições"
                         + (f", {r['params']} parâmetros" if r["params"] is not None else "") + f" ({_cite(g, r)})")
    for r in sorted([r for r in clips if r["events"]], key=lambda r: (-r["events"], r["id"]))[:3]:
        c.details.append(f"clip `{r['name']}` — {r['events']} eventos, {r['length']} s ({_cite(g, r)})")
    return c


def col_audio(conn, g) -> Column:
    c = Column()
    events = _rows(conn, "audio", g["key"])
    files = _rows(conn, "audio_files", g["key"])
    c.cells["eventos de som"] = str(len(events)) if events else NONE
    c.cells["eventos por tipo"] = _top(Counter(r["kind"] for r in events), 6) if events else NONE
    cats = Counter(r["category"] for r in events if r["category"])
    c.cells["categorias (top)"] = (f"{len(cats)}: " + _top(cats, 5)) if cats else NONE
    c.cells["clips referenciados (com repetição)"] = str(sum(r["clips"] or 0 for r in events)) if events else NONE
    c.cells["eventos sem arquivo exportado"] = str(sum(1 for r in events if r["clips"] and not r["files"])) if events else NONE
    c.cells["eventos com quem toca (código/cena)"] = str(sum(1 for r in events if r["users"])) if events else NONE
    if files:
        hours = sum(r["seconds"] or 0 for r in files) / 3600
        c.cells["formatos (arquivos exportados)"] = (", ".join(f"{r['format'] or r['ext']} {r['rate'] or '?'} Hz {r['channels'] or '?'}ch ×{r['files']}"
                                                               for r in files[:4]) + (f" (+{len(files) - 4})" if len(files) > 4 else "")
                                                     + f"; {round(hours, 2)} h")
    else:
        c.cells["formatos (arquivos exportados)"] = NONE
    mw = [r for r in _rows(conn, "middleware", g["key"]) if re.search(r"fmod|wwise|xaudio|cri|vorbis|ogg|opus|steamaudio|resonance|bink",
                                                                       r["name"] or "", re.I)]
    c.cells["middleware de áudio (recon)"] = ", ".join(sorted({r["name"] for r in mw})) if mw else NONE
    c.cells["pacotes Unreal (pelo nome)"], src = _counts(conn, g, facets.UNREAL_TYPES["audio"])
    c.sources += src + _sources(events) + _sources(files) + _sources(mw)
    for cat, n in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))[:5]:
        first = next(r for r in events if r["category"] == cat)
        c.details.append(f"categoria `{cat}` — {n} eventos (ex.: `{first['name']}`, {_cite(g, first)})")
    return c


def col_middleware(conn, g) -> Column:
    c = Column()
    rows = _rows(conn, "middleware", g["key"])
    for kind, label in (("network", "rede"), ("voice", "voz"), ("platform", "plataforma"), ("sdk", "SDKs (regras do recon)")):
        names = sorted({r["name"] for r in rows if r["kind"] == kind})
        c.cells[label] = ", ".join(names) if names else NONE
    c.sources += _sources(rows)
    for r in rows:
        if r["kind"] != "sdk":
            ev = json.loads(r["evidence"] or "[]")
            c.details.append(f"{r['kind']}: {r['name']} — evidência `{'`, `'.join(ev) or NONE}` ({_cite(g, r)})")
    return c


def col_systems(conn, g) -> Column:
    c = Column()
    rows = _rows(conn, "systems", g["key"])
    c.cells["sistemas"] = str(len(rows)) if rows else NONE
    c.cells["tipos atribuídos"] = str(sum(r["types"] or 0 for r in rows)) if rows else NONE
    c.cells["maiores (tipos)"] = _top({r["label"]: r["types"] or 0 for r in rows}, 6) if rows else NONE
    c.sources += _sources(rows)
    for r in sorted(rows, key=lambda r: (-(r["types"] or 0), r["id"]))[:8]:
        top = json.loads(r["top"] or "[]")[:3]
        c.details.append(f"{r['label']} — {r['types']} tipos; centrais: "
                         + (", ".join(f"`{t['name']}` (`{g['slug']}:{t['path']}:{t['line']}`)" for t in top) or NONE)
                         + f" ({_cite(g, r)})")
    return c


def col_shaders(conn, g) -> Column:
    c = Column()
    rows = _rows(conn, "shaders", g["key"])
    c.cells["shaders"] = str(len(rows)) if rows else NONE
    c.cells["materiais que os usam"] = str(sum(r["materials"] or 0 for r in rows)) if rows else NONE
    fams = Counter()
    for r in rows:
        fams[r["family"] or "?"] += r["materials"] or 0
    c.cells["famílias (por materiais)"] = _top(fams, 5) if rows else NONE
    sigs = Counter(s for r in rows for s in json.loads(r["signatures"] or "[]"))
    c.cells["assinaturas (amplify, shader graph...)"] = _top(sigs, 5) if rows else NONE
    passes = [r["passes"] for r in rows if r["passes"] is not None]
    c.cells["passes (máx)"] = str(max(passes)) if passes else NONE
    unis = [r["uniforms"] for r in rows if r["uniforms"] is not None]
    c.cells["uniforms (total / máx por shader)"] = f"{sum(unis)} / {max(unis)}" if unis else NONE
    c.cells["pacotes Unreal (pelo nome)"], src = _counts(conn, g, facets.UNREAL_TYPES["shaders"])
    c.sources += src + _sources(rows)
    for r in sorted(rows, key=lambda r: (-(r["materials"] or 0), r["id"]))[:5]:
        extra = f", {r['uniforms']} uniforms" if r["uniforms"] is not None else ""
        feats = json.loads(r["signatures"] or "[]")
        c.details.append(f"`{r['name']}` ({r['family'] or '?'}) — {r['materials']} materiais"
                         + (f", {r['passes']} passes" if r["passes"] is not None else "") + extra
                         + (f"; traços: {', '.join(feats)}" if feats else "") + f" ({_cite(g, r)})")
    return c


def col_dcc(conn, g) -> Column:
    c = Column()
    rows = _rows(conn, "dcc", g["key"])
    c.cells["presentes [I]"] = ", ".join(r["family"] for r in rows if r["verdict"] == "present") or NONE if rows else NONE
    c.cells["fracas [I]"] = ", ".join(r["family"] for r in rows if r["verdict"] == "weak") or NONE if rows else NONE
    c.cells["ambíguas"] = ", ".join(r["family"] for r in rows if r["verdict"] == "ambiguous") or NONE if rows else NONE
    c.sources += _sources(rows)
    for r in rows:
        if r["verdict"] in ("present", "weak"):
            rules = json.loads(r["rules"] or "{}")
            c.details.append(f"{r['family']} ({r['verdict']}): fortes {r['strong']}, fracas {r['weak']}; regras "
                             + (", ".join(f"{k} {v}" for k, v in rules.items()) or NONE) + f" ({_cite(g, r)})")
    return c


COLUMNS = {"rigs": col_rigs, "joints": col_joints, "animation": col_animation, "audio": col_audio,
           "middleware": col_middleware, "systems": col_systems, "shaders": col_shaders, "dcc": col_dcc}


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _engine(g) -> str:
    return f"{g['engine_label'] or g['engine'] or '?'} {g['engine_version'] or ''}".strip()


def _gaps(conn, g, facet: str) -> list[sqlite3.Row]:
    names = (facet,) + RELATED.get(facet, ())
    return conn.execute(f"SELECT * FROM gaps WHERE game = ? AND facet IN ({', '.join('?' * len(names))}) "
                        "ORDER BY facet != ?, kind, analysis", (g["key"], *names, facet)).fetchall()


def collect(conn: sqlite3.Connection, facet: str, games: list[sqlite3.Row]) -> dict[str, Column]:
    return {g["key"]: COLUMNS[facet](conn, g) for g in games}


def table(conn: sqlite3.Connection, facet: str, games: list[sqlite3.Row], cols: dict[str, Column] | None = None) -> str:
    """The comparison table alone (also used by the `comparativo` note template)."""
    cols = cols or collect(conn, facet, games)
    dims = list(next(iter(cols.values())).cells) if cols else []
    dims = [d for d in dims if any(cols[g["key"]].cells.get(d, NONE) != NONE for g in games)]  # rows nobody has
    lines = ["| dimensão | " + " | ".join(_cell(g["name"]) for g in games) + " |", "|---" * (len(games) + 1) + "|",
             "| engine | " + " | ".join(_cell(_engine(g)) for g in games) + " |"]
    for dim in dims:
        values = []
        for g in games:
            value = cols[g["key"]].cells.get(dim, NONE)
            if value == NONE and _gaps(conn, g, facet) and not cols[g["key"]].sources:
                value = "sem dados (lacuna)"
            values.append(_cell(value))
        lines.append(f"| {dim} | " + " | ".join(values) + " |")
    lines.append("| fontes | " + " | ".join(_cell(", ".join(f"`{s}`" for s in sorted(set(cols[g['key']].sources))) or NONE)
                                          for g in games) + " |")
    return "\n".join(lines)


def render(conn: sqlite3.Connection, facet: str, games: list[sqlite3.Row], command: str) -> str:
    meta = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM meta")}
    cols = collect(conn, facet, games)
    out = [f"# Comparativo: {TITLES[facet]} ({', '.join(g['name'] for g in games)})", "",
           f"> Gerado por `{command}` em {time.strftime('%Y-%m-%d %H:%M')} a partir de `_library/library.db` "
           f"(build {meta.get('built_at', '?')}).",
           "> Cada célula sai das análises do workspace do jogo (linha \"fontes\"); cite como `<slug>:<arquivo>` + `ref`.",
           "> Legenda: **[E]** lido nos dados de um jogo (todas as células, salvo marcação); **[I]** inferência; "
           f"{NONE} = a análise existe e não tem o item, ou a engine não tem a análise (ver lacunas).", "",
           "## Tabela", "", table(conn, facet, games, cols), "", "## Por jogo", ""]
    for g in games:
        col = cols[g["key"]]
        out.append(f"### {g['name']} (`{g['key']}`)")
        out.append("")
        out.append(f"- Engine: {_engine(g)}; build `{g['buildid'] or '?'}`; workspace `{g['workspace']}`.")
        src = {r["path"]: r for r in conn.execute("SELECT * FROM sources WHERE game = ?", (g["key"],))}
        used = sorted(set(col.sources))
        if used:
            out.append("- Fontes: " + "; ".join(
                f"`{g['slug']}:{p}`" + (f" (v{src[p]['version']}, {src[p]['generated_at']})" if p in src and src[p]["version"]
                                        else f" ({src[p]['generated_at']})" if p in src and src[p]["generated_at"] else "")
                for p in used) + ".")
        else:
            out.append("- Fontes: nenhuma análise desta faceta no workspace.")
        out += [f"- {d}" for d in col.details]
        out.append("")
    out += ["## Não verificado / lacunas", ""]
    any_gap = False
    for g in games:
        for gap in _gaps(conn, g, facet):
            any_gap = True
            label = {"missing": "falta", "partial": "parcial", "no-analysis": "sem análise"}.get(gap["kind"], gap["kind"])
            out.append(f"- **{g['name']}** ({label}): {gap['reason']}")
    out += [f"- {c}" for c in CAVEATS.get(facet, [])]
    if not any_gap and not CAVEATS.get(facet):
        out.append("- Nenhuma lacuna registrada pela biblioteca; o que as análises não medem continua fora da tabela.")
    out.append("")
    return "\n".join(out)


def out_path(home: Path | None, facet: str, games: list[sqlite3.Row]) -> Path:
    keys = "_".join(re.sub(r"[^\w@.-]", "-", g["key"]) for g in games) or "all"
    return library_dir(home) / "compare" / f"{facet}-{keys}.md"


def compare(facet: str, names: list[str], *, home: Path | None = None) -> dict:
    """Write the comparison file; returns ``{path, facet, games, gaps}``."""
    facet = facet_name(facet)
    conn = connect(home)
    try:
        games = resolve_games(conn, names, analysis_only=True)
        if not games:
            raise GsError("the library has no workspace to compare (gst lib build)")
        command = f"gst lib compare {facet} " + " ".join(g["key"] for g in games)
        text = render(conn, facet, games, command)
        gaps = [{"game": g["key"], "kind": r["kind"], "reason": r["reason"]} for g in games for r in _gaps(conn, g, facet)]
    finally:
        conn.close()
    path = out_path(home, facet, games)
    write_text(path, text)
    return {"path": path.as_posix(), "facet": facet, "games": [g["key"] for g in games], "gaps": gaps}
