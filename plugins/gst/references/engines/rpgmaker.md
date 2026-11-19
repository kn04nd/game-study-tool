# RPG Maker — study guide

Covers the `gst recon` engine keys `rpgmaker-mvmz` (MV/MZ: NW.js + JavaScript), `rpgmaker-rgss` (XP/VX/VX Ace:
Ruby RGSS) and `rpgmaker-2k` (2000/2003: `RPG_RT`).

## Identify

| Key | On disk | Version |
| --- | --- | --- |
| `rpgmaker-mvmz` | `Game.exe` (NW.js), `nw.dll`, `package.json`; MV `www/js/rpg_core.js`, MZ `js/rmmz_core.js` (no `www/`); `data/*.json` | `Utils.RPGMAKER_NAME` / `RPGMAKER_VERSION` in the core JS |
| `rpgmaker-rgss` | `Game.exe` + `Game.ini` (`Library=` RGSS DLL, `Scripts=`), `Data/*.rxdata`/`.rvdata`/`.rvdata2` or one `Game.rgssad`/`.rgss2a`/`.rgss3a` | rxdata = XP, rvdata = VX, rvdata2 = VX Ace; the RGSS DLL name |
| `rpgmaker-2k` | `RPG_RT.exe`, `RPG_RT.ldb` (database), `RPG_RT.lmt` (map tree), `Map####.lmu`, `.xyz`/`.png` images | `RPG_RT.exe` version info; patched runtimes (Maniacs, DynRPG) |

- `gst recon` evidence: the core JS (with `version` MV or MZ), the archive or `Scripts.r*data`, `RPG_RT.*`.
  Encrypted content appears under protections with a `scheme`: `rpgmaker-mv-systemjson` (MV/MZ media) or
  `rgssad` (archive).
- Signatures for `gst fmt hex`: `RPGMV` (MV/MZ encrypted media), `RGSSAD\0` + version byte (archive), `04 08`
  (Ruby Marshal data), `LcfDataBase` / `LcfMapTree` / `LcfMapUnit` after a length byte (2000/2003).

## Code

**MV/MZ: JavaScript shipped as source.**
- The core scripts (MV `rpg_core/managers/objects/scenes/sprites/windows.js`, MZ `rmmz_*.js`) are RPG Maker's own
  readable engine. Game-specific logic: `js/plugins/*.js`, enabled with parameters in `js/plugins.js` (load order
  matters: later plugins replace prototype methods), plus the event command lists in `data/*.json`.
- Copy `js/` and `data/` (MV: under `www/`) into `decomp/rpgmaker/` so `gst q grep` reaches them (no JS parser in
  `gst index` yet): `gst q grep "prototype\.makeDamageValue *=" --glob "*.js"` lists every override.
- Event commands are numeric codes run by `Game_Interpreter.prototype.command<code>` in `rpg_objects.js` /
  `rmmz_objects.js`: 101/401 text, 102 choices, 111 branch, 121 switches, 122 variables, 201 transfer, 301 battle,
  355/655 script, 356 (MV) / 357 (MZ) plugin command. `data/System.json` names every switch and variable.
- Formulas: the `damage.formula` strings in `Skills.json`/`Items.json`, evaluated by
  `Game_Action.prototype.evalDamageFormula`. `note` fields carry plugin tags (parsed into `meta`).
- Minified or obfuscated plugins: wakaru or webcrack (see their `--help`).

**XP/VX/VX Ace: Ruby.**
- Every script lives in `Data/Scripts.r*data`, a Marshal array of `[id, name, zlib-compressed source]`: the RGSS
  defaults (`Game_*`, `Sprite_*`, `Window_*`, `Scene_*`) plus the custom ones, usually just above `Main`.
- `rvpacker-txt-rs json generate -i "<install>" -o decomp/rgss` writes `json/*.json` for every data file plus the
  scripts as Ruby, reading an RGSS archive on the fly (see Protections). `-o` defaults to the game folder: always
  pass it. Never `json write`, `write` or `purge`: they produce game files. Then `gst q grep "<regex>" --glob "*.rb"`.
