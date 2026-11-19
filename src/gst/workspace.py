"""Study workspaces (one folder per game under GS_HOME), game.json, registry and journal."""

from __future__ import annotations

import re
import time
from pathlib import Path

from gst import config, guard
from gst.common import GsError, append_text, read_json, read_text, slugify, write_json, write_text

SUBDIRS = ("recon", "decomp", "assets", "dumps", "traces", "notes", "index", "logs")
TEMPLATES = ("STUDY.md", "FACTS.md", "ARCHITECTURE.md")
RULES = ("decomp.md", "assets.md")  # .claude/rules/, path-scoped instructions


def template(name: str) -> str:
    from importlib import resources  # ~100 ms on a scanned Python; hooks only use journal()

    return resources.files("gst").joinpath(f"data/templates/{name}").read_text(encoding="utf-8")


def render(text: str, values: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


def guardrails_path() -> str:
    try:
        from gst.cmd_setup import toolkit_root

        return (toolkit_root() / "plugins" / "gst" / "guardrails.md").as_posix()
    except GsError:
        return "plugins/gst/guardrails.md (Game Study Toolkit)"


def rule_path(path: str | Path) -> str:
    """Windows path -> Claude Code permission-rule path (//d/Folder/...)."""
    p = Path(path).as_posix()
    if len(p) >= 2 and p[1] == ":":
        p = f"//{p[0].lower()}{p[2:]}"
    return p.rstrip("/")


FALLBACK_SKILLS = ("game-study", "study-recon", "study-decompile", "study-assets")


def plugin_skills() -> list[str]:
    """Skill folders of the game-study plugin (their Skill(...) rules go into workspace settings)."""
    try:
        from gst.cmd_setup import toolkit_root

        found = sorted(p.parent.name for p in (toolkit_root() / "plugins" / "gst" / "skills").glob("*/SKILL.md"))
    except GsError:
        found = []
    return found or list(FALLBACK_SKILLS)


def workspace_settings(ws: Path, game: dict) -> dict:
    from gst.hooks import gs_call

    install = rule_path(game["install_dir"])
    home = rule_path(ws)
    allow = [
        # gs_call(): what the PreToolUse hook turns a bare `gst` into while gst.exe is not on PATH
        "Bash(gst *)", "PowerShell(gst *)", f"PowerShell({gs_call()} *)", "Bash(rg *)", "PowerShell(rg *)", "Bash(ast-grep *)",
        f"Read({install}/**)", f"Read({home}/**)", f"Edit({home}/**)",
        *[rule for skill in plugin_skills() for rule in (f"Skill(gst:{skill})", f"Skill(gst:{skill} *)")],
        "WebSearch",  # recon always looks for public reverse-engineering knowledge
        "Workflow(gst:map-subsystems)", "Workflow(gst:verify-claims)",
    ]
    deny = [f"Edit({install}/**)"]
    for shell in ("Bash", "PowerShell"):
        deny += [f"{shell}(git add *)", f"{shell}(git commit *)", f"{shell}(git init *)"]
    # Edit(<ws>/**) plus Bash(gst *) would let text from a game file (read by the model) write a file and run it
    # with no prompt. "ask" wins over "allow": these always ask.
    #   fmt roundtrip   imports the reader .py written in the workspace
    #   config set      can point tools_dir/gs_home at a folder the model wrote
    #   tools install, setup, mcp   install or configure programs that run later
    #   rg --pre        runs any program per file
    #   ast-grep -U     rewrites files outside the workspace
    risky = ["fmt roundtrip", "config set", "config unset", "tools install", "setup", "mcp"]
    ask = []
    for prefix in ("Bash(gst", "PowerShell(gst", f"PowerShell({gs_call()}"):
        ask += [f"{prefix} {sub} *)" for sub in risky] + [f"{prefix} {sub})" for sub in risky]
    for shell in ("Bash", "PowerShell"):
        ask += [f"{shell}(rg *--pre*)"]
    ask += ["Bash(ast-grep *-U*)", "Bash(ast-grep *--update-all*)", "Bash(ast-grep *--interactive*)"]
    return {
        "env": {"CLAUDE_CODE_USE_POWERSHELL_TOOL": "1", "GST_GAME": game["slug"],
                "GST_EXE": (Path.home() / ".local" / "bin" / "gst.exe").as_posix()},
        "permissions": {"allow": allow, "ask": ask, "deny": deny,
                        "additionalDirectories": [Path(game["install_dir"]).as_posix()]},
        "enabledMcpjsonServers": [],
    }


def claude_trusted(ws: Path) -> bool | None:
    """Did the user accept Claude Code's trust dialog for this exact folder? Its allow rules and
    additionalDirectories apply only then (a trusted parent doesn't count). None = unknown."""
    data = read_json(Path.home() / ".claude.json", None)
    if not isinstance(data, dict):
        return None
    from gst.common import norm_path

    key = norm_path(ws)
    for path, info in (data.get("projects") or {}).items():
        if norm_path(path) == key and isinstance(info, dict):
            return bool(info.get("hasTrustDialogAccepted"))
    return False


TRUST_HINT = ("open Claude Code in this folder once and accept the trust dialog: until then its permission rules "
              "(gst commands, skills, the game folder) don't apply")


def load(ws: Path) -> dict:
    data = read_json(ws / config.WORKSPACE_MARKER, {})
    if not isinstance(data, dict) or not data.get("gs_workspace"):
        raise GsError(f"{ws} is not a gst workspace (no game.json)")
    return data


def save(ws: Path, game: dict) -> None:
    write_json(ws / config.WORKSPACE_MARKER, game)


def resolve(slug: str | None = None) -> Path | None:
    """Workspace folder: explicit ``slug`` (under GS_HOME), else the one around the cwd, else
    ``GST_GAME``/``GS_GAME`` from the environment. Reads v1 (gs) and v2 (gst) workspaces."""
    from gst.compat import legacy

    if slug:
        found = legacy.find_by_slug(slug, config.gs_home())
        if found is None:
            raise GsError(f"no study workspace '{slug}' under {config.gs_home()}")
        return found
    ws = config.find_workspace()
    if ws is None and config.current_game():
        ws = legacy.find_by_slug(config.current_game(), config.gs_home())
    return ws


def current(required: bool = True, slug: str | None = None) -> tuple[Path, dict] | tuple[None, None]:
    ws = resolve(slug)
    if ws is None:
        if required:
            raise GsError("not inside a study workspace (cd into GS_HOME/<game> or run gst new <game>)")
        return None, None
    return ws, load(ws)


def registry_entry(game: dict, report: dict | None, ws: Path | None) -> dict:
    exes = sorted({Path(e).name.lower() for e in (report or {}).get("executables", {})} | set(game.get("exes", [])))
    entry = {
        "name": game.get("name"), "store": game.get("store"), "appid": game.get("appid"),
        "install_dir": game.get("install_dir") or game.get("path"), "exes": exes,
        "verdict": (report or {}).get("verdict") or game.get("verdict"),
        "engine": ((report or {}).get("engine") or {}).get("key") or (game.get("engine") or {}).get("key"),
        "keys": game.get("keys", []), "protected_modules": (report or {}).get("protected_modules", []),
    }
    if ws:
        entry["workspace"] = str(ws)
        entry["sandbox_dir"] = str(ws / "sandbox")
    return entry


def journal(ws: Path, text: str) -> None:
    """Append ``- HH:MM text`` under today's session heading in STUDY.md."""
    study = ws / "STUDY.md"
    today = time.strftime("%Y-%m-%d")
    content = read_text(study) if study.exists() else ""
    if f"### {today}" not in content:
        append_text(study, f"\n### {today}\n")
    append_text(study, f"- {time.strftime('%H:%M')} {text}\n")


def engine_meta(report: dict) -> dict:
    """game.json `engine` block from a recon report."""
    engine = report["engine"]
    details = engine.get("details") or {}
    return {"key": engine["key"], "label": engine["label"], "version": details.get("version") or details.get("engine_version")}


def middleware_meta(report: dict) -> list[dict]:
    """game.json `middleware`: network/voice/platform SDKs from recon (`[{name, kind}]`)."""
    return [{"name": m["name"], "kind": m["kind"]} for m in report.get("net_voice") or []]


def new_meta(game: dict, report: dict, slug: str, ws: Path) -> dict:
    """game.json of a new workspace ``ws`` (nothing is written: ``create`` saves it, a dry run only shows it)."""
    return {
        "gs_workspace": 1, "slug": slug, "name": game.get("name"), "store": game.get("store"), "appid": game.get("appid"),
        "buildid": game.get("buildid"), "install_dir": game["path"],
        "exes": sorted(Path(e).name.lower() for e in report.get("executables", {})),
        "engine": engine_meta(report), "middleware": middleware_meta(report),
        "verdict": report["verdict"], "protections": report["protections"],
        "protected_modules": report["protected_modules"], "keys": [], "sandbox_dir": str(ws / "sandbox"),
        "language": "pt-BR", "phase": "recon", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "recon_at": report["scanned_at"],
    }


def create(game: dict, report: dict, slug: str | None = None, *, home: Path | None = None) -> Path:
    home = home or config.gs_home()
    root = guard.git_root(home)
    if root is not None:
        raise GsError(f"GS_HOME {home} is inside the git repository {root}; choose another study root")
    slug = slug or slugify(game.get("name") or Path(game["path"]).name)
    ws = home / slug
    if (ws / config.WORKSPACE_MARKER).exists():
        raise GsError(f"workspace {ws} already exists (gst status inside it, or pass --slug)")
    for sub in SUBDIRS:
        (ws / sub).mkdir(parents=True, exist_ok=True)
    meta = new_meta(game, report, slug, ws)
    save(ws, meta)
    values = _template_values(ws, meta)
    write_text(ws / "CLAUDE.md", render(template("CLAUDE.study.md"), values))
    for name in TEMPLATES:
        write_text(ws / name, render(template(name), values))
    for rule in RULES:
        write_text(ws / ".claude" / "rules" / rule, template(f"rules/{rule}"))
    write_json(ws / ".claude" / "settings.json", workspace_settings(ws, meta))
    write_json(ws / ".mcp.json", {"mcpServers": {}})
    if not slug.startswith("_"):  # `_name` = internal (self-tests): kept out of the registry
        guard.save_registry_entry(slug, registry_entry(meta, report, ws))
    return ws


def _template_values(ws: Path, meta: dict) -> dict:
    from gst import policy

    version = (meta.get("engine") or {}).get("version")
    return {
        "name": meta.get("name"), "slug": meta.get("slug"), "install_dir": meta.get("install_dir"),
        "engine_label": (meta.get("engine") or {}).get("label"), "engine_version": f" {version}" if version else "",
        "verdict": meta.get("verdict"), "verdict_text": policy.describe(meta.get("verdict") or ""),
        "buildid": meta.get("buildid") or "?", "date": (meta.get("created_at") or time.strftime("%Y-%m-%d"))[:10],
        "gs_home": str(ws.parent), "guardrails": guardrails_path(),
    }


def _merge(fresh, current):
    """Toolkit settings over the current ones: dicts merge, lists keep the user's extra items."""
    if isinstance(fresh, dict) and isinstance(current, dict):
        return {**current, **{k: _merge(v, current[k]) if k in current else v for k, v in fresh.items()}}
    if isinstance(fresh, list) and isinstance(current, list):
        return [*fresh, *(item for item in current if item not in fresh)]
    return fresh


def refresh(ws: Path, *, claude_md: bool = False) -> list[str]:
    """Re-render the toolkit-owned files of an existing workspace after a toolkit update.

    settings.json is merged (rules the user added survive), scoped rules are replaced, CLAUDE.md
    only with ``claude_md`` (backup kept). Study files (STUDY/FACTS/ARCHITECTURE, notes) are never touched.
    """
    meta = load(ws)
    changed = []
    settings_path = ws / ".claude" / "settings.json"
    current = read_json(settings_path, {}) or {}
    merged = _merge(workspace_settings(ws, meta), current)
    if merged != current:
        write_json(settings_path, merged)
        changed.append(".claude/settings.json")
    for rule in RULES:
        target, text = ws / ".claude" / "rules" / rule, template(f"rules/{rule}")
        if not target.exists() or read_text(target) != text:
            write_text(target, text)
            changed.append(f".claude/rules/{rule}")
    if not (ws / ".mcp.json").exists():
        write_json(ws / ".mcp.json", {"mcpServers": {}})
        changed.append(".mcp.json")
    if claude_md:
        text = render(template("CLAUDE.study.md"), _template_values(ws, meta))
        target = ws / "CLAUDE.md"
        if not target.exists() or read_text(target) != text:
            if target.exists():
                write_text(ws / "CLAUDE.md.bak", read_text(target))
            write_text(target, text)
            changed.append("CLAUDE.md")
    return changed
