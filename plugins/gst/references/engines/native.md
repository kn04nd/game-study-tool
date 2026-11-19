# Native engines — study guide

Covers the `gst recon` engine key `native` (proprietary C/C++ engines: exe and DLLs through Ghidra), plus the
`frostbite` and `forzatech` keys, which recon maps here (sections at the end).

## Identify

- `native` is recon's fallback ("no known engine signature", confidence 10): read RECON.md's other engine signals,
  middleware and imports first.
- recon reads the PE of the main exes and key modules: imports (APIs and middleware: `d3d11`, `d3d12`, `vulkan-1`,
  `opengl32`, `fmod`, `lua51`/`lua54`, `physx3_x64`, `bink2w64`, `steam_api64`), the CodeView PDB path (project and
  build-machine names), shipped `.pdb` files, version info and section names. `gst recon --deep` adds Detect It Easy
  (compiler, linker, packer, protector).
- By hand: `dumpbin /headers /imports <exe>`; FLOSS `floss -j --only static -L <exe>` (`gst tools install floss`);
  capa `capa -j <exe>` (tool table, not in the catalog).
- MSVC RTTI names survive without a PDB: `gst fmt strings <module> --grep "^\.\?AV"` lists `.?AVPlayer@game@@`-style
  class names (bounded to the first 64 MB and 10,000 strings).
- An embedded script VM shows as imports (`lua51.dll`) or as its error strings (`gst fmt strings <exe> --grep "<text>"`).

## Code

- `gst decompile native --list` tags modules `game` (analyzed by default: modules with a PDB, plus the exe of a native
  game), `+pdb`, `third-party` and `large` (over 64 MB: only when named).
- `gst decompile native [<module>...]`: `--max-functions N` (named functions first, the rest listed),
  `--functions "<regex>"` (re-export, no re-analysis), `--timeout <s>` per module (default 7200). Minutes for small
  DLLs; hours and GBs of RAM for 100 MB exes.
- Output: `decomp/ghidra/<module>/src/<namespace>.c`. With a PDB, a class file opens with its layout (field offsets,
  bases, vfptr). Each function starts with `// <module>+0x<RVA> Name`: cite it in FACTS.md as
  `module+0xRVA @ build <id>`. Call edges are exact; imports read `kernel32.CreateFileW`.
- Without a PDB, names are `FUN_<address>`, and Ghidra's RTTI class recovery isn't wired into gst (gap): record
  meanings in `notes/renames.json`.
- Interactive: `gst mcp add ghidra` (pyghidra-mcp over stdio, `--no-symbols`, read-only tools pre-approved), then
  `gst mcp call ghidra <tool> '<json>'`. Never while `gst decompile native` runs: both lock the Ghidra project.
- Optional, not in the catalog: ghidriff (`ghidriff old new`) to diff two builds; radare2 (`r2 -qc "iIj;iij;izzj"`).

Where to start (`gst q` over the Ghidra export):

