"""gst doctor: check the study environment, tools and system state; --brief for hooks."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from gst import __version__, config, hooks, tools
from gst.common import emit, human_size, read_json, write_json

# service/driver names of anti-cheats that may be resident (see references/protections.md)
ANTICHEAT_SERVICES = {
    "BEService": "BattlEye", "EasyAntiCheat": "Easy Anti-Cheat", "EasyAntiCheat_EOS": "Easy Anti-Cheat (EOS)",
    "vgc": "Riot Vanguard", "vgk": "Riot Vanguard (kernel)", "npggsvc": "nProtect GameGuard",
    "PnkBstrA": "PunkBuster", "PnkBstrB": "PunkBuster", "EAAntiCheatService": "EA Javelin",
    "FACEITService": "FACEIT", "FACEIT": "FACEIT (driver)", "ACE-BASE": "Tencent ACE", "AntiCheatExpert Service": "Tencent ACE",
    "xhunter1": "XIGNCODE3", "mhyprot2": "mhyprot",
}
BRIEF_CACHE_TTL = 12 * 3600


def register(sub, parent) -> None:
    p = sub.add_parser("doctor", parents=[parent], help="check environment, tools and anti-cheat services",
                       description=__doc__)
    p.add_argument("--brief", action="store_true", help="at most 5 lines (used by the SessionStart hook)")
    p.add_argument("--profile", default="core", help="tool profile to check (default core; 'all' for everything)")
    p.add_argument("--refresh", action="store_true", help="ignore the --brief cache")
    p.set_defaults(func=cmd_doctor)


# --- individual checks ------------------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 10) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
        return (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def long_paths_enabled() -> bool | None:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except OSError:
        return None


def anticheat_services() -> list[dict]:
    """Installed anti-cheat services/drivers and whether they are running (sc.exe query)."""
    if os.name != "nt":
        return []
    found = []
    try:
        import winreg

        base = r"SYSTEM\CurrentControlSet\Services"
        for name, label in ANTICHEAT_SERVICES.items():
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{name}") as key:
                    start = winreg.QueryValueEx(key, "Start")[0]
            except OSError:
                continue
            state = "RUNNING" if "RUNNING" in _run(["sc.exe", "query", name], 5) else "stopped"
            found.append({"service": name, "product": label, "state": state,
                          "start": {0: "boot", 1: "system", 2: "auto", 3: "manual", 4: "disabled"}.get(start, str(start))})
    except ImportError:
        pass
    return found


def nvidia_driver() -> str | None:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    out = _run([smi, "--query-gpu=driver_version", "--format=csv,noheader"], 10).strip()
    return out.splitlines()[0].strip() if out else None


def user_bin_on_path() -> bool:
    return hooks.gs_on_path()


LEGACY_PLUGIN = "game-study@"  # the gs plugin (game-study@game-study-local); docs/MIGRATION.md retires it


def plugin_state() -> dict:
    """gst plugin and the legacy gs plugin in the user's Claude Code config (read-only: settings.json
    enabledPlugins + plugins/installed_plugins.json)."""
    claude_dir = Path.home() / ".claude"
    settings = read_json(claude_dir / "settings.json", {}) or {}
    enabled = settings.get("enabledPlugins", {}) if isinstance(settings, dict) else {}
    enabled = enabled if isinstance(enabled, dict) else {}
    installed = (read_json(claude_dir / "plugins" / "installed_plugins.json", {}) or {}).get("plugins", {})
    names = set(enabled) | (set(installed) if isinstance(installed, dict) else set())
    key = next((k for k in sorted(names) if k.startswith("gst@")), None)
    legacy = next((k for k in sorted(names) if k.startswith(LEGACY_PLUGIN)), None)
    from gst.cmd_setup import claude_cli

    return {"installed": key is not None, "enabled": bool(key and enabled.get(key)), "key": key,
            "legacy": {"key": legacy, "installed": legacy is not None, "enabled": bool(legacy and enabled.get(legacy))},
            "claude_cli": claude_cli()}


def workspace_trust() -> list[dict]:
    from gst.guard import registry_games
    from gst.workspace import claude_trusted

    out = []
    for slug, game in sorted(registry_games().items()):
        ws = Path(game["workspace"]) if game.get("workspace") else None
        if ws and (ws / "game.json").is_file():
            out.append({"slug": slug, "path": str(ws), "claude_trusted": claude_trusted(ws)})
    return out


def home_state() -> dict:
    from gst.guard import git_root

    home = config.gs_home()
    data = {"path": str(home), "exists": home.exists(), "inside_git": None, "free": None}
    probe = home if home.exists() else home.anchor
    try:
        data["free"] = human_size(shutil.disk_usage(probe).free)
    except OSError:
        pass
    root = git_root(home)
    data["inside_git"] = str(root) if root else None
    return data


# --- report -----------------------------------------------------------------------------------------

def collect(profile: str = "core") -> dict:
    tool_rows = [tools.status(t) for t in tools.profile_tools([profile])]
    return {
        "gst": {"version": __version__, "python": platform.python_version(), "executable": sys.executable},
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "gs_home": home_state(),
        "tools_dir": str(config.tools_dir()),
        "long_paths": long_paths_enabled(),
        "user_bin_on_path": user_bin_on_path(),
        "nvidia_driver": nvidia_driver(),
        "anticheat": anticheat_services(),
        "plugin": plugin_state(),
        "hooks": hooks.launcher_state(),
        "workspaces": workspace_trust(),
        "tools": tool_rows,
        "checked_at": int(time.time()),
    }


def findings(report: dict) -> list[tuple[str, str]]:
    """[(level, message)] with level 'error' (exit 1) or 'warn' (informative)."""
    out: list[tuple[str, str]] = []
    home = report["gs_home"]
    if home["inside_git"]:
        out.append(("error", f"GS_HOME {home['path']} is inside a git repo ({home['inside_git']}): move it (gst config set gs_home ...)"))
    missing = [t["id"] for t in report["tools"] if t["state"] == "missing"]
    if missing:
        out.append(("error", f"missing tools: {', '.join(missing)} (gst tools install {' '.join(missing[:6])})"))
    if report["long_paths"] is not True and os.name == "nt":
        state = "0" if report["long_paths"] is False else "unset"
        out.append(("warn", f"HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled={state} (read only): gst writes "
                            "long paths with the \\\\?\\ prefix and keeps full names in the sidecars, but third-party tools "
                            "(GDRE, uas, Ghidra) may fail on deep outputs; install.ps1 -EnableLongPaths enables it (admin)"))
    plugin = report.get("plugin") or {}
    legacy = plugin.get("legacy") or {}
    if legacy.get("enabled") and plugin.get("enabled"):
        out.append(("warn", f"both plugins are enabled: {plugin['key']} and the legacy {legacy['key']} (skills gst:* and "
                            "game-study:* side by side, two sets of hooks); when gst covers your studies, disable the "
                            "legacy one (docs/MIGRATION.md)"))
    elif legacy.get("enabled"):
        out.append(("warn", f"the legacy plugin {legacy['key']} is enabled and gst@gst-local is not: install.ps1 -Plugin "
                            "registers gst; docs/MIGRATION.md has the switch-over and rollback commands"))
    other = [f"{t['id']} {t['version']}" for t in report["tools"] if t["state"] == "other-version"]
    if other:
        out.append(("warn", f"other versions than pinned: {', '.join(other)}"))
    running = [s for s in report["anticheat"] if s["state"] == "RUNNING"]
    if running:
        names = ", ".join(f"{s['product']} ({s['service']})" for s in running)
        out.append(("warn", f"anti-cheat service running: {names}. Close protected games before runtime work; gst never stops these services"))
    if not report["user_bin_on_path"]:
        out.append(("warn", "~/.local/bin not on PATH in this process: bare `gst` works after restarting VS Code/shells"))
    untrusted = [w["slug"] for w in report.get("workspaces", []) if w["claude_trusted"] is False]
    if untrusted:
        out.append(("warn", f"workspaces not yet trusted in Claude Code: {', '.join(untrusted)} "
                            "(open each once and accept the trust dialog; their allow rules apply only then)"))
    if report["plugin"]["installed"] and report["hooks"]["state"] != "ok":
        out.append(("warn", f"hook fast path {report['hooks']['state']}: plugin hooks start slower via gst.exe (gst setup hooks)"))
    return out


def problems(report: dict) -> list[str]:
    return [msg for _, msg in findings(report)]


def brief(refresh: bool = False) -> list[str]:
    cache = config.cache_dir() / "doctor-brief.json"
    data = read_json(cache, {}) or {}
    if not refresh and data.get("checked_at", 0) > time.time() - BRIEF_CACHE_TTL and data.get("lines"):
        return data["lines"]
    report = collect("core")
    issues = problems(report)
    ok = sum(1 for t in report["tools"] if t["state"] == "ok")
    head = f"gst {report['gst']['version']} | core tools {ok}/{len(report['tools'])} ok | GS_HOME {report['gs_home']['path']}"
    lines = [head] + issues[:4]
    write_json(cache, {"checked_at": int(time.time()), "lines": lines})
    return lines


def cmd_doctor(args) -> int:
    if args.brief:
        lines = brief(refresh=args.refresh)
        emit({"lines": lines}, args.json, "\n".join(lines))
        return 0
    report = collect(args.profile)
    found = findings(report)
    errors = [m for lvl, m in found if lvl == "error"]
    if args.json:
        emit({**report, "errors": errors, "warnings": [m for lvl, m in found if lvl == "warn"]}, True)
        return 1 if errors else 0
    home = report["gs_home"]
    print(f"gst {report['gst']['version']} on Python {report['gst']['python']} ({report['os']})")
    print(f"GS_HOME   {home['path']}  exists={home['exists']}  free={home['free']}  inside_git={bool(home['inside_git'])}")
    print(f"tools dir {report['tools_dir']}")
    print(f"LongPathsEnabled={report['long_paths']}  ~/.local/bin on PATH={report['user_bin_on_path']}  "
          f"NVIDIA driver={report['nvidia_driver']}")
    plugin = report["plugin"]
    print(f"Claude plugin installed={plugin['installed']} enabled={plugin['enabled']}  claude CLI={plugin['claude_cli'] or 'not on PATH'}"
          f"  hook fast path={report['hooks']['state']}")
    legacy = plugin["legacy"]
    print(f"legacy gs plugin {legacy['key'] or '(none)'} installed={legacy['installed']} enabled={legacy['enabled']}"
          + ("  (coexists with gst: docs/MIGRATION.md)" if legacy["installed"] and plugin["installed"] else ""))
    if report["anticheat"]:
        print("anti-cheat services: " + "; ".join(f"{s['product']} {s['state']} ({s['start']})" for s in report["anticheat"]))
    print("")
    for t in report["tools"]:
        print(f"  {t['id']:<14} {t['state']:<14} {str(t['version'] or '-'):<18} {t['path'] or ''}")
    if found:
        print("")
        for lvl, line in found:
            print(f"  {'ERROR' if lvl == 'error' else 'warn '} {line}")
    return 1 if errors else 0


def summary_json() -> str:
    return json.dumps(brief(), ensure_ascii=False)
