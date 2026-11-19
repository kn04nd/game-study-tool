# Other engines — study guides

Compact guides for the `gst recon` engine keys `wolfrpg`, `idtech`, `clausewitz`, `redengine`, `love2d`, `defold`,
`cocos`, `hashlink`, `haxe` and `flash`.

Common to all of them:

- No gst backend, except `gst decompile lua` for loose Lua in the install: `gst decompile` stops with "no decompile
  backend for <key>", `gst assets list` with "handle Unity, Godot and Unreal 4/5". Extract with the tools below into
  `decomp/<engine>/` or `assets/<engine>/`, never next to the game files; log each command in STUDY.md.
- `gst index` parses `.lua`, `.java`, `.cs`, `.gd` and Ghidra's C. Other languages:
  `gst q grep "<regex>" --glob "*.<ext>"` and `gst q show <path>:<line>` (after one `gst index`). Text read in place
  with `rg` is cited as install-relative `path:line`.
- `gst fmt id`, `entropy`, `hex`, `strings` for unknown files; `gst assets audio` for audio containers of any engine;
  `gst trace snapshot` and `diff` for saves at any verdict; `gst trace scan`, `next`, `watch` on `offline-ok` games only
  (`watch` refuses 32-bit processes; JIT-compiled code shows up as "outside any module").
- "Not in catalog": install only with the user's OK ([tools.md](../tools.md)).
- Protections, all engines: encrypted content only with a key the community already published, its source URL recorded
  in `game.json` `keys`; never a key read out of the exe, the scripts or memory, never brute force.
  Limits: [guardrails.md](../../guardrails.md).

## Wolf RPG Editor (`wolfrpg`)

- **Identify:** `Game.exe` (x86, built on DxLib) with `Data.wolf` or per-folder archives (`Data/BasicData.wolf`,
  `MapData.wolf`, `Picture.wolf`…). Recon keys on `.wolf` files; a loose `Data/BasicData/` falls back to `native`.
  `gst fmt id` doesn't know `.wolf` (a DxLib archive).
- **Code:** event commands, not code: common events (`BasicData/CommonEvent.dat`), databases (`DataBase`, `CDataBase`,
  `SysDatabase`: `.dat` + `.project`), maps (`MapData/*.mps`), settings (`BasicData/Game.dat`). The free official
  WOLF RPG Editor of the same major version opens a decrypted `Data/` copy kept in the workspace.
- **Data:** WolfDec (not in catalog; usage in its README) unpacks `.wolf`; copy the archives into the workspace
  first so nothing lands next to the game files. Then `gst fmt` on the `.dat`/`.mps` files.
- **Runtime / graphics:** x86: `scan` and `next` only. DxLib uses Direct3D 9 or 11 depending on its version
  (`gst fmt strings Game.exe --grep "d3d(9|11)"`); D3D9 can't be captured.
- **Protections:** `.wolf` encryption uses per-version keys; WolfDec's built-in keys are published in its repository
  (record that URL in `keys` first). Games protected with a developer-chosen key (WOLF RPG Editor Pro): diagnose only,
  unless the community published that key. Recon doesn't flag `.wolf` encryption: record it yourself.

## id Tech (`idtech`)

- **Identify:** classic `*.wad` (`IWAD`/`PWAD`), Quake `id1/pak0.pak` (`PACK`), Quake III `*.pk3` and Doom 3
  `base/*.pk4` (zips); id Tech 6–8 (DOOM 2016, Eternal, The Dark Ages): `base/*.resources` with `.streamdb` files and
  `packagemapspec.json`. Recon scores it 70; a lone `*.wad` also matches GoldSrc texture WADs (`WAD3`, Half-Life).
- **Code:** classics: read id Software's GPL releases (github.com/id-Software: DOOM, Quake, Quake 2, Quake III Arena,
  DOOM 3 BFG, quake2-rerelease-dll) before any binary; clone them into `decomp/` for `gst q grep` (C is indexed only
  when Ghidra wrote it). Modern: native C++, usually Denuvo (`static-only`); gameplay is declared in text decls and
  `.entities` inside `.resources` (DOOM Eternal) or binary entities (The Dark Ages). idStudio, the official DOOM
  Eternal editor, is the ground truth.
