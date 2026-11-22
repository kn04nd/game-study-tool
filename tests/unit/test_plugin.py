"""Plugin files: frontmatter limits, and every `gst` command they mention must exist."""

import json
import re
from pathlib import Path

import pytest

from gst import cli

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "gst"
SKILLS = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
AGENTS = sorted((PLUGIN / "agents").glob("*.md"))


def frontmatter(path: Path) -> tuple[dict, str]:
    """Tiny YAML-subset reader: `key: value` and `key:` + `  - item` lists (all our files use)."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: no frontmatter"
    head, body = text[4:].split("\n---\n", 1)
    data: dict = {}
    key = None
    for line in head.splitlines():
        if line.startswith("  - ") and key:
            data.setdefault(key, []).append(line[4:].strip())
        elif ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            data[key] = value.strip('"') if value else []
    return data, body


def gs_subcommands() -> dict[str, set[str]]:
    parser = cli.build_parser()
    groups = next(a for a in parser._actions if a.dest == "group").choices
    out = {}
    for name, sub in groups.items():
        actions = [a for a in sub._actions if getattr(a, "choices", None) and a.dest == "action"]
        out[name] = set(actions[0].choices) if actions else set()
    return out


def test_plugin_has_the_planned_files():
    assert {p.parent.name for p in SKILLS} >= {"game-study", "study-recon", "study-decompile", "study-assets"}
    assert {p.stem for p in AGENTS} >= {"recon", "code-navigator", "asset-analyst"}
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "gst"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_skill_frontmatter_and_size(path):
    meta, body = frontmatter(path)
    assert meta["name"] == path.parent.name
    listing = f"{meta['description']} {meta.get('when_to_use', '')}"
    assert len(listing) <= 1536, f"description + when_to_use is {len(listing)} chars"
    assert re.search(r"analis|estud|engine|jogo", meta.get("when_to_use", "")), "PT triggers missing"
    assert len(path.read_text(encoding="utf-8").splitlines()) < 500
    # no `!`cmd`` injection: a skill whose shell command can't run (no Bash, a denied command, evals on
    # native Windows) fails to load at all; the SessionStart hook already gives the workspace context
    assert not re.search(r"(^|\s)!`", body) and "```!" not in body


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_agent_frontmatter(path):
    meta, _ = frontmatter(path)
    assert meta["name"] == path.stem and ":" not in meta["name"]
    assert meta["description"] and meta["tools"]
    assert meta.get("model") in {"haiku", "sonnet", "opus", "inherit"}
    skills = {f"gst:{p.parent.name}" for p in SKILLS}
    assert set(meta.get("skills", [])) <= skills


def code_snippets(text: str) -> list[str]:
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, re.S)
    spans = re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", text, flags=re.S))
    return blocks + spans


@pytest.mark.parametrize("path", SKILLS + AGENTS, ids=lambda p: p.parent.name if p.name == "SKILL.md" else p.stem)
def test_mentioned_gs_commands_exist(path):
    known = gs_subcommands()
    found = 0
    for snippet in code_snippets(path.read_text(encoding="utf-8")):
        for group, action in re.findall(r"(?:^|[\s;(&|])gst ([a-z]+)(?: ([a-z][a-z-]*))?", snippet, re.M):
            found += 1
            assert group in known, f"{path.name}: unknown command `gst {group}`"
            if action and known[group]:
                assert action in known[group], f"{path.name}: unknown `gst {group} {action}`"
    assert found, "no gst commands found: did the snippet parser break?"


REFERENCES = sorted((PLUGIN / "references").rglob("*.md"))
README = PLUGIN.parents[1] / "README.md"


@pytest.mark.parametrize("path", REFERENCES + [README], ids=lambda p: p.stem)
def test_references_and_readme_only_name_existing_gs_commands(path):
    known = gs_subcommands()
    for snippet in code_snippets(path.read_text(encoding="utf-8")):
        for group, action in re.findall(r"(?:^|[\s;(&|])gst ([a-z]+)(?: ([a-z][a-z-]*))?", snippet, re.M):
            assert group in known, f"{path.name}: unknown command `gst {group}`"
            if action and known[group]:
                assert action in known[group], f"{path.name}: unknown `gst {group} {action}`"


def test_every_engine_recon_names_has_its_study_guide():
    from gst.recon import fingerprint

    guides = {row[1] for row in fingerprint.ENGINES.values() if row[1]}
    missing = sorted(g for g in guides if not (PLUGIN / "references" / "engines" / g).is_file())
    assert not missing, f"engine guides named by recon but missing: {missing}"


TEMPLATES = sorted((Path(cli.__file__).parent / "data" / "templates").rglob("*.md"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_workspace_templates_only_name_existing_gs_commands(path):
    """Templates are rendered once per workspace and go stale: no commands from future phases."""
    known = gs_subcommands()
    for snippet in code_snippets(path.read_text(encoding="utf-8")):
        for group, action in re.findall(r"(?:^|[\s;(&|])gst ([a-z]+)(?: ([a-z]+))?", snippet, re.M):
            assert group in known, f"{path.name}: unknown command `gst {group}`"


def test_recon_next_steps_only_name_existing_gs_commands():
    from gst.recon import fingerprint, scan

    known = gs_subcommands()
    for key in fingerprint.ENGINES:
        for verdict in ("offline-ok", "static-only", "no-runtime"):
            for step in scan.recommended_steps(key, verdict, {"encrypted": True}):
                for group, action in re.findall(r"(?:^|[\s:(|&;])gst ([a-z]+)(?: ([a-z][a-z-]*))?", step):
                    assert group in known, f"{key}/{verdict}: `{step}` names `gst {group}`"
                    if action and known[group] and not action.startswith("-"):
                        assert action in known[group], f"{key}/{verdict}: `{step}` names `gst {group} {action}`"
    # with the full design available, the gst commands are named
    full = scan.recommended_steps("unity-mono", "offline-ok", {}, available={"decompile", "assets", "index", "map", "trace"})
    assert "gst decompile dotnet" in full


def test_workspace_settings_allow_every_plugin_skill(tmp_path):
    from gst import workspace

    skills = {p.parent.name for p in SKILLS}
    assert set(workspace.plugin_skills()) == skills
    assert set(workspace.FALLBACK_SKILLS) <= skills
    allow = workspace.workspace_settings(tmp_path, {"install_dir": str(tmp_path / "game"), "slug": "x"})["permissions"]["allow"]
    for skill in skills:
        assert f"Skill(gst:{skill} *)" in allow


# --- evals, deliver-first agents, workflows ------------------------------------------------------------------

EVALS = sorted(p for p in (PLUGIN / "evals").iterdir() if p.is_dir())
GRADER_TYPES = {"tool_used", "llm", "regex"}


def eval_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: no frontmatter"
    head, body = text[4:].split("\n---\n", 1) if "\n---\n" in text[4:] else (text[4:].removesuffix("---\n"), "")
    data = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            data[key.strip()] = value.strip()
    return data, body


@pytest.mark.parametrize("case", EVALS, ids=lambda p: p.name)
def test_eval_cases_are_well_formed(case):
    meta, body = eval_frontmatter(case / "prompt.md")
    assert meta.get("description") and body.strip(), "prompt.md needs a description and a prompt"
    assert int(meta.get("runs", "1")) >= 1 and int(meta.get("max_turns", "1")) >= 1
    graders = sorted((case / "graders").glob("*.md"))
    assert graders, "no graders"
    skills = {p.parent.name for p in SKILLS}
    for grader in graders:
        gmeta, gbody = eval_frontmatter(grader)
        assert gmeta.get("type") in GRADER_TYPES, f"{grader.name}: type {gmeta.get('type')}"
        if gmeta["type"] == "llm":
            assert "PASS" in gbody and "FAIL" in gbody and len(gbody) < 1500, f"{grader.name}: keep llm rubrics short"
        if gmeta["type"] == "regex":
            re.compile(gmeta["pattern"].strip("'\""))
        if gmeta["type"] == "tool_used" and "input_match" in gmeta:
            pattern = gmeta["input_match"].strip("'")
            re.compile(pattern)
            named = set(re.findall(r"[a-z]+(?:-[a-z]+)*", pattern.split("(?:", 2)[-1])) if "skill" in pattern else set()
            assert not named or named & (skills | {"w"}), f"{grader.name}: names no existing skill"


def test_new_skills_have_eval_cases():
    covered = " ".join((p / "graders" / "skill-fired.md").read_text(encoding="utf-8") for p in EVALS
                       if (p / "graders" / "skill-fired.md").is_file())
    for skill in ("mechanics", "compare", "dossier"):
        assert skill in covered, f"no eval case fires {skill}"


DELIVER_AGENTS = [p for p in AGENTS if p.stem != "recon"]


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_agents_follow_deliver_first(path):
    meta, body = frontmatter(path)
    assert int(meta["maxTurns"]) <= 40
    assert "Deliver-first" in body
    if path in DELIVER_AGENTS:
        assert int(meta["maxTurns"]) == 40
        assert "DELIVERABLE:" in body and "Não verificado" in body and "every 10 turns" in body


def test_new_agents_and_their_write_scopes():
    from gst import guard

    names = {p.stem for p in AGENTS}
    assert {"mechanics-analyst", "comparer"} <= names
    assert names <= set(guard.AGENT_WRITE_SCOPES)
    _meta, body = frontmatter(PLUGIN / "agents" / "mechanics-analyst.md")
    assert "Relevância para o meu jogo" in body


WORKFLOWS = sorted((PLUGIN / "workflows").glob("*.js"))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_workflow_syntax_phases_and_agents(path, tmp_path):
    import shutil
    import subprocess

    text = path.read_text(encoding="utf-8")
    assert text.startswith("export const meta = {")
    meta_titles = re.findall(r"\{ title: '([^']+)'", text.split("\n}\n", 1)[0])
    used = set(re.findall(r"phase\('([^']+)'\)", text))
    assert used and used <= set(meta_titles), f"phase() titles not in meta: {used - set(meta_titles)}"
    agents = {p.stem for p in AGENTS}
    for name in re.findall(r"'gst:([a-z-]+)'", text):
        assert name in agents or (PLUGIN / "workflows" / f"{name}.js").is_file(), f"unknown agent/workflow gst:{name}"
    for snippet in re.findall(r"gst ([a-z]+)(?: ([a-z][a-z-]*))?", text):
        group, action = snippet
        known = gs_subcommands()
        assert group in known, f"unknown `gst {group}`"
        if action and known[group]:
            assert action in known[group], f"unknown `gst {group} {action}`"
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    body = text.replace("export const meta", "const meta", 1)
    wrapped = tmp_path / f"{path.stem}.check.js"
    wrapped.write_text("async function workflowBody(args, agent, parallel, pipeline, phase, log, workflow, budget) {\n"
                       + body + "\n}\n", encoding="utf-8")
    proc = subprocess.run([node, "--check", str(wrapped)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_study_all_maps_every_kind_to_an_agent_and_template():
    from gst import notetemplates

    text = (PLUGIN / "workflows" / "study-all.js").read_text(encoding="utf-8")
    kinds = re.findall(r"^\s+(\w+): \['gst:([a-z-]+)', '([a-z0-9-]+)', '([a-z]+)'\]", text, re.M)
    assert len(kinds) == 9
    for _kind, agent, template, _layer in kinds:
        assert (PLUGIN / "agents" / f"{agent}.md").is_file()
        assert template in notetemplates.TEMPLATES
    assert "DELIVERABLE: ${deliverable(t)}" in text and "DELIVERABLE: ARCHITECTURE.md" in text
