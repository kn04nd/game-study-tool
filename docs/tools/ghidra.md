# Ghidra in gst

## What it is

[Ghidra](https://github.com/NationalSecurityAgency/ghidra) is the NSA's native code decompiler (Apache 2.0). It opens a
compiled `.exe`/`.dll` (C, C++, IL2CPP), finds the functions, the calls between them and the strings, and produces
readable pseudo-C for each function. gst only uses **headless** mode (no UI), reading a **copy** of the binary inside
the workspace: nothing runs, nothing is injected, the game install is not touched.

Catalog version: `ghidra` 12.1.4 (`src/gst/data/catalog.toml`), with the `temurin21` JDK. Install:
`gst tools install ghidra` (pulls the JDK too). If the legacy gs already installed it, gst reuses the same folder.

## What it is for in a game study

Use it when the logic you want to understand **exists only as machine code**:

| case | binary | what Ghidra gives |
| --- | --- | --- |
| C/C++ game (own engine, Unreal) | the game's `.exe` and DLLs | functions, who calls whom, constants and strings used by each function |
| Unity IL2CPP | `GameAssembly.dll` | the game's compiled C# logic; with the IL2CPP names (`Type$$Method`) it becomes pseudo-C you can navigate by name |
| Native plugin of a Mono/Godot game | e.g. `SimDLL.dll` of Oxygen Not Included | the simulation/physics that is not in the C# |

Do not use it when the code is already readable: Unity Mono (`Assembly-CSharp.dll`, through ilspycmd), Godot
(GDScript/C# recovered by GDRE/ilspycmd), Java, Lua. In IL2CPP, the classes, fields and signatures already come from
Cpp2IL/Inspector (`decomp/cs-il2cpp`) and the member graph from REA. Ghidra is only needed when you want **the bodies**
of the functions: formulas, fixed values, the order of the calls.

## How gst runs it

| path | when | scripts (in `src/gst/data/ghidra_scripts/`) |
| --- | --- | --- |
| `gst decompile native [modules] [--list]` or the `code.native` job of `gst study` | native exe/dll (the game itself; third-party runtimes are left out, see `THIRD_PARTY` in `decompile/native.py`) | `GsPdbPrescript` (uses only the PDB shipped next to the binary, never a symbol server) and `GsExport` |
| the `code.il2cpp.native` job of `gst study` (`unity-il2cpp` only) | `GameAssembly.dll` with the names from `decomp/il2cpp/names.jsonl` (Inspector, Cpp2IL, REA, Il2CppDumper) | `GsPdbPrescript`, `GstIl2cppNames pre` (labels and entry points before the analysis), `GstIl2cppNames post` (creates/names the rest, comment with the C# signature), `GsExport` |

Output (same layout in both cases):

- `decomp/ghidra/<module>/src/**.c`: pseudo-C grouped by namespace, with a `.c.map.json` next to it (functions, lines,
  exact call sites);
- `decomp/ghidra/<module>/` also has `functions/calls/imports/strings` as JSONL and `meta.json`;
  for IL2CPP, also `il2cpp_names.json` with the `post` counts;
- `decomp/ghidra/_projects/<module>/`: the Ghidra project; it can be opened in the UI later;
- `decomp/ghidra/MANIFEST.json`.

Then `gst index` reads the sidecars and enables `gst q def/refs/callers/callees/show` on native code, for example
`gst q callers "FIMSpace.FProceduralAnimation.RagdollAnimator2$$Start"`.

Useful options:

- `--max-functions N` (default 0 = all; with N, named ones first);
- `--functions <regex>`: re-exports only those, without reanalyzing;
- `--timeout S` (no default);
- `--function-timeout S` / `--opt native.function_timeout=S`: decompiler limit per function (default: none, since
  `SCRIPT_VERSION = 4`; it used to be 30 s). The export has no caps: every string streamed to `strings.jsonl` (full
  value, every referencing function), every field of a class layout;
- `--opt il2cpp.reanalyze=1`, `il2cpp.force=1`, `il2cpp.flat=1`;
- memory: `GHIDRA_HEADLESS_MAXMEM` (default half the RAM, up to 16G).

Resuming: the analysis is saved in the project. Changing only the names (`NAMES_VERSION`) or the export
(`SCRIPT_VERSION`) reuses the analysis; `ANALYSIS_VERSION` forces a reanalysis.

## Cost

| binary | size | analysis time | RAM |
| --- | --- | --- | --- |
| system DLL (`cabinet.dll`, `-m tools` test) | < 1 MB | seconds | < 1 GB |
| `SimDLL.dll` (Oxygen Not Included, gs study) | a few MB | minutes | a few GB |
| Safari-TV `GameAssembly.dll` (IL2CPP, ~280k methods) | 160 MB | over 3 h without finishing, at the 15 GB heap cap | 14.5 GB |

The `GameAssembly.dll` of a big IL2CPP game costs hours and the whole heap. On Safari-TV the full run was stopped on
purpose (the capability was already validated) and nothing depends on it. To run a big IL2CPP:

- leave it in the background;
- raise `GHIDRA_HEADLESS_MAXMEM` (20-24G if the machine has it);
- or limit it to a family of functions with `--functions`.

To study one mechanic, `decomp/cs-il2cpp` (signatures + RVA), the REA graph and the pseudo-C of only the functions
of interest are usually enough.

## Validated on

- `tests/unit/test_il2cpp_names.py` (marker `tools`): real Ghidra on a system DLL, with the names applied, the export
  with namespaces and the indexing;
- `gst selftest --tools`: `SampleGame.exe` (the repo's test game) and ONI's `SimDLL.dll`;
- Safari-TV: names from 4 sources merged (724k lines) and the `pre` step applied (281,916 methods, 0 errors); the full
  analysis did not run to the end (see `docs/KNOWN-LIMITATIONS.md`, "Ghidra").
