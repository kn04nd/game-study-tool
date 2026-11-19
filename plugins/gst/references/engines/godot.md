# Godot — study guide

Covers the `gst recon` engine key `godot`: Godot 3 and 4, including Godot .NET (C#).

## Identify

- A `.pck` next to the exe, or the pack appended to the exe (recon `pck_where`: `pck` or `embedded in exe`; embedding
  is not a protection). Header: `GDPC`, `pack_format` (1 = Godot 3, 2 = Godot 4.0–4.4 era, 3+ newer), engine version, flags
  (bit 0 `PACK_DIR_ENCRYPTED`, bit 1 `PACK_REL_FILEBASE`). Pratfall: format 3, Godot 4.6.1, flags `0x02`.
- Godot .NET: `data_<Game>_windows_x86_64/` with `GodotSharp.dll`, `<Game>.dll` (+ `.pdb`), `<Game>.deps.json` and the
  .NET runtime → recon `dotnet=true` and `dotnet_dir`. The `GodotSharp.dll` ProductVersion reads `x.y.z+<commit>`;
  the exe's version info is the game's, not the engine's.
- recon details: `version`, `pack_format`, `dir_encrypted`, `pck_where`, `dotnet`, `dotnet_dir`; shipped `.pdb` files
  under symbols (Pratfall: `pratfall.pdb`).
- GDExtension/GDNative DLLs next to the exe are native code. Scenes that reference `.cs` scripts with no `data_*`
  assemblies anywhere mean a NativeAOT build (native code only).
- User data: `%APPDATA%\Godot\app_userdata\<Project>\`, or `%APPDATA%\<Name>\` with a custom user dir: saves and
  `logs/godot.log`.

## Code

`gst decompile` runs the `godot` backend and, for .NET games, `dotnet`; then it indexes both.

- GDScript: GDRE recovers `.gd` (text) and `.gdc` (tokenized) into `decomp/godot/`; `MANIFEST.json` records the pack
  and the Godot version. Pratfall: 6 files, all in addons. `gst decompile godot` runs this part alone.
- C#: ilspycmd on the assemblies listed as projects in `<Game>.deps.json` (Pratfall: 1 of 177), with PDB names →
  `decomp/cs/<Game>/` (Pratfall: 3,129 files, ~5% generic names, 51,158 symbols in 64 s).
- GDScript 1.0 (Godot 3) and 2.0 (Godot 4) differ (`yield` vs `await`, annotations, typing). GDExtension and NativeAOT
  code → `gst decompile native --list`.
- The index reads `class_name`, `extends`, `func`, `signal` and `var`/`const`: `gst q def Player` →
  `decomp/cs/Pratfall/Player.cs`; `gst q def ImGuiConfig` → a GDScript class.

Where to start:

- `project.godot` (recovered by `gst assets project`): `run/main_scene`, `[autoload]` = the global singletons (the
  managers), `[input]` actions.
- The main and player scenes (`.tscn`): the node tree, the attached scripts
  (`[ext_resource type="Script" path="res://Player.cs"]`) and the signal wiring (`[connection signal=... method=...]`).
- Callbacks: `_ready`, `_process(delta)`, `_physics_process(delta)`, `_input`/`_unhandled_input`; in C# `_Ready`,
  `_Process`, `_PhysicsProcess`. Signals: `signal`, `emit_signal`/`.emit()`, `connect`; C# `[Signal]` + `EmitSignal`.
- Saves: `gst q strings "user://"`, then the writer (`FileAccess`, `ConfigFile`, `ResourceSaver`, `JSON`). Pratfall
  serializes with MemoryPack in `SavegameManager.Save`.

## Data and assets

| Need | Command |
| --- | --- |
| inventory | `gst assets list` → every `res://` path in the pack (Pratfall: 11,755 files in 3.6 s) |
| selected files | `gst assets extract --types scene,texture,audio,text --name "<regex on the res:// path>"` → `assets/godot/extract/` |
| other types | `--types resource`, `shader` (`.gdshader` sources), `font`, `script`, `model` |
| whole project | `gst assets project` → `assets/godot/project/` (C# comes from `gst decompile`) |

- GDRE converts while it recovers: binary scenes/resources → `.tscn`/`.tres`, `.ctex` → PNG, `.sample` → WAV,
  streams → OGG/MP3.
- Godot 4 stores exported scenes as `.godot/exported/<hash>/export-<hash>-<name>.scn`: match `-player\.scn$`; GDRE
  restores the original path (`player.tscn`).
- Text data (`.json`, `.cfg`, `.csv`, `.translation`): `--types text`. Middleware banks outside the pack:
  `gst assets audio --list`.
- `gst fmt id` knows `GDPC` (pack), `RSRC` (binary resource), `GST2` (Godot 4 texture) and `GDSC` (compiled GDScript).

## Analyses (run by `gst study`, or `gst analyze <name>`)

| Analysis | What it answers |
| --- | --- |
| `godot_pck` | the pack directory without GDRE (format, version, encrypted flags, sizes, md5), cross-checked with the inventory |
| `godot_scenes` | every `.tscn`/`.tres` as JSON (`analysis/godot_scenes/<res path>.json`); node types, scripts per node, signal connections, instanced-scene graph; **tunables**: the exported script variables each scene sets, next to the default in the code (Pratfall: `BaseSpeed` 50 in `player.tscn`, 100f in code) |
| `godot_animation` | Animation tracks (method/audio tracks), AnimationTree state machines (states, transitions, `advance_condition`/`advance_expression`, xfade) and blend trees, skeletons, ragdolls (`PhysicalBone3D` + joints with limits); **characters**: scene -> model (.glb) -> imported scene -> Skeleton3D (bones) -> AnimationPlayer/Tree -> ragdoll bodies (Pratfall: `player.tscn` -> `diggahChar02.glb`, 48 bones, 34 animations, 19 bodies) |
| `godot_shaders` | every `.gdshader`/`.gdshaderinc`, embedded Shader and VisualShader: shader_type, render modes, uniforms (hints, defaults), ShaderMaterials and the files using them, keyword features (toon, outline, dissolve...) |

Text and binary scenes/resources read alike: `.scn`/`.res` that GDRE left binary and the imported scenes under
`.godot/imported` go through the stdlib RSRC reader (`gst.engines.godot.rsrc`; compressed RSCC included). Clips inside
imported models (glb) are also in `gltf_summary`.

## Runtime

- The engine is native C++ (official templates ship no PDB). GDScript runs in the engine's VM: a watch on a script
  variable names the interpreter inside the exe, not the script → find the variable in `decomp/godot` (`gst q refs`).
- GDScript numbers are 64-bit `Variant`s: `gst trace scan <value> --type int64` or `--type double`.
- Godot .NET: CoreCLR JIT, so hits read "outside any module"; the .NET GC compacts, so scan again right before
  `gst trace watch`.
- `gst trace snapshot before` / `after` + `gst trace diff` on the `app_userdata` folder around a save.

## Graphics

- Godot 4: Forward+ and Mobile on Vulkan (D3D12 since 4.3); Compatibility on OpenGL 3.3 core. Godot 3: GLES3
  (OpenGL 3.3) or GLES2 (OpenGL 2.1). RenderDoc captures all of them except GLES2.
- `gst gfx capture` runs the sandbox copy (`gst sandbox create` first). Pick the API when the export allows it:
  `--args "--rendering-driver d3d12"` (Godot 4: `vulkan`, `d3d12`, `opengl3`; Godot 3: `--video-driver GLES3`).
- Vulkan needs RenderDoc's layer registered, a registry change: ask the user first (study-graphics skill).
- `frame.md` shows the shaders as SPIR-V, DXIL or GLSL; their sources are usually in the pack (`--types shader`).

## Gotchas

- `--name` finds no scene → exported names are `export-<hash>-<name>.scn` → write the regex for that form.
- C# types missing from `gst q` → `gst decompile godot` alone skips C# → `gst decompile` (auto) or `gst decompile dotnet`.
- `gst q hier Node` → `no type named` → engine classes are not indexed → `gst q grep "^extends " --glob "*.gd"`, or the
  game's own base classes.
- A function with no callers → connected in a scene (`[connection ...]`) or by name (`call("name")`, `connect`) →
  `rg` over the recovered `.tscn` files.
- GDRE fails on the version → a custom engine fork or a bytecode version GDRE lacks → record it; read scenes, text
  resources and the C#.
- `gst assets project` refuses → `assets/godot/project/` is not empty → remove it first.
- Pratfall's `.meta.sav` holds `data` rather than `meta` → the game's own bug → trust the code (`SavegameManager.Save`).

## Protections

- Encrypted content: `PACK_DIR_ENCRYPTED` (recon `dir_encrypted`, `encrypted-content`; `gst decompile godot` and
  `gst assets` stop at the diagnosis, rule 4), per-file encrypted entries (the listing works, their contents don't), or
  Godot 3 `.gde` scripts. The AES key is compiled into the export template. What remains: files outside the pack, the
  C# assemblies, configs and public docs.
- Keys: only community-published keys with their source in `game.json`; never extracted from the exe or memory,
  never brute-forced.
- Anti-cheat is rare in Godot games; recon decides the verdict and records it in `recon/protections.md`.
- Limits: `plugins/gst/guardrails.md`.

Adapted from universal-modder `skills/mod-any-game/references/engines/godot.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
