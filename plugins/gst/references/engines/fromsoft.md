# FromSoftware — study guide

Covers the `gst recon` engine key `fromsoft`: the Dantelion-engine games (Dark Souls I–III, Sekiro, Elden Ring,
Nightreign, Armored Core VI).

## Identify

- recon evidence: `eldenring.exe`, `DarkSoulsIII.exe`, `sekiro.exe`, `armoredcore6.exe`, `nightreign.exe` or a
  `regulation.bin`. DS1/DS2 exes aren't on that list and may come out as `native`: same route. Next to the exe:
  `start_protected_game.exe` + `EasyAntiCheat/` (EAC), `Data*.bhd/.bdt` (plus `DLC*` and `sd/` for sound),
  `regulation.bin` (params), `oo2core_*.dll` (Oodle).
- `.bhd` is the BHD5 file table, RSA-encrypted from DS3 on (`gst fmt entropy` shows ~8 bits/byte); `.bdt` holds the
  data. Inside, files are `DCX` (`DCX\0`, codec `DFLT` = zlib or `KRAK` = Oodle Kraken) wrapping `BND4` binders,
  `FLVER` models, `TPF` textures (`gst fmt hex` on unpacked files).
- Paths in the BHD are hashes (64-bit from Elden Ring on): unpackers ship community name dictionaries.
- Already modified: ModEngine2/me3 (`modengine2_launcher.exe`, `config_*.toml`), `dinput8.dll`, Seamless Co-op
  (`SeamlessCoop/`), or unpacked folders (`chr/`, `map/`, `param/`…) inside the game folder (a UXM unpack plus a
  patched exe) → recon warns about known loaders; study the vanilla archives.

## Code

- The exe is native, and EAC titles are `no-runtime` (no client-code decompilation). Gameplay is data-driven
  anyway; read, in this order:
  - params: `regulation.bin` (AES-encrypted, DCX-compressed BND4 of `*.param`; DS3 keeps it as `Data0.bdt`):
    stats, damage, AI numbers, drops, effects. Field layouts: Paramdex (`soulsmods/Paramdex`);
  - event scripts in `event/*.emevd.dcx` (common and per-map logic): DarkScript3 shows them as JS-like code;
  - AI and behaviour: compiled Lua (`script/*.luabnd.dcx`) and HavokScript (`action/script/*.hks`). WitchyBND
    decompiles both (DSLuaDecompiler); unluac can't read HavokScript;
  - ESD state machines (NPC talk, menus; see the soulsmodding wiki) and FMG text (`msg/<lang>/*.msgbnd.dcx`).
- WitchyBND (CLI, `-s` silent; see `--help`; turn on its Offline mode so it never checks for updates) unpacks
  DCX/BND/TPF and serializes PARAM, FMG and more to XML. Smithbox (GUI) browses params, maps, text and models.
- Keep data under `dumps/fromsoft/` and the decompiled Lua/HKS/EMEVD text under `decomp/fromsoft/`, so `gst q grep`
  covers the scripts (`gst index` doesn't parse them).
- Titles without anti-cheat (Sekiro): the exe only within the verdict, through `gst decompile native`.

## Data and assets

- Unpack with UXM Selective Unpacker (GUI). Set its output path to the workspace (blank = the game folder), pick only
  the folders you need (`param/`, `event/`, `script/`, `msg/`, `chr/`…; a full unpack is tens of GB), and never use
  its Patch, which rewrites the exe. Then run WitchyBND on the `.dcx`/BND files.
- Textures: TPF → DDS (WitchyBND) → `texconv -ft png -o <out> <file>.dds` (`gst tools path texconv`). Models: FLVER,
  viewable in Smithbox.
- Oodle (`KRAK`): the tools need the game's own `oo2core_*.dll`. The plan allows loading it in place, opt-in and
  without copying it; WitchyBND looks for it in the Steam game folders. If a tool wants a copy beside itself, ask
  the user first.
- Sound: the `sd/` archives (FMOD or Wwise, depending on the title) → unpack, then
  `gst assets audio "<absolute path of a bank>"`.
- Saves: `%APPDATA%\<Game>\<SteamID64>\*.sl2`, a BND4 of slots; some titles encrypt them (key rule below).
- `gst assets list` and `gst assets extract` don't cover FromSoftware.

## Runtime

- EAC titles (DS3, Elden Ring, Nightreign, AC6) are `no-runtime`: no `gst trace`, no sandbox, no capture, offline
  included. Starting the exe without EAC (ModEngine2/me3's offline launch) is circumvention: not used here. Study
  the files on disk.
- Titles without anti-cheat (Sekiro): whatever `gst recon` decides; an anti-tamper hit (`static-only`) still rules
  out runtime work.

## Graphics

- DS3 and Sekiro use D3D11; Elden Ring, Nightreign and AC6 use D3D12. No capture on EAC titles.
- Static: lighting and fog presets (`GPARAM`, through WitchyBND), materials (`.mtd`/`.matbin`) and compiled shaders
  in `shader/*.shaderbnd.dcx`. Blobs that start with `DXBC`, or hold DXIL, disassemble with `fxc /dumpbin` or
  `dxc -dumpbin` (`gst tools path fxc`).

## Gotchas

- `gst decompile` says "no decompile backend for fromsoft" → no client code for EAC titles anyway → params, EMEVD,
  Lua/HKS.
- recon's next steps suggest `gst assets list` → unsupported for FromSoftware → the UXM + WitchyBND route.
- Unpacked files land in the game folder → UXM's output path was blank → set it to the workspace; the guard can't
  see GUI tools.
- A file comes out without a name → its hash is missing from the dictionary → keep the hash and identify the file
  by its magic (`gst fmt id`).
- `KRAK` files fail to open → no `oo2core_*.dll` found → see the Oodle note.
- Param values change after an update → balance patches rewrite `regulation.bin` → cite the game build with every
  param fact.
- WitchyBND hangs on a prompt → its update check → enable Offline mode.
- `gst decompile lua` finds nothing → it scans loose files of the install, not archives → WitchyBND.

## Protections

- EAC (`start_protected_game.exe`, `EasyAntiCheat/`) → `no-runtime`. Recent exes also carry anti-tamper (recon,
  `gst recon --deep`).
- Encrypted containers (BHD5 tables: RSA; `regulation.bin`: AES): the toolkit accepts only keys already published by
  the community, with their source recorded. It never extracts a key from the executable or from memory, and never
  brute-forces. UXM and WitchyBND/SoulsFormats carry the community-published keys: record the tool and its repo URL
  in `game.json` → `keys` before unpacking (e.g. `{"scheme": "bhd5", "source": "<repo URL>"}`; the source must be a
  URL). A tool that reads keys out of the exe is the excluded case.
- Alternatives: the unpacked data, Paramdex, DarkScript3's instruction definitions, the soulsmodding wiki.
- Full rules: `plugins/gst/guardrails.md`.

Adapted from universal-modder `skills/mod-any-game/references/engines/big-frameworks.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
