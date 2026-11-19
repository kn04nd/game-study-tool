"""Note templates (`gst notes new <layer>/<topic> --template <name>`), pre-filled from `analysis/*.json`.

Templates live in `src/gst/data/templates/notes/<name>.md` (pt-BR). Besides `{{title}}`, `{{game}}`, `{{engine}}`,
`{{date}}`, `{{note}}` they hold `{{auto:<block>}}` lines that this module replaces with small tables computed from
the workspace's analyses (counts, summaries, the first rows of a list): cheap, deterministic, each table citing the
JSON it came from. An analysis that is missing becomes a line telling which `gst analyze` to run. What the agent must
write stays as `<!-- preencher: ... -->` comments (`unfilled` counts them).
"""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

from gst.common import GsError, read_json, read_text

# name -> default layer (folder under notes/) when the spec is only a topic
TEMPLATES: dict[str, str] = {
    "sistema": "logic",
    "mecanica": "logic",
    "ragdoll-fisica": "logic",
    "mapa-niveis": "logic",
    "rede-voz": "logic",
    "modelos-3d-dossie": "assets",
    "personagem-modelos-animacoes": "assets",
    "audio-catalogo": "assets",
    "comparativo": "compare",
}
AUTO = re.compile(r"^\{\{auto:([a-z_-]+)(?::([^}]*))?\}\}[ \t]*$", re.M)
UNFILLED = re.compile(r"<!--\s*preencher\b", re.I)
ROWS = 25


def template_names() -> list[str]:
    return sorted(TEMPLATES)


def template_text(name: str) -> str:
    if name not in TEMPLATES:
        raise GsError(f"unknown note template '{name}' (templates: {', '.join(template_names())})")
    return resources.files("gst").joinpath(f"data/templates/notes/{name}.md").read_text(encoding="utf-8")


def unfilled(text: str) -> int:
    """`<!-- preencher ... -->` placeholders still in a note."""
    return len(UNFILLED.findall(text))


# --- helpers ------------------------------------------------------------------------------------------

def _cell(value) -> str:
    text = value if isinstance(value, str) else str(value)
    return " ".join(text.split()).replace("|", "\\|")[:120]


def _table(headers: list[str], rows: list) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


class _Analyses:
    """Lazy loader of analysis/<name>.json; remembers which ones were used (for the header)."""

    def __init__(self, ws: Path):
        self.ws = ws
        self.used: list[str] = []
        self._cache: dict[str, dict | None] = {}

    def get(self, name: str) -> dict | None:
        if name not in self._cache:
            data = read_json(self.ws / "analysis" / f"{name}.json", None)
            self._cache[name] = data if isinstance(data, dict) else None
            if self._cache[name] is not None:
                self.used.append(f"analysis/{name}.json")
        return self._cache[name]

    def missing(self, *names: str) -> str:
        return "_(sem " + ", ".join(f"`analysis/{n}.json`" for n in names) + ": rode `gst analyze " + names[0] + \
               "` ou `gst study`; se a engine não tem essa análise, preencha à mão)_"


def _flat(summary: dict) -> list[tuple[str, str]]:
    rows = []
    for key, value in summary.items():
        if isinstance(value, dict):
            value = ", ".join(f"{k} {v}" for k, v in list(value.items())[:8])
        elif isinstance(value, list):
            value = ", ".join(map(str, value[:8]))
        rows.append((key, value))
    return rows


# --- blocks -------------------------------------------------------------------------------------------

def block_summary(an: _Analyses, arg: str, opts: dict) -> str:
    out = []
    names = [n.strip() for n in (arg or "").split(",") if n.strip()]
    for name in names:
        data = an.get(name)
        if data is None:
            continue
        out.append(f"`analysis/{name}.json` (summary):\n\n" + _table(["chave", "valor"], _flat(data.get("summary") or {})))
    return "\n\n".join(out) if out else an.missing(*names) if names else ""


