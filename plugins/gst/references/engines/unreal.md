# Unreal Engine 4/5 — study guide

Covers the `gst recon` engine key `unreal` (UE 1–3 is `unreal3`, in `ue3.md`).

## Identify

- `<Project>/Binaries/Win64/<Project>-Win64-Shipping.exe` (`-WinGDK-Shipping.exe` in GDK builds). The root
  `<Project>.exe` is a small bootstrap that starts it and waits.
- `<Project>/Content/Paks/`: `*.pak` (footer magic `E1 12 6F 5A`), IoStore `*.utoc`/`*.ucas` beside a small pak
  (UE 4.26+ and UE5), `*.sig` when signed. `Manifest_UFSFiles_Win64.txt` at the root lists every packed path in plain text.
- recon details: `project`, `exe`, `engine_version` + `engine_version_source`, `pak_version`, `pak_footers`
  (`encrypted_index`, `key_guid`, `default_key`), `utoc_headers` (compressed, encrypted, signed, indexed), `paks`,
  `iostore`, `sig_files`; `engine_version_hint` when only the pak version is known (v11 = UE 4.26–5.3, v12 = 5.4+).
- Shipping exes rarely contain `++UE4+Release-x.y`: recon reads the FileVersion of
  `Engine/Binaries/Win64/CrashReportClient.exe` (or of the game exe). DRG: 4.27.2, pak v11, unencrypted.
  Witch It: 5.7.1, pak v12 + IoStore, encrypted and signed.
- User data: `%LOCALAPPDATA%\<Project>\Saved\` (`SaveGames/*.sav`, `Config/Windows*/*.ini`, `Logs/` when the build
  logs). Some builds keep `Saved/` inside the install (DRG: `FSD/Saved/SaveGames`).

## Code

Gameplay is split between C++ in the shipping exe and Blueprints compiled to Kismet bytecode inside the paks.
`gst decompile` has no `unreal` backend and says which route to take.

- **Blueprints:** find them with `gst assets list --name "/BP_"`, then
  `gst assets extract --name "Character/BP_PlayerCharacter\.uasset$" --bytecode` → `assets/unreal/json/<packed path>.json`
  (DRG: the 64 functions of `BP_PlayerCharacter` in 6 s).
  - `BlueprintGeneratedClass` → `SuperStruct` is the C++ parent (DRG: `/Script/FSD.PlayerCharacter`); `SCS_Node`
    entries are the components; every `Function` carries `ScriptBytecode` as `EX_*` statements.
  - The event graph compiles into `ExecuteUbergraph_<BP>`; events (`ReceiveBeginPlay`, `ReceiveTick`, input) are
    stubs that jump into it at an offset.
  - Calls (`EX_FinalFunction`, `EX_VirtualFunction`, `EX_CallMath`) name their target; `/Script/<Module>.<Class>`
    targets are C++.
  - `gst q` does not index Blueprint JSON yet (gap): `rg -n "<name>" assets/unreal/json`.
- **C++:** `gst decompile native <Project>-Win64-Shipping.exe` (a large module: runs only when named; hours, GBs of
  RAM). Start with `--max-functions 5000`, add `--functions "<regex>"` later (no re-analysis). Without a shipped
  `.pdb` names are `FUN_…`, and UE builds usually have no C++ RTTI: names come from reflection (Blueprint JSON, a
  runtime dump) and strings (`gst q strings`). Compare engine code with the public UE source of the same version (GitHub,
  Epic-linked account).
- **Where to start:** the `GameMode`, `GameState`, `PlayerController` and character Blueprints and their C++
  parents; the `GameInstance` (lives for the whole session); `SaveGame` subclasses; DataTables (`DT_*`).

## Data and assets

| Need | Command |
| --- | --- |
| containers + every path | `gst assets list` → `assets/INVENTORY.md` (version, encrypted, signed per container) + `inventory.jsonl`; DRG: 109,914 files in 5.4 s |
| search paths | `gst assets list --name "<regex>"` (paths look like `FSD/Content/Folder/BP_Name.uasset`) |
| properties | `gst assets extract --name "<regex>"` (JSON; add `--bytecode` for Blueprints) |
| media | `gst assets extract --types textures --name "<regex>"` (also `audio`, `models`, `animations`, `raw`; `--types` repeats) |
| IoStore to legacy `.uasset` | `gst assets to-legacy --filter "<path substring>"` → `dumps/unreal/legacy/*.pak` + uas JSON in `assets/unreal/iostore/json/` |
| summary for study | `gst analyze unreal_assets` (families per folder, DataTable numbers, Blueprint parents, native classes of `global.utoc`) |
| wrong engine guess | `--ue 4.27` / `--ue 5.4` |
| Wwise, FMOD | `gst assets audio --list`; Wwise `.pck` only by pattern: `gst assets audio "**/*.pck"` |

- No whole-project export from `gst assets` (one pak holds 100k+ files): narrow `--name`; `--limit N` exports the
  first N matches. `gst study` exports everything, one job per container and mode (`--opt unreal.name=<regex>` narrows).
- `assets/unreal/<mode>/` is incremental: every run re-exports the union of the filters used so far, and `--bytecode`
  stays on once used.
