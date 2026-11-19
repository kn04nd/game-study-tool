"""Il2CppInspectorRedux wrapper (LukeFZ; the legacy command-line edition, ``Il2CppInspector.exe``).

The new ``Il2CppInspector.Redux.CLI`` finishes the export and then keeps an ASP.NET server listening on
``localhost:5000`` without exiting, so gst uses the legacy CLI, which exits.

Command::

    Il2CppInspector.exe -i <GameAssembly.dll> -m <global-metadata.dat> --select-outputs
        -o <raw>/metadata.json -p <raw>/il2cpp_ghidra.py -t Ghidra -h <raw>/cpp [-d <dll>]

Outputs kept in ``decomp/il2cpp/il2cppinspector/``:

- ``metadata.json``: ``addressMap.methodDefinitions[] {virtualAddress, name, signature, dotNetSignature, group}``,
  ``methodInvokers``, ``stringLiterals``, ``typeInfoPointers``... (the name map for Ghidra; Safari-TV: 472 MB);
- ``il2cpp_ghidra.py``: Inspector's Ghidra script (reads ``metadata.json`` + the C++ types header);
- ``cpp/appdata/*.h``: the C++ type declarations (``il2cpp-types.h``...). Only ``appdata`` is kept: the rest of
  ``-h`` is a DLL-injection project scaffold, which gst does not keep;
- ``dll/``: dummy DLLs when asked (``-d``; used only when Cpp2IL cannot make them).

No C# output (``-c``): the C# stubs come from the dummy DLLs through ilspycmd (``decomp/cs-il2cpp``), and a second
copy under ``decomp/`` would be indexed twice. Inspector loads plugins from its working directory: it runs in a gst-made empty folder. No timeout unless asked.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from gst.engines.il2cpp import tooling

TOOL_ID = "il2cppinspector"
KEEP_CPP = ("appdata",)


def command(exe: Path, binary: Path, metadata: Path, raw: Path, *, dlls: Path | None = None) -> list[str]:
    cmd = [str(exe), "-i", str(binary), "-m", str(metadata), "--select-outputs",
           "-o", str(raw / "metadata.json"), "-p", str(raw / "il2cpp_ghidra.py"), "-t", "Ghidra", "-h", str(raw / "cpp")]
    if dlls is not None:
        cmd += ["-d", str(dlls)]
    return cmd


def parse_log(text: str) -> dict:
    out: dict = {}
    m = re.search(r"Detected metadata version\s+(\S+)", text)
    if m:
        out["metadata_version"] = m.group(1)
    m = re.search(r"CodeRegistration struct found at (0x[0-9A-Fa-f]+)", text)
    if m:
        out["code_registration"] = m.group(1)
    m = re.search(r"MetadataRegistration struct found at (0x[0-9A-Fa-f]+)", text)
    if m:
        out["metadata_registration"] = m.group(1)
    m = re.search(r"Selected Unity version\(s\) (.+)", text)
    if m:
        out["unity_versions"] = m.group(1).strip()
    m = re.search(r"Global offset: (0x[0-9A-Fa-f]+)", text)
    if m:
        out["global_offset"] = m.group(1)
    errors = [line.strip() for line in text.splitlines() if re.search(r"(?i)\berror\b|exception", line)]
    if errors:
        out["errors"] = errors
    return out


def prune_cpp(raw: Path) -> list[str]:
    """Keep only the type headers of the C++ output (drop the injection project scaffold)."""
    cpp = raw / "cpp"
    removed = []
    if cpp.is_dir():
        for child in sorted(cpp.iterdir()):
            if child.name in KEEP_CPP:
                continue
            removed.append(child.name)
            shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
    return removed


def run(exe: Path, *, binary: Path, metadata: Path, raw_dir: Path, dll_dir: Path | None = None,
        timeout: float | None = None) -> dict:
    raw_dir.mkdir(parents=True, exist_ok=True)
    cmd = command(exe, binary, metadata, raw_dir, dlls=dll_dir)
    res = tooling.run_tool(cmd, cwd=raw_dir / ".cwd", log=raw_dir / "il2cppinspector.log", timeout=timeout)
    removed = prune_cpp(raw_dir)
    facts = parse_log(res["output"])
    dlls = tooling.files_under(dll_dir, "*.dll") if dll_dir else []
    ok = (raw_dir / "metadata.json").is_file() and res["exit_code"] == 0 and not res["timed_out"]
    return {"ok": ok, "dlls": len(dlls), "facts": facts, "cpp_pruned": removed,
            "outputs": sorted(p.name for p in raw_dir.iterdir() if not p.name.startswith(".")),
            "run": {k: v for k, v in res.items() if k != "output"}, "output": res["output"], "command": cmd}
