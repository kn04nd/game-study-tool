# REA in gst (and the optional IL2CPP patch)

## What it does

[REA](https://github.com/morluto/rea) (`rea-agents` on npm, MIT) is a third-party reverse-engineering CLI. gst uses
only its static JSON commands (never Frida, debuggers or captures):

| gst job | REA command | what it gives |
| --- | --- | --- |
| `rea.managed_members`, `rea.managed_graph` | `inspect-managed-members`, `project-managed-application-graph` | members and call graph per assembly (Mono / .NET) |
| `rea.il2cpp_metadata` | `inspect-il2cpp-metadata` (with `--binary GameAssembly.dll`) | IL2CPP types, method addresses, field/parameter types |
| `rea.artifact` | `inspect-artifact`, `extract-artifact` | inventory cross-check (e.g. Godot PCK) |
| `rea.import` | - | `rea_*` tables in `index/code.db` (`gst q callers/callees`) |

`gst tools install rea` installs the official `rea-agents@6.9.0` from npm into gst's tools folder (a private
prefix, never a global install). gst does not ship REA's code.

## The IL2CPP cap

The official 6.9.0 has fixed allowances in `src/il2cpp/Il2cppMetadataProvider.ts`: 256 MiB for the metadata file
and 64 MiB for the complete report. A large IL2CPP game exceeds the report cap (a Unity 6 game with a 160 MB
`GameAssembly.dll` produces an 81.8 MB report), REA refuses it, and gst falls back to a metadata-only run: types and
names, but no method addresses and no field/parameter types from REA. The job ends `unsupported` with that reason.

This only matters for large IL2CPP games, and only for the REA job: Cpp2IL (`decomp/il2cpp/addresses.tsv`) and
Ghidra with names give method addresses without that cap.

## Optional: build REA with the patch

`patches/rea/il2cpp-configurable-allowances.patch` makes both allowances configurable (CLI `--max-input-bytes` /
`--max-output-bytes`, 1 byte to 2 GiB; defaults unchanged). gst detects the options in the installed REA's `--help`
and passes 2 GiB. The patch is distributed under REA's MIT license.

Needs Git and Node 24.22+ (Node 26 breaks REA's own build scripts; Node 22.23+ is accepted by REA's `engines` but
was not tried). In PowerShell:

```powershell
git clone https://github.com/morluto/rea.git C:\src\rea
cd C:\src\rea
git checkout rea-agents-6.9.0
git apply <path of game-study-tool>\patches\rea\il2cpp-configurable-allowances.patch
npm ci
npm run build
npm pack                      # writes rea-agents-6.9.0.tgz in this folder

$env:GST_REA_SPEC = "C:\src\rea\rea-agents-6.9.0.tgz"   # or: gst config set rea_spec C:\src\rea\rea-agents-6.9.0.tgz
gst tools install rea
gst study <game> --only rea   # rea.il2cpp_metadata reruns: its fingerprint includes whether REA takes the caps
```

The patched build goes into `<tools_dir>/rea/custom-<hash>/`, next to the official one, which stays installed.
Removing `GST_REA_SPEC` (or the `rea_spec` config key) goes back to the official 6.9.0.

The patch targets the tag `rea-agents-6.9.0`. On a newer REA it may not apply; check whether the newer version
already lets callers raise these allowances (`rea inspect-il2cpp-metadata --help`).
