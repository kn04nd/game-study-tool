"""MCP servers for a study workspace (`gst mcp`): verified templates only, over stdio or bound to 127.0.0.1
(guardrails rule 9), written to the workspace's `.mcp.json` and enabled in its settings.

`audit_text` is shared with the guard: a `.mcp.json` written by hand (Write/Edit) that binds beyond the local
machine, drops pyghidra-mcp's --no-symbols, or names a server the research rejected is refused.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gst.common import GsError, read_json, write_json

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

# name -> template. Tool names are the MCP tools the server exposes (pyghidra-mcp 0.2.7, checked live).
TEMPLATES: dict[str, dict] = {
    "ghidra": {
        "tool": "pyghidra-mcp",
        "title": "pyghidra-mcp over this workspace's Ghidra project (gst decompile native): decompile, xrefs, search",
        "allow": ["decompile_function", "search_symbols_by_name", "search_code", "list_project_binaries",
                  "list_project_binary_metadata", "list_exports", "list_imports", "list_xrefs", "search_strings",
                  "read_bytes", "disassemble", "gen_callgraph"],
        "deny": ["delete_project_binary"],
        # rename_function, rename_variable, set_comment, set_variable_type, set_function_prototype, save and
        # import_binary annotate or extend the workspace's project: left to the user's approval
    },
}

# Servers rejected during research (reason on each entry): never configured.
REJECTED = {
    r"GhidraMCP|bridge_mcp_ghidra": "LaurieWired GhidraMCP: HTTP on every interface without auth, unmaintained",
    r"die[-_]?mcp|D\.I\.E-MCP": "D.I.E-MCP: SSE on 0.0.0.0:8080",
    r"ILSpy[-_]?MCP": "cervonwong ILSpy-MCP: HTTP on 0.0.0.0:3001",
    r"dnSpy[-_]?MCP": "xrkk dnSpy MCP: binds a LAN address by default",
    r"il2cpp[-_]dump[-_]analyzer": "il2cpp-dump-analyzer: remote database",
    r"WireMCP": "WireMCP: external lookups",
    r"MCP-UAsset-Toolkit|UndertaleModTool.*MCP": "write tools over game assets (modding)",
    r"cheatengine-mcp-bridge": "no safety switches, kernel evasion tools",
}


def audit_text(text: str) -> list[str]:
    """Problems in MCP configuration text (a whole .mcp.json or an edited fragment)."""
    problems = []
    for host in re.findall(r"\bhttps?://\[?([^/:\]\"'\s]+)\]?", text):
        if host.lower() not in LOCAL_HOSTS and f"[{host}]" not in LOCAL_HOSTS:
            problems.append(f"an MCP URL points at {host}: only 127.0.0.1/localhost")
    if "0.0.0.0" in text:
        problems.append("binds 0.0.0.0 (every interface): MCP servers bind 127.0.0.1 only")
    for host in re.findall(r"(?:--host|--bind|--listen|\"-o\")[\"']?\s*[,:=]?\s*[\"']?([\w.:\[\]-]+)", text):
        if host.lower() not in LOCAL_HOSTS and host != "0.0.0.0":
            problems.append(f"a server binds {host}: only 127.0.0.1")
    for pattern, reason in REJECTED.items():
        if re.search(pattern, text, re.IGNORECASE):
            problems.append(f"rejected server: {reason}")
    try:
        servers = json.loads(text).get("mcpServers", {})
    except (ValueError, AttributeError):
        servers = {}
    for name, cfg in servers.items() if isinstance(servers, dict) else []:
        joined = " ".join([str(cfg.get("command", "")), *map(str, cfg.get("args", []))])
        if "pyghidra-mcp" in joined and "--no-symbols" not in joined:
            problems.append(f"server '{name}': pyghidra-mcp needs --no-symbols (no symbol servers)")
    return list(dict.fromkeys(problems))


def projects(ws: Path) -> list[Path]:
    root = ws / "decomp" / "ghidra" / "_projects"
    return sorted(root.glob("*/*.gpr")) if root.is_dir() else []


def server_config(ws: Path, name: str, *, module: str | None = None) -> dict:
    from gst import tools

    if name not in TEMPLATES:
        raise GsError(f"no verified template for '{name}' (available: {', '.join(TEMPLATES)})")
    if name == "ghidra":
        exe = tools.bin_path("pyghidra-mcp")
        if exe is None:
            raise GsError("pyghidra-mcp is not installed (gst tools install pyghidra-mcp)")
        found = projects(ws)
        if module:
            found = [p for p in found if p.stem.lower() == module.lower()]
        if not found:
            raise GsError("no Ghidra project in this workspace" + (f" for {module}" if module else "")
                          + " (gst decompile native first)")
        if len(found) > 1:
            raise GsError("several Ghidra projects: pass --module (" + ", ".join(p.stem for p in found) + ")")
        from gst.decompile import native

        env = native.environment()
        ghidra = tools.bin_path("ghidra")
        return {"type": "stdio", "command": str(exe),
                "args": ["--project-path", found[0].as_posix(), "--no-symbols", "--transport", "stdio"],
                "env": {"GHIDRA_INSTALL_DIR": Path(ghidra).parent.parent.as_posix() if ghidra else "",
                        "JAVA_HOME": Path(env["JAVA_HOME"]).as_posix(), "ANONYMIZED_TELEMETRY": "False"}}
    raise GsError(f"template {name} has no builder")


def add(ws: Path, game: dict, name: str, *, module: str | None = None) -> dict:
    from gst import policy

    decision = policy.check(game, "decompile", module)
    if not decision.allowed:
        raise GsError(f"{decision.reason} (guardrails.md rule {decision.rule})", 3)
    config = server_config(ws, name, module=module)
    problems = audit_text(json.dumps({"mcpServers": {name: config}}))
    if problems:  # a template change that would break rule 9 never reaches the workspace
        raise GsError("; ".join(problems), 3)
    mcp_path = ws / ".mcp.json"
    data = read_json(mcp_path, {}) or {}
    data.setdefault("mcpServers", {})[name] = config
    write_json(mcp_path, data)
    settings_path = ws / ".claude" / "settings.json"
    settings = read_json(settings_path, {}) or {}
    enabled = settings.setdefault("enabledMcpjsonServers", [])
    if name not in enabled:
        enabled.append(name)
    perms = settings.setdefault("permissions", {})
    template = TEMPLATES[name]
    for key, tools_ in (("allow", template["allow"]), ("deny", template["deny"])):
        rules = perms.setdefault(key, [])
        rules += [f"mcp__{name}__{t}" for t in tools_ if f"mcp__{name}__{t}" not in rules]
    write_json(settings_path, settings)
    return {"server": name, "config": config, "mcp_json": str(mcp_path), "settings": str(settings_path)}


def remove(ws: Path, name: str) -> bool:
    mcp_path = ws / ".mcp.json"
    data = read_json(mcp_path, {}) or {}
    found = data.get("mcpServers", {}).pop(name, None) is not None
    write_json(mcp_path, data)
    settings_path = ws / ".claude" / "settings.json"
    settings = read_json(settings_path, {}) or {}
    if name in settings.get("enabledMcpjsonServers", []):
        settings["enabledMcpjsonServers"].remove(name)
    perms = settings.get("permissions", {})
    for key in ("allow", "deny"):
        if key in perms:
            perms[key] = [r for r in perms[key] if not r.startswith(f"mcp__{name}__")]
    write_json(settings_path, settings)
    return found


def configured(ws: Path) -> dict:
    return (read_json(ws / ".mcp.json", {}) or {}).get("mcpServers", {})
