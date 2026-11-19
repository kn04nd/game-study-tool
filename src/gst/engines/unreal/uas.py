"""UnrealAssetScout (uas, CUE4Parse) list/export. Every argument goes through a response file
(``uas @args.rsp``, one token per line), so a filter of any size is one run: no command-line ceiling,
no batches that would delete each other's outputs (uas removes outputs outside a narrower ``--filter`` on
an incremental run). Keys reach uas as ``--aes-file`` (a workspace file), never on the command line.

Filters for exact packed paths are compiled into a prefix trie (``^(?:FSD/Content/(?:A|B)...)\\.``), so .NET
matches each path in time linear to its length whatever the number of paths.
"""

from __future__ import annotations

import re
from pathlib import Path

MODES = {"json": "Json", "textures": "Textures", "audio": "Audio", "models": "Models", "raw": "Raw",
         "animations": "Animations", "simple": "Simple"}
EXPORTABLE = (".uasset", ".umap", ".uexp", ".ubulk", ".bnk", ".wem", ".ini", ".json", ".locres", ".png", ".txt", ".ttf")


def _quote(token: str) -> str:
    if token == "" or any(c.isspace() for c in token) or token.startswith("#") or '"' in token:
        return '"' + token.replace('"', '\\"') + '"'
    return token


def response_file(path: Path, args: list[str]) -> Path:
    from gst.common import write_text

    write_text(path, "\n".join(_quote(a) for a in args) + "\n")
    return path


def trie_regex(words: list[str]) -> str:
    """A regex matching exactly ``words`` (as prefixes are anchored by the caller), factored by common prefix."""
    root: dict = {}
    for w in sorted(set(words)):
        node = root
        for ch in w:
            node = node.setdefault(ch, {})
        node[""] = {}

    def emit(node: dict) -> str:
        end = "" in node
        alts = []
        for ch in sorted(k for k in node if k):
            s, child = re.escape(ch), node[ch]
            while len(child) == 1 and "" not in child:
                (c2, nxt), = child.items()
                s += re.escape(c2)
                child = nxt
            alts.append(s + emit(child))
        if not alts:
            return ""
        if len(alts) == 1 and not end:
            return alts[0]
        return "(?:" + "|".join(alts) + ")" + ("?" if end else "")

    import sys

    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 10_000))  # one frame per branching point of a path
    try:
        return emit(root)
    finally:
        sys.setrecursionlimit(limit)


def exact_filter(paths: list[str]) -> str:
    """Regex for these packed files and their companions (.uasset -> .uasset/.uexp/.ubulk)."""
    stems = sorted({p.rsplit(".", 1)[0] if "/" not in p.rsplit(".", 1)[-1] else p for p in paths})
    return "^" + trie_regex(stems) + r"\."


def union_filter(patterns: list[str]) -> str:
    """User regexes (``--name``) as one alternation: uas gets the union of every filter of a folder."""
    return "|".join(f"(?:{p})" for p in dict.fromkeys(patterns))


def common_args(paks: Path, game_enum: str, *, log: Path, key_file: Path | None = None,
                usmap: Path | None = None) -> list[str]:
    args = ["--paks", str(paks), "--game", game_enum, "--log", str(log)]
    if key_file is not None:
        args += ["--aes-file", str(key_file)]
    if usmap is not None:
        args += ["--usmap", str(usmap)]
    return args


def export_args(paks: Path, out: Path, mode: str, game_enum: str, *, log: Path, flt: str | None = None,
                bytecode: bool = False, key_file: Path | None = None, usmap: Path | None = None,
                all_types: bool = False) -> list[str]:
    args = ["export", MODES[mode], *common_args(paks, game_enum, log=log, key_file=key_file, usmap=usmap),
            "--output", str(out), "--compact"]
    if flt:
        args += ["--filter", flt]
    if bytecode and mode == "json":
        args.append("--script-bytecode")
    if all_types and mode == "json":  # uas skips "specialized export" classes (SkeletalMesh, AnimMontage...) by default
        args.append("--no-skip-types")
    return args


def run_args(args: list[str], rsp: Path):
    """``uas @rsp``: the whole argument list from a file."""
    from gst.engines.unreal import run, tool_bin

    response_file(rsp, args)
    return run([str(tool_bin("uas")), f"@{rsp}"])


def export(paks: Path, out: Path, mode: str, game_enum: str, *, log: Path, rsp: Path, flt: str | None = None,
           bytecode: bool = False, key_file: Path | None = None, usmap: Path | None = None, all_types: bool = False):
    out.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    return run_args(export_args(paks, out, mode, game_enum, log=log, flt=flt, bytecode=bytecode, key_file=key_file,
                                usmap=usmap, all_types=all_types), rsp)


def list_paths(paks: Path, game_enum: str, *, log: Path, rsp: Path, key_file: Path | None = None,
               usmap: Path | None = None) -> tuple[list[str], list[str]]:
    """(paths, paths uas marks as needing a .usmap) from ``uas list --format List --mark-usmap``."""
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = run_args(["list", *common_args(paks, game_enum, log=log, key_file=key_file, usmap=usmap), "--format", "List",
                     "--mark-usmap"], rsp)
    paths, needs = [], []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("[*]"):
            line = line[3:].strip()
            needs.append(line)
        if "/" in line and not line.startswith("["):
            paths.append(line)
    return paths, needs
