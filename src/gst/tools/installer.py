"""Install catalog tools: winget, verified zip/file downloads, dotnet tools, uv tools.

Integrity: winget verifies its manifest hash; zip/file downloads are checked against the pinned
SHA-256, the digest GitHub publishes on the release page (``sha256 = "github"``), or a hash
recorded on first use (``"tofu"``). Nothing is extracted until the hash matches.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from gst import config
from gst.common import GsError, IS_WINDOWS, run, warn
from gst.tools import Tool, bin_path, load_catalog, read_lock, status, write_lock_entry

USER_AGENT = "game-study-toolkit/0.1 (+local install)"
WINGET_ALREADY = {-1978335189, -1978335135}  # update not applicable / package already installed


# --- downloads ----------------------------------------------------------------------------------

def _open(url: str, timeout: float = 60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - https/file URLs from the catalog only


def fetch_text(url: str, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with _open(url) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as exc:  # includes ConnectionResetError
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise GsError(f"could not fetch {url}: {last}")


def download(url: str, dest: Path, retries: int = 4) -> str:
    """Stream ``url`` to ``dest`` and return its SHA-256 (hex)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(retries):
        sha = hashlib.sha256()
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with _open(url, timeout=120) as resp, open(tmp, "wb") as fh:
                while chunk := resp.read(1 << 20):
                    sha.update(chunk)
                    fh.write(chunk)
            os.replace(tmp, dest)
            return sha.hexdigest()
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            tmp.unlink(missing_ok=True)
            time.sleep(2 * (attempt + 1))
    raise GsError(f"download failed: {url}: {last}")


