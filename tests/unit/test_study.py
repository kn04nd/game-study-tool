"""gst notes / gst report / gst map on synthetic workspaces."""

import json
from pathlib import Path

import pytest

from gst import index, mapping, notes, report
from gst.common import GsError

TEMPLATE = (Path(__file__).resolve().parents[2] / "src" / "gst" / "data" / "templates" / "ARCHITECTURE.md").read_text(encoding="utf-8")


def make_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    code = ws / "decomp" / "cs" / "Game"
    code.mkdir(parents=True)
    (code / "SaveLoader.cs").write_text("public class SaveLoader\n{\n    public void Save() { }\n}\n", encoding="utf-8")
    (ws / "FACTS.md").write_text("# Fatos\n\n| Fato | Valor | Evidência | Verificado |\n|---|---|---|---|\n"
                                 "| Engine | Unity | recon/RECON.md | sim |\n", encoding="utf-8")
    return ws


def complete_doc(systems: int = 5, cite: str = "decomp/cs/Game/SaveLoader.cs:3") -> str:
    text = TEMPLATE.replace("{name}", "Game")
    names = ["Áudio", "Mapas e níveis"] + [f"Sistema {i}" for i in range(2, systems)]
    body = "\n".join(f"### {names[i]}\n\nFaz algo; evidência `{cite}`.\n" for i in range(systems))
    filled = {1: "- Jogo: Game", 2: "- Engine: Unity", 3: "Assemblies e loop.", 4: body, 5: "Dados em YAML.",
              6: "Dois passes.", 7: "HP confirmado (traces/watch-1.json).",
              8: "```mermaid\ngraph LR\n  a --> b\n```", 9: "- Chore: tarefa", 10: "- O formato do save não foi verificado.",
              11: "- Código decompilado."}
    out = []
    for block in text.split("\n## ")[1:]:
        heading, _, _rest = block.partition("\n")
        number = int(heading.split(".", 1)[0])
        out.append(f"## {heading}\n\n{filled[number]}\n")
    return "# Arquitetura de Game\n\n" + "\n".join(out)


def test_notes_new_index_search_and_citations(tmp_path):
    ws = make_ws(tmp_path)
    note = notes.new(ws, "code/save-system", "Como o save funciona")
    assert note == ws / "notes" / "code" / "save-system.md" and "# Como o save funciona" in note.read_text(encoding="utf-8")
    with pytest.raises(GsError, match="layer>/<topic"):
        notes.new(ws, "Bad Name", None)
    note.write_text(note.read_text(encoding="utf-8") + "\nSalva em decomp/cs/Game/SaveLoader.cs:3 e decomp/cs/Game/SaveLoader.cs:99.\n",
                    encoding="utf-8")
    ok, broken = notes.check_citations(ws, note.read_text(encoding="utf-8"))
    assert [c["line"] for c in ok] == [3] and broken[0]["line"] == 99 and "has 5 lines" in broken[0]["why"]
    _ok, missing = notes.check_citations(ws, "veja decomp/cs/Game/Nope.cs:1")
    assert "no such file" in missing[0]["why"]
    assert "[Como o save funciona](code/save-system.md)" in notes.index(ws).read_text(encoding="utf-8")
    assert notes.search(ws, "SaveLoader")[0]["path"] == "notes/code/save-system.md"


def test_report_check_flags_every_gap(tmp_path):
    ws = make_ws(tmp_path)
    (ws / "ARCHITECTURE.md").write_text(TEMPLATE.replace("{name}", "Game"), encoding="utf-8")
    problems = report.check(ws, {"install_dir": None})["problems"]
    assert "section 4 (Sistemas) is still empty" in problems and "no Mermaid diagram (section 8)" in problems
    assert any("describes 0 systems" in p for p in problems)
    (ws / "ARCHITECTURE.md").write_text(complete_doc(), encoding="utf-8")
    result = report.check(ws, {"install_dir": None})
    assert result["ok"], result["problems"]
    assert len(result["systems"]) == 5 and result["citations"] == 5 and result["diagrams"] == 1
    bad = complete_doc(systems=4, cite="decomp/cs/Game/SaveLoader.cs:40").replace("graph LR", "grph LR")
    bad = bad.replace("- O formato do save não foi verificado.", "Nada.")
    (ws / "ARCHITECTURE.md").write_text(bad + "\nTODO revisar\n", encoding="utf-8")
    (ws / "FACTS.md").write_text((ws / "FACTS.md").read_text(encoding="utf-8") + "| HP | 100 |  | não |\n", encoding="utf-8")
    problems = report.check(ws, {"install_dir": None})["problems"]
    joined = "\n".join(problems)
    for expected in ("describes 4 systems", "cites no evidence", "broken citation", "starts with 'grph'",
                     "section 10 must list", "FACTS.md row 'HP' has no evidence", "placeholder 'TODO'"):
        assert expected in joined, expected


