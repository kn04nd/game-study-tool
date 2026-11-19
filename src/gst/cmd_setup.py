"""gst setup: register the Claude Code plugin and check the shell setup (used by install.ps1)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import gst
from gst.common import GsError, emit, read_json, run, write_json


def register(sub, parent) -> None:
    p = sub.add_parser("setup", parents=[parent], help="register the Claude Code plugin (install.ps1 calls this)",
                       description=__doc__)
    p.set_defaults(group_parser=p)
    s = p.add_subparsers(dest="action", metavar="<action>")
    plug = s.add_parser("plugin", parents=[parent], help="add the local marketplace and enable game-study")
    plug.add_argument("--root", help="toolkit folder holding .claude-plugin/marketplace.json (default: this checkout)")
    plug.add_argument("--declarative", action="store_true",
                      help="edit ~/.claude/settings.json even if the claude CLI is available")
    plug.set_defaults(func=cmd_plugin)
    hooks = s.add_parser("hooks", parents=[parent], help="record hook.cfg so plugin hooks start Python directly (fast path)")
    hooks.set_defaults(func=cmd_hooks)


def claude_cli() -> str | None:
    """The claude CLI: PATH first, then the winget and native-installer locations (PATH may be stale
    in a VS Code started before the install)."""
    found = shutil.which("claude")
    if found:
        return found
    import os

    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    candidates = [local / "Microsoft" / "WinGet" / "Links" / "claude.exe", Path.home() / ".local" / "bin" / "claude.exe"]
    candidates += sorted((local / "Microsoft" / "WinGet" / "Packages").glob("Anthropic.ClaudeCode_*/claude.exe"))
    return next((str(c) for c in candidates if c.is_file()), None)


def toolkit_root(explicit: str | None = None) -> Path:
    candidates = [Path(explicit)] if explicit else []
    candidates.append(Path(gst.__file__).resolve().parents[2])  # <root>/src/gst/__init__.py
    for root in candidates:
        if (root / ".claude-plugin" / "marketplace.json").is_file():
            return root
    raise GsError("toolkit root not found; pass --root <folder with .claude-plugin/marketplace.json>")


def register_declarative(root: Path, settings_path: Path | None = None) -> Path:
    """Add the directory marketplace + enable game-study in ~/.claude/settings.json (with backup)."""
    settings_path = settings_path or Path.home() / ".claude" / "settings.json"
    data = read_json(settings_path, {}) or {}
    if not isinstance(data, dict):
        raise GsError(f"{settings_path} is not a JSON object; refusing to edit it")
    if settings_path.exists():
        backup = settings_path.with_name(f"settings.json.bak-gs-{int(time.time())}")
        shutil.copy2(settings_path, backup)
    markets = data.setdefault("extraKnownMarketplaces", {})
    markets["gst-local"] = {"source": {"source": "directory", "path": root.as_posix()}}
    data.setdefault("enabledPlugins", {})["gst@gst-local"] = True
    write_json(settings_path, data)
    return settings_path


def cmd_hooks(args) -> int:
    from gst.hooks import ensure_launcher_cfg, launcher_state

    if ensure_launcher_cfg() is None:
        raise GsError("this gst install can't use the hook fast path (base Python or gst source folder not found); "
                      "hooks keep using gst.exe")
    state = launcher_state()
    emit(state, args.json, f"hook.cfg {state['state']}: {state['path']}\n  python {state['python']}\n  src    {state['src']}")
    return 0


def cmd_plugin(args) -> int:
    root = toolkit_root(args.root)
    from gst.hooks import ensure_launcher_cfg

    hook_cfg = ensure_launcher_cfg()
    claude = claude_cli()
    if claude and not args.declarative:
        run([claude, "plugin", "marketplace", "add", root.as_posix()], check=False, timeout=120)
        proc = run([claude, "plugin", "install", "gst@gst-local", "--scope", "user"], check=False, timeout=300)
        if proc.returncode == 0:
            emit({"method": "claude-cli", "root": str(root), "hook_cfg": hook_cfg}, args.json,
                 f"plugin game-study installed from {root} via the claude CLI. Reload VS Code / restart claude.")
            return 0
    path = register_declarative(root)
    emit({"method": "settings.json", "settings": str(path), "root": str(root), "hook_cfg": hook_cfg}, args.json,
         f"marketplace gst-local -> {root}\nenabled gst@gst-local in {path}\n"
         "Reload VS Code (Developer: Reload Window) so Claude Code picks it up.")
    return 0