_GH_ASSET = re.compile(r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/download/(?P<tag>[^/]+)/(?P<asset>[^/?#]+)")


def github_digest(url: str) -> str | None:
    """SHA-256 that GitHub shows for a release asset (expanded_assets page; no API quota)."""
    match = _GH_ASSET.match(url)
    if not match:
        return None
    owner, repo, tag, asset = match.group("owner", "repo", "tag", "asset")
    html = fetch_text(f"https://github.com/{owner}/{repo}/releases/expanded_assets/{tag}")
    anchor = html.find(f"/releases/download/{tag}/{asset}")
    if anchor < 0:
        return None
    window = html[anchor : anchor + 4000]
    found = re.search(r"sha256:([0-9a-f]{64})", window)
    return found.group(1) if found else None


def expected_hash(tool: Tool) -> str | None:
    if re.fullmatch(r"[0-9a-fA-F]{64}", tool.sha256 or ""):
        return tool.sha256.lower()
    if tool.sha256 == "github":
        digest = github_digest(tool.url)
        if digest:
            return digest
        warn(f"{tool.id}: GitHub did not publish a digest for this asset; using trust-on-first-use")
    recorded = read_lock().get(tool.id, {}).get("sha256")
    return recorded if recorded and read_lock().get(tool.id, {}).get("version") == tool.version else None


# --- extraction -----------------------------------------------------------------------------------

def _long(path: Path) -> str:
    """Prefix with \\\\?\\ on Windows so extraction survives LongPathsEnabled=0."""
    s = str(path.resolve())
    return s if not IS_WINDOWS or s.startswith("\\\\?\\") else "\\\\?\\" + s


def extract_zip(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = Path(_long(dest))
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise GsError(f"unsafe path in archive: {name}")
            target = root / name
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)


# --- per-kind installers --------------------------------------------------------------------------

def _record(tool: Tool, **extra) -> None:
    bins = {}
    for b in tool.bins:
        p = bin_path(tool, b)
        if p:
            bins[b] = str(p)
    write_lock_entry(tool.id, {
        "version": tool.version,
        "kind": tool.kind,
        "bins": bins,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **extra,
    })


def _install_winget(tool: Tool) -> None:
    winget = shutil.which("winget")
    if not winget:
        raise GsError("winget not found (App Installer). Install it from the Microsoft Store.")
    cmd = [winget, "install", "--id", tool.winget, "-e", "--accept-source-agreements",
           "--accept-package-agreements", "--silent", "--disable-interactivity"]
    if tool.version:
        cmd += ["--version", tool.version]
    proc = run(cmd, check=False, timeout=3600)
    if proc.returncode not in (0, *WINGET_ALREADY):
        tail = (proc.stdout or proc.stderr or "").strip()[-600:]
        raise GsError(f"winget install {tool.winget} failed ({proc.returncode}): {tail}")
    _record(tool, source=f"winget:{tool.winget}")


def _install_download(tool: Tool) -> None:
    if not tool.url:
        raise GsError(f"{tool.id}: catalog entry has no url")
    downloads = config.tools_dir() / ".downloads"
    asset = tool.url.rsplit("/", 1)[-1].split("?")[0]
    if tool.kind == "file" and tool.bins and "*" not in tool.bins[0]:
        asset = Path(tool.bins[0]).name  # e.g. SourceForge URLs end in /download
    archive = downloads / f"{tool.id}-{tool.version}-{asset}"
    expected = expected_hash(tool)
    got = download(tool.url, archive)
    if expected and got != expected:
        archive.unlink(missing_ok=True)
        raise GsError(f"{tool.id}: SHA-256 mismatch (expected {expected[:16]}..., got {got[:16]}...); download removed")
    if asset.lower().endswith((".jar", ".zip")) and archive.read_bytes()[:2] != b"PK":
        archive.unlink(missing_ok=True)
        raise GsError(f"{tool.id}: the download is not a zip/jar (a web page instead?); nothing installed")
    base = config.tools_dir() / tool.id / tool.version
    staging = Path(tempfile.mkdtemp(prefix=f"{tool.id}-", dir=config.tools_dir()))
    try:
        if tool.kind == "zip":
            extract_zip(archive, staging)
        else:
            shutil.copy2(archive, staging / asset)
        if base.exists():
            shutil.rmtree(_long(base))
        base.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, base)
    finally:
        if staging.exists():
            shutil.rmtree(_long(staging), ignore_errors=True)
    archive.unlink(missing_ok=True)
    _record(tool, sha256=got, source=tool.url, verified=bool(expected))


def _install_dotnet_tool(tool: Tool) -> None:
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise GsError(".NET SDK not found (winget install Microsoft.DotNet.SDK.10)")
    proc = run([dotnet, "tool", "install", "-g", tool.package, "--version", tool.version], check=False, timeout=900)
    if proc.returncode != 0 and "already installed" in (proc.stdout + proc.stderr).lower():
        proc = run([dotnet, "tool", "update", "-g", tool.package, "--version", tool.version], check=False, timeout=900)
    if proc.returncode != 0:
        raise GsError(f"dotnet tool install {tool.package} failed: {(proc.stderr or proc.stdout)[-600:]}")
    _record(tool, source=f"nuget:{tool.package}")


def uv_exe() -> str:
    found = shutil.which("uv")
    if found:
        return found
    pkg = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    hits = sorted(pkg.glob("astral-sh.uv_*/uv.exe")) if pkg.exists() else []
    if hits:
        return str(hits[-1])
    raise GsError("uv not found (winget install astral-sh.uv)")


def _install_uv_tool(tool: Tool) -> None:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    proc = run([uv_exe(), "tool", "install", "--python", "3.13", f"{tool.package}=={tool.version}"],
               check=False, timeout=1800, env=env)
    if proc.returncode != 0:
        raise GsError(f"uv tool install {tool.package} failed: {(proc.stderr or proc.stdout)[-600:]}")
    _record(tool, source=f"pypi:{tool.package}")


def _install_python(tool: Tool) -> None:
    """Pinned packages into gst's own environment (the uv tool venv, or the dev .venv)."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    proc = run([uv_exe(), "pip", "install", "--python", sys.executable, *tool.requirements],
               check=False, timeout=1800, env=env)
    if proc.returncode != 0:
        raise GsError(f"uv pip install {' '.join(tool.requirements)} failed: {(proc.stderr or proc.stdout)[-600:]}")
    import importlib

    importlib.invalidate_caches()
    _record(tool, source="pypi:" + " ".join(tool.requirements))


def npm_exe() -> str:
    found = shutil.which("npm")
    if found:
        return found
    raise GsError("npm not found (install Node.js 22.23+, 24.22+ or 26.12+ and put it on PATH)")


def _install_npm(tool: Tool) -> None:
    """``npm install --prefix <tools_dir>/<id>/<label> <spec>``: a private prefix per spec (the pinned release or
    a custom spec such as a local tarball), never a global install. Staged in a sibling folder and renamed into place."""
    from gst.tools import npm_spec

    spec, label = npm_spec(tool)
    if spec and Path(spec).exists():  # local tarball or folder: npm wants an absolute path
        spec = str(Path(spec).resolve())
    base = config.tools_dir() / tool.id / label
    base.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=base.parent))
    try:
        proc = run([npm_exe(), "install", "--prefix", str(staging), "--no-audit", "--no-fund", "--omit=dev", spec],
                   check=False, timeout=None)
        if proc.returncode != 0:
            raise GsError(f"npm install {spec} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
        if base.exists():
            shutil.rmtree(_long(base))
        os.replace(staging, base)
    finally:
        if staging.exists():
            shutil.rmtree(_long(staging), ignore_errors=True)
    _record(tool, source=f"npm:{spec}", spec=spec, prefix=str(base))


def install(tool: Tool, *, force: bool = False, _seen: set[str] | None = None) -> dict:
    """Install ``tool`` (and its ``requires``) unless an acceptable version is present."""
    seen = _seen if _seen is not None else set()
    if tool.id in seen:
        return status(tool)
    seen.add(tool.id)
    catalog = load_catalog()
    for dep in tool.requires:
        install(catalog[dep], force=False, _seen=seen)
    current = status(tool)
    if current["state"] == "ok" and not force:
        return {**current, "action": "kept"}
    if tool.kind == "builtin":
        return {**current, "action": "builtin"}
    if tool.kind == "guided":
        return {**current, "action": "guided", "steps": tool.steps}
    installer = {
        "winget": _install_winget,
        "zip": _install_download,
        "file": _install_download,
        "dotnet-tool": _install_dotnet_tool,
        "uv-tool": _install_uv_tool,
        "python": _install_python,
        "npm": _install_npm,
    }[tool.kind]
    sys.stderr.write(f"gst: installing {tool.title} {tool.version} ({tool.kind})...\n")
    installer(tool)
    return {**status(tool), "action": "installed"}
