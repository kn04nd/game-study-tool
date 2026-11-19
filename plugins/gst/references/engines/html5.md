# HTML5: Electron, NW.js and web engines — study guide

Covers the `gst recon` engine key `electron`: Electron and NW.js shells and the web engines inside them (Construct,
Phaser, PixiJS). RPG Maker MV/MZ also runs on NW.js but has its own guide: [rpgmaker.md](rpgmaker.md).

## Identify

- Electron: `resources/app.asar` (+ `resources/app.asar.unpacked/`) or a loose `resources/app/`; Chromium files beside
  the exe (`*.pak`, `v8_context_snapshot.bin`, `LICENSES.chromium.html`, `LICENSE.electron.txt`).
- NW.js: `nw.dll`, with the app as `package.nw` (a zip), as a zip appended to the exe
  (`gst fmt hex <game>.exe --offset -22 --length 22` shows the zip end record `50 4b 05 06`), or as loose
  `package.json` + HTML next to the exe.
- Inside: Construct 3 (`scripts/c3runtime.js` + `data.json`), Construct 2 (`c2runtime.js` + `data.js`), Phaser
  (`phaser*.js`, `Phaser.VERSION`), PixiJS (`PIXI.VERSION`). RECON.md's engine details flag `construct` and `phaser`.
- Recon reports `electron` (90) for `app.asar`, `resources/app/package.json`, `package.nw` or `nw.dll`. Versions: the
  exe's ProductVersion (Electron or NW.js) and `package.json` (`name`, `main`, NW.js `chromium-args`).

## Code

- The logic is JavaScript inside `app.asar`, `package.nw` or `www/`. Extract it into the workspace:
  - `npx @electron/asar@4.3.1 list "<install>/resources/app.asar"`, then
    `npx @electron/asar@4.3.1 extract "<install>/resources/app.asar" decomp/js/app` (Node 22.12+; never `pack`);
  - NW.js: `7z x -tzip "<install>/package.nw" -o<ws>/decomp/js/app` (`-tzip` also opens a zip appended to the exe;
    `gst tools install 7zip`).
- Source maps first: `//# sourceMappingURL=` comments or `*.map` files carry the original sources (`sourcesContent`).
- Bundles: wakaru (not in catalog) unpacks webpack, esbuild and Browserify bundles and unminifies them:
  `npx wakaru@1.13.0 decomp/js/app/<bundle>.js --unpack -o decomp/js/unpacked` (it refuses a non-empty output folder
  without `--force`). obfuscator.io output: webcrack (not in catalog; Node 22 or 24)
  `webcrack decomp/js/app/<bundle>.js -o decomp/js/webcrack`.
- `gst index` doesn't parse JavaScript yet: `gst q grep "<regex>" --glob "*.js"`, then
  `gst q show decomp/js/<file>.js:<line>` (run `gst index` once first).
- Read first: `package.json` `main` → the main process (window, IPC, file I/O, saves); then the renderer page
  (`index.html` → the game script). Construct: `data.json` holds layouts, object types and the compiled event sheets
  (read it as JSON). Phaser: each scene's `preload`, `create` and `update`.
- Native addons (`app.asar.unpacked/**/*.node`) are DLLs, but `gst decompile native` lists only `.exe`/`.dll`.

## Data and assets

- Images, audio (`.ogg`, `.m4a`, `.webm`), JSON levels and fonts sit unencrypted in the archive:
  `gst fmt scan decomp/js/app` surveys them; `gst fmt id` for anything odd.
- Saves: browser storage in the user-data folder (Electron `%APPDATA%/<app name>/`, NW.js
  `%LOCALAPPDATA%/<app name>/User Data/Default/`): `Local Storage/leveldb/`, `IndexedDB/`, or plain files written by
  the main process. Diff them around a save: `gst trace snapshot before --path "<folder>"`, save, `after`, then
  `gst trace diff before after`.
- Audio banks are rare here: `gst assets audio --list` finds them if present.

## Runtime

- Several processes (main, renderers, GPU): `gst trace ps`, then `--pid`. Game values live in V8's heap (doubles and
  tagged small integers): memory scans are awkward, and `watch` hits land in JIT code ("outside any module").
- DevTools reads the JS state directly: start the game with `--remote-debugging-port=9222` (a Chromium switch that
  listens on 127.0.0.1; `http://127.0.0.1:9222/json` lists the targets). `offline-ok` games only; read state and set
  breakpoints, never edit it. Some apps ignore or disable the switch.

## Graphics

- Chromium draws in its GPU process through ANGLE (D3D11 on Windows). `gst gfx capture` doesn't follow child processes,
  so a normal launch registers no API; `--args "--in-process-gpu"` keeps the GPU thread in the main process (untested
  with gst).
- `frame.md` then shows ANGLE's translated shaders (DXBC disassembly); the game's own WebGL shaders are GLSL strings in
  the JS (`gst q grep "gl_FragColor|precision mediump"`). Canvas 2D games draw through Skia, without shaders of their own.

## Gotchas

- `gst decompile` stops with "no decompile backend for electron (js)" → extract as above.
- `gst assets list` stops ("handle Unity, Godot and Unreal 4/5") → the assets are already plain files in the archive.
- `asar` fails → Node older than 22.12 (webcrack needs Node 22 or 24).
- Files missing from the extracted archive → they sit in `app.asar.unpacked/` (native modules, large media).
- `gst fmt id` says PE for an NW.js exe → the head wins; the appended zip shows at the tail (`--offset -22`).
- `.jsc` files (bytenode) or V8 snapshots instead of JS → compiled V8 bytecode: no decompiler, diagnose only.

## Protections

- ASAR integrity checks and Electron fuses: diagnose only (flipping a fuse modifies the binary).
- javascript-obfuscator output (string arrays, rotated): webcrack and wakaru clean up the game's own code.
- Encrypted assets: only with a key the community already published, its source URL recorded in `game.json` `keys`;
  never a key read out of the game's code or memory, never brute force. RPG Maker MV/MZ's `System.json` key is the
  documented exception ([rpgmaker.md](rpgmaker.md)).
- Account-bound or online web games: recon's verdict decides; runtime only offline.
- Limits: [guardrails.md](../../guardrails.md).

Adapted from universal-modder `skills/mod-any-game/references/engines/misc-engines.md` (MIT, Copyright (c) 2026 Rehan
and universal-modder contributors).