def block_systems(an: _Analyses, arg: str, opts: dict) -> str:
    data = an.get("systems")
    if data is None:
        return an.missing("systems")
    rows = []
    for s in (data.get("data") or {}).get("systems") or []:
        top = s.get("top") or []
        rows.append((s.get("label"), s.get("types"),
                     ", ".join(f"`{t.get('name')}` ({t.get('path')}:{t.get('line')})" for t in top[:3])))
    return "Sistemas de `analysis/systems.json`:\n\n" + _table(["sistema", "tipos", "tipos centrais (caminho:linha)"], rows)


def _match_system(data: dict, pattern: str | None) -> dict | None:
    systems = (data.get("data") or {}).get("systems") or []
    if not pattern:
        return None
    rx = re.compile(pattern, re.I)
    for s in systems:
        if rx.search(s.get("label") or ""):
            return s
    for s in systems:
        if any(rx.search(t.get("name") or "") for t in s.get("top") or []):
            return s
    return None


def block_system(an: _Analyses, arg: str, opts: dict) -> str:
    data = an.get("systems")
    if data is None:
        return an.missing("systems")
    s = _match_system(data, opts.get("system"))
    if s is None:
        return ("_(nenhum sistema de `analysis/systems.json` casou com `--system`; liste com `gst map --list` e "
                "preencha os tipos à mão)_")
    rows = [(f"`{t.get('qname') or t.get('name')}`", f"{t.get('path')}:{t.get('line')}", t.get("refs"))
            for t in (s.get("top") or [])[:ROWS]]
    uses = ", ".join(f"{k} ({v})" for k, v in list((s.get("uses") or {}).items())[:8]) or "-"
    return (f"Sistema **{s.get('label')}** em `analysis/systems.json`: {s.get('types')} tipos. Usa: {uses}.\n\n"
            + _table(["tipo", "definição", "refs"], rows))


VALUE_TYPES = {"float", "double", "int", "long", "uint", "short", "byte", "bool", "string", "vector2", "vector3", "vector4",
               "vector2i", "vector3i", "color", "quaternion", "curve", "curve2d", "curve3d", "rect2", "basis", "transform3d"}
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?(?:e-?\d+)?f?$|^(?:true|false)$", re.I)


def is_value(info: dict) -> bool:
    """A tunable worth a row: numbers, flags, vectors, curves, enums; not node paths or resource references."""
    kind = str(info.get("type") or "").strip().lower().removesuffix("[]")
    if kind in VALUE_TYPES:
        return True
    values = list((info.get("values") or {}))
    default = str(info.get("default") or "")
    return bool(values or default) and all(_NUMBER.match(str(v).strip()) for v in values + ([default] if default else []))


def _godot_tunables(an: _Analyses, rx) -> list[tuple]:
    data = an.get("godot_scenes")
    if data is None:
        return []
    rows = []
    for script, entry in sorted(((data.get("data") or {}).get("tunables") or {}).items()):
        if rx and not rx.search(script):
            continue
        for prop, info in sorted((entry.get("exports") or {}).items()):
            if not is_value(info):
                continue
            seen = sorted((info.get("values") or {}).items(), key=lambda kv: -int((kv[1] or {}).get("count", 0)))
            values = ", ".join(f"{v} ×{(w or {}).get('count')}" for v, w in seen[:3]) or "-"
            rows.append((f"`{entry.get('class') or Path(script).stem}`", f"`{prop}`", info.get("type") or "",
                         info.get("default") if info.get("default") is not None else "", values,
                         entry.get("source") or script))
    return rows


def _unity_tunables(an: _Analyses, rx) -> list[tuple]:
    rows = []
    for name in ("mono_values", "catalogs"):
        data = an.get(name)
        if data is None:
            continue
        scripts = (data.get("data") or {}).get("scripts") or {}
        for script, entry in sorted(scripts.items(), key=lambda kv: (-int((kv[1] or {}).get("objects", 0) or 0), kv[0])):
            if rx and not rx.search(script):
                continue
            if not isinstance(entry, dict):
                continue
            fields = list((entry.get("fields") or {}))
            rows.append((f"`{script}`", ", ".join(f"`{f}`" for f in fields[:10]) + (" ..." if len(fields) > 10 else ""),
                         entry.get("objects"), f"analysis/{name}.json"))
    return rows


