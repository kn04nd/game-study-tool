# GameMaker — study guide

Covers the `gst recon` engine key `gamemaker` (GameMaker: Studio 1.x, GameMaker Studio 2 and the 2022+ releases).

## Identify

- `data.win` next to the runner exe (`game.unx`, `game.ios`, `game.droid` on other platforms): an IFF `FORM` with
  chunks `GEN8 OPTN STRG SPRT TPAG TXTR ROOM OBJT CODE VARI FUNC SHDR AUDO …`. Also `options.ini`,
  `audiogroup*.dat`, loose `*.ogg` (streamed music) and the game's included files.
- `gst recon` reports `form` (False = not an IFF container: custom or encrypted) and `bytecode_version`
  (GEN8 byte 17). `UndertaleModCli info "<install>\data.win" -v` prints the full GEN8 (IDE and bytecode versions).
- **VM or YYC** decides the route:
  - VM: GML bytecode in `CODE`/`FUNC`/`VARI`; UTMT decompiles it.
  - YYC: GML compiled to C++ inside the exe. `CODE` is empty, UTMT warns that the code is inaccessible, and the
    exe is much larger than a stock runner.
- GMRT (the new runtime): on-disk layout and UTMT support are unconfirmed. Record what
  `gst fmt scan "<install>"` shows and stop there.

## Code

VM builds: UndertaleModTool's CLI (UTMT 0.9.2.0, GPL). It is not in the gst catalog: install it from the GitHub
release zip. Only `info` and `dump` are read-only; `load -o`, `replace`, `new` and `project build` write game files.

    UndertaleModCli info "<install>\data.win" -v
    UndertaleModCli dump "<install>\data.win" -c UMT_DUMP_ALL -s -o decomp/gml   # every code entry + strings
    UndertaleModCli dump "<install>\data.win" -t --sprites -o assets/gamemaker    # texture pages, sprites

- Code entries are named by owner and event: `gml_Object_<obj>_<Event>_<n>`, `gml_Script_<name>`,
  `gml_GlobalScript_<file>` (2.3+ script files that hold functions), `gml_Room_<room>_Create` and
  `gml_RoomCC_<room>_<id>_Create` (room and instance creation code). Events: `Step_0/1/2` = step/begin/end,
  `Draw_0` = draw, `Draw_64` = Draw GUI, `Other_10` and up = user events.
- Read first: the first room in GEN8's room order and its creation code, the persistent controller objects
  (`Create_0`, `Step_0`), the player object, then the `gml_GlobalScript_*` libraries. Text the player sees →
  the `-s` strings dump → `gst q grep` for it.
- `gst index` has no GML parser: search with `gst q grep "<regex>" --glob "*.gml"`. Run `gst index` once (it creates
  the index even with nothing to parse); then `gst q show decomp/gml/<file>.gml:<line>` prints the lines around a hit.
- Semantics (events, instances, `with`, alarms, surfaces, `global`): the official manual, manual.gamemaker.io.

YYC builds are native code: `gst decompile native --list`, then `gst decompile native <Game>.exe` (big exes:
`--max-functions 5000`, `--functions "<regex>"`). YYC usually keeps script and object names as strings
(`gml_Object_…`, `gml_Script_…`) referenced next to the compiled functions: after `gst mcp add ghidra`, its
`search_strings` and `list_xrefs` tools lead from a name to its function. Record the names in
`notes/renames.json`. The assets stay readable in `data.win`.

## Data and assets

- `data.win` holds sprites and texture pages (`SPRT`/`TPAG`/`TXTR`), rooms with their instances (`ROOM`), objects
  with parent and events (`OBJT`), fonts, paths, timelines, shaders (`SHDR`, with source text), strings (`STRG`)
  and embedded sounds (`AUDO`). The UTMT GUI browses rooms and objects (guided).
- `audiogroup*.dat` are more `FORM` files with `AUDO` data. Streamed music is loose `.ogg`:
  `gst assets audio --list "**/*.ogg"` lists the streams with their durations.
- `options.ini` and the included files sit next to the exe as plain files: read them in place.
- `gst assets list` and `gst assets extract` don't handle GameMaker. Unknown included files: `gst fmt id "<file>"`.
- Saves usually go to `%LOCALAPPDATA%\<project name>\` (ini, JSON, `buffer_save` binaries). recon lists that folder
  only when its name matches the game. Formats: the study-formats skill (`gst fmt diff` between two saves).

## Runtime

Verdict `offline-ok` only.

- GML numbers are doubles: `gst trace scan 100 --type double`, then `gst trace next 87` (or `--decreased`).
- VM builds: `gst trace watch scan` names the runner's interpreter, not the GML. Find the writers of the variable
  in the dump instead: `gst q grep "<variable> *[-+*/]?=" --glob "*.gml"`. YYC builds: the hit is the compiled
  event function, named once the YYC names are mapped.
- Older 32-bit runners: `gst trace watch` needs a 64-bit process; scan and next still work.
- Saves around an action: `gst trace snapshot before --path "<save folder>"`, act in the game,
  `gst trace snapshot after --path "<save folder>"`, `gst trace diff before after`.

## Graphics

- GameMaker Studio 2 and later draw with D3D11 on Windows: `gst sandbox create`, then `gst gfx capture` (add
  `--exe <runner>.exe` if recon picked another exe). GameMaker: Studio 1.x used D3D9: not capturable.
  `gst recon` lists the imported graphics APIs.
- A frame is the application surface plus user surfaces (render targets), sprites batched from texture pages,
  Draw then Draw GUI. Shaders are written in GLSL ES and cross-compiled for D3D11; `SHDR` keeps their source
  text, so read it before any disassembly.

## Gotchas

- `gst decompile` says "no decompile backend for gamemaker" → no gst backend yet → UTMT by hand (VM) or
  `gst decompile native` (YYC).
- recon's next steps suggest `gst assets list` → it handles only Unity, Godot and Unreal → UTMT
  `dump -t --sprites` for images, `gst assets audio` for sound.
- The dump has no code, or UTMT says the code is inaccessible → YYC build → the native route above.
- UTMT rejects the file or its bytecode version → a runtime newer than the UTMT build (0.9.2.0 reads up to the
  2026.1 betas), or GMRT → record the GEN8 versions and stop.
- Output appears next to `data.win` → `-o` was missing → always pass a workspace folder (the guard doesn't watch
  UTMT).
- `gst map` finds no systems → GML isn't indexed → group objects and scripts by name prefix (`obj_`, `scr_`) in
  `notes/` by hand.
- UTMT's MCP fork exposes write tools → `gst mcp` and the guard reject it, even in a hand-written `.mcp.json` →
  work from the dump.

## Protections

- YYC is not a protection, only native code: decompile it within the verdict (`static-only` allows unprotected
  modules only).
- Steam DRM (`.bind`) or another protector on the runner: never unwrap it; `data.win` stays readable.
- A `data.win` without `FORM` (custom container or encryption): diagnose with `gst fmt entropy` and `gst fmt hex`.
  The toolkit accepts only keys already published by the community, with their source recorded. It never extracts
  a key from the executable or from memory, and never brute-forces.
- Alternatives: the assets in `data.win`, the included files, the GameMaker manual, UTMT's documentation.
- Full rules: `plugins/gst/guardrails.md`.

Adapted from universal-modder `skills/mod-any-game/references/engines/misc-engines.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