- DataTables, CurveTables, `.locres` text and `Config/*.ini` inside the paks export as JSON or raw: the tunables.
- Unversioned properties (most UE5 cooks): the JSON lacks property values without a `.usmap` mapping. gst uses a
  published `.usmap` (`game.json` `usmap.path` + source) or one the user puts in `notes/*.usmap`; it never dumps one
  (that injects: see Runtime). Without it, those exports are `blocked-policy` with the reason.
- Files gst skips (e.g. `AssetRegistry.bin`): `repak unpack <pak> -o <workspace dir> -i <Project>/AssetRegistry.bin`,
  then `retoc asset-registry <file>` (every asset with its class). IoStore: `retoc info <utoc>`, `retoc list <utoc>`.
  Comparing builds: `repak hash-list <pak>`.
- Saves are GVAS (`gst fmt id` → `ue-gvas`). gst ships a header reader, `src/gst/data/readers/gvas_header.py` in the
  toolkit: `gst fmt roundtrip <that file> "<saves>"` (DRG: 9 saves, `/Script/FSD.FSDSaveGame`); the property stream
  stays raw bytes.
- Video is often Bink 2 (`.bk2`): ffmpeg reads only Bink 1.

## Runtime

- Native code: `gst trace watch` names `<Project>-Win64-Shipping.exe+0xRVA`; a function name needs the Ghidra export
  of that exe. UE's garbage collector does not move objects: a found address holds while the object lives.
- `gst trace ps` shows the bootstrap and the shipping exe: pass `--pid` of the shipping exe.
- Values that a Blueprint writes come from the Kismet VM's generic handlers in the exe: read the Blueprint's
  bytecode instead.
- Property offsets, class dumps and `.usmap` files come from RE-UE4SS (Live View, dumpers), Dumper-7 (SDK headers
  with offsets) or jmap. They inject or attach: only inside `gst sandbox`, offline, guided.
- `gst trace snapshot before` / `after` + `gst trace diff` around a save (`Saved/`).

## Graphics

- UE4 defaults to D3D11 (D3D12 optional); UE5 usually runs D3D12, Vulkan optional. The switches `-dx11`, `-dx12` and
  `-vulkan` go in `--args`. RenderDoc covers all three.
- `gst gfx capture --exe <Project>-Win64-Shipping.exe` runs the sandbox copy (`gst sandbox create` first). Name the
  shipping exe: RenderDoc doesn't follow the bootstrap's child process.
- Shipping builds usually strip event markers and resource names: `frame.md` groups passes by target changes.
  Deferred frames show the G-buffer MRTs, an HDR scene color and velocity; UE5 Nanite, Lumen and virtual shadow maps
  show as long runs of dispatches.
- Uniform buffers keep engine names (`View`, `Primitive`, ...): look them up in the UE source.

## Gotchas

- `repak list` fails on a container → pak v12 is past repak 0.2.3 (v1–v11) → gst lists v10-v12 with its own reader
  (stdlib) when the index is plain.
- JSON fails or looks wrong → wrong CUE4Parse version: recon's engine version, else a guess (`GAME_UE4_27` for
  `WindowsNoEditor` paks, `GAME_UE5_3` otherwise) → `--ue <major.minor>`.
- Earlier JSON vanished after a narrower uas export by hand → uas deletes outputs outside the new `--filter` → let gst
  export (it passes the union of filters), or use a fresh `--output`.
- Paths only in `Manifest_UFSFiles` while `global.utoc` reads fine → the global container holds no assets → the
  content sits in closed containers.
- `no native game module recognized` → the shipping exe counts as large → name it.
- `repak unpack` denied → no `-o` → add `-o <workspace dir>` (without it, repak writes next to the pak).
- `several game processes: pass --pid` → bootstrap + shipping exe → `--pid` of the shipping exe.
- `no graphics API registered` → the capture started the bootstrap → `--exe <Project>-Win64-Shipping.exe`.

## Protections

- An encrypted pak index (`encrypted_index`) or the IoStore `Encrypted` flag becomes recon's `encrypted-content`.
  `gst assets list` then inventories `Manifest_UFSFiles_*.txt` (Witch It: 54,619 paths) and extraction stops (rule 4);
  the folder layout and asset names still map the game. A plain `global.utoc` still gives the native module and class
  list (`gst analyze unreal_assets`; Witch It: 519 modules, `/Script/PropWitchHuntModule` with 72 classes).
- Keys: only a community-published key with its source URL, in `game.json` `keys` or `notes/keys.json`
  (`{"keys": [{"key": "0x...", "guid": "...", "source": "https://..."}]}`); never extracted from the exe or memory,
  never brute-forced.
- `.sig` files and the IoStore `Signed` flag are integrity checks; they don't block reading unencrypted content.
- EAC, BattlEye or VAC → `no-runtime` (Witch It: VAC); Denuvo or other anti-tamper → `static-only`. recon records each
  in `recon/protections.md`; the alternatives are data on disk, an offline mode, official docs and the UE source.
- Limits: `plugins/gst/guardrails.md`.

Adapted from universal-modder `skills/mod-any-game/references/engines/unreal.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