def _unreal_tables(an: _Analyses, rx) -> list[tuple]:
    data = an.get("unreal_assets")
    if data is None:
        return []
    rows = []
    for table in (data.get("data") or {}).get("datatables") or []:
        name = table.get("package") or table.get("file") or "?"
        if rx and not rx.search(name):
            continue
        rows.append((f"`{name}`", table.get("row_struct") or "", table.get("rows"),
                     ", ".join(f"`{c}`" for c in list(table.get("columns") or {})[:8])))
    return rows


def _unreal_values(an: _Analyses, rx) -> list[tuple]:
    """One row per default value a Blueprint or data asset sets (`analysis/unreal_values.json`); ``--match`` is tried
    on the package, the class and the value path (`--match "Walk|Jump"` finds `CharMoveComp.MaxWalkSpeed`)."""
    data = an.get("unreal_values")
    if data is None:
        return []
    d = data.get("data") or {}
    rows = []
    for kind, items in (("blueprints", d.get("blueprints") or []), ("data_assets", d.get("data_assets") or [])):
        for i, item in enumerate(items):
            owner = item.get("class") or item.get("name") or item.get("package")
            for path, value in (item.get("values") or {}).items():
                if rx and not (rx.search(str(item.get("package"))) or rx.search(str(owner)) or rx.search(path)):
                    continue
                rows.append((f"`{owner}`", f"`{path}`", value, item.get("parent") or item.get("class") or "",
                             f"`unreal_values.json` `data.{kind}[{i}].values`"))
    return rows


def block_tunables(an: _Analyses, arg: str, opts: dict) -> str:
    rx = re.compile(opts["match"], re.I) if opts.get("match") else None
    parts = []
    godot = _godot_tunables(an, rx)
    if godot:
        parts.append(f"Variáveis exportadas (Godot) de `analysis/godot_scenes.json` `data.tunables`, só valores (números, "
                     f"flags, vetores, curvas; referências a nós e recursos ficam no JSON) ({len(godot)} linhas"
                     + (f", primeiras {ROWS}" if len(godot) > ROWS else "") + "):\n\n"
                     + _table(["classe", "propriedade", "tipo", "padrão no código", "valores nas cenas", "fonte"], godot[:ROWS]))
    unity = _unity_tunables(an, rx)
    if unity:
        parts.append(f"MonoBehaviours/ScriptableObjects (Unity) ({len(unity)} scripts"
                     + (f", primeiros {ROWS}" if len(unity) > ROWS else "") + "):\n\n"
                     + _table(["script", "campos", "objetos", "fonte"], unity[:ROWS]))
    values = _unreal_values(an, rx)
    if values:
        parts.append(f"Valores padrão (Unreal) do class default object dos Blueprints, dos componentes e dos data assets, "
                     f"de `analysis/unreal_values.json` (só o que o asset define; ausente = herdado do pai) ({len(values)} "
                     "valores" + (f", primeiros {ROWS}" if len(values) > ROWS else "") + "):\n\n"
                     + _table(["classe", "valor", "padrão", "pai/classe", "fonte"], values[:ROWS]))
    unreal = _unreal_tables(an, rx)
    if unreal:
        parts.append(f"DataTables (Unreal) de `analysis/unreal_assets.json` ({len(unreal)}):\n\n"
                     + _table(["tabela", "struct", "linhas", "colunas"], unreal[:ROWS]))
    if parts:
        return "\n\n".join(parts)
    if rx:
        return f"_(nenhum parâmetro casou com `--match {opts['match']}` nas análises; procure no código com `gst q grep`)_"
    return an.missing("godot_scenes", "mono_values", "catalogs", "unreal_values", "unreal_assets")


