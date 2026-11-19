# XNA / FNA / MonoGame and other .NET games — study guide

Covers the `gst recon` engine keys `xna-fna` (XNA, FNA, MonoGame) and `dotnet` (any other managed game).

## Identify

- A managed exe (`.NET` in RECON.md's executables list) plus a framework:

| Framework | Files | Notes |
| --- | --- | --- |
| XNA 4.0 | `Microsoft.Xna.Framework*.dll` | often only in the GAC (XNA redist): then `Content/*.xnb` is the tell; always 32-bit |
| FNA | `FNA.dll`, `FNA3D.dll`, `SDL2.dll`/`SDL3.dll`, `FAudio.dll` | XNA's API reimplemented; recent games are 64-bit .NET |
| MonoGame | `MonoGame.Framework.dll` (+ `SDL2.dll` for DesktopGL) | XNB or raw content files |

- recon: `xna-fna` (confidence 95) from those DLLs or `Content/*.xnb`; `dotnet` (70) for any other managed exe near the
  root when no Unity or Godot signal exists.
- .NET 5+ apps: `<Game>.runtimeconfig.json` + `<Game>.deps.json`, a native apphost `<Game>.exe`, the code in
  `<Game>.dll`. .NET Framework apps: the exe is the assembly.
- XNB header: `XNB` + platform (`w` = Windows) + version (5 = XNA 4.0) + flags (`0x80` LZX, `0x40` LZ4 in MonoGame).
- User data: `Documents\My Games\<Game>`, `%APPDATA%\<Game>`, `Saved Games\<Game>`: recon lists the ones that exist.

## Code

- `gst decompile` (dotnet backend, ilspycmd) → `decomp/cs/<Assembly>/`, with PDB variable names when `<name>.pdb` ships,
  and indexes the result. It searches the install root, `bin/`, `x64/`, `managed/` and subfolders with a
  `*.runtimeconfig.json`.
- Game assemblies: the projects in `<Game>.deps.json`, else every assembly outside the framework list (`FNA`,
  `MonoGame.*`, `Microsoft.Xna.*`, `System.*`, Steamworks, Harmony...). `gst decompile --list` shows the split;
  `gst decompile dotnet FNA` adds the framework when its behavior matters.
- Single-file bundle (an exe and no game `.dll`): gst finds nothing (gap). `ilspycmd -d -o dumps/bundle <Game>.exe`
  dumps the bundled assemblies; decompile one with
  `ilspycmd -p --nested-directories -r dumps/bundle -o decomp/cs/<Name> dumps/bundle/<Name>.dll`, then `gst index`.
- NativeAOT (no IL anywhere) → native code: `gst decompile native --list`.

Where to start:

- `Program.Main` → `new <Game>().Run()`. The `Game` subclass is the main loop: `Initialize`, `LoadContent`,
  `Update(GameTime)`, `Draw(GameTime)` → `gst q grep ":\s*(Microsoft\.Xna\.Framework\.)?Game\b" --glob "*.cs"`, then
  `gst q callees <Game>.Update`.
- Screen and state managers hang off `Update`/`Draw`; static fields often hold the world (`gst q refs <Type>`).
- Content: `Content.Load<T>("name")` → `gst q grep "Load<\w+>\(" --glob "*.cs"`. Custom `ContentTypeReader<T>`
  classes define the game's own XNB formats (`gst q grep "ContentTypeReader<" --glob "*.cs"`).
- Saves: `gst q strings ".sav"` or `"save"`, then the writer. `XmlSerializer`, `BinaryFormatter` (MS-NRBF),
  `BinaryWriter` and JSON serializers are public formats: map the game's types onto them.

## Data and assets

- `gst assets list` and `gst assets extract` don't cover XNA or .NET games (gap): read loose files in place (PNG, OGG,
  XML, JSON, TMX...); `gst fmt scan Content` surveys the rest.
- XNB: xnbcli from the tool table (not in the gst catalog): `xnbcli unpack <input> <output>` with the output under
  `assets/`. StardewXnbHack is the Stardew Valley-only alternative.
- XACT audio: `.xwb` wave banks → `gst assets audio --list`, then `gst assets audio "Content/**/*.xwb"`; `.xsb` (cues)
  and `.xgs` (global settings) are tables.
- Fonts (`SpriteFont`) and effects (compiled D3D9 shaders) are XNB files too.

## Runtime

- JIT code: `gst trace watch` hits read "outside any module" → find the field in the C# (`gst q refs <Field>`).
- The .NET GC compacts: an address from `gst trace scan` can move after a collection → scan again right before
  `gst trace watch` and keep the window short (`--seconds`).
- XNA 4.0 games are 32-bit: scans work, but `gst trace watch` refuses WoW64 processes.
- `gst trace snapshot before` / `after` + `gst trace diff` around a save; the changed bytes go to study-formats.
- dnSpyEx (GUI, guided) steps through managed code as a debugger: like every runtime tool, `offline-ok` games only.

## Graphics

| Framework | API | `gst gfx capture` |
| --- | --- | --- |
| XNA 4.0 | Direct3D 9 | no: RenderDoc has no D3D9; apitrace (tool table, not in gst) traces it from the sandbox |
| FNA (FNA3D) | Vulkan, D3D11 or OpenGL core | yes |
| MonoGame WindowsDX | D3D11 | yes |
| MonoGame DesktopGL | OpenGL without a core profile | usually no |

- `gst gfx capture` runs the sandbox copy (`gst sandbox create` first).
- XNA effects are D3D9 effect bytecode; FNA translates them at runtime (MojoShader).
- 2D games draw most of a frame as sprite batches into the back buffer; extra render targets reveal lighting and
  post-effects.

## Gotchas

- `no .NET assemblies found` → single-file bundle, NativeAOT, or code in an unusual folder → see Code.
- recon says `dotnet` for an XNA game → the framework sits only in the GAC and no `.xnb` lies under a root `Content/`
  → `gst decompile` works the same; the assembly references name the framework.
- `gst fmt id` calls an `.xnb` unknown → gst has no XNB signature (gap) → read the header (Identify); high entropy after
  it = LZX or LZ4 payload.
- Decompiled names `num`, `flag`, `text` → no PDB → record real names in `notes/renames.json`.
- Garbled method bodies and one string-decrypting helper called everywhere → obfuscation → `de4dot -d <dll>`.
- `gst trace watch` refuses the process → 32-bit (WoW64) → scans plus code reading.
- Errors in `decomp/cs/<Assembly>.log` → the method carries the error text → `ilspycmd -il -t <Type> <dll>` shows the IL.

## Protections

- Obfuscators (ConfuserEx, Dotfuscator, SmartAssembly...): recon's `obfuscation` hint, informational (the verdict
  doesn't change). `de4dot -d <dll>` (de4dotEx, optional, not in the catalog) names the obfuscator.
- Anti-cheat or an always-online design → `no-runtime`; store ownership checks are never bypassed. recon records each
  finding in `recon/protections.md`; the code and data on disk stay readable unless the verdict says otherwise.
- Limits: `plugins/gst/guardrails.md`.

Adapted from universal-modder `skills/mod-any-game/references/engines/dotnet-xna.md` (MIT, Copyright (c) 2026 Rehan
and universal-modder contributors).
