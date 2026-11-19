# No Man's Sky (Hello Games) — study guide

Covers the `gst recon` engine key `nms`: Hello Games' proprietary engine as shipped in No Man's Sky (HGPAK paks since 5.50 "Worlds Part II", MBIN data).

## Identify

- `Binaries/NMS.exe` (native C++, SteamStub), `vulkan-1.dll`, `libSceFios2.dll`, PlayFab DLLs, and plain-text
  settings in `Binaries/SETTINGS/*.MXML` (e.g. `TKGRAPHICSSETTINGS.MXML`).
- `GAMEDATA/PCBANKS/NMSARC.*.pak`: dozens of HGPAK paks named by content (`NMSARC.globals.pak`,
  `NMSARC.MetadataEtc.pak`, `NMSARC.audioBNK.pak`...) + `BankSignatures.bin`; also `GAMEDATA/SHADERCACHE/`.
- `gst recon` matches `GAMEDATA/PCBANKS/NMSARC.*.pak` (gst rule `NoMansSkyEngine` also lists `Binaries/NMS.exe`);
  engine details show `banks=<count>`. User data: `%APPDATA%\HelloGames\NMS` (one `st_<id>` folder per account).
- Version: Steam build id plus the in-game version; together they pick the MBINCompiler release.
- Before 5.50 the paks were PSARC: older guides and PSARC tools don't apply to current files.

## Code

- All logic is native C++ in `NMS.exe`. SteamStub wraps it, so `static-only` never decompiles it, and the other
  modules are third-party (Vulkan loader, Sony Fios I/O, PlayFab): there is no client code to open.
- The readable layer is data: `.MBIN` files are serialized C++ structs, one template class per file (`Gc*` game
  components, `Tk*` toolkit/engine). MBINCompiler turns them into MXML, an XML tree with every field named.
- Read first:
  - `*GLOBALS.GLOBAL.MBIN`: constants per system (player, gameplay, spaceship, debug options...);
  - `METADATA/REALITY/TABLES/NMS_REALITY_GC*TABLE.MBIN`: products, substances, technology, recipes;
  - `METADATA/SIMULATION/`: procedural-generation inputs, e.g. `SOLARSYSTEM/BIOMES/`.
- Struct definitions are public (the libMBIN sources in the MBINCompiler repo); the MXML field names suffice to start.
- Procedural generation itself is code you can't read here: describe it from its parameters and observed
  outcomes, and label inferences as such.
- Public docs: the HGPAKtool and MBINCompiler READMEs, the NMS modding wikis (stepmodifications.org,
  nmsmodding.fandom.com).

## Data and assets

- Not in the `gst tools` catalog yet: HGPAKtool 1.1.3 (PyPI `hgpaktool` or its exe) and MBINCompiler (release exe,
  .NET 8+; 7.04.1-pre3 on 2026-10-05). Get them with the user's OK, outside the install; log versions and commands
  in STUDY.md.
- HGPAKtool writes `EXTRACTED/` next to the paks it reads: copy the paks the question needs into the workspace first.

```text
(copy the paks named for the question, e.g. NMSARC.globals.pak and NMSARC.MetadataEtc.pak, into assets\nms\pak\)
hgpaktool -U -f="*GLOBAL*" -f="*NMS_REALITY_GC*" assets\nms\pak   # -> assets\nms\pak\EXTRACTED\
MBINCompiler.exe <file.MBIN> [...]                                  # .MXML next to each input
rg -n -g "*.MXML" "<field or value>" assets\nms                     # MXML is data: rg, not gst index
```

- `-f` filters by name and repeats; `hgpaktool --help` and `MBINCompiler.exe help` list the rest.
- Textures `.DDS`: `texconv -ft png -o <out> <in.dds>`. Models: `.SCENE.MBIN` (scene graph) and `.MATERIAL.MBIN`
  convert to MXML; geometry sits in `*.MBIN.PC` files (`gst fmt` where MBINCompiler stops).
- Audio: Wwise banks from `NMSARC.audioBNK.pak`. Pass extracted files by absolute path (relative patterns resolve
  against the install), e.g. in PowerShell:
  `gst assets audio (Get-ChildItem assets\nms -Recurse -Filter *.BNK).FullName --list`
- Saves (`st_<id>\save*.hg`): JSON whose keys are obfuscated; the `mapping.json` shipped with each MBINCompiler
  release maps them back. `gst fmt id` / `gst fmt entropy` before assuming a layout.

## Runtime

- `static-only`: no debugger, scans, injection or capture.
- Static route: save changes around a normal session (`gst trace snapshot before`, play, `gst trace snapshot after`,
  `gst trace diff before after`; the default paths are recon's user-data folders), MXML settings, pak data.

## Graphics

- Vulkan (`vulkan-1.dll` in `Binaries/`); no capture at this verdict.
- Shaders ship as SPIR-V `.SPV` in the paks (`gst fmt id` shows the magic `03 02 23 07`); the number ending a shader
  name is its material-flag mask (community wiki). `spirv-cross <X.SPV>` prints GLSL; `--hlsl` gives HLSL.
- Materials: `.MATERIAL.MBIN` → MXML (shader, flags, samplers). `GAMEDATA/SHADERCACHE/` is a compiled cache:
  `gst fmt scan` it before treating it as content.

## Gotchas

- The guard denies `hgpaktool` on an install path → it extracts next to its input → copy the paks first.
- MBINCompiler fails or mis-reads a file → every game update changes MBIN layouts → use the release that matches
  the build (`MBINCompiler.exe version <file>` reads the version stamped in a file).
- `MBINCompiler.exe register` → adds its folder to the system PATH → skip it; call the exe by path.
- `hgpaktool --platform switch` → downloads an Oodle DLL from a third party → not needed for PC paks; never use it.
- Guides talk about EXML → MXML replaced EXML in Worlds Part II → same tree, new extension.
- `gst assets list` says it handles Unity, Godot and Unreal only → no gst backend for NMS → HGPAKtool,
  MBINCompiler and `gst fmt`; `gst assets audio` for banks.
- `gst assets audio "<absolute glob>"` → `Non-relative patterns are unsupported` → pass files, not an absolute glob.

## Protections

- SteamStub (`drm-wrapper`) on `Binaries/NMS.exe` → `static-only`; the exe is a protected module, never unpacked.
  Online play and services (PlayFab) are out of scope.
- `BankSignatures.bin` lists pak signatures; it is not encryption, and reading needs no key.
- `recon/protections.md` records them. What remains: pak data, MXML settings, saves, public docs.
- Limits and reasons: [`plugins/gst/guardrails.md`](../../guardrails.md).