def block_middleware(an: _Analyses, arg: str, opts: dict) -> str:
    recon = read_json(an.ws / "recon" / "recon.json", {}) or {}
    found = recon.get("net_voice")
    if found is None:
        return "_(recon sem `net_voice`: rode `gst recon` de novo nesta versão do gst)_"
    if not found:
        return "Nenhum middleware de rede/voz reconhecido pelos nomes de arquivo (`recon/recon.json` `net_voice`): rede própria ou embutida no executável."
    rows = [(m.get("name"), m.get("kind"), ", ".join(f"`{e}`" for e in m.get("evidence") or [])) for m in found]
    return "Middleware de `recon/recon.json` `net_voice`:\n\n" + _table(["middleware", "tipo", "evidência (instalação)"], rows)


def block_ragdoll(an: _Analyses, arg: str, opts: dict) -> str:
    parts = []
    godot = an.get("godot_animation")
    if godot is not None:
        rows = [(r.get("file"), r.get("physical_bones"), r.get("joint_nodes"),
                 ", ".join(f"{k} {v}" for k, v in (r.get("joint_node_types") or r.get("builtin_joint_types") or {}).items()),
                 r.get("mass_total"))
                for r in (godot.get("data") or {}).get("ragdolls") or []]
        if rows:
            parts.append("Ragdolls de `analysis/godot_animation.json` `data.ragdolls`:\n\n"
                         + _table(["cena", "PhysicalBone3D", "joints", "tipos de joint", "massa total"], rows[:ROWS]))
    ue = an.get("unreal_animation")
    if ue is not None:
        rows = [(p.get("package"), len(p.get("bodies") or []), len(p.get("constraints") or []),
                 ", ".join(f"{k} {v}" for k, v in (p.get("joint_types") or {}).items()),
                 ", ".join(f"{k} {v}" for k, v in (p.get("shape_counts") or {}).items()), p.get("mass_override_total"))
                for p in (ue.get("data") or {}).get("physics_assets") or []]
        if rows:
            parts.append("PhysicsAssets (Unreal) de `analysis/unreal_animation.json` `data.physics_assets`:\n\n"
                         + _table(["PhysicsAsset", "corpos", "constraints", "tipos", "formas", "massa definida (kg)"], rows[:ROWS]))
    joints = an.get("joints")
    if joints is not None:
        rags = (joints.get("data") or {}).get("ragdolls") or []
        fams = (joints.get("data") or {}).get("families") or {}
        rows = [(r.get("prefab") or r.get("name") or r.get("root"), r.get("file"), ", ".join(str(s) for s in (r.get("scripts") or [])[:4]))
                for r in rags if isinstance(r, dict)]
        parts.append(f"`analysis/joints.json`: {len(rags)} ragdolls, famílias de joints: "
                     + (", ".join(f"{k} ({v if not isinstance(v, (dict, list)) else len(v)})" for k, v in list(fams.items())[:10]) or "-")
                     + ("\n\n" + _table(["prefab", "arquivo", "scripts"], rows[:ROWS]) if rows else ""))
    return "\n\n".join(parts) if parts else an.missing("joints", "godot_animation", "unreal_animation")


def block_audio(an: _Analyses, arg: str, opts: dict) -> str:
    data = an.get("audio_catalog")
    if data is None:
        return an.missing("audio_catalog", "audio_refs")
    summary = data.get("summary") or {}
    cats = (data.get("data") or {}).get("categories") or {}
    rows = sorted(cats.items(), key=lambda kv: (-int(kv[1] if isinstance(kv[1], int) else len(kv[1] or [])), kv[0]))
    return ("Resumo de `analysis/audio_catalog.json`:\n\n" + _table(["chave", "valor"], _flat(summary))
            + "\n\nEventos por categoria:\n\n"
            + _table(["categoria", "eventos"], [(k, v if isinstance(v, int) else len(v or [])) for k, v in rows[:ROWS]]))


