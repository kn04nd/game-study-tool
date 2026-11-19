"""Study notes (`notes/<layer>/<topic>.md`) and the evidence they cite.

Evidence is `path:line` (a file of the workspace - decomp/, assets/, notes/, recon/, traces/ - or of the game
install), `module+0xRVA`, or a trace file. `check_citations` resolves every `path:line` and reports the ones
that point nowhere. `gst notes` and `gst report --check` share it.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from gst.common import GsError, read_text, write_text

CITATION = re.compile(r"(?<![\w/.-])((?:[\w.+~-]+/)+[\w.+~-]+\.[A-Za-z0-9]{1,8}):(\d+)(?:-(\d+))?")
ADDRESS = re.compile(r"\b[\w.-]+\.(?:exe|dll)\+0x[0-9a-fA-F]+\b")
TRACE = re.compile(r"\btraces/[\w./+-]+\.(?:json|rdc|md|png)\b")

NOTE_TEMPLATE = """# {title}

> Camada: {layer}. Criada em {date}. Evidência em `caminho:linha` (decomp/, assets/, notes/) ou `módulo+0xRVA`.

## Pergunta

## O que o código e os dados mostram

## Conclusão

## Não verificado

-
"""


def new(ws: Path, spec: str, title: str | None = None, *, template: str | None = None, game: dict | None = None,
        options: dict | None = None) -> Path:
    """Create ``notes/<layer>/<topic>.md``: the plain note, or ``template`` pre-filled from ``analysis/*.json``
    (then ``spec`` may be just ``<topic>``: the template's default layer is used)."""
    parts = spec.replace("\\", "/").strip("/").removesuffix(".md").split("/")
    if template and len(parts) == 1:
        from gst import notetemplates

        if template not in notetemplates.TEMPLATES:
            notetemplates.template_text(template)  # raises with the list
        parts = [notetemplates.TEMPLATES[template], parts[0]]
    if len(parts) != 2 or not all(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", p) for p in parts):
        raise GsError("notes are <layer>/<topic>, lower case: e.g. code/save-system, logic/oxygen, formats/save")
    path = ws / "notes" / parts[0] / f"{parts[1]}.md"
    if path.exists():
        raise GsError(f"{path} already exists")
    title = title or parts[1].replace("-", " ").capitalize()
    date = time.strftime("%Y-%m-%d")
    if template:
        from gst import notetemplates

        text = notetemplates.render(ws, game or {}, template, title=title, note=f"notes/{parts[0]}/{parts[1]}.md",
                                    date=date, options=options)
    else:
        text = NOTE_TEMPLATE.format(title=title, layer=parts[0], date=date)
    write_text(path, text)
    return path


def all_notes(ws: Path) -> list[Path]:
    root = ws / "notes"
    return sorted(p for p in root.rglob("*.md") if p.name != "INDEX.md") if root.is_dir() else []


def _resolve(ws: Path, install: Path | None, rel: str) -> Path | None:
    for base in (ws, install):
        if base is not None and (base / rel).is_file():
            return base / rel
    return None


def check_citations(ws: Path, text: str, install: Path | None = None) -> tuple[list[dict], list[dict]]:
    """(resolved, broken) `path:line` citations of a text."""
    ok, broken = [], []
    line_counts: dict[Path, int] = {}
    for m in CITATION.finditer(text):
        rel, first = m.group(1), int(m.group(2))
        last = int(m.group(3)) if m.group(3) else first
        path = _resolve(ws, install, rel)
        cite = {"cite": m.group(0), "path": rel, "line": first}
        if path is None:
            broken.append({**cite, "why": "no such file in the workspace or the install"})
            continue
        if path not in line_counts:
            line_counts[path] = read_text(path).count("\n") + 1
        if first < 1 or last > line_counts[path] or last < first:
            broken.append({**cite, "why": f"the file has {line_counts[path]} lines"})
        else:
            ok.append(cite)
    return ok, broken


def evidence_count(text: str, ws: Path, install: Path | None = None) -> int:
    ok, _broken = check_citations(ws, text, install)
    return len(ok) + len(ADDRESS.findall(text)) + len(TRACE.findall(text))


def first_heading(path: Path) -> str:
    for line in read_text(path).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def index(ws: Path) -> Path:
    """notes/INDEX.md: every note by layer, with its title."""
    rows: dict[str, list[str]] = {}
    for path in all_notes(ws):
        rel = path.relative_to(ws / "notes").as_posix()
        layer = rel.split("/", 1)[0] if "/" in rel else "(raiz)"
        rows.setdefault(layer, []).append(f"- [{first_heading(path)}]({rel})")
    lines = ["# Notas", "", f"> Gerado por `gst notes index` em {time.strftime('%Y-%m-%d %H:%M')}.", ""]
    for layer in sorted(rows):
        lines += [f"## {layer}", "", *rows[layer], ""]
    out = ws / "notes" / "INDEX.md"
    write_text(out, "\n".join(lines))
    return out


def study_root_index(home: Path) -> Path:
    """GS_HOME/INDEX.md: one line per study workspace (engine, verdict, phase, facts, notes)."""
    from gst.common import read_json

    lines = ["# Estudos", "", "| Jogo | Engine | Veredito | Fase | Fatos | Notas | Pasta |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for game_json in sorted(home.glob("*/game.json")):
        ws = game_json.parent
        if ws.name.startswith("_"):
            continue
        meta = read_json(game_json, {}) or {}
        facts = ws / "FACTS.md"
        n_facts = max(0, sum(1 for line in read_text(facts).splitlines() if line.startswith("|")) - 2) if facts.exists() else 0
        engine = meta.get("engine") or {}
        lines.append(f"| {meta.get('name')} | {engine.get('label', '?')} {engine.get('version') or ''} | {meta.get('verdict')} | "
                     f"{meta.get('phase', '-')} | {n_facts} | {len(all_notes(ws))} | `{ws.name}` |")
    out = home / "INDEX.md"
    write_text(out, "\n".join(lines) + "\n")
    return out


def search(ws: Path, pattern: str, limit: int = 60) -> list[dict]:
    rx = re.compile(pattern, re.I)
    hits = []
    for path in all_notes(ws) + [p for p in (ws / "FACTS.md", ws / "ARCHITECTURE.md", ws / "STUDY.md") if p.exists()]:
        for number, line in enumerate(read_text(path).splitlines(), 1):
            if rx.search(line):
                hits.append({"path": path.relative_to(ws).as_posix(), "line": number, "text": line.strip()[:200]})
                if len(hits) >= limit:
                    return hits
    return hits
