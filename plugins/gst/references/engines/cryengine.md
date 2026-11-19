# CryEngine / Lumberyard — study guide

Covers the `gst recon` engine key `cryengine`: Crytek's CryEngine 2-5 titles and Amazon Lumberyard games (O3DE is Lumberyard's open-source successor).

## Identify

- Engine modules in `Bin64/` (CryEngine 2-3), `bin/win_x64/` (CRYENGINE 5) or `Bin64vc14*/` (Lumberyard):
  `CrySystem.dll`, `Cry3DEngine.dll`, `CryAction.dll`, a renderer (`CryRenderD3D11.dll`, `CryRenderD3D12.dll`,
  `CryRenderVulkan.dll`) and a game DLL whose name varies by title.
- Data: `.pak` archives (zip) in `Game/`, `GameSDK/`, `Engine/` or `Data/` depending on the title;
  `Localization/*_xml.pak`; level paks under `Levels/<map>/`. Lumberyard adds `bootstrap.cfg`.
- `gst recon` matches `CrySystem.dll`; SteamDB's CryEngine rules (`Cry3DEngine.dll`, `CryD3DCompilerStub.dll`,
  `CryRender*.dll`, `Localization/English_xml.pak`) corroborate. Lumberyard's `bootstrap.cfg` rule only shows up in
  the rule list, and O3DE builds aren't fingerprinted.
- Version: the file version of `CrySystem.dll` (`(Get-Item <path>).VersionInfo.FileVersion`) and the Steam build id.
- Verdicts differ per title: AreWeAntiCheatYet lists Hunt: Showdown 1896 (Easy Anti-Cheat) and Crysis (PunkBuster)
  → `no-runtime`; a DRM-wrapped single-player exe → `static-only`; an unprotected single-player game → `offline-ok`.

## Code

- C++ game DLL on the Cry* modules, plus scripts that are often plain text:
  - Lua entity and AI scripts (`Scripts/Entities/**`, `Scripts/AI/**`); in CryEngine 3 layouts, `Entities/*.ent`
    (XML) binds entity classes to their scripts;
  - XML: entity archetypes, Flow Graph (visual scripting stored with the level data), Mannequin databases (`.adb`), UI;
  - Lumberyard adds Script Canvas graphs and Lua components.
- Plain-text scripts are data on disk at any verdict. Extract them into the workspace and index them:

```text
7z x "<install>\Game\Scripts.pak" -odecomp\cry-scripts "-ir!*.lua"   # pak name and folder vary by title
gst index
gst q outline BasicEntity             # e.g. a CryEngine 3 entity table: tables become classes, X:m() members
gst q callers OnInit
gst map
```

- Loose `.lua`/`.luac` in the install: `gst decompile lua` (copies text, runs unluac on Lua 5.x bytecode).
  Compiled Lua inside paks, only when the verdict allows decompiling: `java -jar <gst tools path unluac> <file>`
  (Java: `gst tools path temurin21`).
- Native modules (`offline-ok`, or unprotected ones under `static-only`): `gst decompile native --list`, then
  `gst decompile native <GameDll>.dll --max-functions 5000` (no PDB: `FUN_` names).
- Public source as reference: aws/lumberyard (archived 2022, AWS license; `dev/Code/CryEngine/` keeps CrySystem,
  Cry3DEngine, CryPhysics, CryNetwork and RenderDll from the CryEngine 3.8 lineage) and o3de/o3de (Apache-2.0 or
  MIT). The CRYTEK/CRYENGINE GitHub repository returns 404 (2026-10).

## Data and assets

- `.pak` = zip (`gst tools install 7zip`): `7z l -slt <pak> > dumps\<pak>.txt`, then `7z x <pak> -o<workspace dir>`.
  Typical tree: `Objects/` (`.cgf` static, `.cga` animated, `.chr`/`.skin` characters, `.cdf` character XML),
  `.mtl` XML materials, `Textures/` (DDS), `Animations/` (`.caf`, `.dba`, `.cal`, `.adb`), `Libs/`, `Levels/`.