def block_models(an: _Analyses, arg: str, opts: dict) -> str:
    parts = []
    gltf = an.get("gltf_summary")
    if gltf is not None:
        fams = (gltf.get("data") or {}).get("families") or {}
        rows = [(k, v.get("files"), v.get("tris"), v.get("tris_median"), v.get("skinned"),
                 ", ".join(f"{j} ossos×{n}" for j, n in list((v.get("joints") or {}).items())[:4]),
                 f"{v.get('anims_min')}-{v.get('anims_max')}")
                for k, v in sorted(fams.items(), key=lambda kv: -int((kv[1] or {}).get("tris", 0) or 0)) if isinstance(v, dict)]
        parts.append("Famílias de modelos de `analysis/gltf_summary.json` `data.families`:\n\n"
                     + _table(["família (pasta)", "arquivos", "tris", "tris mediana", "skinned", "esqueletos", "anims"], rows[:ROWS]))
    dcc = an.get("dcc")
    if dcc is not None:
        fams = (dcc.get("data") or {}).get("families") or {}
        rows = [(k, v.get("verdict"), v.get("strong"), v.get("weak"), v.get("ambiguous")) for k, v in fams.items() if isinstance(v, dict)]
        parts.append("Origem (DCC) de `analysis/dcc.json` `data.families` (veredito = inferência):\n\n"
                     + _table(["ferramenta/padrão", "veredito", "fortes", "fracas", "ambíguas"], rows))
    for name in ("prefabs", "rig", "shaders", "textures"):
        data = an.get(name)
        if data is not None:
            parts.append(f"`analysis/{name}.json` (summary): " + "; ".join(f"{k} {v}" for k, v in _flat(data.get("summary") or {})))
    return "\n\n".join(parts) if parts else an.missing("gltf_summary", "dcc", "prefabs", "rig")


def block_animation(an: _Analyses, arg: str, opts: dict) -> str:
    parts = []
    for name in ("godot_animation", "animation", "controller", "avatar", "unreal_animation"):
        data = an.get(name)
        if data is not None:
            parts.append(f"`analysis/{name}.json` (summary): " + "; ".join(f"{k} {v}" for k, v in _flat(data.get("summary") or {})))
    return "\n\n".join(parts) if parts else an.missing("animation", "controller", "godot_animation")


def block_maps(an: _Analyses, arg: str, opts: dict) -> str:
    parts = []
    scenes = an.get("godot_scenes")
    if scenes is not None:
        graph = (scenes.get("data") or {}).get("instance_graph") or {}
        top = graph.get("top_level") or []
        most = graph.get("most_instanced") or {}
        parts.append(f"Cenas (`analysis/godot_scenes.json`): {len(top)} cenas de topo (não instanciadas por outras); "
                     "mais instanciadas:\n\n" + _table(["cena", "instâncias"], list(most.items())[:15]))
        rx = re.compile(r"level|map|world|biome|room|stage|nivel|mapa|procedural|chunk|terrain", re.I)
        named = [s for s in top if rx.search(str(s))]
        if named:
            parts.append("Cenas de topo com nome de mapa/nível: " + ", ".join(f"`{s}`" for s in named[:ROWS]))
    for name in ("prefabs", "unreal_assets"):
        data = an.get(name)
        if data is not None:
            parts.append(f"`analysis/{name}.json` (summary): " + "; ".join(f"{k} {v}" for k, v in _flat(data.get("summary") or {})))
    return "\n\n".join(parts) if parts else an.missing("godot_scenes", "prefabs", "unreal_assets")


def block_workspaces(an: _Analyses, arg: str, opts: dict) -> str:
    from gst import config
    from gst.library import norm_name

    home = config.gs_home()
    wanted = [s.strip() for s in (opts.get("games") or "").split(",") if s.strip()]
    loose = {norm_name(w) for w in wanted}
    rows = []
    for game_json in sorted(home.glob("*/game.json")):
        ws = game_json.parent
        meta = read_json(game_json, {}) or {}
        if ws.name.startswith("_") or (wanted and not {norm_name(ws.name), norm_name(meta.get("name") or "")} & loose):
            continue
        engine = meta.get("engine") or {}
        analyses = sorted(p.stem for p in (ws / "analysis").glob("*.json")) if (ws / "analysis").is_dir() else []
        mw = ", ".join(m.get("name", "?") for m in meta.get("middleware") or []) or "-"
        rows.append((meta.get("name") or ws.name, f"`{ws.name}`", f"{engine.get('label', '?')} {engine.get('version') or ''}".strip(),
                     mw, ", ".join(analyses) or "-"))
    if not rows:
        return f"_(nenhum workspace em `{home.as_posix()}`" + (f" com os slugs {', '.join(wanted)}" if wanted else "") + ")_"
    return f"Workspaces em `{home.as_posix()}`:\n\n" + _table(["jogo", "pasta", "engine", "rede/voz", "análises"], rows)


