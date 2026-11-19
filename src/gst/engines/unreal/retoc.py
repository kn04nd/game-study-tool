"""retoc 0.1.5 on IoStore (``.utoc``/``.ucas``), CLI as printed by ``retoc --help``:

- ``retoc list --path <utoc>``: one row per chunk (container, chunk id, type, path);
- ``retoc to-legacy [-f <substring>]... [--version UE5_x] <utoc or Paks dir> <out.pak or dir>``: Zen packages
  back to legacy ``.uasset``/``.uexp`` (``-f`` keeps packages whose path *contains* one of the substrings);
  a directory input mounts every ``.utoc`` (``global.utoc`` included: its script objects resolve imports);
- ``retoc print-script-objects <global.utoc>``: the script objects of the global container (native
  ``/Script/<Module>`` packages, classes and structs).

Only ``-a <key>`` decrypts, and only with a published key (``keys``). Building containers (``to-zen``,
``pack-raw``) is modding: never called (guardrails.md rule 1). Outputs go under ``dumps/unreal/``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from gst.common import GsError

MAX_CMDLINE = 30_000  # characters per retoc call (Windows stops near 32k); more substrings -> more calls


def _base(key: dict | None) -> list[str]:
    from gst.engines.unreal import tool_bin

    cmd = [str(tool_bin("retoc"))]
    if key is not None:
        cmd += ["--aes-key", key["key"]]
    return cmd


def list_paths(utoc: Path, *, key: dict | None = None) -> list[str]:
    """Packed paths of one container (``retoc list --path``; chunks without a path are skipped)."""
    from gst.engines.unreal import run

    proc = run(_base(key) + ["list", "--path", str(utoc)])
    if proc.returncode != 0:
        raise GsError(f"retoc list {utoc.name} failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()}",
                      3 if "encryption key" in (proc.stderr + proc.stdout) else 1)
    out = []
    for line in proc.stdout.splitlines():
        token = line.split()[-1] if line.split() else ""
        if "/" in token and token != "-":
            out.append(re.sub(r"^(\.\./)+", "", token).lstrip("/"))
    return out


def to_legacy_commands(source: Path, out_pak: Path, *, filters: list[str] | None = None, version: str | None = None,
                       key: dict | None = None, shaders: bool = False) -> list[list[str]]:
    """One ``to-legacy`` command per batch of ``-f`` substrings (each output pak gets a ``-NNN`` suffix when
    there is more than one batch). No filter = the whole input. Shader libraries are left out unless ``shaders``
    (uas reads packages; a shader library that fails to convert would stop the whole run)."""
    base = _base(key) + ["to-legacy"]
    if version:
        base += ["--version", version]
    if not shaders:
        base.append("--no-shaders")
    if not filters:
        return [base + [str(source), str(out_pak)]]
    batches: list[list[str]] = [[]]
    used = len(subprocess.list2cmdline(base + [str(source), str(out_pak)])) + 8
    for f in filters:
        cost = len(subprocess.list2cmdline(["-f", f])) + 1
        if batches[-1] and used + cost > MAX_CMDLINE:
            batches.append([])
            used = len(subprocess.list2cmdline(base + [str(source), str(out_pak)])) + 8
        batches[-1] += ["-f", f]
        used += cost
    if len(batches) == 1:
        return [base + batches[0] + [str(source), str(out_pak)]]
    return [base + b + [str(source), str(out_pak.with_name(f"{out_pak.stem}-{i + 1:03d}{out_pak.suffix}"))]
            for i, b in enumerate(batches)]


def to_legacy(source: Path, out_pak: Path, log: Path, *, filters: list[str] | None = None, version: str | None = None,
              key: dict | None = None, shaders: bool = False) -> dict:
    """Run the conversion; returns {paks, extracted, failed, exit_code}. The full output goes to ``log``.
    ``extracted`` is None when retoc printed no count (another retoc version) but wrote a pak."""
    from gst.common import write_text
    from gst.engines.unreal import run

    out_pak.parent.mkdir(parents=True, exist_ok=True)
    extracted = failed = 0
    counted = False
    code = 0
    text = []
    made = []
    for cmd in to_legacy_commands(source, out_pak, filters=filters, version=version, key=key, shaders=shaders):
        proc = run(cmd)
        code = code or proc.returncode
        shown = [("<key>" if i > 0 and cmd[i - 1] == "--aes-key" else c) for i, c in enumerate(cmd)]
        text.append("$ " + subprocess.list2cmdline(shown) + f"\n(exit {proc.returncode})\n{proc.stdout}\n{proc.stderr}")
        for m in re.finditer(r"Extracted (\d+) \((\d+) failed\) legacy assets", proc.stdout + proc.stderr):
            extracted += int(m.group(1))
            failed += int(m.group(2))
            counted = True
        target = Path(cmd[-1])
        if target.exists() and target.stat().st_size:
            made.append(target)
    write_text(log, "\n".join(text))
    if code != 0 and not made:
        blob = "\n".join(text)
        raise GsError(f"retoc to-legacy failed (exit {code}); see {log}", 3 if "encryption key" in blob else 1)
    return {"paks": [str(p) for p in made], "extracted": extracted if counted or not made else None, "failed": failed,
            "exit_code": code}


def script_objects(global_utoc: Path, *, key: dict | None = None) -> list[dict]:
    """``print-script-objects``: [{name, global_index, outer_index, cdo_class_index}] in file order."""
    from gst.engines.unreal import run

    proc = run(_base(key) + ["print-script-objects", str(global_utoc)])
    if proc.returncode != 0:
        raise GsError(f"retoc print-script-objects failed (exit {proc.returncode}): {proc.stderr.strip()}")
    return parse_script_objects(proc.stdout)


def parse_script_objects(text: str) -> list[dict]:
    out: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            cur = {"name": line.rstrip()[:-1], "global_index": None, "outer_index": None, "cdo_class_index": None}
            out.append(cur)
            continue
        m = re.match(r"\s+(global_index|outer_index|cdo_class_index):\s+(?:Some\((\d+)\)|None)", line)
        if m and cur is not None:
            cur[m.group(1)] = int(m.group(2)) if m.group(2) else None
    return out


def script_modules(objects: list[dict]) -> dict[str, dict]:
    """Group script objects by their ``/Script/<Module>`` package: {module: {objects, classes: [names]}}.
    A class is an object some CDO (``Default__X``) points to through ``cdo_class_index``."""
    by_index = {o["global_index"]: o for o in objects if o["global_index"] is not None}
    class_ids = {o["cdo_class_index"] for o in objects if o["cdo_class_index"] is not None}

    def package(o: dict) -> str | None:
        seen = 0
        while o is not None and seen < 64:
            if o["outer_index"] is None:
                return o["name"] if o["name"].startswith("/Script/") else None
            o = by_index.get(o["outer_index"])
            seen += 1
        return None

    out: dict[str, dict] = {}
    for o in objects:
        if o["outer_index"] is None:
            continue
        pkg = package(o)
        if pkg is None:
            continue
        entry = out.setdefault(pkg, {"objects": 0, "classes": []})
        entry["objects"] += 1
        if o["global_index"] in class_ids:
            entry["classes"].append(o["name"])
    for entry in out.values():
        entry["classes"].sort()
    return dict(sorted(out.items()))
