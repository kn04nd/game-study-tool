# Capcom RE Engine — study guide

Covers the `gst recon` engine key `re-engine` (Resident Evil 2/3/4/7/8 and later, Devil May Cry 5, Monster Hunter
Rise/Wilds, Street Fighter 6, Dragon's Dogma 2…).

## Identify

- `re_chunk_000.pak` plus patch paks `re_chunk_000.pak.patch_00N.pak` (the highest N wins; newer titles also chain
  `.sub_000.pak` files), DLC paks, and one large exe (`re2.exe`, `MonsterHunterWilds.exe`…). recon evidence:
  `re_chunk_000.pak`.
- Pak header `KPKA` (`gst fmt hex re_chunk_000.pak --length 32`): version, flags, entry count. Entries are keyed by
  murmur3 hashes of the lower- and upper-case path, so file names come from community lists.
- Inside: `natives/STM/…` paths. Extensions carry a per-title version: `.tex.NNN`, `.mesh.NNN`, `.user.N`,
  `.scn.NN`, `.pfb.NN`, `.msg.NN`, `.motlist.NNN`, `.mdf2.NN`.
- Already modified: `reframework/` or `dinput8.dll` (REFramework), a loose `natives/` folder or extra paks (mod
  managers) → recon warns about known loaders; study the vanilla paks.
- Saves: Steam's `userdata\<id>\<appid>\remote\win64_save\`.

## Code

- The exe is native C++, but the engine carries a reflection database (the TDB: `via.*` engine and `app.*` game
  types, with fields and methods). Those names are the map of the game. In order:
  1. community TDB/RSZ type dumps for the game (JSON, generated with REFramework and published by modders);
  2. REFramework's object explorer and its type dump: an injected inspector, so only in `gst sandbox`, offline, with
     verdict `offline-ok`;
  3. a native decompile, only if recon shows no protector on the exe: `gst decompile native <game>.exe` (hundreds of
     MB, so hours; `--max-functions`, `--functions "<regex>"`). Titles with Denuvo or Enigma: never.
- Tuning lives in data: `.user` (RSZ-serialized parameter objects), `.scn`/`.pfb` (scenes and prefabs: GameObjects
  with their components and fields), `.msg` (text). Read those first, labelled with the TDB names.
- Decompiled or dumped text goes under `decomp/ree/`; `gst index` doesn't parse it, `gst q grep` searches it.

## Data and assets

    REE.Unpacker <LIST_TAG> "<install>\re_chunk_000.pak" "<workspace>\assets\ree"

- REE.PAK.Tool (Ekey; not in the gst catalog): the tag picks the game's file list, and its `Projects/` folder must
  sit beside the tool. The output folder is optional and defaults to the pak's folder, the install: always pass it.
  Unpack the base pak, then each patch pak in ascending order into the same folder. A full unpack is tens of GB.
- RSZ files: RszTool (GUI) with the game's RSZ type dump shows the fields. Materials (`.mdf2`) and textures
  (`.tex`) need community converters: inspection only.
- Audio: Wwise banks and packages, often renamed with version suffixes: copy one into the workspace under a standard
  extension (`.bnk`/`.pck`), then `gst assets audio "<absolute path>"`.
- `gst assets list` and `gst assets extract` don't cover RE Engine. Unknown files: `gst fmt id`,
  `gst fmt scan "<folder>"`.

## Runtime

Verdict `offline-ok` only, which is rare in this engine.

- Most titles are `static-only` (Denuvo, Enigma) or rely on online services: no runtime, no injection.
- When allowed: REFramework only in the `gst sandbox` copy (it drops `dinput8.dll` and `reframework/`), offline,
  for the object explorer, TDB names and live field values. `gst trace scan`/`next`/`watch` against the sandboxed
  game; name the writers from the TDB dump.
- re-engine-mcp (stdio, talking to an in-game REFramework plugin that serves HTTP on localhost:8899 without auth) is
  not a `gst mcp` template: leave it out.

## Graphics

- D3D12 (older titles also D3D11). A capture needs `offline-ok`, `gst sandbox create` and `gst gfx capture`.
- Otherwise: material parameters in `.mdf2` and shader names from the unpacked data, plus Capcom's own conference
  talks on RE ENGINE.

## Gotchas

- `gst decompile` says "no decompile backend for re-engine" → no gst route → TDB names and data first; native only
  within the verdict.
- recon's next steps suggest `gst assets list` → unsupported for RE Engine → the REE.PAK.Tool route above.
- Unpacked files keep hash names → the list doesn't match this build (a patch added paths) → take the current list
  from the tool's `Projects/`; the rest stays hashed: identify it with `gst fmt id`.
- Files appear next to the paks → REE.Unpacker ran without its third argument → always pass a workspace folder (the
  guard doesn't watch this tool).
- A value differs from the base pak → a later patch pak overrides that file → unpack the patches in order and cite
  the pak a fact comes from.
- RSZ fields look shifted or unreadable → the type dump belongs to another build → find the dump matching the
  game's buildid.

## Protections

- The exe: Denuvo on many titles (the store's `drm_notice`, DIE through `gst recon --deep`) and Enigma Protector,
  which Capcom added to some older titles in 2024 (`.enigma1/.enigma2`) → `static-only`; the exe is never unpacked
  or decompiled. Online titles: the verdict decides; never inject into an online session.
- Encrypted pak tables or resources in newer titles: the toolkit accepts only keys already published by the
  community, with their source recorded. It never extracts a key from the executable or from memory, and never
  brute-forces. A key published in a public tool's source or in a public write-up counts: record that URL in
  `game.json` → `keys`.
- Alternatives: the paks' data (RSZ, text, materials), community TDB/RSZ dumps, REFramework's documentation,
  Capcom's talks.
- Full rules: `plugins/gst/guardrails.md`.

Adapted from universal-modder `skills/mod-any-game/references/engines/big-frameworks.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
