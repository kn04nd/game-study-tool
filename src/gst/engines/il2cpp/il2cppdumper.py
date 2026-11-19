"""Il2CppDumper wrapper (Perfare; metadata up to v31 in 6.7.46, the last release: v39 fails with
``NotSupportedException: Metadata file supplied is not a supported version[39]``).

Command: ``Il2CppDumper.exe <GameAssembly.dll> <global-metadata.dat> <out>``. It reads ``config.json`` next to its
exe (gst never edits the tool's install) whose ``RequireAnyKey: true`` ends every run with "Press any key", which
throws ``InvalidOperationException`` when stdin is closed: success is judged by the files written, not the exit code.
It may also ask for CodeRegistration/MetadataRegistration addresses when its search fails; with stdin closed that
is a failure with the full text kept.

Outputs, all kept in ``decomp/il2cpp/il2cppdumper/``: ``script.json`` (method addresses and names, string literals,
metadata usages: the name map the Ghidra step reads), ``dump.cs.txt`` (``dump.cs`` renamed so the code index does not
read the same types twice), ``il2cpp.h``, ``stringliteral.json`` and
``DummyDll/`` (copied to ``decomp/il2cpp/dummydll/`` when Il2CppDumper is the dummy DLL source).
"""

from __future__ import annotations

import re
from pathlib import Path

from gst.engines.il2cpp import tooling

TOOL_ID = "il2cppdumper"
OUTPUTS = ("script.json", "dump.cs.txt", "il2cpp.h", "stringliteral.json")
RENAMED = {"dump.cs": "dump.cs.txt"}  # not indexed as C# next to decomp/cs-il2cpp (same types twice)


def command(exe: Path, binary: Path, metadata: Path, out: Path) -> list[str]:
    return [str(exe), str(binary), str(metadata), str(out)]


def parse_log(text: str) -> dict:
    out: dict = {}
    m = re.search(r"Metadata Version: ([\d.]+)", text)
    if m:
        out["metadata_version"] = m.group(1)
    m = re.search(r"CodeRegistration : ([0-9a-fA-Fx]+)", text)
    if m:
        out["code_registration"] = m.group(1)
    m = re.search(r"MetadataRegistration : ([0-9a-fA-Fx]+)", text)
    if m:
        out["metadata_registration"] = m.group(1)
    m = re.search(r"not a supported version\[([\d.]+)\]", text)
    if m:
        out["unsupported_version"] = m.group(1)
    if "Done!" in text:
        out["done"] = True
    errors = [line.strip() for line in text.splitlines()
              if re.search(r"Exception|ERROR", line) and "Cannot read keys" not in line and "ReadKey" not in line]
    if errors:
        out["errors"] = errors
    return out


def run(exe: Path, *, binary: Path, metadata: Path, raw_dir: Path, timeout: float | None = None) -> dict:
    """Run into ``raw_dir``; ``{ok, dlls, facts, outputs, run, output, command}`` (``DummyDll/`` stays in ``raw_dir``)."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    cmd = command(exe, binary, metadata, raw_dir)
    res = tooling.run_tool(cmd, cwd=raw_dir / ".cwd", log=raw_dir / "il2cppdumper.log", timeout=timeout)
    facts = parse_log(res["output"])
    for old, new in RENAMED.items():
        if (raw_dir / old).is_file():
            (raw_dir / old).replace(raw_dir / new)
    present = [name for name in OUTPUTS if (raw_dir / name).is_file()]
    dlls = tooling.files_under(raw_dir / "DummyDll", "*.dll")
    ok = "script.json" in present and not res["timed_out"] and not facts.get("unsupported_version")
    return {"ok": ok, "dlls": len(dlls), "outputs": present, "facts": facts,
            "run": {k: v for k, v in res.items() if k != "output"}, "output": res["output"], "command": cmd}
