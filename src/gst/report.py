"""ARCHITECTURE.md, the study's final document: status, the sections gst can fill from recon, and the check that
it holds - every section written, enough systems each with evidence, citations that resolve, a diagram, the
unverified claims listed, and FACTS.md rows with evidence.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from gst.common import GsError, read_json, read_text, write_text
from gst.notes import ADDRESS, TRACE, check_citations

SECTION = re.compile(r"^## (\d+)\.\s*(.+?)\s*$", re.M)
COMMENT = re.compile(r"<!--.*?-->", re.S)
MERMAID = re.compile(r"```mermaid\s*\n(.*?)```", re.S)
DIAGRAM_TYPES = ("graph", "flowchart", "sequenceDiagram", "classDiagram", "stateDiagram", "stateDiagram-v2", "erDiagram",
                 "journey", "gantt", "pie", "mindmap", "timeline", "block-beta")
REQUIRED = 11
# Layers every study covers (a ### system in section 4, with evidence, or one that says why it does not apply)
ALWAYS = {
    "audio": (re.compile(r"\b(áudio|audio|sons?|sound|música|musica|music|efeitos sonoros)\b", re.I),
              "### Áudio: how sound is organized and used (events -> files -> who triggers them, the sound engine, music)"),
    "maps": (re.compile(r"\b(mapas?|maps?|níveis|nível|niveis|nivel|levels?|fases|missões|missions)\b", re.I),
             "### Mapas e níveis: format, content (terrain, placed objects, scripts/triggers, spawns) and how a map is loaded"),
}
NOT_APPLICABLE = re.compile(r"não se aplica|nao se aplica|not applicable|n/a\b", re.I)


def sections(text: str) -> dict[int, dict]:
    found = list(SECTION.finditer(text))
    out = {}
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        out[int(m.group(1))] = {"title": m.group(2), "body": text[m.end():end], "line": text.count("\n", 0, m.start()) + 1}
    return out


def content(body: str) -> str:
    return COMMENT.sub("", body).strip()


def systems(body: str) -> list[tuple[str, str]]:
    parts = re.split(r"^### +(.+?)\s*$", body, flags=re.M)
    return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def _facts_rows(ws: Path) -> list[list[str]]:
    path = ws / "FACTS.md"
    if not path.exists():
        return []
    rows = [line for line in read_text(path).splitlines() if line.startswith("|")]
    return [[c.strip() for c in row.strip("|").split("|")] for row in rows[2:]]  # skip header and separator


def check(ws: Path, game: dict, *, min_systems: int = 5) -> dict:
    path = ws / "ARCHITECTURE.md"
    if not path.exists():
        raise GsError("no ARCHITECTURE.md in this workspace (gst new creates it from the template)")
    text = read_text(path)
    install = Path(game["install_dir"]) if game.get("install_dir") else None
    problems: list[str] = []
    secs = sections(text)
    for n in range(1, REQUIRED + 1):
        if n not in secs:
            problems.append(f"section {n} is missing")
        elif not content(secs[n]["body"]):
            problems.append(f"section {n} ({secs[n]['title']}) is still empty")
    found = systems(secs[4]["body"]) if 4 in secs else []
    if len(found) < min_systems:
        problems.append(f"section 4 describes {len(found)} systems (### headings); at least {min_systems} wanted")
    for title, body in found:
        ok, _broken = check_citations(ws, body, install)
        if not ok and not ADDRESS.search(body) and not TRACE.search(body) and not NOT_APPLICABLE.search(body):
            problems.append(f"system '{title}' cites no evidence (path:line, module+0xRVA or a trace)")
    if 4 in secs and found:
        for _key, (pattern, wanted) in ALWAYS.items():
            if not any(pattern.search(title) for title, _ in found):
                problems.append(f"section 4 has no subsection for {wanted}; if the game has none, write the heading "
                                "and say why it does not apply")
    ok, broken = check_citations(ws, text, install)
    problems += [f"broken citation {b['cite']}: {b['why']}" for b in broken]
    diagrams = MERMAID.findall(text)
    if not diagrams:
        problems.append("no Mermaid diagram (section 8)")
    for d in diagrams:
        first = d.strip().split(None, 1)[0] if d.strip() else ""
        if first not in DIAGRAM_TYPES:
            problems.append(f"a Mermaid block starts with '{first}', not a diagram type")
    unverified = content(secs[10]["body"]) if 10 in secs else ""
    if 10 in secs and not re.search(r"^\s*[-*] +\S", unverified, re.M):
        problems.append("section 10 must list what was NOT verified (a bullet list)")
    for row in _facts_rows(ws):
        if len(row) >= 3 and not row[2]:
            problems.append(f"FACTS.md row '{row[0]}' has no evidence")
    for m in re.finditer(r"\bTODO\b|\?\?\?", content(text)):
        problems.append(f"placeholder '{m.group(0)}' at line {text.count(chr(10), 0, m.start()) + 1}")
        break
    return {"path": str(path), "systems": [t for t, _ in found], "citations": len(ok), "broken": broken,
            "diagrams": len(diagrams), "problems": problems, "ok": not problems}


def _identity(ws: Path, game: dict) -> str:
    recon = read_json(ws / "recon" / "recon.json", {}) or {}
    g = recon.get("game") or {}
    exes = recon.get("executables") or {}
    lines = [f"- Jogo: {game.get('name')} ({g.get('store') or game.get('store') or 'pasta'}"
             + (f", AppID {g.get('appid')}" if g.get("appid") else "") + ")",
             f"- Build estudado: {g.get('buildid') or game.get('buildid') or 'desconhecido'} "
             f"(recon de {str(recon.get('scanned_at', ''))[:10] or '?'}, `recon/RECON.md`)",
             f"- Instalação: `{game.get('install_dir')}`"]
    for name, info in list(exes.items())[:3]:
        version = (info.get("version") or {}).get("ProductVersion") or (info.get("version") or {}).get("FileVersion")
        lines.append(f"- Executável `{name}` ({info.get('arch', '?')}" + (f", versão {version}" if version else "") + ")")
    return "\n".join(lines)


def _stack(ws: Path, game: dict) -> str:
    recon = read_json(ws / "recon" / "recon.json", {}) or {}
    engine = recon.get("engine") or {}
    details = engine.get("details") or {}
    version = details.get("version") or details.get("engine_version") or (game.get("engine") or {}).get("version")
    imports = {i.lower() for info in (recon.get("executables") or {}).values() for i in info.get("imports", [])}
    apis = [api for dll, api in (("d3d11.dll", "Direct3D 11"), ("d3d12.dll", "Direct3D 12"), ("vulkan-1.dll", "Vulkan"),
                                 ("opengl32.dll", "OpenGL")) if dll in imports]
    lines = [f"- Engine: {engine.get('label', '?')}" + (f" {version}" if version else "")
             + (f"; código: {engine.get('decompile_backend')}" if engine.get("decompile_backend") else ""),
             f"- Middleware: {', '.join(recon.get('middleware') or []) or 'nenhum detectado'}",
             f"- Rede e voz: {', '.join(m['name'] + ' (' + m['kind'] + ')' for m in recon.get('net_voice') or []) or 'nada detectado pelos nomes de arquivo'}",
             f"- APIs gráficas importadas pelos executáveis: {', '.join(apis) or 'nenhuma direta (o engine carrega)'}",
             f"- Proteções: {', '.join(p.get('name', '?') for p in recon.get('protections') or []) or 'nenhuma'}; "
             f"veredito `{recon.get('verdict') or game.get('verdict')}`"]
    return "\n".join(lines)


def autofill(ws: Path, game: dict) -> list[int]:
    """Fill sections 1 and 2 from recon while they are still empty; returns the sections written."""
    path = ws / "ARCHITECTURE.md"
    text = read_text(path)
    secs = sections(text)
    filled = []
    for n, build in ((1, _identity), (2, _stack)):
        if n in secs and not content(secs[n]["body"]):
            body = secs[n]["body"]
            comments = "".join(COMMENT.findall(body))
            new_body = "\n" + (comments + "\n" if comments else "") + build(ws, game) + "\n\n"
            text = text.replace(f"## {n}. {secs[n]['title']}{body}", f"## {n}. {secs[n]['title']}{new_body}", 1)
            filled.append(n)
    if filled:
        write_text(path, text)
    return filled


def status(ws: Path, game: dict, *, min_systems: int = 5) -> dict:
    result = check(ws, game, min_systems=min_systems)
    secs = sections(read_text(ws / "ARCHITECTURE.md"))
    result["sections"] = {n: {"title": s["title"], "filled": bool(content(s["body"]))} for n, s in sorted(secs.items())}
    result["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return result