| Question | Query |
| --- | --- |
| main loop | `gst q callers PeekMessageW` (message pump), `gst q callers QueryPerformanceCounter` (frame timing) |
| renderer setup | `gst q callers D3D11CreateDeviceAndSwapChain` (or `CreateDXGIFactory1`, `vkCreateInstance`) |
| files and formats | `gst q callers CreateFileW`, `gst q strings ".pak"`; magic strings (sample game: `gst q strings SGSV` → `sg.SaveWorld`) |
| checksums | `gst q callers <Crc32>` (sample: `sg.SaveWorld`, `sg.LoadWorld`) |
| class graph | `gst q hier <Class>` (sample: `sg::Entity` → `sg.Enemy`, `sg.Player`); `gst q outline SimBase` (ONI's `SimDLL.dll`) |
| script VM | callers of `luaL_loadbuffer` and `lua_pcall`, or of the VM's error strings |

- Optimized builds inline small methods: the sample game's `Entity::TakeDamage` lives inside `Enemy::Update`.

## Data and assets

- No `gst assets list`/`extract` here: find the containers from the code (`CreateFileW` callers, path strings), then
  `gst fmt scan <folder>`, `gst fmt id`, `gst fmt entropy` and the study-formats loop (`gst fmt new-reader`,
  `gst fmt roundtrip`; the sample game's saves are fully mapped, CRC checked).
- Audio of any engine: `gst assets audio --list` (FMOD, Wwise, CRI, XACT); Wwise `.pck` files only by pattern.
- Textures: `texconv -ft png -o <out> <in.dds>`. Zip-based archives (`gst fmt id` also spots a zip end record at the
  tail): `7z l -slt` / `7z x` into the workspace (`gst tools install 7zip`). Bink 2 needs RAD Video Tools (GUI);
  ffmpeg reads Bink 1.
- Before guessing a layout: ImHex-Patterns, the Kaitai gallery, the XeNTaX archive, ResHax, ModdingWiki.

## Runtime

- `gst trace scan <value>` → `gst trace next ...` → `gst trace watch scan`: each writer comes back as a function (from
  the Ghidra export), `module+0xRVA` and `file:line`. Sample game: 10 addresses held 100, 1 was left after damage;
  writes in `sg.Enemy.Update` (`SampleGame.exe+0x3bd6`, `Enemy.c:95`) and `sg.Player.Update` (regeneration).
- Data breakpoints stop after the write (gst looks up RIP-1). An export from another build is refused (sha256 check).
  64-bit processes only.
- PDB layouts turn an address into a field (`+0x34 int hp` in the sample's `Entity.c`); native objects don't move, so
  an address holds until the owner is freed (level reloads, respawns).
- Beyond gst, for `offline-ok` games only: x64dbg, WinDbg, Cheat Engine (guided; never its kernel driver), TTD
  recording and Procmon (both need UAC: guided), ReClass.NET for structs. Frida injects, so only in the `gst sandbox`
  copy: `frida-trace -p <pid> -i "kernel32.dll!CreateFileW"`.

## Graphics

- The imports name the API: `d3d11`, `d3d12`, `vulkan-1` and `opengl32` (3.2+ core only) are RenderDoc's; `d3d9` and
  legacy GL are not (apitrace from the tool table, not in gst).
- `gst gfx capture` runs the sandbox copy (`gst sandbox create` first). Sample game: D3D11, 2 passes (HDR scene with
  depth, post-process into the swap chain), 4 shaders, capture and summary in 8 s.
- Shader bytecode by hand: `fxc /dumpbin` (DXBC), `dxc -dumpbin` (DXIL), `spirv-cross --hlsl` (SPIR-V).

## Gotchas

- `no native game module recognized` → no PDB, and the exe isn't the recognized game exe or is too large → name it.
- A function you expect is missing → the optimizer inlined it → read its callers.
- Ghidra failed or crawls → `logs/ghidra-<module>.log`; one module at a time, a lower `--max-functions`.
- `gst trace watch` reports "outside any module" → JIT or script-VM code → find the field in the managed or script code.
- The watch names no function → the export is from another build, or missing → `gst decompile native <module>`.
- A module recon lists as protected is refused → it's never unpacked → decompile the clean DLLs instead.

## Protections

- PE heuristics: `.bind` (SteamStub) → `static-only`; `.vmp*` (VMProtect), `.themida`/`.winlice`/`.vm_sec` (Themida),
  `.enigma*`, `.arch`/`.xtls` (Denuvo heuristic), two `.text` sections (Arxan heuristic) or 3+ unnamed sections →
  anti-tamper → `static-only`; anti-cheat files, services or store data → `no-runtime`.
- recon records each finding in `recon/protections.md`; the alternatives are clean modules, data on disk, official
  tools and public source (Source SDK 2013 for TF2).
- Keys: only community-published keys with their source recorded; never extracted from the exe or memory, never
  brute-forced.
- Limits: `plugins/gst/guardrails.md`.

## Frostbite (`frostbite`)

- Identify: `Data/cas.cat` + `cas_NN.cas`, `Data/Win32/**/*.toc` and `*.sb` (superbundles), `layout.toc`,
  `initfs_Win32`, `Patch/`; SteamDB rules match `Engine.BuildInfo_Win64_retail.dll` and
  `Runtime_Win64_retail.BuildSettings`. recon confidence 90.
- Code: native C++; logic is largely data-driven EBX (typed asset graphs) whose type info lives in the exe.
- Data: CAS storage + TOC/SB superbundles (EBX, RES, chunks, custom texture formats). gst has no reader (gap):
  `gst fmt id` and `gst fmt entropy` on the `.toc` files diagnose them. Frosty Toolsuite (GUI, guided) reads EBX for the
  titles it supports; its SDK generation dumps the running game, so runtime rules apply.
- Protections: EA Javelin kernel anti-cheat (BF2042 and other EA titles; recon names it from `EAAntiCheat.*.exe`),
  earlier EAC, Denuvo → `no-runtime` or `static-only`: file listing and public docs (Frosty on GitHub, the
  Battlefront modding wiki).

## ForzaTech (`forzatech`)

- Identify: `media/zipmanifest.xml` (recon confidence 95) and `media/ziphashseeds.xml`; GDK builds add
  `MicrosoftGame.Config` (its `Version` attribute), `Game.ChunkMeta`, `xgameruntime.dll` and PlayFab DLLs. Forza
  Horizon 6 (Steam, GDK build) → `static-only` (Xbox account).
- Data: `media/*.zip` (Cinematics, Physics, Rendering, ShadersDXIL, UI...), loose plaintext XML/INI
  (`ControllerFFB.ini`, `DefaultOverlaysHierarchy.xml`...) and folders (`Cars`, `Tracks`, `Livery`): read the text in
  place.
- Zip entries use custom compression methods (community: 21 = LZX, 22 = encrypted): `7z l -slt <zip>`, or
  `gst fmt hex <zip> --length 32` (the method sits at offset 8 of a local header). Encrypted entries stay closed
  (rule 4); record which zips use them.
- Code: native exe; under `static-only`, only unprotected modules decompile and there is no runtime work.

Adapted from universal-modder `skills/mod-any-game/references/engines/native.md` and `big-frameworks.md` (Frostbite)
(MIT, Copyright (c) 2026 Rehan and universal-modder contributors).