- **Data:** WAD lumps behind a 12-byte header; pk3/pk4: `7z x <file> -o<ws dir>`; DOOM Eternal `.resources`
  (Oodle): EternalResourceExtractor (not in catalog) `EternalResourceExtractor <file.resources> <ws dir> -f <filter>`.
  It loads the game's Oodle DLL, which the toolkit allows only in place, never as a copy: check how it finds the DLL
  first. The Dark Ages: EntityAtlan's "Extractor" release (never its mod loader).
- **Runtime / graphics:** classic games run in open-source ports: study the port's code rather than a process.
  Modern titles are Vulkan and usually `static-only`.
- **Protections:** Denuvo anti-tamper on modern titles (`gst recon --deep` names it): data and decls only.

## Paradox Clausewitz / Jomini (`clausewitz`)

- **Identify:** `common/`, `events/`, `interface/*.gui`, `localisation/` and `launcher-settings.json` (recon: 90);
  older titles through SteamDB's `common/defines` rule (60). Jomini games (CK3, Victoria 3) keep scripts under `game/`.
  Victoria 2: `victoria2.exe` is only a .NET launcher; the game is `v2game.exe` (x86).
- **Code:** script, not code: PDX script text (triggers, effects, scopes) in `common/`, `events/`, `decisions/`,
  `history/`; GUI in `interface/*.gui` plus `.gfx` sprite definitions; constants in `common/defines`. Read it in
  place with `rg` (older games are Windows-1252: `rg --encoding windows-1252`). Triggers and effects are implemented
  in the engine: the official wikis list them, and newer Jomini games print them with the `script_docs` console command.
- **Data:** localisation (Victoria 2 era: `;`-separated CSV in cp1252; Jomini: `*_l_<lang>.yml`, UTF-8 with BOM);
  map `map/provinces.bmp` (one colour per province) + `definition.csv` (`id;r;g;b;name;x`) + `default.map`; textures
  `.dds`/`.tga` (`texconv`); meshes `.mesh` (binary). Saves: text, or zips whose payload may be binary tokens
  (ironman); jomini (Rust library) reads both.
- **Runtime / graphics:** user data in `Documents/Paradox Interactive/<Game>/` (`save games/`, `logs/error.log`);
  recon doesn't find it: `gst trace snapshot before --path "<that folder>"`. `gst gfx capture --exe v2game.exe` skips a
  launcher. x86 titles: no `watch`.
- **Protections:** none in single player; the multiplayer/ironman checksum only identifies modified game files.

## CD Projekt REDengine (`redengine`)

- **Identify:** Cyberpunk 2077: `archive/pc/content/*.archive` (`RDAR`), `bin/x64/Cyberpunk2077.exe`, `r6/cache/`
  (`final.redscripts`, `tweakdb.bin`). The Witcher 3: `bin/x64/witcher3.exe` (`bin/x64_dx12/` in the next-gen build),
  `.bundle` and `.cache` files under `content/`.
- **Code:** native C++ exe plus scripts. The Witcher 3 ships its scripts as WitcherScript source (`.ws` under
  `content/content0/scripts/`): copy them into `decomp/w3scripts/` for `gst q grep`. Cyberpunk 2077 runs compiled
  redscript (`r6/cache/final.redscripts`); the official REDmod tools (free DLC) install the script and TweakDB sources
  under `tools/redmod/`: read those first. Official editor for The Witcher 3: REDkit.
- **Data:** WolvenKit (not in catalog; CLI `WolvenKit.CLI`, see its `--help`) lists and exports archives, bundles and
  TweakDB records into the workspace. Audio is Wwise (`.wem`, `.bnk`) inside the containers: `gst assets audio` on the
  exported files (absolute paths).
- **Runtime / graphics:** single player, x64. Cyberpunk 2077 renders with D3D12, The Witcher 3 with D3D11 (D3D12 in
  next-gen): both capturable from the sandbox when `offline-ok`.
- **Protections:** usually none (GOG builds are DRM-free); recon checks the Steam exes for wrappers.

