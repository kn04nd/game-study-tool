"""No ceilings: no DEFAULT_LIMIT and no constant timeout in extractors,
the pipeline, the engines and the non-Unity backends. A limit or a timeout is only an explicit
option (None by default)."""

from __future__ import annotations

import ast
from pathlib import Path

import gst

SRC = Path(gst.__file__).resolve().parent
SCANNED_DIRS = ("extractors", "pipeline", "engines", "analysis/godot", "library")
SCANNED_FILES = ("assets/godot.py", "assets/unreal.py", "assets/audio.py", "assets/audiocat.py", "decompile/godot.py",
                 "decompile/dotnet.py", "decompile/native.py", "decompile/java.py", "decompile/lua.py", "formats/__init__.py",
                 "recon/index.py", "runtime/snapshot.py", "index/__init__.py", "index/query.py", "cmd_lib.py")


def files() -> list[Path]:
    out = [p for d in SCANNED_DIRS for p in sorted((SRC / d).rglob("*.py"))]
    return out + [SRC / f for f in SCANNED_FILES]


def numeric(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)


def module_constants(tree: ast.Module) -> dict[str, ast.AST]:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            out[node.target.id] = node.value
    return out


def violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    consts = module_constants(tree)
    rel = path.relative_to(SRC).as_posix()
    found = []
    if "DEFAULT_LIMIT" in consts:
        found.append(f"{rel}: DEFAULT_LIMIT")
    lock_waits = {id(k) for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "connect"
                  for k in n.keywords}  # sqlite3.connect(timeout=): waiting on a database lock, not a work limit
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "timeout" and id(node) not in lock_waits:
            value = node.value
            if isinstance(value, ast.Name) and value.id in consts:
                value = consts[value.id]
            if numeric(value):
                found.append(f"{rel}:{node.value.lineno}: timeout={ast.unparse(node.value)}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args.args + node.args.kwonlyargs
            defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults) + list(node.args.kw_defaults)
            for arg, default in zip(args, defaults):
                if arg.arg in {"timeout", "limit", "max_functions", "analysis_timeout", "max_bytes"} and default is not None:
                    if numeric(default) and not (arg.arg == "max_functions" and default.value == 0):
                        found.append(f"{rel}:{node.lineno}: {node.name}({arg.arg}={default.value})")
    return found


def test_no_ceilings_in_extractors_pipeline_and_backends():
    problems = [v for path in files() if path.exists() for v in violations(path)]
    assert problems == []


def test_detector_catches_ceilings(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("DEFAULT_LIMIT = 200\nTIMEOUT = 600\ndef f(x, limit=50):\n    run(x, timeout=TIMEOUT)\n    run(x, timeout=30)\n",
                   encoding="utf-8")
    tree_src = SRC
    try:
        globals()["SRC"] = tmp_path
        found = violations(bad)
    finally:
        globals()["SRC"] = tree_src
    assert len(found) == 4


def test_godot_m5_modules_are_scanned():
    """The Godot parsers and analyses are under the no-ceilings scan."""
    scanned = {p.relative_to(SRC).as_posix() for p in files()}
    for rel in ("engines/godot/pck.py", "engines/godot/tres.py", "engines/godot/animtree.py", "engines/godot/scripts.py",
                "analysis/godot/scenes.py", "analysis/godot/animation.py", "analysis/godot/pck.py"):
        assert rel in scanned, rel


def test_cli_defaults_have_no_caps():
    from gst.cli import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "group").choices

    def default(group: str, action: str | None, dest: str):
        p = sub[group]
        if action:
            p = next(a for a in p._actions if a.dest == "action").choices[action]
        return next(a for a in p._actions if a.dest == dest).default

    assert default("q", None, "limit") is None and default("q", None, "lines") is None
    assert default("mcp", "call", "chars") is None
    assert default("decompile", None, "max_functions") == 0 and default("decompile", None, "timeout") is None
    assert default("assets", "audio", "limit") is None
    assert default("study", None, "timeout") is None
