# Bethesda Creation Engine / Gamebryo — study guide

Covers the `gst recon` engine key `creation`: Skyrim (LE/SE/AE), Fallout 3, New Vegas, 4 and 76, Oblivion, Starfield.

## Identify

- `Data/*.esm` (+ `.esp`/`.esl` plugins) next to `*.bsa` (Oblivion to Skyrim) or `*.ba2` (Fallout 4/76,
  Starfield); `SkyrimSE.exe`, `Fallout4.exe`, `FalloutNV.exe`… plus a `*Launcher.exe`; `<Game>.ccc` lists the
  Creation Club masters.
- `gst recon` reports `creation` when `Data/` holds an `.esm` and a BSA/BA2 (details: `archives=N`). There is no
  version probe: RECON.md lists the exes with arch and ProductVersion, and a master's format version is the
  `HEDR` float of its `TES4` header (`gst fmt hex Data/Skyrim.esm --length 64`).
- `gst fmt id` names `TES4` (plugin), `BSA` and `BTDX` (BA2), DDS and xWMA.
- User data: `Documents/My Games/<Game>/` (INIs, `Saves/`, `Logs/`). Recon knows Skyrim SE and Fallout 4; for
  the others give the folder to `gst trace snapshot --path`. Load order: `%LOCALAPPDATA%/<Game>/Plugins.txt`.

## Code

Gameplay is mostly data (records) plus Papyrus scripts; the exe is the engine.