## LÖVE (`love2d`)

- **Identify:** `love.dll`, `lua51.dll` (LuaJIT), `SDL2.dll`, `OpenAL32.dll`. The game is a zip: `game.love`, or appended
  to the exe ("fused": `gst fmt hex <game>.exe --offset -22 --length 22` shows the zip end record `50 4b 05 06`).
  LÖVE version: `t.version` in `conf.lua`.
- **Code:** Lua source (sometimes LuaJIT bytecode, `1b 4c 4a`) inside the zip. `7z x -tzip <game>.exe -o<ws>/decomp/love`
  (`-tzip` makes 7-Zip open the appended zip, not the PE; `gst tools install 7zip`), then `gst index`: `gst q def`,
  `refs`, `callers` cover Lua. Keep it out of `decomp/lua/`: `gst decompile lua` owns that folder and scans only the
  install, where a fused game shows no Lua. Bytecode: luajit-decompiler-v2 (not in catalog) `<in> -o <out> -s -f`
  under a timeout (it can open modal dialogs). Read first: `conf.lua`, then `main.lua` (`love.load`, `love.update(dt)`,
  `love.draw`) and its `require` graph.
- **Data:** images, sounds and maps (often Tiled `.lua`/`.tmx`) in the zip. Saves: `%APPDATA%/LOVE/<identity>`
  (fused games: `%APPDATA%/<identity>`), `t.identity` in `conf.lua`.
- **Runtime / graphics:** Lua numbers are doubles (`gst trace scan <v> --type double`); `watch` hits land in `lua51.dll`
  (the VM), so map them back through the Lua source. OpenGL: RenderDoc captures only 3.2+ core contexts.
- **Protections:** usually none; obfuscated or custom-encrypted Lua → diagnose.

## Defold (`defold`)

- **Identify:** `game.arcd` (data), `game.arci` (index), `game.dmanifest`, `game.projectc` next to the engine exe. The
  archive format is public: `engine/docs/ARCHIVE_FORMAT.md` in the Defold repository.
- **Code:** compiled Lua (LuaJIT bytecode on desktop) inside `game.arcd`, LZ4 per entry. arcdEx (Java, AGPL, not in
  catalog; Java from `gst tools path temurin21`): `java -jar arcdEx.full.jar archive -l game.arcd` lists; extraction
  takes `--outdir=<ws>/decomp/defold` and `--extract-lua` (check its README). Bytecode: luajit-decompiler-v2, as
  for LÖVE.
- **Data:** compiled resources are protobuf (`.collectionc`, `.goc`, `.texturec`…): decode them with the `.proto`
  files of Defold's public engine source. `game.projectc` holds the project settings as text.
- **Runtime / graphics:** the LuaJIT VM as for LÖVE (doubles; `watch` lands in the engine). OpenGL by default,
  Vulkan optional.
- **Protections:** an index entry can carry an encryption flag → check whether its key is the one published in Defold's
  engine source; a game-specific key compiled into the exe → diagnose only.

## Cocos2d-x / Cocos Creator (`cocos`)

- **Identify:** `libcocos2d.dll` (2d-x) with `src/` (`.lua`/`.luac` or `.js`/`.jsc`) and `res/`; Creator 2.x:
  `src/settings.js`, `main.js`, `assets/<bundle>/config.json` with uuid-named `import/` and `native/`; Creator 3.x:
  `application.js`, `cocos-js/`. Creator games often ship as web builds in an Electron/NW.js shell: see
  [html5.md](html5.md). Version: `gst fmt strings libcocos2d.dll --grep "cocos2d-x"`.
- **Code:** plain Lua in the install: `gst decompile lua` (copies it, decompiles standard Lua bytecode, indexes). Plain
  JS: copy into `decomp/cocos/` and unpack with wakaru or webcrack as in html5.md. `.luac`/`.jsc` that start with a
  signature (often `XXTEA`) and look random are encrypted.
- **Data:** `res/`: plist + PNG atlases, `.ccbi` (CocosBuilder), `.csb` (Cocos Studio); Creator: uuid trees mapped by each
  bundle's `config.json`.
