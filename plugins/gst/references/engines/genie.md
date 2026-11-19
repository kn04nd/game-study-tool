# Genie engine (Age of Empires II DE) — study guide

Covers the `gst recon` engine key `genie`: Age of Empires II: Definitive Edition (the DE rewrite of the Genie engine).

## Identify

- `AoE2DE_s.exe` (x64) and `resources/_common/dat/empires2_x2_p1.dat`; recon reports `genie` when
  `resources/_common/dat/*.dat` and an `aoe*_s.exe` exist. Version: the Steam build id and the exe's
  ProductVersion in RECON.md.
- Profile: `%USERPROFILE%\Games\Age of Empires 2 DE\<steamid>\` (`savegame/`, `mods/`, scenarios). Recon doesn't
  find it (the folder name differs from the store name): give it to `gst trace snapshot --path`.
- `gst fmt id` knows none of the Genie formats: the `.dat` reads as unknown and high-entropy, `.sld` as unknown.

## Code

Most gameplay is data in the `.dat`; the exe is the engine, the simulation and the UI.

- **The `.dat` database** (civs, units, techs, effects, graphics, sounds, terrains): read it, don't decompile it.
  genieutils-py (Python, not in catalog) parses it, read-only:
  `DatFile.parse("<install>/resources/_common/dat/empires2_x2_p1.dat").civs[1].units[38].name` → `'KNGHT'`.
  Dump the slices a question needs (one civ's units, a tech's effects) to `dumps/`.
- **Scripts as text**, read in place: AI rules in `.per` files (`(defrule <conditions> => <actions>)`), random maps
  in `.rms` files.
- **Scenarios** `.aoe2scenario` (triggers, placed units): AoE2ScenarioParser (Python) reads them.
- **Recorded games** `.aoe2record` (in `savegame/`) are the lockstep command stream: replays re-simulate from player
  commands. Community parser: aoc-mgz (`mgz` on PyPI).
- **Native exe** (C++, no PDB): `gst decompile native AoE2DE_s.exe` (a big exe: hours; `--functions <regex>` narrows
  later exports). Some rules live only here: the civ picker lists only civs in an index-based table compiled
  into the exe, whatever the `.dat` holds.

Read first: one unit end to end: its `.dat` entry (stats, graphic ids, train location) → the techs and effects that
change it → its SLD sprites → its strings.

## Data and assets

- `.dat`: raw DEFLATE without a zlib header; inflated, it starts with an ASCII `VER x.y` tag. For `gst fmt` work,
  inflate a copy into `dumps/` (`zlib.decompress(data, -15)`, what genieutils-py does).
- Sprites `resources/_common/drs/graphics/*.sld`, layout reverse-engineered in universal-modder's
  `examples/aoe2-de-civ/sld.py`:
  - header `SLDX`, u16 version (4), u16 frame count, u16 0, u16 `0x10`, u32 `0xff` (most files; others use `0x0e`
    and an extra u16 of unknown meaning);
  - frame: u16 canvas width and height, i16 hotspot x and y, u8 layer mask, u8 0, u16 frame index;
  - layers in bit order: `0x01` main (BC1), `0x02` shadow (BC4), `0x04` unknown, `0x08` damage mask (BC1), `0x10`
    player colour (BC4). Each: u32 length (itself included, padded to 4), a u16 box `x1 y1 x2 y2` (main and shadow
    only; the others reuse the main box), u8 flags, u8 unknown, u16 command count, `(u8 skip, u8 draw)` pairs over
    the layer's 4x4 blocks in row order, then 8-byte blocks. Flag `0x80`: undrawn blocks copy the previous frame.
  - Units: 16 headings clockwise from east, the hotspot at the ground point, one extra trailing frame. The camera is
    orthographic at 30° elevation (2:1 tiles).
  - Prove it with `gst fmt new-reader sld --sample <file.sld>` and `gst fmt roundtrip notes/formats/sld.py "<glob>"`,
    keeping the BC blocks as raw bytes.
- Tech tree and civ UI: `resources/_common/dat/civilizations.json`, `unitlines.json`, `futuravailableunits.json`.
- UI art: `resources/_common/wpfg/resources/` (PNG: `uniticons/`, `civ_emblems/`, `civ_techtree/`); in-game command
  icons come from a prebuilt `widgetui` atlas instead.
- Strings: `resources/<lang>/strings/key-value/*.txt` (`id "text"`); DE keeps the old DLL help strings at
  `id - 79000` (Knight help 105068 → key 26068).
- Audio: Wwise banks and streams: `gst assets audio --list`, then `gst assets audio "<glob>" --limit 20`.
- Textures (`.dds`): `texconv -ft png -o <ws dir> <in.dds>` (`gst tools path texconv`).

## Runtime

- Single player has no anti-cheat; expect `offline-ok` (recon decides). `gst trace ps`, `scan`, `next`, `watch` work on
  the x64 exe; `watch` names the writer once `gst decompile native AoE2DE_s.exe` has run.
- Player resources (food, wood, stone, gold, population) are floats: `gst trace scan 200 --type float`.
- Saves and records: `gst trace snapshot before --path "<profile>/savegame"`, act, `after`, `gst trace diff before after`.

## Graphics

- D3D11: `gst gfx capture` from the sandbox (`offline-ok` only). If Steam relaunches the original install instead of the
  copy, the capture registers no API: use `--manual`, or study the sprites statically.
- Units and buildings are pre-rendered sprites (SLD), so most draws are 2D sprite batches.

## Gotchas

- `gst decompile` stops with "no decompile backend for genie" → the `.dat` route above; `gst decompile native` for code.
- `gst assets list` stops ("handle Unity, Godot and Unreal 4/5") → genieutils-py, the SLD layout, `texconv`;
  `gst assets audio` still works.
- The `.dat` looks encrypted (no magic, entropy ~8) → raw DEFLATE, not encryption.
- `gst fmt roundtrip` of an SLD reader fails after decoding → BC1/BC4 re-encoding is lossy; keep blocks raw.
- `gst trace scan` finds no int32 hit for a resource → it's a float (`--type float`).
- Values differ from the vanilla `.dat` → a mod under `<profile>/mods/` overrides resources, or a data mod was
  picked in the lobby: study the install's files.
- After a crash Steam says the game is running → BugSplat's `BsSndRpt64.exe` lingers: stop it by its exact PID.

## Protections

- No anti-cheat for single player and lobbies; ranked play is online (out of scope). The DE data isn't encrypted.
- Nothing to diagnose in the usual case; if recon flags a wrapper on the exe, the data route above still works.
- Limits: [guardrails.md](../../guardrails.md).

Adapted from universal-modder `skills/mod-any-game/references/engines/genie-aoe2.md`,
`knowledge/games/age-of-empires-ii-de/san-franciscans-civ.md` and `examples/aoe2-de-civ/sld.py` (MIT, Copyright (c)
2026 Rehan and universal-modder contributors).