def block_library(an: _Analyses, arg: str, opts: dict) -> str:
    """The `gst lib compare` table of one facet (`--facet`, else the note's topic: `compare/rigs`) for `--games`."""
    from gst.common import GsError
    from gst.library import compare, connect, db_path, resolve_games

    hint = arg or opts.get("facet") or Path(opts.get("note") or "").stem
    try:
        facet = compare.facet_name(hint)
    except GsError:
        return (f"_(o tópico `{hint}` não é uma faceta da biblioteca ({', '.join(compare.FACETS)}): use `--facet <faceta>` "
                "para a tabela de `gst lib compare`, ou preencha a tabela abaixo à mão)_")
    if not db_path().is_file():
        return f"_(sem biblioteca: rode `gst lib build` e depois `gst lib compare {facet} <jogos>`)_"
    wanted = [s.strip() for s in (opts.get("games") or "").split(",") if s.strip()]
    conn = connect()
    try:
        games = resolve_games(conn, wanted, analysis_only=True)
        built = conn.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
        text = compare.table(conn, facet, games) if games else ""
    except GsError as exc:
        return f"_(biblioteca: {exc})_"
    finally:
        conn.close()
    if not text:
        return "_(a biblioteca não tem workspaces: `gst lib build`)_"
    when = json.loads(built[0]) if built else "?"
    return (f"Tabela da biblioteca (`gst lib compare {facet} {' '.join(g['key'] for g in games)}`, `_library/library.db` "
            f"de {when}; fontes por jogo e lacunas no arquivo que `gst lib compare` grava):\n\n" + text)


BLOCKS = {
    "summary": block_summary, "systems": block_systems, "system": block_system, "tunables": block_tunables,
    "middleware": block_middleware, "ragdoll": block_ragdoll, "audio": block_audio, "models": block_models,
    "animation": block_animation, "maps": block_maps, "workspaces": block_workspaces, "library": block_library,
}


def render(ws: Path, game: dict, name: str, *, title: str, note: str, date: str, options: dict | None = None) -> str:
    """The template ``name`` with its placeholders and ``{{auto:...}}`` blocks filled for workspace ``ws``."""
    opts = {**(options or {}), "note": note}
    an = _Analyses(ws)

    def fill(m: re.Match) -> str:
        block = BLOCKS.get(m.group(1))
        if block is None:
            return f"<!-- bloco desconhecido: {m.group(1)} -->"
        try:
            return block(an, m.group(2) or "", opts)
        except (KeyError, TypeError, ValueError, AttributeError, re.error) as exc:  # odd analysis shapes never break a note
            return f"_(bloco {m.group(1)} não pôde ser preenchido: {exc.__class__.__name__}: {exc})_"

    body = AUTO.sub(fill, template_text(name))
    engine = game.get("engine") or {}
    sources = ", ".join(f"`{u}`" for u in an.used) or "nenhuma análise encontrada"
    values = {"title": title, "game": game.get("name") or ws.name, "engine": f"{engine.get('label', '?')} {engine.get('version') or ''}".strip(),
              "build": str(game.get("buildid") or "?"), "date": date, "note": note, "sources": sources}
    return re.sub(r"\{\{(title|game|engine|build|date|note|sources)\}\}", lambda m: values[m.group(1)], body)


def read_template_list() -> list[dict]:
    """[{name, layer, title}] for `gst notes templates`."""
    out = []
    for name in template_names():
        text = template_text(name)
        head = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), name)
        out.append({"name": name, "layer": TEMPLATES[name], "title": head})
    return out


def note_unfilled(path: Path) -> int:
    return unfilled(read_text(path))
