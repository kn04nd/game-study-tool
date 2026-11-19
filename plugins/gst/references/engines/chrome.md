# Chrome Engine (Techland) — study guide

Covers the `gst recon` engine key `chrome`: Dying Light 1 (Chrome Engine 6) first; DL2, The Beast and Dead Island DE share the `.rpack` family.

## Identify

- Dying Light: `DyingLightGame.exe`, `*_x64_rwdi.dll` modules (`engine_`, `gamedll_`, `filesystem_`, `rd3d11_`...),
  `DW/Data0-3.pak` plus per-language `Data*`/`Speech*` paks, `DW/Data/*.rpack` (`engine_PC`, `weapons_PC`,
  `common_textures_*`, map packs such as `slums_*`), `DW/Data/*.csb` sound banks, FMOD and EOS SDK DLLs.
- `DevTools/` is the official Dying Light Developer Tools (separate Steam tool): `DyingLightPlayer.exe`,
  `ropengl_x64_rwdi.dll`, and `DevTools/DW/Data/` with plaintext `.scr` (`Characters.scr`, `campaigns.scr`,
  `frame_script.scr`, `Scripts/`, `Presets/`, `Foliage/`).
- `gst recon` matches `*_x64_rwdi.dll` / `engine_pc.rpack` (SteamDB adds `ChromEd.exe` and `ChromeEngine<N>.dll` for
  older Chrome titles). Engine details show `devtools=True` when `DevTools/` is installed.
- Version: Steam build id and the PE versions listed in `recon/RECON.md`.
- DL2 / The Beast (C-Engine) keep paks in `ph/source/`; RP6_UnpackCLI reads DL1, DL2, The Beast, Bad Blood and
  Dead Island DE/Riptide DE packs.
- User data: recon lists `Documents/DyingLight` (`out/settings/video.scr` is config in script syntax); saves are in
  Steam `userdata/<id>/239140/remote/out/save/`.

## Code

- Gameplay C++ lives in `gamedll_x64_rwdi.dll` on `engine_x64_rwdi.dll`. Dying Light is `no-runtime` (see
  Protections), so client code is not decompiled: the logic you can read is the `.scr` script layer.
- Chrome titles whose verdict allows it (`offline-ok`, or unprotected modules under `static-only`):
  `gst decompile native --list`, then `gst decompile native gamedll_x64_rwdi.dll` (no PDB: `FUN_` names).
- `.scr` is C-like text: `import`, `sub main() { ... }`, call statements such as `Param("Name", "Value");`.
  Scripts hold tuning, items, character/AI presets and campaigns.
- Read first: the DevTools scripts (`DevTools/DW/Data/*.scr`, official, on disk), then the shipping ones in
  `DW/Data0.pak`, e.g. `scripts/player/player_variables.scr` and `scripts/inventory/`.

Snapshot both into the workspace so citations stay stable (`gst tools install 7zip` first):

```text
7z l -slt "<install>\DW\Data0.pak" > dumps\Data0.pak.txt      # paths, sizes, CRC: search this file
7z x "<install>\DW\Data0.pak" -odecomp\scr\Data0 "-ir!*.scr"   # only the scripts, paths kept
gst q grep "Param\(.Jump" --glob "*.scr"                        # regex over decomp/ (.scr has no symbol index)
```

- Copy the `DevTools/DW/Data/` scripts to `decomp/scr/devtools/` too, so one search covers both.
- The language's vocabulary: count call names, then read how the DevTools scripts use each one (Bash):
  `rg -oNI "^\s*(\w+)\(" -r '$1' decomp/scr | sort | uniq -c | sort -rn | head -50`
- Public docs: Techland's "Developer Tools - info" support page, the Steam DevTools guide, cemodding.wiki.

## Data and assets

- Not in the `gst tools` catalog yet: RP6_UnpackCLI 1.5.1 (12brendon34/Dying-Light-ResourcePack-Tools). Get it with
  the user's OK, outside the install; log version and command in STUDY.md.
- `.pak` = zip (scripts and presets; the language and speech paks too): 7-Zip as above.
- `.rpack` (`RP6L` resource packs): RP6_UnpackCLI has no output option (files go to the working folder,
  `options.ini` next to its exe), so copy the pack into the workspace and run it there:

```text
(cwd = assets\chrome\rpack, holding a copy of DW\Data\weapons_PC.rpack)
RP6_UnpackCLI weapons_PC.rpack
```

- Output: DDS/PNG textures, meshes in the Chrome Engine editor format, ANM2 animations, FX; unknown types raw.
- `options.ini`: `EnablePngFixup` turns `.png.dds` back into `.png`; `EnableRawDumping` writes every chunk raw (no
  fixups), the input for `gst fmt`. DDS to PNG: `texconv -ft png -o <out> <in.dds>` (`gst tools path texconv`).
- Audio: FMOD DLLs ship with the game; `.csb` is outside `gst assets audio`'s default set:
  `gst assets audio "DW/Data/*.csb" --list` (vgmstream support for `.csb` unverified). If it rejects them,
  `gst fmt strings <bank> --min 4 --grep "FSB[45]"` looks for an embedded FMOD bank.
- Unknown files (`optimized_dx11.mp`, editor meshes, raw chunks): `gst fmt scan DW/Data`, `gst fmt id`,
  `gst fmt entropy`, then `gst fmt new-reader` proved by `gst fmt roundtrip`.

## Runtime

- `no-runtime`: no debugger, scans, injection or capture, sandbox included.
- Static route instead:
  - the DevTools editor (official GUI; the user opens it) shows how levels, presets and scripts fit together;
  - what a save holds, without touching the process: snapshot the save folder, play normally, snapshot again,
    diff, then `gst fmt` on the changed file:

```text
gst trace snapshot before --path "<Steam>\userdata\<id>\239140\remote\out"
gst trace snapshot after --path "<Steam>\userdata\<id>\239140\remote\out"
gst trace diff before after
```

## Graphics

- Direct3D 11 renderer (`rd3d11_x64_rwdi.dll`); DevTools also ships an OpenGL one (`ropengl_x64_rwdi.dll`).
- No capture at this verdict. Static: textures and materials out of `.rpack` (raw dumps for unknown chunks).

## Gotchas

- `gst assets list` says it handles Unity, Godot and Unreal only → no gst asset backend for Chrome → 7-Zip,
  RP6_UnpackCLI and `gst fmt`; `gst assets audio` still works.
- `gst decompile` says `no decompile backend for chrome` → expected → the logic you can read is `.scr` text.
- Windows tries to run a `.scr` → it is the screensaver executable type → never open one through the shell
  (`Invoke-Item`, `start`); read it as text.
- RP6 output lands in the wrong folder → no output option → copy the `.rpack` into the workspace, cwd there.
- Same script in two `DataN.pak` or in DevTools → the copies can differ → cite which one
  (`decomp/scr/<source>/...:line`); compare them with `gst fmt diff`.
- Mesh exports don't open in Blender → Chrome Engine editor format, not glTF → study the layout with `gst fmt`.

## Protections

- SteamStub (`drm-wrapper`) on `DyingLightGame.exe`, never unpacked, plus VAC (AreWeAntiCheatYet, Steam store)
  → `no-runtime`. Co-op and online services (EOS) are out of scope.
- `recon/protections.md` records them. What remains: DevTools, `.pak`/`.rpack` data, saves on disk, public docs.
- Limits and reasons: [`plugins/gst/guardrails.md`](../../guardrails.md).