1. **Records** (quests, NPCs, items, perks, leveled lists, conditions): read them, don't decompile.
   - Spriggit (not in catalog) writes one YAML file per record, under `decomp/` so `gst q grep` reaches it:
     `Spriggit.CLI serialize -i "<install>/Data/Update.esm" -o decomp/records/Update -g SkyrimSE -p Spriggit.Yaml`
     (`-g` takes Mutagen's game release, e.g. `Fallout4`, `Starfield`; the first run fetches the `Spriggit.Yaml`
     package from NuGet). Mutagen (C# library, pin `0.54.4`) answers typed queries; xEdit (GUI) browses
     records and the overrides between plugins.
   - Layout (UESP "Mod File Format" pages): a `TES4` header (`HEDR`, `MAST` masters), then `GRUP`s of records:
     4-char type, size, flags, FormID, subrecords.
   - `VMAD` attaches Papyrus scripts and their property values to a record: the bridge from data to code.
     `CTDA` subrecords are the conditions on quests, dialogue, perks and packages.
   - Oblivion, Fallout 3 and New Vegas: scripts are `SCPT` records whose `SCTX` subrecord holds the source text.
2. **Papyrus** (Skyrim, Fallout 4, Starfield): compiled `.pex` under `Scripts/`, inside the archives or loose.
   - Source first: the Creation Kit (Steam > Library > Tools) ships the vanilla `.psc` files (Skyrim SE:
     `Data/Scripts.zip`). Copy or unzip them into `decomp/papyrus/`, never into `Data/`.
   - The rest (DLC, Creation Club) with Champollion (not in catalog):
     `Champollion -r -t -p decomp/papyrus/pex <folder of .pex>`.
   - `gst index` doesn't parse Papyrus: `gst q grep "Event OnActivate" --glob "*.psc"`, then
     `gst q show decomp/papyrus/<file>.psc:<line>` (run `gst index` once first).
   - `Event` blocks are the entry points; `native` functions are implemented in the exe.
3. **Native exe** (C++: engine, AI, physics, the Papyrus VM and its natives). Steam exes usually carry
   SteamStub (`static-only`): never unpack or decompile them. DRM-free builds (GOG) can be `offline-ok`:
   `gst decompile native SkyrimSE.exe` (hours on an exe this size). Engine classes are documented in public
   community headers (CommonLibSSE-NG, CommonLibF4) and on UESP: read those before any binary.

Read first: one quest end to end: its `QUST` record (stages, aliases, `VMAD`) → its `.psc` → the natives it calls.

## Data and assets

- Archives: BSA v103 (Oblivion), v104 (Fallout 3/NV, Skyrim LE), v105 (Skyrim SE, LZ4); BA2 (`BTDX`) holds
  `GNRL` (any file) or `DX10` (textures) entries. Loose files in `Data/` win over archive copies.
- Unpack into the workspace with BSArch (not in catalog, a Nexus download: `BSArch unpack "<archive>" <ws dir>`),
  or Archive2 (GUI, ships with the Fallout 4 Creation Kit).
- Meshes `.nif` (NetImmerse/Gamebryo): NifSkope (GUI). Animation and behaviour graphs: Havok `.hkx` (binary, no
  tool in the plan).
- Textures `.dds` (BC1–BC7): `texconv -ft png -o <ws dir> <in.dds>` (`gst tools path texconv`).
- Audio: `.xwm` (xWMA), `.wav`, voice `.fuz` (lip data + xWMA). After unpacking,
  `gst assets audio "<absolute file path>"` converts them (its globs are relative to the install).
- UI: Scaleform `.swf` under `Interface/` (Skyrim: `Skyrim - Interface.bsa`): FFDec, see the Flash section of
  [misc-engines.md](misc-engines.md).
- Strings: localized plugins keep their text in `Strings/<Plugin>_<lang>.STRINGS`, `.DLSTRINGS` and `.ILSTRINGS`
  (loose or inside the interface archive).
- Tunables: `GMST` records (game settings) and the INIs. Saves `.ess` (Skyrim, Oblivion) and `.fos` (Fallout) are
  documented on UESP; Skyrim SE compresses the body after the header. Anything else: `gst fmt id`, `entropy`, `hex`.

## Runtime

- Steam builds are `static-only`: no `gst trace` on the process, no sandbox. `gst trace snapshot` and `diff` are not
  gated (the user plays, gst only reads the save folder):
  `gst trace snapshot before --path "<Documents>/My Games/<Game>/Saves"`, play, `after`, then `gst trace diff before after`.
- The game's own observers need no tool: the console (`~`, e.g. `player.getav health`: the user types, you record)
  and Papyrus logs (`[Papyrus] bEnableLogging=1` in the user's INI, ask first; output in `Logs/Script/Papyrus.0.log`).
- `offline-ok` builds: `gst trace scan`, `next`, `watch`. Oblivion, Fallout 3/NV and Skyrim LE are x86: `scan` and
  `next` work, `watch` refuses 32-bit (WoW64) processes.

## Graphics

- Skyrim SE and Fallout 4: D3D11; Starfield: D3D12; Oblivion, Fallout 3/NV and Skyrim LE: D3D9, which RenderDoc
  can't capture (DXVK in the sandbox copy is the only route, untested with gst).
- Capture only `offline-ok` builds, from the sandbox and past the launcher: `gst gfx capture --exe SkyrimSE.exe`.
- Fallout 4 and Starfield ship their compiled shaders in `* - Shaders.ba2`.

## Gotchas

- `gst decompile` stops with "no decompile backend for creation" → records and Papyrus come from the tools above;
  `gst decompile native <module>` works only on unprotected modules.
- `gst assets list` stops ("handle Unity, Godot and Unreal 4/5") → unpack with BSArch; `gst assets audio` still works.
- Names come out as numbers → localized plugin (`TES4` flag `0x80`) → look the ids up in the `.STRINGS` files.
- A record's data looks random → compressed record (flag `0x00040000`): a 4-byte size, then zlib.
- A FormID points at the wrong plugin → in files its top byte indexes that plugin's own `MAST` list, not the
  load order.
- A file differs from its archive copy → a loose copy in `Data/` overrides the archive.
- `Skyrim.esm` serializes to a huge tree → learn the layout on `Update.esm` or a DLC first.

## Protections

- SteamStub (`.bind` section) on the Steam exes → recon `drm-wrapper` → `static-only`. Records, scripts, data on
  disk and DRM-free builds remain.
- No anti-cheat in the single-player games; BSA/BA2 archives aren't encrypted (Creation Club content included).
  Fallout 76 is online-only: treat it as `no-runtime` even when recon flags nothing (unencrypted data only).
- Official and public alternatives: the Creation Kit (record semantics, `.psc` sources), UESP and the CK wiki
  (formats), community headers (engine classes).
- Limits: [guardrails.md](../../guardrails.md).

Adapted from universal-modder `skills/mod-any-game/references/engines/bethesda.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
