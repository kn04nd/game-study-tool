"""Cpp2IL wrapper: ``GameAssembly`` + ``global-metadata.dat`` -> dummy DLLs (types, fields, signatures, ``[Address]``,
``[Token]``, ``[FieldOffset]`` attributes through the ``attributeinjector`` processor).

Command: ``Cpp2IL --game-path <install> --use-processor attributeinjector --output-as dummydll --output-to <out>``
(``--force-binary-path``/``--force-metadata-path``/``--force-unity-version`` when the inputs are given explicitly).
Cpp2IL only reads the game folder; the output dir is always a gst folder inside the workspace and the working
directory a gst-made empty folder (Cpp2IL loads plugins from ``<cwd>/Plugins``). No timeout unless asked.

Raw output kept in ``decomp/il2cpp/cpp2il/``: ``cpp2il.log`` (full console text) and ``MANIFEST.json`` (tool, version,
command, metadata version, Unity version, CodeRegistration/MetadataRegistration addresses, DLL count). The DLLs
themselves go to ``decomp/il2cpp/dummydll/`` (the typetree chain and ``code.il2cpp.decompile`` read them there).
"""

from __future__ import annotations

import re
from pathlib import Path

from gst.engines.il2cpp import tooling

TOOL_ID = "cpp2il"
PROCESSORS = ("attributeinjector",)


def command(exe: Path, out: Path, *, install: Path | None = None, binary: Path | None = None,
            metadata: Path | None = None, unity_version: str | None = None, output_as: str = "dummydll",
            processors: tuple[str, ...] = PROCESSORS) -> list[str]:
    cmd = [str(exe)]
    if binary and metadata and unity_version:
        cmd += ["--force-binary-path", str(binary), "--force-metadata-path", str(metadata),
                "--force-unity-version", unity_version]
    elif install:
        cmd += ["--game-path", str(install)]
    else:
        raise ValueError("cpp2il.command needs the install folder or binary + metadata + Unity version")
    if processors:
        cmd += ["--use-processor", ",".join(processors)]
    return cmd + ["--output-as", output_as, "--output-to", str(out)]


def parse_log(text: str) -> dict:
    """Facts Cpp2IL prints: Unity version, metadata version, CodeRegistration/MetadataRegistration, errors."""
    out: dict = {}
    m = re.search(r"game's unity version to be (\S+)", text)
    if m:
        out["unity_version"] = m.group(1)
    m = re.search(r"Using actual IL2CPP Metadata version ([\d.]+)", text)
    if m:
        out["metadata_version"] = m.group(1)
    m = re.search(r"codereg: (0x[0-9A-Fa-f]+), metareg: (0x[0-9A-Fa-f]+)", text)
    if m:
        out["code_registration"], out["metadata_registration"] = m.group(1), m.group(2)
    m = re.search(r"Done\. Total execution time: ([\d.]+)ms", text)
    if m:
        out["tool_ms"] = float(m.group(1))
    errors = [line.strip() for line in text.splitlines()
              if re.search(r"\[(Error|Fatal)\]|Unhandled exception|Exception:", line)]
    if errors:
        out["errors"] = errors
    if re.search(r"(?i)unsupported metadata version|not a supported (metadata )?version", text):
        out["unsupported_version"] = True
    return out


def run(exe: Path, *, install: Path, raw_dir: Path, dll_dir: Path, binary: Path | None = None,
        metadata: Path | None = None, unity_version: str | None = None, timeout: float | None = None) -> dict:
    """Run Cpp2IL into ``dll_dir``; log + facts in ``raw_dir``. Returns ``{ok, dlls, facts, run, command}``."""
    cmd = command(exe, dll_dir, install=install, binary=binary, metadata=metadata, unity_version=unity_version)
    res = tooling.run_tool(cmd, cwd=raw_dir / ".cwd", log=raw_dir / "cpp2il.log", timeout=timeout)
    dlls = tooling.files_under(dll_dir, "*.dll")
    facts = parse_log(res["output"])
    ok = res["exit_code"] == 0 and bool(dlls) and not res["timed_out"]
    return {"ok": ok, "dlls": len(dlls), "dll_bytes": sum(p.stat().st_size for p in dlls), "facts": facts,
            "run": {k: v for k, v in res.items() if k != "output"}, "output": res["output"], "command": cmd}

