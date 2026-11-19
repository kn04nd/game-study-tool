# Ren'Py — study guide

Covers the `gst recon` engine key `renpy` (Ren'Py 6–7 on Python 2, 8.x on Python 3).

## Identify

- `renpy/` (the engine, as Python source) + `game/` + `<Name>.exe`/`<Name>.py`; `lib/py3-windows-x86_64/` (8.x) or
  `lib/windows-i686`, `lib/py2-*` (6/7). recon evidence: `renpy/ + game/`, plus `archives` (count of `game/*.rpa`).
- `game/`: compiled scripts `*.rpyc` (sometimes the `*.rpy` source too), archives `*.rpa`, `cache/`, `saves/`,
  `tl/<language>/` (translations).
- Version: `renpy/vc_version.py`, or the version tuple in `renpy/__init__.py`.
- Signatures: a `.rpa` starts with `RPA-3.0 <index offset> <key>` (older: `RPA-2.0`); a `.rpyc` with `RENPY RPC2`
  (`gst fmt hex "game/script.rpyc" --length 32`).

## Code

    unrpa -l "<install>\game\archive.rpa"                    # list
    unrpa -mp decomp/renpy "<install>\game\archive.rpa"      # extract into the workspace (-m mkdir, -p path)
    python unrpyc.py -c decomp/renpy                         # every .rpyc under it → .rpy beside it

- unrpyc v2 (Python 3.9+) reads Ren'Py 6.18–8.x; before 6.99.10 add `--no-init-offset`; `-d` dumps the AST of a
  file that won't decompile. It writes next to its input: copy loose `game/*.rpyc` into `decomp/renpy/` first.
- Read `script.rpy` first (labels; `jump`/`call` draw the story graph), then `screens.rpy` (UI), `options.rpy` and
  `gui.rpy` (config), `define`/`default` (variables, flags, meters) and `init python:` blocks (the game's systems).
- The engine is open source: `renpy/` in the install, or github.com/renpy/renpy at the same version.
  `renpy/common/` holds the default screens and statements.
- `gst index` has no Ren'Py or Python parser: `gst q grep "^\s*(label|call|jump) " --glob "*.rpy"`. After one
  `gst index` (it creates the index even with nothing to parse), `gst q show decomp/renpy/<file>.rpy:<line>`.

## Data and assets

- Images, audio, video and fonts are plain files (PNG/WEBP, OGG/OPUS/MP3, WEBM, TTF/OTF), loose in `game/` or inside
  `.rpa`; after unrpa they need no conversion. Loose audio: `gst assets audio --list "game/**/*.ogg"`.
- A `.rpyc` is a zlib-compressed pickle of the script AST; `python` blocks keep their source text in it.
- Saves: `game/saves/` and `%APPDATA%\RenPy\<save_directory>\` (`config.save_directory` in `options.rpy`). A
  `.save` is a zip (`log` = the pickled game state and rollback, `screenshot.png`, `json`); `persistent` is a zlib
  pickle. Read pickles with `pickletools` (stdlib, it only disassembles): `pickle.load` would run code.
- `gst assets list` and `gst assets extract` don't cover Ren'Py. Unknown files: `gst fmt id "<file>"`.

## Runtime

Verdict `offline-ok` only.

- The state is Python objects: ints and floats are boxed and immutable (a change makes a new object), so
  `gst trace scan`/`next` rarely pin a value, and `gst trace watch` names the Python runtime, never a script line.
- What works: `gst trace snapshot before --path "<save folder>"`, act in the game,
  `gst trace snapshot after --path "<save folder>"`, `gst trace diff before after`; the decompiled script then
  explains the change.
- Ren'Py's own inspectors (console Shift+O, developer menu Shift+D) need `config.console` or `config.developer`:
  only in the `gst sandbox` copy, offline.

## Graphics

- Two renderers: `gl2` (OpenGL, normally a compatibility context, which RenderDoc does not capture) and `angle2`
  (ANGLE → D3D11, capturable from the sandbox with `gst gfx capture`). Shift+G in the game picks the renderer.
- Most of the visual logic is ATL transforms, layers and screens in the scripts; the shader parts are GLSL in the
  engine source.

## Gotchas

- `gst decompile` says "no decompile backend for renpy" → no gst backend yet → unrpa + unrpyc by hand, output in
  `decomp/renpy/`.
- recon's next steps suggest `gst assets list` → unsupported for Ren'Py → media are plain files after unrpa.
- `.rpy` files show up in the install's `game/` → unrpyc ran on the install → always work on copies.
- The guard refuses unrpa or rpatool → a key flag without a published key recorded in `game.json` → see Protections.
- unrpyc fails on every file → obfuscated `.rpyc` (a custom loader in `game/*.py` or early init code) → diagnose
  and record; `--try-harder` only when the user agrees.
- The same dialogue appears twice → `tl/<language>/` translations → grep the original-language files.
- `gst map` finds no systems → Ren'Py scripts aren't indexed → map labels and screens by hand in `notes/`.

## Protections

- Standard RPA is Ren'Py's own open-source container (its index key sits in plain text in the header), not a
  protection: unrpa reads it, plus several community-documented variants (`-f VERSION` forces one).
- A custom header, scheme or encrypted archive: the toolkit accepts only keys already published by the community,
  with their source recorded. It never extracts a key from the executable or from memory, and never brute-forces.
  Record such a key in `game.json` → `keys` and re-run `gst recon` (the guard reads the registry copy); otherwise
  diagnose with `gst fmt hex` and `gst fmt entropy`, and stop.
- Obfuscated `.rpyc`, DRM on the launcher exe: diagnose and record; the loose `game/` files and the public engine
  source remain.
- Full rules: `plugins/gst/guardrails.md`.

Adapted from universal-modder `skills/mod-any-game/references/engines/misc-engines.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
