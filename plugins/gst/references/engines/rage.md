# Rockstar RAGE — study guide

Covers the `gst recon` engine key `rage`: GTA IV/EFLC, GTA V (Legacy and Enhanced), Red Dead Redemption 2.

## Identify

- `title.rgl` (Rockstar launcher entitlement), `*.rpf`, `PlayGTAIV.exe`/`PlayGTAV.exe`/`PlayRDR2.exe`. recon
  evidence: the exe or `title.rgl`, plus `rpf_archives` (count).
- GTA IV: `GTAIV.exe` (32-bit), `pc/` (anim, audio, data, models, textures), `pc/data/cdimages/*.img`, plaintext
  `common/data/` (`WeaponInfo.xml`, `handling.dat`, `Relationships.dat`, `Scenarios.dat`, `action_table.csv`…),
  the episodes in `TLAD/` and `TBoGT/`.
- GTA V Enhanced: `GTA5_Enhanced.exe` + `GTA5_Enhanced_BE.exe`, `BattlEye/`, `x64a…x64w.rpf`, `common.rpf`,
  `update/update*.rpf`, `oo2core_5_win64.dll`. Legacy: `GTA5.exe`.
- RDR2: `RDR2.exe`, `*_0.rpf` (anim, common, data, levels_0–7, shaders_x64, textures…), `update_1–4.rpf`,
  `x64/dlcpacks/`.
- Archive headers, read-only, with `gst fmt hex "<file>.rpf" --length 32`:
  - `2FPR` = RPF2 (GTA IV): the uint32 at 0x10 is the encrypted flag (0 = plain);
  - `7FPR` = RPF7 (GTA V): the uint32 at offset 12 is the encryption: `F9 FF FF 0F` AES, `FF FF EF 0F` NG, ASCII
    `OPEN` = rewritten by OpenIV, so the install was modified;
  - `8FPR` = RPF8 (RDR2).
- Verdicts in this library: GTA V Enhanced `no-runtime` (BattlEye), RDR2 out of runtime scope, GTA IV `static-only`
  (launcher/account).

## Code

- Logic splits between the native exe (engine, AI tasks, vehicles, physics) and compiled scripts run by the script
  VM (`.sco` in GTA IV, `.ysc` in GTA V and RDR2, inside the archives), which call engine "natives" by hash.
  Missions, minigames and most game rules are scripts.
- Read the public decompilations instead of decompiling: GTA V Legacy `calamity-inc/GTA-V-Decompiled-Scripts`,
  Enhanced `acidlabsdev/gtav-enhanced-scripts` (pseudo-C). Download an archive into `decomp/ysc-public/` (not
  `git clone`: no git work tree of decompiled code inside GS_HOME), note the commit and the game build it matches,
  and search it with `gst q grep "<native or label>" --glob "*.c"`.
- Natives: alloc8or's NativeDB (`gta5-nativedb-data`, with an Enhanced view; `rdr3-nativedb-data`): names,
  parameters and notes, the vocabulary of every script.
- The exe: never for GTA V (`no-runtime`) or RDR2 (protected exe). GTA IV only if `gst recon --deep` flags no
  protector on `GTAIV.exe` (`static-only` allows unprotected modules): `gst decompile native GTAIV.exe`.
- Formats and engine docs: the GTAMods wiki (RPF, IMG, cryptography, file formats) and the rdr2mods wiki.

## Data and assets

- GTA IV's `common/data/` and `pc/data/*.dat` are plaintext tables (weapons, handling, peds, relationships,
  scenarios, time cycle): read them in place. They are the best static target of the family.
- Archive contents are encrypted: GTA IV uses AES-256 on RPF/IMG tables and `.sco` scripts, GTA V uses AES or NG,
  RDR2 encrypts too. The keys live in the game exe, and GUI explorers such as CodeWalker and SparkIV read them out
  of it at startup (see Protections). So the contents stay diagnose-only: headers, `gst fmt entropy`, the file
  inventory, public format docs.
- Formats to know from the docs: GTA IV `.wdr/.wft/.wtd/.wbn`; GTA V `.ydr/.ydd/.yft/.ytd/.ybn/.ymap/.ytyp`, data in
  `.meta`/`.ymt` (XML or binary PSO), audio in `.awc`.
- `gst assets list` and `gst assets extract` don't cover RAGE.
- Saves (GTA IV: `%LOCALAPPDATA%\Rockstar Games\GTA IV`, known to recon): static reading only, with `gst fmt`.

## Runtime

None here: GTA V Enhanced is `no-runtime` (BattlEye), RDR2 is out of runtime scope and GTA IV is `static-only`, so
no `gst trace`, `gst sandbox` or `gst gfx`. Study the files on disk. Script hooks (ScriptHookV, ASI loaders) and
story mode with BattlEye switched off belong to modding and to circumvention: they are not used here.

## Graphics

- APIs: GTA IV D3D9, GTA V Legacy D3D11, GTA V Enhanced D3D12, RDR2 Vulkan or D3D12. No capture here (verdicts
  above; RenderDoc never runs on a game with anti-cheat).
- Public frame breakdowns instead: Adrian Courrèges' "GTA V – Graphics Study" (a full D3D11 frame) and Rockstar's
  SIGGRAPH 2019 Advances talk on RDR2's atmosphere and clouds. The shaders sit in encrypted archives.

## Gotchas

- `gst decompile` says "no decompile backend for rage" → no gst route for the exe or the scripts → public
  decompilations + NativeDB.
- recon's next steps suggest `gst assets list` → unsupported for RAGE → plaintext data + `gst fmt`.
- `OPEN` in an RPF7 header, or `mods/`, `scripts/`, `*.asi`, `dinput8.dll`, `ScriptHookV.dll` in the game folder →
  the install was modified → note it; facts from those archives are not vanilla.
- A script fact contradicts the game → the public dump matches another build → compare the repo's commit with the
  Steam buildid.
- Legacy and Enhanced differ (exes, scripts, natives) → use the matching repo and NativeDB view.
- Pseudo-C calls read `NAMESPACE::NATIVE_NAME(...)` or raw hashes → look them up in NativeDB before guessing.

## Protections

- BattlEye (GTA V Enhanced: `BattlEye/`, `*_BE.exe`, the `BEService` service) → `no-runtime`. Arxan-style
  anti-tamper on the GTA V and RDR2 exes (recon's duplicate-`.text` heuristic) and the Rockstar launcher/account
  (`title.rgl`, `Play*.exe`) → at least `static-only`.
- Encrypted archives: the toolkit accepts only keys already published by the community, with their source
  recorded. It never extracts a key from the executable or from memory, and never brute-forces. Tools that read the
  key out of the exe are that excluded case: diagnose, record, move on.
- Alternatives: GTA IV's plaintext data, the public decompiled scripts and NativeDB, the GTAMods and rdr2mods wikis,
  public rendering talks, the file inventory.
- Full rules: `plugins/gst/guardrails.md`.

Adapted from universal-modder `skills/mod-any-game/references/engines/big-frameworks.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