- Not in the `gst tools` catalog yet: Cryengine-Converter 2.0.0 (`cgf-converter`, GPL-2.0). Use the standalone exe
  (the installer adds itself to PATH); get it with the user's OK; log version and command in STUDY.md.

```text
cgf-converter <file.cgf|.cga|.chr|.skin> -objectdir "<extracted root>" -glb -outdir "<ws>\assets\cry\models"
```

- `-objectdir` lets it resolve materials and textures; `-anim` adds animations; `-ut` joins split DDS files.
- Textures can be split into `x.dds` + `x.dds.1`, `x.dds.2`...: `-ut` joins them on model export; for other
  textures check the split with `gst fmt` before `texconv -ft png -o <out> <in.dds>`.
- Audio: FMOD in older titles, Wwise or FMOD Studio behind CryEngine 5's audio layer. Loose banks:
  `gst assets audio "<pattern relative to the install>" --list`; extracted ones by absolute file path.
- XML and Lua are text: read them after extraction. Unknown binaries: `gst fmt id`, `gst fmt entropy`, then
  `gst fmt new-reader` proved by `gst fmt roundtrip`.

## Runtime

- `offline-ok` titles, solo and offline: `gst trace scan` / `next` / `watch` (who writes a value) and
  `gst trace snapshot` / `diff` around saves; tools that inject run only from the `gst sandbox create` copy.
- `static-only` / `no-runtime`: no process work. The static route is the paks (scripts, XML, shader sources) plus
  the public engine source.
- Multiplayer modes and servers stay out of scope at any verdict.

## Graphics

- The renderer DLL names the API: `CryRenderD3D11.dll`, `CryRenderD3D12.dll`, `CryRenderVulkan.dll`. CryEngine 2
  titles (Crysis, Warhead) render with D3D9/D3D10, which RenderDoc can't capture.
- Capture only at `offline-ok`: `gst sandbox create`, `gst gfx capture` (`--manual` to get past menus),
  `gst gfx export` → `frame.md` (RenderDoc: D3D11, D3D12, Vulkan, OpenGL 3.2+ core).
- At any verdict, shader sources ship as text in a shaders pak: `.cfx` shaders, `.cfi` includes, `.ext`
  material-flag definitions (`Shaders/HWScripts/CryFX/` in Crysis' `Game/Shaders.pak`,
  `Engine/Shaders/HWScripts/CryFX/` in CRYENGINE 5). `.mtl` files name each material's shader, flags and textures.

## Gotchas

- 7-Zip can't open a pak, or lists garbage names → custom-encrypted or signed paks (some CryEngine 3+ titles:
  `gst fmt id` no longer says ZIP, `gst fmt entropy` stays flat near 8 bits/byte) → stop at the diagnosis (Protections).
- A converted model has no textures → cgf-converter didn't find the materials → pass `-objectdir` at the
  extracted root.
- Scripts vanished from `decomp/lua/` → `gst decompile lua` rebuilds that folder from the install on each run → keep
  pak-extracted scripts in `decomp/cry-scripts/`.
- `gst decompile lua` decompiles bytecode without checking the verdict in this version → on `no-runtime` titles, stop
  at plain-text scripts.
- `gst decompile` (auto) says `no decompile backend for cryengine` → name the backend: `lua` or `native <dll>`.
- `gst assets list` says it handles Unity, Godot and Unreal only → 7-Zip, cgf-converter and `gst fmt`; audio through
  `gst assets audio`.
- A Lumberyard game shows only the "CryEngine / Lumberyard" label → recon doesn't tell them apart → check
  `Bin64vc14*/` and `bootstrap.cfg` by hand.

## Protections

- Multiplayer titles carry anti-cheat (Easy Anti-Cheat, PunkBuster) → `no-runtime`. DRM wrappers or anti-tamper on
  single-player exes → `static-only`, with the protected modules listed in `recon/RECON.md` and never unpacked.
- Encrypted or signed paks: only a community-published key with its source URL recorded in game.json; never a key
  from the exe or memory, never brute force.
- `recon/protections.md` records them. Limits and reasons: [`plugins/gst/guardrails.md`](../../guardrails.md).