def test_report_check_requires_audio_and_maps(tmp_path):
    ws = make_ws(tmp_path)
    doc = complete_doc().replace("### Áudio", "### Sistema A").replace("### Mapas e níveis", "### Sistema B")
    (ws / "ARCHITECTURE.md").write_text(doc, encoding="utf-8")
    problems = "\n".join(report.check(ws, {"install_dir": None})["problems"])
    assert "### Áudio" in problems and "### Mapas e níveis" in problems
    # a game without levels keeps the heading and says why; that one needs no evidence
    doc = complete_doc().replace("### Mapas e níveis\n\nFaz algo; evidência `decomp/cs/Game/SaveLoader.cs:3`.",
                                 "### Mapas e níveis\n\nNão se aplica: o jogo tem uma única tela, sem fases.")
    (ws / "ARCHITECTURE.md").write_text(doc, encoding="utf-8")
    result = report.check(ws, {"install_dir": None})
    assert result["ok"], result["problems"]
    # a world-simulation system is not a maps system
    doc = complete_doc(systems=6).replace("### Mapas e níveis", "### Simulação do mundo")
    (ws / "ARCHITECTURE.md").write_text(doc, encoding="utf-8")
    assert any("Mapas e níveis" in p for p in report.check(ws, {"install_dir": None})["problems"])


def test_report_autofill_from_recon(tmp_path):
    ws = make_ws(tmp_path)
    (ws / "ARCHITECTURE.md").write_text(TEMPLATE.replace("{name}", "Game"), encoding="utf-8")
    (ws / "recon").mkdir()
    (ws / "recon" / "recon.json").write_text(json.dumps({
        "scanned_at": "2026-10-08T00:00:00", "verdict": "offline-ok", "middleware": ["FMOD"], "protections": [],
        "game": {"store": "steam", "appid": "1", "buildid": "42"},
        "engine": {"label": "Unity (Mono)", "decompile_backend": "dotnet", "details": {"version": "6000.3.5f2"}},
        "executables": {"game.exe": {"arch": "x64", "imports": ["d3d11.dll"], "version": {"ProductVersion": "1.0"}}}}),
        encoding="utf-8")
    assert report.autofill(ws, {"name": "Game", "install_dir": "D:/G"}) == [1, 2]
    secs = report.sections((ws / "ARCHITECTURE.md").read_text(encoding="utf-8"))
    assert "Build estudado: 42" in secs[1]["body"] and "Unity (Mono) 6000.3.5f2" in secs[2]["body"]
    assert "Direct3D 11" in secs[2]["body"] and "<!--" in secs[1]["body"]  # the guidance comment stays
    assert report.autofill(ws, {"name": "Game", "install_dir": "D:/G"}) == []  # written once


def test_map_assigns_ranks_and_links_systems(tmp_path):
    ws = tmp_path / "ws"
    code = ws / "decomp" / "cs" / "Game"
    code.mkdir(parents=True)
    (code / "ChoreDriver.cs").write_text(
        "public class ChoreDriver\n{\n    public void Run()\n    {\n        SaveLoader.Instance.Save();\n"
        "        var e = new ElementLoader();\n    }\n}\n", encoding="utf-8")
    (code / "SaveLoader.cs").write_text(
        "public class SaveLoader\n{\n    public static SaveLoader Instance;\n    public void Save() { }\n}\n", encoding="utf-8")
    (code / "ElementLoader.cs").write_text("public class ElementLoader\n{\n}\n", encoding="utf-8")
    (code / "Misc.cs").write_text("public class Misc\n{\n}\n", encoding="utf-8")
    index.build(ws)
    result = mapping.build(ws)
    by_label = {s["label"]: s for s in result["systems"]}
    assert by_label["IA e tarefas"]["top"][0]["name"] == "ChoreDriver"
    assert by_label["Save e serialização"]["top"][0]["refs"] >= 1
    assert by_label["IA e tarefas"]["uses"] == {"Save e serialização": 1, "Simulação do mundo": 1}
    assert result["unassigned"] == 1 and result["unassigned_top"] == ["Misc"]
    md = (ws / "notes" / "MAP.md").read_text(encoding="utf-8")
    assert "```mermaid" in md and "| `SaveLoader` |" in md
    (ws / "notes" / "map-keywords.json").write_text(json.dumps({"Utilidades": "Misc"}), encoding="utf-8")
    assert "Utilidades" in {s["label"] for s in mapping.build(ws)["systems"]}