- **Runtime / graphics:** OpenGL (2d-x) or WebGL inside a browser shell: capture limits as for LÖVE or html5.md.
- **Protections:** XXTEA-encrypted scripts and `.pvr.ccz` textures use a key compiled into the binary → diagnose only.

## Haxe: HashLink (`hashlink`) and hxcpp / OpenFL / Lime (`haxe`)

- **Identify:** `hashlink`: `hlboot.dat` (bytecode, magic `HLB`) + `libhl.dll` + `*.hdll` native libraries (`sdl.hdll`,
  `directx.hdll`, `fmt.hdll`…); Heaps games pack resources in `res.pak`. `haxe`: `lime.ndll`/`lime.hdll` without
  `hlboot.dat`: Haxe compiled to C++ (hxcpp) or HL/C, so the exe is native code.
- **Code:** crashlink (Python, not in catalog): `crashlink hlboot.dat -c funcs` lists the functions; its REPL
  (`crashlink hlboot.dat`, then `decomp <index>`) decompiles to pseudo-Haxe; hlbc-cli gives a second opinion (see its
  help). Keep outputs in `decomp/hashlink/` (`gst q grep`; Haxe isn't indexed). crashlink's stdio MCP mode
  (`crashlink mcp`) has no `gst mcp` template. `haxe` games: `gst decompile native <Game>.exe`.
- **Data:** `res.pak` (Heaps) → `gst fmt id` and `hex`; OpenFL/Lime: loose `assets/` and `manifest/*.json`.
- **Runtime / graphics:** HashLink JIT-compiles the bytecode: `watch` hits are "outside any module"; map them through
  crashlink's function index. The renderer follows the `.hdll`: `directx.hdll` (D3D11, capturable) or `sdl.hdll` (OpenGL).
- **Gotcha:** `.hdll`/`.ndll` are DLLs under another extension, which `gst decompile native` doesn't list.
- **Protections:** a custom-encrypted `res.pak` → diagnose only.

## Adobe Flash / AIR (`flash`)

- **Identify:** `*.swf` (`FWS` plain, `CWS` zlib, `ZWS` LZMA; byte 3 is the SWF version); AIR adds
  `META-INF/AIR/application.xml` (its namespace gives the AIR SDK version), a captive runtime (`Adobe AIR/` with
  `Adobe AIR.dll`) and native extensions (`META-INF/ANE`). Recon reports `flash` for up to 30 SWFs (more usually means
  an engine's UI, e.g. Scaleform). A projector exe carries its SWF at the end: `gst fmt hex <game>.exe --offset -8`
  shows `56 34 12 fa` and the SWF length.
- **Code:** ActionScript 2 or 3 in the SWF → JPEXS FFDec (not in catalog; Java 8+, e.g. `gst tools path temurin21`):
  - `ffdec-cli -dumpAS3 game.swf` lists the classes;
  - `ffdec-cli -format script:as,image:png -export script,image,sound,text decomp/flash game.swf` exports them;
  - `ffdec-cli -swf2xml game.swf dumps/game.xml` gives the tag structure.

  `.as` isn't indexed: `gst q grep`. Read first: the document class and its frame scripts.
- **Data:** shapes, bitmaps, sounds, fonts and `binaryData` (embedded levels, XML) in the SWF; external SWF/XML loaded at
  runtime (`gst q grep "URLRequest|Loader"`).
- **Runtime / graphics:** an AIR captive-runtime exe is the game's own process (`gst trace`; x86 builds: no `watch`;
  AVM2 JIT code is "outside any module"). Ruffle (open-source player) runs a bare SWF offline to watch behaviour, but
  `gst trace` doesn't target it (not started from the install). Captures rarely help: read the display list in the SWF.
- **Protections:** SWF encryptors and obfuscators (secureSWF, DoSWF; a small loader that decrypts a large `binaryData`
  blob at runtime) → diagnose only: the key lives in the loader.

Adapted from universal-modder `skills/mod-any-game/references/engines/misc-engines.md`,
`skills/mod-any-game/references/engines/big-frameworks.md` and
`knowledge/games/victoria-2/south-central-gang-total-conversion.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
