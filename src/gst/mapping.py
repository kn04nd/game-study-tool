"""`gst map`: a first map of the game's systems from the code index.

Each type is assigned to a system by keywords in its name (CamelCase-aware), ranked by how often the rest
of the code uses it (constructions and calls through the type), and calls between types are added up into
system-to-system dependencies. It is a starting point for the study, not a truth: `notes/map-keywords.json`
({"Label": "regex", ...}) refines or adds systems for a given game, then `gst map` again.
"""

from __future__ import annotations

import collections
import json
import re
import time
from pathlib import Path

from gst.common import GsError, read_json, write_json, write_text

# label -> regex on the type name; the first system with the most keyword hits wins
SYSTEMS: dict[str, str] = {
    "IA e tarefas": r"Brain|Chore|Sensor|Navigat|Pathfind|BehaviourTree|Goal|Steering|Schedule|Errand|Task|Agent|Decision",
    "Simulação do mundo": r"Sim(?=[A-Z_]|$)|Element|Gas|Liquid|Temperature|Thermal|Pressure|Conduit|Flow|Cell|Grid|Fluid|Heat|Weather|Disease|Germ",
    "Construção e estruturas": r"Build|Construct|Deconstruct|Tile|Structure|Blueprint|Placement|Wall|Door|Foundation",
    "Pesquisa e progressão": r"Research|Tech|Unlock|Skill|Upgrade|Achievement|Progress|Quest|Mission|Objective|Milestone|Experience",
    "Criaturas e entidades": r"Critter|Creature|Monster|Enemy|Animal|Egg|Spawn|Npc|Mob|Ranch|Duplicant|Minion",
    "Geração de mundo": r"WorldGen|Procedural|Biome|Noise|Terrain|Cluster|Seed|Dungeon|Asteroid|Planet",
    "Economia e recursos": r"Econom|Money|Price|Trade|Shop|Currency|Resource|Market|Cost|Reward|Loot|Fuel|Food|Calorie|Oxygen",
    "Combate e dano": r"Combat|Damage|Health|Weapon|Attack|Projectile|Armor|Wound|Death|Kill|Stress|Hit(?=[A-Z]|$)",
    "Inventário e itens": r"Inventor|Item|Storage|Equip|Pickup|Container|Slot|Cosmetic|Pickupable",
    "Save e serialização": r"Save|Serializ|Persist|Deserializ",
    "Interface": r"UI(?=[A-Z_]|$)|Screen|Panel|Widget|Hud|Menu|Tooltip|Button|Dialog|Window|Overlay|Notifier|Notification",
    "Áudio": r"Audio|Sound|Music|Fmod|Wwise|Sfx",
    "Renderização e câmera": r"Render|Shader|Material|Camera|Light|Mesh|Sprite|Texture|Particle|Vfx|Fx(?=[A-Z]|$)",
    "Animação": r"Anim|Kanim|Animator|Skeleton|Bone|Rig(?=[A-Z]|$)",
    "Rede e online": r"Network|Net(?=[A-Z])|Socket|Lobby|Steam|Multiplayer|Peer|Rpc|Matchmak|Replicat",
    "Jogador e entrada": r"Player|Input|Controller|Keybind|Gamepad|Cursor",
    "Dados e configuração": r"Config|Setting|Tuning|Database|Db(?=[A-Z])|Definition|Table|Catalog|Prefab|Def$",
}
TYPE_KINDS = ("class", "struct", "interface", "enum", "record")
TOP = 10


def _systems(ws: Path) -> dict[str, re.Pattern]:
    custom = read_json(ws / "notes" / "map-keywords.json", {}) or {}
    merged = {**SYSTEMS, **{k: v for k, v in custom.items() if isinstance(v, str)}}
    return {label: re.compile(expr) for label, expr in merged.items() if expr}


def assign(name: str, systems: dict[str, re.Pattern]) -> str | None:
    best, hits = None, 0
    for label, rx in systems.items():
        n = len(rx.findall(name))
        if n > hits:
            best, hits = label, n
    return best


def build(ws: Path) -> dict:
    """compute() and write index/map.json + notes/MAP.md."""
    result = compute(ws)
    write_json(ws / "index" / "map.json", result)
    write_text(ws / "notes" / "MAP.md", markdown(result))
    return result


