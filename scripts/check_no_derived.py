"""Fail when git tracks derived game material (guardrail: derived content never goes into git).

Checked on the tracked (and staged) files of this repository:
- provenance sidecars (``*.gs.json``) and asset inventories (``inventory.jsonl``);
- media (``.png .jpg .jpeg .dds .tga .wav .ogg .mp3 .bank .obj .fbx .glb .gltf .bin``) outside ``tests/fixtures/``
  and ``samples/`` sources;
- the repository itself living under GS_HOME (every file would then be study material), or tracked
  symlinks pointing into GS_HOME.

Usage: ``python scripts/check_no_derived.py [repo]`` - exit 0 when clean, 1 with the offending paths.
Runs from the pre-commit hook (``.githooks/pre-commit``) and from ``tests/unit/test_no_derived.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MEDIA = {".png", ".jpg", ".jpeg", ".dds", ".tga", ".wav", ".ogg", ".mp3", ".bank", ".obj", ".fbx", ".glb", ".gltf"}
ALLOWED_PREFIXES = ("tests/fixtures/",)
DERIVED_NAMES = ("inventory.jsonl",)


def tracked_files(repo: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached"], cwd=repo, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def gs_home() -> Path | None:
    try:
        from gst import config
    except ImportError:  # running without the package installed: env only
        value = os.environ.get("GST_HOME") or os.environ.get("GS_HOME")
        return Path(value) if value else None
    return config.gs_home()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def problems(repo: Path, files: list[str] | None = None, home: Path | None = None) -> list[str]:
    files = tracked_files(repo) if files is None else files
    home = gs_home() if home is None else home
    found = []
    if home and _is_under(repo, home):
        found.append(f"repository {repo} is inside GS_HOME {home}")
    for rel in files:
        low = rel.lower()
        name = low.rsplit("/", 1)[-1]
        if low.endswith(".gs.json"):
            found.append(f"{rel}: provenance sidecar (derived)")
        elif name in DERIVED_NAMES:
            found.append(f"{rel}: asset inventory (derived)")
        elif os.path.splitext(low)[1] in MEDIA and not low.startswith(ALLOWED_PREFIXES):
            found.append(f"{rel}: media outside tests/fixtures/")
        elif home:
            full = repo / rel
            if full.is_symlink() and _is_under(full, home):
                found.append(f"{rel}: symlink into GS_HOME")
    return found


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    repo = Path(argv[0]) if argv else Path(__file__).resolve().parents[1]
    found = problems(repo)
    for line in found:
        print(f"derived material tracked by git: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
