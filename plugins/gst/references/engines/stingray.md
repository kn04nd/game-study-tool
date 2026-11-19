# Autodesk Stingray / Bitsquid — study guide

Covers the `gst recon` engine key `stingray`: Helldivers 2 (Arrowhead's Stingray fork) first; Vermintide 1/2, Darktide and Magicka 2 belong to the same family.

## Identify

- Helldivers 2: `bin/helldivers2.exe`, `bin/lua51.dll` (LuaJIT's DLL name), `*_pluginw64_release.dll` plugins (level
  generation, Wwise), `bin/GameGuard/` (`GameGuard.des`, `*.erl`), `tools/GGSetup.exe`.
- `data/` in the post-"Slim" layout (seen 2026-10): `bundles.00..29.nxa` + `bundles.nxa`, `bundle_database.data`,
  `bundles.region_hints`, `settings.ini`, a few 16-hex-digit hashed files with `.stream` companions;
  `NxStorage_*.txt` logs at the install root.
- Classic layout (other titles, older HD2 builds): `data/` full of "triads" sharing one hashed name: `<hash>` (main:
  metadata), `<hash>.stream` (streamed CPU data such as audio), `<hash>.gpu_resources` (meshes, textures).
- `gst recon` matches `data/bundle_database.data` / `data/bundles.NN.nxa` (gst rules) or `lua51.dll` plus many hashed
  files; SteamDB's `Bitsquid` rule (`<16 hex>.stream`) corroborates. No engine details beyond the evidence.
- Version: the Steam build id; nothing on disk names the public patch.

## Code

- Gameplay is Lua (LuaJIT bytecode inside the bundles) over a C++ engine; resources are addressed by 64-bit hashes
  of type and name. Helldivers 2 is `no-runtime`: neither the exe nor the Lua chunks are decompiled.
- Read instead:
  - what the Lua consumes: units (models), prefabs and text tables (JSON) exported by filediver (Data, below);
  - the engine's model (resources, packages, units, worlds, the Lua API): Autodesk's Stingray Help (Lua API
    reference, still online) and the Bitsquid development blog (2009-2017);
  - community repositories of Vermintide 2's decompiled Lua: public reference for Bitsquid Lua conventions. It is
    another game and branch, so anything carried over to HD2 stays a hypothesis.
- Stingray titles whose verdict allows decompiling: LuaJIT chunks (`\x1bLJ`) go through luajit-decompiler-v2
  (`ljdv2 in -o out -s -f`, under a timeout: it can open a modal dialog) into `decomp/luajit/`, then `gst index`.
  `gst decompile lua` only diagnoses LuaJIT, and only in loose install files.

## Data and assets

- Not in the `gst tools` catalog yet: filediver 0.7.58 (xypwn/filediver, BSD-3-Clause; CLI zip). Get it with the
  user's OK, outside the install; log version and command in STUDY.md.
- filediver finds the install by itself and writes to the current folder unless `-o` is given:

```text
filediver -h
filediver -o "<ws>\assets\filediver" -T <type> -t <archive id>    # e.g. -T unit, -T bik
```

- `-t` takes archive ids from the community "HD 2 Archive Labelling" sheet linked in the README; `-i` selects one
  file; `--audio-format` picks the audio output.
- Output: models glTF/.blend, textures PNG, audio ogg/aac/wav (`wwise_bank`, `wwise_stream` types), video bik/mp4,
  prefabs and text tables as JSON.
- Names are hashes: filediver resolves all type names and most file names (its wiki); keep the rest as hashes in notes.
- `.nxa` containers are undocumented publicly, and filediver's README and release notes don't mention them (checked
  2026-10-08). A universal-modder field note exported GLB units with filediver 0.7.55 from an HD2 install in
  2026-10: confirm coverage on a small export first.
- Survey before converting: `gst fmt scan data` (kinds, sizes, entropy per extension),
  `gst fmt id data/bundle_database.data`, `gst fmt entropy` on one `.nxa`. Plain text first: `data/settings.ini` and
  the `NxStorage_*.txt` logs.

## Runtime

- `no-runtime` (kernel anti-cheat, always online): no debugger, scans, injection or capture, sandbox included.
- Keep the game closed while tools read `data/`: GameGuard inspects other processes while it runs.
- No save route either: progression lives on the servers. The static route is filediver exports, `gst fmt` and the
  public docs above.

## Graphics

- Direct3D 12 by default; D3D11 behind the `--use-d3d11` launch option (community guides). No capture at this verdict.
- Static view through filediver's model and texture exports. From the field note: gear ships no albedo map (colour
  comes from layered material IDs); normal maps keep X/Y in R/G (+Y up), B is not Z, and A is a height map.

## Gotchas

- `gst assets list` says it handles Unity, Godot and Unreal only → no gst backend for Stingray → filediver + `gst fmt`.
- `gst decompile lua` finds nothing → the Lua is inside the bundles (and HD2 is `no-runtime`) → nothing to run.
- filediver output in a random folder → it writes to the cwd without `-o` → always pass `-o <workspace dir>`.
- The filediver GUI offers to download FFmpeg and scripts → internet downloads → ask the user; the CLI exports
  without them.
- GLB meshes look like triangle soups (a colour cell per triangle, every edge a UV seam) → that is how they export →
  weld by position before counting vertices or reading topology.
- Normal maps look wrong as RGB → R/G are X/Y and A is height (Graphics) → rebuild Z before interpreting them.
- Guides describe loose `data/<hash>` triads → the post-Slim layout packs most data into `.nxa` bundles →
  `gst fmt scan data` first.

## Protections

- nProtect GameGuard (kernel anti-cheat: `bin/GameGuard/`, `tools/GGSetup.exe`), Themida on `helldivers2.exe` and an
  always-online design → `no-runtime`. AreWeAntiCheatYet lists Easy Anti-Cheat for Vermintide 2 and Darktide: same
  verdict.
- A container that proves encrypted (`gst fmt entropy` near 8 bits/byte, no recognizable header): only a
  community-published key with its source URL recorded in game.json; never a key from the exe or memory, never
  brute force.
- `recon/protections.md` records them. Limits and reasons: [`plugins/gst/guardrails.md`](../../guardrails.md).

Adapted from universal-modder `knowledge/games/risk-of-rain-2/helldiver-survivor.md` (MIT, Copyright (c) 2026 Rehan and universal-modder contributors).
