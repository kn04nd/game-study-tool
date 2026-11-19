"""Locations and user configuration.

Precedence for every setting: environment variable > config file > default.
Config file: ``%LOCALAPPDATA%/game-study-tool/config.toml`` (flat ``key = "value"`` pairs).

Coexistence with the legacy ``gs`` (game-study-toolkit): while gst has no config file of its own it
reads ``%LOCALAPPDATA%/game-study/config.toml`` (so ``gs_home = D:/game-study`` is inherited), and
every ``GST_*`` environment variable also accepts its legacy ``GS_*`` name. ``gst config set`` writes
only gst's own file (seeded with the inherited values); the legacy file is never written.
"""

from __future__ import annotations

import os
from pathlib import Path

from gst.common import read_json, write_text

APP = "game-study-tool"
LEGACY_APP = "game-study"  # state folder of the legacy gs CLI (read-only for gst)
WORKSPACE_MARKER = "game.json"

KEYS = {
    "gs_home": ("GST_HOME", "Study root: one folder per game (must be outside any git repo)."),
    "tools_dir": ("GST_TOOLS", "Where gst installs third-party tools."),
    "online_metadata": ("GST_ONLINE", "Allow public metadata lookups (Steam store API): yes/no."),
    "focus": ("GST_FOCUS", "What /gst:study-all studies first: comma-separated topics (default: DEFAULT_FOCUS)."),
}
# The user's study priorities (topics for the game they are making); `gst config set focus "a,b"` overrides.
DEFAULT_FOCUS = ("ragdoll", "voz por proximidade", "mapa procedural", "co-op", "loop de extração", "inventário", "física")
# Legacy environment names, read after the GST_* ones.
LEGACY_ENV = {"GST_HOME": "GS_HOME", "GST_TOOLS": "GS_TOOLS", "GST_ONLINE": "GS_ONLINE", "GST_GAME": "GS_GAME"}


def env(name: str) -> str | None:
    """``GST_X`` from the environment, else its legacy ``GS_X`` (empty values count as unset)."""
    value = os.environ.get(name)
    if not value and name in LEGACY_ENV:
        value = os.environ.get(LEGACY_ENV[name])
    return value or None


def _local_base() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state"))


def state_dir() -> Path:
    return _local_base() / APP


def legacy_state_dir() -> Path:
    """``%LOCALAPPDATA%/game-study``: config, tools and hook.cfg of the legacy gs (never written by gst)."""
    return _local_base() / LEGACY_APP


def config_path() -> Path:
    return state_dir() / "config.toml"


def legacy_config_path() -> Path:
    return legacy_state_dir() / "config.toml"


def effective_config_path() -> Path:
    """Where settings come from: gst's own config, else the legacy gs config when only that exists."""
    own = config_path()
    if own.is_file():
        return own
    legacy = legacy_config_path()
    return legacy if legacy.is_file() else own


def _parse_flat(text: str) -> dict[str, str] | None:
    """The ``key = "value"`` lines save_config writes; None for anything else (then tomllib parses)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not (sep and key.replace("_", "").isalnum() and len(value) >= 2 and value[0] == value[-1] == '"'
                and "\\" not in value and '"' not in value[1:-1]):
            return None
        out[key] = value[1:-1]
    return out


def load_config() -> dict[str, str]:
    path = effective_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    flat = _parse_flat(text)  # the hook path reads config on every guarded command: skip tomllib
    if flat is not None:
        return flat
    import tomllib

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    return {k: str(v) for k, v in data.items() if not isinstance(v, (dict, list))}


def save_config(values: dict[str, str]) -> Path:
    lines = ["# Game Study Toolkit configuration (gst config set <key> <value>)"]
    for key in sorted(values):
        escaped = str(values[key]).replace("\\", "/").replace('"', '\\"')
        lines.append(f'{key} = "{escaped}"')
    return write_text(config_path(), "\n".join(lines) + "\n")


def get(key: str, default: str | None = None) -> str | None:
    env_name = KEYS.get(key, (key.upper(), ""))[0]
    value = env(env_name)
    if value:
        return value
    return load_config().get(key, default)


def _default_home() -> Path:
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/game-study")
    return Path.home() / "game-study"


def gs_home() -> Path:
    value = get("gs_home")
    return Path(value).expanduser() if value else _default_home()


def tools_dir() -> Path:
    """gst's own tools root (installs go here). A ``tools_dir`` inherited from the legacy config is
    not adopted: that folder is searched as the legacy root instead (see :func:`tools_dirs`)."""
    value = os.environ.get("GST_TOOLS")
    if not value and effective_config_path() == config_path():
        value = load_config().get("tools_dir")
    return Path(value).expanduser() if value else state_dir() / "tools"


def legacy_tools_dir() -> Path | None:
    """Tools root of the legacy gs (GS_TOOLS, its config's ``tools_dir``, else
    ``%LOCALAPPDATA%/game-study/tools``), searched after gst's own so tools gs installed (Ghidra,
    ilspycmd, GDRE...) are reused. None when it is the same folder as :func:`tools_dir`."""
    legacy_cfg: dict[str, str] = {}
    try:
        legacy_cfg = _parse_flat(legacy_config_path().read_text(encoding="utf-8")) or {}
    except OSError:
        pass
    value = os.environ.get("GS_TOOLS") or legacy_cfg.get("tools_dir")
    folder = Path(value).expanduser() if value else legacy_state_dir() / "tools"
    try:
        same = folder.resolve() == tools_dir().resolve()
    except OSError:
        same = False
    return None if same else folder


def tools_dirs() -> list[Path]:
    """Tool roots in search order: gst's own, then the legacy gs one."""
    legacy = legacy_tools_dir()
    return [tools_dir()] + ([legacy] if legacy else [])


def focus() -> list[str]:
    """Study priorities, in order: ``focus`` setting (GST_FOCUS or config), else :data:`DEFAULT_FOCUS`."""
    value = get("focus")
    items = [v.strip() for v in (value or "").split(",") if v.strip()]
    return items or list(DEFAULT_FOCUS)


def current_game() -> str | None:
    """Slug from ``GST_GAME`` (or the legacy ``GS_GAME``) that workspace settings export."""
    return env("GST_GAME")


def cache_dir() -> Path:
    return state_dir() / "cache"


def online_allowed() -> bool | None:
    """True/False when the user decided; None when never asked."""
    value = (get("online_metadata") or "").strip().lower()
    if value in {"1", "yes", "true", "on"}:
        return True
    if value in {"0", "no", "false", "off"}:
        return False
    return None


def find_workspace(start: str | os.PathLike | None = None) -> Path | None:
    """Walk up from ``start`` (default cwd) to the folder holding a gst ``game.json``."""
    here = Path(start or os.getcwd()).resolve()
    for folder in (here, *here.parents):
        marker = folder / WORKSPACE_MARKER
        if marker.is_file():
            data = read_json(marker, {})
            if isinstance(data, dict) and data.get("gs_workspace"):
                return folder
    return None