- Built-in classes (`Bitmap`, `Sprite`, `Window`, `Graphics`, `Input`, `Audio`, `RPG::*`) are native code in the
  RGSS DLL: the RGSS reference (the editor's help) and mkxp, an open-source RGSS reimplementation, document them.

**2000/2003: no scripts; events are the logic.**
- `RPG_RT.ldb` (database, common events), `RPG_RT.lmt` and `Map####.lmu` are LCF binaries: `lcf2xml` (EasyRPG
  liblcf; see its `--help`, run it on copies inside the workspace) or `rvpacker-txt-rs json generate`, which also
  reads LCF.
- Command semantics: EasyRPG Player, an open-source reimplementation of `RPG_RT` (`src/game_interpreter.cpp`).

## Data and assets

- MV/MZ: `data/*.json` is the whole database (Actors, Classes, Skills, Items, Weapons, Armors, Enemies, Troops,
  States, Tilesets, CommonEvents, System, MapInfos, `Map###`): read it in place. Media in `img/`, `audio/`,
  `movies/`; encrypted copies are `.rpgmvp/.rpgmvo/.rpgmvm` (MV) or `.png_/.ogg_/.m4a_` (MZ).
- MV/MZ encryption: a 16-byte header (`RPGMV` + version), then only the first 16 bytes of the original file are
  XOR-ed with `encryptionKey` from `System.json`; the rest is plain. The routine ships in the core JS (MV
  `Decrypter` in `rpg_core.js`, MZ `Utils.decryptArrayBuffer`).
- XP–VX Ace: `Data/*.r*data` through rvpacker; `Graphics/` and `Audio/` loose or inside the archive (RGSSAD is an
  XOR stream: v1 for XP/VX with a fixed seed, v3 for VX Ace with the seed in its header).
- 2000/2003: `.xyz` images (zlib-compressed 8-bit), `.png`/`.bmp`, MIDI/WAV/MP3 in `Music/` and `Sound/`.
- `gst assets list` and `gst assets extract` don't cover RPG Maker. Loose audio: `gst assets audio --list "**/*.ogg"`.
- Saves sit in the game folder, so recon may miss them: MV `www/save/*.rpgsave` (LZ-String base64 JSON), MZ
  `save/*.rmmzsave` (zlib JSON), XP–VX Ace `Save*.r*data` (Marshal), 2000/2003 `Save##.lsd` (LCF).

## Runtime

Verdict `offline-ok` only.

- MV/MZ: NW.js starts several `Game.exe` processes and the game's JS runs in the renderer one (`gst trace ps`, then
  `--pid`). V8 tags small integers, boxes doubles and writes them from its interpreter or JIT code: scans and
  watches fit poorly. Prefer the save diff and the code. Devtools are usually off in shipped builds; turning them on
  edits `package.json`, so only in the `gst sandbox` copy, offline.
- XP/VX/VX Ace: `Game.exe` is 32-bit Ruby (MRI), which stores an Integer n as 2n+1: `gst trace scan 201` finds 100.
  `gst trace watch` needs a 64-bit process.
- 2000/2003: `RPG_RT.exe` is 32-bit; variables are plain int32 arrays, so `gst trace scan`/`next` find them directly.
- Saves around an action: `gst trace snapshot before --path "<install>\www\save"` (MZ: `save`), act in the game,
  `gst trace snapshot after --path "<same folder>"`, `gst trace diff before after`.

## Graphics

- MV/MZ: PIXI.js on WebGL inside NW.js, drawn by Chromium's GPU process (ANGLE → D3D11). `gst gfx capture` hooks
  only the process it launches, so expect "no graphics API registered". Read the renderer in the core JS instead
  (`Spriteset_*`, `Tilemap`, `WindowLayer`, `Bitmap`).
- XP–VX Ace and 2000/2003 composite in software and present through legacy APIs: not capturable. The RGSS
  reference and mkxp describe `Bitmap`/`Sprite`/`Window`; EasyRPG Player does the same for `RPG_RT`.

## Gotchas

- `gst decompile` says "no decompile backend for rpgmaker-…" → there is nothing to decompile → the JS and JSON are
  source; rvpacker and lcf2xml convert the rest.
- recon's next steps suggest `gst assets list` → unsupported for RPG Maker → read `data/`, `img/`, `audio/` in place.
- A single `Game.exe` with no `www/`, `js/` or `Data/` and with `.enigma1/.enigma2` sections → usually Enigma
  Virtual Box (a file bundler), but recon can't tell it from Enigma Protector → `static-only` and the exe counts as
  protected → don't unpack it; tell the user, the call is theirs.
- rvpacker or lcf2xml output appears in the game folder → `-o` or the working directory was wrong → always write
  into the workspace (the guard doesn't watch these tools).
- A behaviour differs from the core JS → a plugin replaced the method →
  `gst q grep "<Class>\.prototype\.<method> *="`; the plugin loaded last wins.
- Data JSON records are one long line each → `gst q grep` cuts hits at 200 characters → load the file
  (`ConvertFrom-Json` in PowerShell) and query fields.
- `Data.wolf` or `*.wolf` → Wolf RPG Editor, not RPG Maker (key `wolfrpg`, notes in `misc-engines.md`) → its
  `.wolf` encryption follows the general key rule below.
- 2003 games on a patched runtime (Maniacs Patch, DynRPG plugins) → commands beyond EasyRPG/liblcf → note it.

## Protections

- Keys: the toolkit accepts only keys already published by the community, with their source recorded. It never
  extracts a key from the executable or from memory, and never brute-forces. RPG Maker counts as public: MV/MZ use
  the encryption key in `data/System.json`; RGSSAD (XP/VX/VX Ace) uses a public algorithm.
- Record it in `game.json` → `keys` before decrypting, e.g.
  `{"scheme": "rpgmaker-mv-systemjson", "source": "data/System.json"}` or
  `{"scheme": "rgssad", "source": "<URL of the tool or of the algorithm's description>"}`.
- Steam DRM (`.bind`) or another protector on `Game.exe`/`RPG_RT.exe`: never unwrap it; the data folders stay
  readable. Enigma Virtual Box builds: see Gotchas.
- Full rules: `plugins/gst/guardrails.md`.

Adapted from universal-modder `skills/mod-any-game/references/engines/misc-engines.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
