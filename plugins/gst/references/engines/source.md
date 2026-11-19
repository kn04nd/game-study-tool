# Source and Source 2 — study guide

Covers the `gst recon` engine keys `source` (Source 1: Half-Life 2, Portal 2, TF2, L4D2, CS:S, Garry's Mod) and
`source2` (CS2, Dota 2, Half-Life: Alyx, Deadlock).

## Identify

- Source 1: `<mod>/gameinfo.txt` (KeyValues), `*_dir.vpk` plus numbered chunks, `bin/engine.dll` (`bin/x64/` in
  64-bit builds), the game DLLs in `<mod>/bin/`, maps in `<mod>/maps/*.bsp`. Version: `<mod>/steam.inf`
  (`PatchVersion=`) and the Steam build id. Recon reports `source` for `gameinfo.txt` plus a `_dir.vpk` or
  `bin/engine.dll`.
- Source 2: `game/bin/win64/engine2.dll`, `game/<mod>/gameinfo.gi` (KV3 text), `pak01_dir.vpk` (VPK v2), compiled
  resources ending in `_c` (`vmdl_c`, `vtex_c`, `vjs_c`, `vents_c`, `vdata_c`…). Version: `game/bin/built_from_cl.txt`,
  `game/<mod>/steam.inf`. Recon reports `source2`.
- `gst fmt id` names VPK (`34 12 aa 55`), BSP (`VBSP`, then the version: 19–21 in Source 1), MDL (`IDST`) and VTF.
- RECON.md's APIs line only reads the main exes: the renderer is `bin/shaderapidx9.dll` (Source 1) or
  `rendersystemdx11.dll` / `rendersystemvulkan.dll` (Source 2). Its arch column matters: Portal 2, L4D2 and older
  builds are x86; TF2 and HL2 ship x64 since 2024.

## Code

**Source 1: read the public code first.** Source SDK 2013 (`github.com/ValveSoftware/source-sdk-2013`, non-commercial
licence) holds the game code of Half-Life 2, its episodes and HL2DM and, since February 2025, the full TF2 client
and server. Clone it into the workspace so `gst q grep` reaches it:
`git clone --depth 1 https://github.com/ValveSoftware/source-sdk-2013 decomp/source-sdk-2013`.

- Game code sits in `.../game/{client,server,shared}/` with per-game folders (`tf/`, `hl2/`); engine interfaces in
  `public/` (`cdll_int.h`, `eiface.h`). For L4D2/Portal 2-era branches, Valve's Alien Swarm SDK is the closest public
  code. Valve's public releases only, never leaked code.
- Entry points: `LINK_ENTITY_TO_CLASS(<classname>, <C++ class>)` maps map entities to classes; `BEGIN_DATADESC` lists
  keyvalues, inputs and saved fields; send/receive tables (`m_iHealth`…) are the networked state.
- `gst index` doesn't parse C++: `gst q grep "LINK_ENTITY_TO_CLASS\( *prop_physics" --glob "*.cpp"`, then
  `gst q show decomp/source-sdk-2013/<path>:<line>` (run `gst index` once first); `ctags -R --output-format=json`
  (core profile) gives a symbol list.
- Data-driven logic: KeyValues scripts (`scripts/*.txt`: weapons, sounds; TF2 `scripts/items/items_game.txt`), HUD
  layouts `resource/*.res`, map I/O in the BSP entity lump, VScript (Squirrel `scripts/vscripts/*.nut`) in L4D2,
  Portal 2 and TF2.
- Binaries, only when the verdict allows (no VAC): `gst decompile native --list`, then name them,
  e.g. `gst decompile native server.dll` (with no name it analyzes only the small launcher exe). Interfaces are
  registered by version string (`VEngineClient015`, `VClient016`) through `CreateInterface`; the SDK headers give
  their method order. Network property names (`m_iHealth`, `DT_BasePlayer`) are strings: `gst q strings m_iHealth`.

**Source 2:** C++ in `game/<mod>/bin/win64/client.dll` and `server.dll`; the schema system (`schemasystem.dll`)
describes classes and fields at runtime; UI in Panorama (`vjs_c`, `vxml_c`, `vcss_c`); map entities in `vents_c`; data
in `vdata_c`.

- Source2Viewer-CLI (not in catalog): `Source2Viewer-CLI -i "<install>/game/<mod>/pak01_dir.vpk" --vpk_list`; decompile
  a slice with `-i <vpk> -e "vjs_c,vdata_c,vents_c" -d -o decomp/s2` (Panorama JS, data and entity lumps as text).
- SteamDatabase's GameTracking repos (e.g. GameTracking-CS2) track each build's protobufs, Panorama sources and
  strings: read them before any binary.

## Data and assets

- Source 1: VPK archives; KeyValues text; materials `.vmt` (text) with textures `.vtf`; models `.mdl` with
  `.vvd`, `.vtx` and `.phy`; maps `.bsp` (their pakfile lump is an embedded zip); sound `.wav`/`.mp3` in the VPKs.
  `gameinfo.txt` `SearchPaths` set the mount order: loose files and `custom/` override VPK copies.
- Tools (not in catalog; outputs under `assets/`): VPKEdit (GUI; CLI `vpkeditcli`, see its `--help`) for VPKs and BSP
  pakfiles; srctools (Python parsers for KeyValues, VMF, BSP, VMT, MDL); BSPSource `bspsrc.bat <map.bsp>` turns a map
  into VMF (see `--help` for the output option; work on a copy in the workspace); Crowbar (GUI) for MDL → QC/SMD.
- Source 2: VPK v2 (multi-chunk); `_c` files are block containers (`RERL`, `REDI`/`RED2`, `DATA`, `NTRO`) holding binary
  KV3. Source2Viewer-CLI decompiles them: textures to PNG, models to glTF (`--gltf_export_format glb`), sounds, and
  the shaders in `shaders_*_dir.vpk`.
- Demos `.dem` record the game state tick by tick: an oracle for gameplay logic (community parsers: DemoFile.Net,
  demoparser).
- Anything else: `gst fmt id` or `gst fmt scan <folder>`. `gst assets audio` finds no banks here: sound is loose or in VPKs.

## Runtime

- VAC titles (TF2, CS:S, L4D2, Garry's Mod, CS2, Dota 2, Deadlock) are `no-runtime`: no `gst trace` on the process,
  no sandbox, no capture.
- Saves change on disk at any verdict: `gst trace snapshot` and `diff` are not gated
  (`gst trace snapshot before --path "<install>/<mod>/save"`, play, `after`, `gst trace diff before after`).
- `offline-ok` single-player builds: `gst trace scan`, `next`, `watch`. On x86 builds (Portal 2, L4D2) `watch` refuses
  32-bit (WoW64) processes; `scan` and `next` work.

## Graphics

- Source 1 renders through D3D9 (`bin/shaderapidx9.dll`), which RenderDoc can't capture: `gst gfx capture` registers no
  API. DXVK in the sandbox copy is the only route (untested with gst); Source loads its renderer DLLs with an altered
  search path, so a proxy `d3d9.dll` is only picked up from `bin/`, not next to the exe.
- Source 2: D3D11 or Vulkan. Capture only `offline-ok` games, from the sandbox (Vulkan needs RenderDoc's layer
  registered: ask the user first). Shaders (`vcs`) can be read statically with Source2Viewer at any verdict.

## Gotchas

- `gst decompile` stops with "no decompile backend for source" → read the SDK; name modules for `gst decompile native`.
- `gst assets list` stops ("handle Unity, Godot and Unreal 4/5") → VPKEdit / Source2Viewer-CLI into `assets/`.
- Same-named DLLs in several mod folders (`hl2/bin/client.dll`, `episodic/bin/client.dll`) → `gst decompile native`
  selects by file name only, so just one is reachable → check which in `decomp/ghidra/MANIFEST.json` (`source`).
- A `.vmt` or script isn't the one in the VPK → `SearchPaths` order: a loose or `custom/` copy wins.
- `.nuc` or `.ctx` files look random → ICE-encrypted VScript or weapon scripts (see Protections).
- SDK code and the shipped DLL disagree → the SDK is a snapshot; match the build (`steam.inf`) and the commit date.

## Protections

- VAC: recon finds it through AreWeAntiCheatYet, the SteamDB rules, or the store data
  (`gst config set online_metadata yes`) → `no-runtime`: no runtime and no client-code decompilation. What remains:
  data on disk, the public SDK code, GameTracking dumps, the Valve Developer Community format docs.
- CS2 adds signed file manifests (`*.signatures`); CS2, Dota 2 and Deadlock are also on recon's always-online list.
- ICE-encrypted `.ctx`/`.nuc` files: decrypt only with a key the community already published, its source URL recorded
  in `game.json` `keys`; never a key read out of the binaries or memory, never brute force.
- Limits: [guardrails.md](../../guardrails.md).

Adapted from universal-modder `skills/mod-any-game/references/engines/source.md` and
`knowledge/games/portal-2/portalcraft-minecraft-inside-portal-2.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
