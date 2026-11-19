"""Third-party tool catalog: load entries, locate installed binaries, keep tools.lock.json.

``gst tools path <id>`` and every wrapper resolve executables through :func:`bin_path`, so a
tool can live in the gst tools dir, a winget package folder, Program Files or PATH.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from gst import config
from gst.common import read_json, write_json

KINDS = {"winget", "zip", "file", "dotnet-tool", "uv-tool", "python", "npm", "builtin", "guided"}
WINGET_PACKAGES = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"


@dataclass
class Tool:
    id: str
    title: str
    kind: str
    profiles: list[str]
    bins: list[str]
    version: str = ""
    winget: str = ""
    package: str = ""
    url: str = ""
    sha256: str = ""
    root: str = ""
    search: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    version_cmd: list[str] = field(default_factory=list)
    version_re: str = ""
    version_file: str = ""
    admin: bool = False
    license: str = ""
    home: str = ""
    purpose: str = ""
    policy: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)  # kind "python": importable modules that must exist
    metadata_versions: list[str] = field(default_factory=list)  # IL2CPP tools: "39", "24-29.1"
    spec: str = ""  # kind "npm": pinned install spec (default ``<package>@<version>``)

    @property
    def requirements(self) -> list[str]:
        """kind "python": the pinned requirement strings in ``package`` (space separated)."""
        return self.package.split()

    @property
    def install_dir(self) -> Path:
        """Where zip/file kinds are unpacked (tools_dir/<id>/<version>[/root])."""
        return self.install_dir_in(config.tools_dir())

    def install_dir_in(self, root: Path) -> Path:
        base = root / self.id / (self.version or "current")
        return base / self.root if self.root else base

    @property
    def install_dirs(self) -> list[Path]:
        """install_dir under every tools root: gst's own first, then the legacy gs one."""
        return [self.install_dir_in(root) for root in config.tools_dirs()]


def load_catalog() -> dict[str, Tool]:
    text = resources.files("gst").joinpath("data/catalog.toml").read_text(encoding="utf-8")
    raw = tomllib.loads(text).get("tools", {})
    catalog = {}
    for tool_id, entry in raw.items():
        known = {k: v for k, v in entry.items() if k in Tool.__dataclass_fields__}
        catalog[tool_id] = Tool(id=tool_id, **known)
    return catalog


def get(tool_id: str) -> Tool:
    catalog = load_catalog()
    if tool_id not in catalog:
        from gst.common import GsError

        raise GsError(f"unknown tool '{tool_id}' (see gst tools list)")
    return catalog[tool_id]


# --- lock file ----------------------------------------------------------------------------------

def lock_path() -> Path:
    return config.tools_dir() / "tools.lock.json"


def read_lock() -> dict:
    data = read_json(lock_path(), {})
    return data if isinstance(data, dict) else {}


def read_lock_entry(tool_id: str) -> dict:
    """A tool's lock entry: gst's own tools.lock.json first, then the legacy gs one (read-only)."""
    for root in config.tools_dirs():
        data = read_json(root / "tools.lock.json", {})
        if isinstance(data, dict) and isinstance(data.get(tool_id), dict):
            return data[tool_id]
    return {}


def write_lock_entry(tool_id: str, entry: dict) -> None:
    lock = read_lock()
    lock[tool_id] = entry
    write_json(lock_path(), lock)


# --- locating binaries --------------------------------------------------------------------------

def _expand(pattern: str) -> str:
    return os.path.expandvars(pattern.replace("%USERPROFILE%", str(Path.home())))


def _search_globs(tool: Tool) -> list[str]:
    patterns = [_expand(p) for p in tool.search]
    if tool.kind == "winget" and tool.winget and WINGET_PACKAGES.exists():
        for folder in WINGET_PACKAGES.glob(f"{tool.winget}_*"):
            for b in tool.bins:
                patterns.append(str(folder / "**" / Path(b).name))
    if tool.kind in {"zip", "file"}:
        for folder in tool.install_dirs:
            for b in tool.bins:
                patterns.append(str(folder / b))
    if tool.kind == "npm":
        for folder in npm_install_dirs(tool):
            for b in tool.bins:
                patterns.append(str(folder / b))
    return patterns


# --- kind "npm" ---------------------------------------------------------------------------------

def npm_spec(tool: Tool) -> tuple[str, str]:
    """``(spec, label)`` to install a kind "npm" tool. The spec comes from ``GST_<ID>_SPEC`` (an npm spec, a
    local tarball or a folder, e.g. REA built with ``patches/rea/``), then the config key
    ``<id>_spec`` (``gst config set rea_spec <spec>``), then the catalog (``spec`` or ``<package>@<version>``).
    ``label`` names the install prefix ``<tools_dir>/<id>/<label>``: the version for the catalog spec, else
    ``custom-<sha256(spec)[:12]>``, so a local build never overwrites the pinned release."""
    import hashlib

    key = re.sub(r"[^A-Za-z0-9]", "_", tool.id).upper()
    chosen = (os.environ.get(f"GST_{key}_SPEC") or config.load_config().get(f"{tool.id.replace('-', '_')}_spec")
              or "").strip()
    default = tool.spec or (f"{tool.package}@{tool.version}" if tool.version else tool.package)
    if not chosen or chosen == default:
        return default, tool.version or "current"
    return chosen, "custom-" + hashlib.sha256(chosen.encode("utf-8")).hexdigest()[:12]