def compute(ws: Path, *, with_members: bool = False) -> dict:
    """Systems from the code index, nothing written. ``with_members`` adds ``system_of`` (type -> system) and
    ``inbound`` (type -> uses) for callers that enrich the map (``gst analyze systems``)."""
    from gst import index

    t0 = time.time()
    con = index.connect(ws)
    try:
        systems = _systems(ws)
        types = {}
        for r in con.execute(f"SELECT s.id, s.name, s.qname, s.kind, s.line, f.path, f.module FROM symbols s JOIN files f "
                             f"ON f.id = s.file_id WHERE s.kind IN ({','.join('?' * len(TYPE_KINDS))})", TYPE_KINDS):
            types.setdefault(r["name"], {"name": r["name"], "qname": r["qname"], "kind": r["kind"], "path": r["path"],
                                         "line": r["line"], "module": r["module"], "id": r["id"]})
        if not types:
            raise GsError("the code index has no types yet: gst decompile (or gst index) first")
        system_of = {name: assign(name, systems) for name in types}
        parent_type: dict[int, str] = {}
        type_ids = {t["id"]: name for name, t in types.items()}
        for r in con.execute("SELECT id, parent_id FROM symbols WHERE parent_id IS NOT NULL"):
            if r["parent_id"] in type_ids:
                parent_type[r["id"]] = type_ids[r["parent_id"]]
        inbound: collections.Counter = collections.Counter()
        edges: collections.Counter = collections.Counter()
        for r in con.execute("SELECT kind, target, name, symbol_id FROM refs WHERE kind IN ('call', 'new')"):
            if r["kind"] == "new":
                callee = r["name"]
            else:
                head = (r["target"] or "").rpartition(".")[0]
                parts = [p for p in head.split(".") if p]
                callee = next((p for p in reversed(parts) if p in types), None) if parts else None
            if callee not in types:
                continue
            inbound[callee] += 1
            caller = parent_type.get(r["symbol_id"])
            a, b = system_of.get(caller) if caller else None, system_of.get(callee)
            if a and b and a != b:
                edges[(a, b)] += 1
    finally:
        con.close()
    out_systems = []
    for label in systems:
        members = [n for n, s in system_of.items() if s == label]
        if not members:
            continue
        ranked = sorted(members, key=lambda n: (-inbound[n], n))
        out_systems.append({"label": label, "types": len(members),
                            "files": len({types[n]["path"] for n in members}),
                            "modules": dict(collections.Counter(types[n]["module"] for n in members).most_common(3)),
                            "top": [{"name": n, "qname": types[n]["qname"], "path": types[n]["path"], "line": types[n]["line"],
                                     "refs": inbound[n]} for n in ranked[:TOP]],
                            "uses": {b: c for (a, b), c in edges.most_common() if a == label}})
    out_systems.sort(key=lambda s: -sum(t["refs"] for t in s["top"]))
    unassigned = [n for n, s in system_of.items() if s is None]
    result = {"systems": out_systems, "types": len(types), "unassigned": len(unassigned),
              "unassigned_top": sorted(unassigned, key=lambda n: -inbound[n])[:20],
              "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t0, 1)}
    if with_members:
        result["system_of"] = system_of
        result["inbound"] = dict(inbound)
        result["type_info"] = {n: {k: t[k] for k in ("qname", "kind", "path", "line", "module")} for n, t in types.items()}
    return result


def markdown(result: dict) -> str:
    lines = ["# Mapa de sistemas", "",
             f"> Gerado por `gst map` em {result['built_at'][:16].replace('T', ' ')}: {result['types']} tipos, "
             f"{result['unassigned']} sem sistema. Atribuição por palavras-chave no nome do tipo, ordenada por uso no "
             "código; é um ponto de partida. Ajuste em `notes/map-keywords.json` e rode `gst map` de novo.", "",
             "| Sistema | Tipos | Arquivos | Mais usados |", "| --- | --- | --- | --- |"]
    for s in result["systems"]:
        lines.append(f"| {s['label']} | {s['types']} | {s['files']} | " + ", ".join(t["name"] for t in s["top"][:5]) + " |")
    edges = sorted(((a["label"], b, c) for a in result["systems"] for b, c in a["uses"].items()), key=lambda e: -e[2])[:24]
    if edges:
        used = sorted({a for a, _b, _c in edges} | {b for _a, b, _c in edges})
        node = {label: f"s{i}" for i, label in enumerate(used)}  # stable ids: the diagram diffs cleanly
        lines += ["", "## Dependências entre sistemas", "", "```mermaid", "graph LR"]
        lines += [f'    {node[label]}["{label}"]' for label in used]
        lines += [f"    {node[a]} -->|{c}| {node[b]}" for a, b, c in edges]
        lines.append("```")
    for s in result["systems"]:
        lines += ["", f"## {s['label']}", "", "| Tipo | Usos | Onde |", "| --- | --- | --- |"]
        lines += [f"| `{t['qname']}` | {t['refs']} | {t['path']}:{t['line']} |" for t in s["top"]]
    if result["unassigned_top"]:
        lines += ["", "## Sem sistema (os mais usados)", "", ", ".join(f"`{n}`" for n in result["unassigned_top"])]
    return "\n".join(lines) + "\n"


def load(ws: Path) -> dict:
    data = read_json(ws / "index" / "map.json", None)
    if data is None:
        raise GsError("no map yet: gst map")
    return data


def systems_json(ws: Path) -> str:
    """The system list as compact JSON, for workflows and agents."""
    return json.dumps([{"label": s["label"], "types": s["types"], "top": [t["qname"] for t in s["top"][:6]]}
                       for s in load(ws)["systems"]], ensure_ascii=False)