def npm_install_dirs(tool: Tool) -> list[Path]:
    """Install prefixes of a kind "npm" tool under every tools root: the selected spec's only (a pinned release
    and a custom spec never stand in for each other)."""
    _spec, label = npm_spec(tool)
    return [root / tool.id / label for root in config.tools_dirs()]


def bin_path(tool: Tool | str, name: str | None = None) -> Path | None:
    """Absolute path of one of the tool's executables (default: the first in ``bins``)."""
    tool = get(tool) if isinstance(tool, str) else tool
    wanted = name or (tool.bins[0] if tool.bins else None)
    if not wanted:
        return None
    lock = read_lock_entry(tool.id)
    recorded = lock.get("bins", {}).get(wanted)
    if recorded and Path(recorded).exists() and tool.kind != "npm":  # npm: the selected spec's prefix decides
        return Path(recorded)
    base = Path(wanted).name.lower()
    for pattern in _search_globs(tool):
        hits = [Path(h) for h in glob.glob(pattern, recursive=True) if Path(h).name.lower() == base]
        if hits:
            return sorted(hits)[-1]  # newest version folder sorts last
    from shutil import which

    found = which(Path(wanted).name)
    return Path(found) if found else None


def installed_version(tool: Tool) -> str | None:
    """Best-effort version of the installed tool (None if unknown or not installed)."""
    lock = read_lock_entry(tool.id)
    if tool.version_file:
        path = next((d / tool.version_file for d in tool.install_dirs if (d / tool.version_file).exists()),
                    tool.install_dir / tool.version_file)
        if path.exists() and tool.version_re:
            match = re.search(tool.version_re, path.read_text(encoding="utf-8", errors="replace"))
            if match:
                return match.group(1)
    exe = bin_path(tool)
    if exe is None:
        return None
    if tool.version_cmd and tool.version_re:
        cmd = [str(exe) if part == "{bin}" else part for part in tool.version_cmd]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=20, stdin=subprocess.DEVNULL)
            match = re.search(tool.version_re, (proc.stdout or "") + (proc.stderr or ""))
            if match:
                return match.group(1)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return lock.get("version") or ("present" if exe else None)


def python_status(tool: Tool) -> tuple[str | None, str | None]:
    """(version, location) of a kind "python" tool in gst's own environment, or (None, None)."""
    import importlib.metadata
    import importlib.util

    location = None
    for module in tool.modules:
        try:
            spec = importlib.util.find_spec(module)
        except ModuleNotFoundError:  # a dotted name whose parent package is missing (dissect.cstruct)
            spec = None
        if spec is None:
            return None, None
        location = location or (spec.origin or (list(spec.submodule_search_locations or []) or [None])[0])
    dist = re.match(r"[A-Za-z0-9_.-]+", tool.requirements[0]).group(0) if tool.requirements else tool.id
    try:
        version = importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        version = "present"
    return version, location


def status(tool: Tool) -> dict:
    if tool.kind == "python":
        version, location = python_status(tool)
        state = "missing" if version is None else ("ok" if version == "present" or _same_version(version, tool.version)
                                                   else "other-version")
        return {"id": tool.id, "title": tool.title, "state": state, "version": version, "wanted": tool.version,
                "path": location, "kind": tool.kind, "profiles": tool.profiles}
    exe = bin_path(tool)
    extra: dict = {}
    if tool.kind == "npm":
        spec, label = npm_spec(tool)
        extra = {"spec": spec}
        if label != (tool.version or "current") and exe is not None and not any(
                _under(exe, d) for d in npm_install_dirs(tool)):
            extra["other_install"] = str(exe)  # e.g. the global npm one; the selected spec is not installed yet
            exe = None
    version = installed_version(tool) if exe else None
    if exe is None:
        state = "guided" if tool.kind == "guided" else "missing"
    elif tool.version and version and version not in {"present"} and not _same_version(version, tool.version):
        state = "other-version"
    else:
        state = "ok"
    return {"id": tool.id, "title": tool.title, "state": state, "version": version, "wanted": tool.version,
            "path": str(exe) if exe else None, "kind": tool.kind, "profiles": tool.profiles, **extra}


def _under(path: Path, folder: Path) -> bool:
    from gst.common import is_under

    return is_under(path, folder)


def _same_version(found: str, wanted: str) -> bool:
    norm = lambda v: re.sub(r"[^0-9.]", "", v.replace("-", ".").replace("_", ".")).strip(".")  # noqa: E731
    a, b = norm(found), norm(wanted)
    return bool(a) and bool(b) and (a == b or a.startswith(b) or b.startswith(a))


def profile_tools(profiles: list[str]) -> list[Tool]:
    catalog = load_catalog()
    if "all" in profiles:
        return list(catalog.values())
    return [t for t in catalog.values() if set(t.profiles) & set(profiles)]
