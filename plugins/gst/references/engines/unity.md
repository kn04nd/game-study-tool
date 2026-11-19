# Unity — study guide

Covers the `gst recon` engine keys `unity-mono` and `unity-il2cpp`.

## Identify

- `UnityPlayer.dll` + `<Game>_Data/`: `globalgamemanagers`, scenes `level0..N`, `sharedassets*.assets`, `resources.assets`,
  `*.resS`/`*.resource` streams, `StreamingAssets/`. Compressed builds pack them into one `data.unity3d` (UnityFS).
- **Mono:** `<Game>_Data/Managed/Assembly-CSharp.dll` + `MonoBleedingEdge/`. **IL2CPP:** `GameAssembly.dll` +
  `<Game>_Data/il2cpp_data/Metadata/global-metadata.dat` (first bytes `AF 1B B1 FA`).
- recon details: `version` (from the `globalgamemanagers` header; ONI `6000.3.5f2`), `data_dir`, `managed_dir`,
  `addressables` (`StreamingAssets/aa/`), `render_pipeline` (URP/HDRP), `burst` (`lib_burst_generated.dll`),
  `company`/`product` (from `app.info`).
- `<Game>_Data/ScriptingAssemblies.json` lists the managed assemblies; `RuntimeInitializeOnLoads.json` lists the
  methods that run at startup without any scene.
- `%USERPROFILE%\AppData\LocalLow\<company>\<product>\` holds `Player.log` (Unity version, graphics API, exceptions:
  read it first) and often the saves; recon lists it under user data. ONI saves under `Documents\Klei\OxygenNotIncluded`.
- BepInEx or MelonLoader files: recon warns that the install was modified; study the vanilla files.

## Code

**Mono:** `gst decompile` (dotnet backend, ilspycmd) → `decomp/cs/<Assembly>/`, one `.cs` per type, PDB names when a
`.pdb` ships; it builds the code index at the end.

- `gst decompile --list` marks the game's assemblies; `UnityEngine.*`, `Unity.*`, `System.*` and well-known third-party
  DLLs are skipped. ONI: `Assembly-CSharp` + `Assembly-CSharp-firstpass`, 6,856 files and 136,921 symbols in 87 s.
- asmdef assemblies (`<Studio>.<Module>.dll`) count as game code; add a skipped assembly with
  `gst decompile dotnet <Name>`, or take everything with `--all`.
- Native plugins (`<Game>_Data/Plugins/x86_64/*.dll`): `gst decompile native --list`. ONI's `SimDLL.dll` ships its PDB:
  5,036 functions, 1,182 classes with field layouts. C# reaches them through `extern` methods:
  `gst q grep "DllImport" --glob "*.cs"`.
- Burst: `lib_burst_generated.dll` is the native build of C# jobs; read the job's C# instead.
- Embedded Lua (xLua, ToLua) ships as TextAssets: export them (`gst assets extract --types TextAsset --name "lua"`);
  loose `.lua` or bytecode under `StreamingAssets/` → `gst decompile lua`.

**Where to start** (`gst q`):

- Boot: scene 0 (`level0`) and `RuntimeInitializeOnLoads.json`, then the MonoBehaviour messages `Awake`, `OnEnable`,
  `Start`, `FixedUpdate`, `Update`, `LateUpdate`.
- Managers: singletons (`X.Instance`, `Singleton<T>.Instance`; `gst q callees` resolves both) and persistent objects
  (`gst q grep "DontDestroyOnLoad\(" --glob "*.cs"`).
- Main loop: `gst q callees Game.Update` (ONI resolves `gasConduitSystem.Update` to `UtilityNetworkManager.Update`);
  fixed ticks such as ONI's `Sim200ms`.
- Save/load: `gst q strings "save"`, then `gst q callers` (ONI: `SaveLoader.Save` ← `PauseScreen.DoSave`, `Game.DelayedSave`).
- Data: `ScriptableObject` subclasses, `[SerializeField]` fields and config classes; the values live in the assets.

**IL2CPP:** `gst decompile il2cpp` (also part of `gst study`): reads the `global-metadata.dat` header
(`decomp/il2cpp/metadata.json`), makes dummy DLLs with the first catalog tool that supports the metadata version
(Cpp2IL; Il2CppDumper only up to v31) in `decomp/il2cpp/dummydll/`, decompiles them with ilspycmd into
`decomp/cs-il2cpp/` and indexes them: `gst q def/outline/hier` cover types, fields and signatures. The dummy DLLs are
also the MonoBehaviour typetree source when the `*_BackUpThisFolder*` folder is gone. `--maps` adds the name maps
for Ghidra (Il2CppInspectorRedux `metadata.json`, Il2CppDumper `script.json`) under `decomp/il2cpp/<tool>/`.

- Method bodies are stubs; each method's RVA is in `decomp/il2cpp/addresses.tsv` (and the `il2cpp_addresses`
  table of `index/code.db`).
- Bodies: `gst decompile native GameAssembly.dll` (large: named only, hours; `FUN_` names, since gst does not apply
  the dumpers' Ghidra scripts).
- Stripping: methods never called and unused generic instantiations are not in the build at all.

## Data and assets

| Need | Command |
| --- | --- |
| inventory | `gst assets list` → `assets/INVENTORY.md` + `inventory.jsonl` (ONI: 57,593 objects in 9 s) |
| a slice | `gst assets list --types Texture2D --name "^hatch_" --limit 20`; `--container` (bundle or Resources path), `--file sharedassets0` |
| export | `gst assets extract --types Texture2D,Sprite,AudioClip,TextAsset,Font,Mesh,MonoBehaviour --name "<regex>"` → PNG + thumbnails, WAV, text/bytes, TTF/OTF, OBJ, JSON |
| scenes, prefabs | `gst assets project` (AssetRipper; YAML under `assets/assetripper/project/`; minutes, GBs); `--mode primary` = content only |
| audio banks | `gst assets audio --list`, then `gst assets audio "<glob>"` (FMOD `.bank`; ONI `Creatures.bank` holds 2,000+ streams) |

- MonoBehaviour JSON uses typetrees generated from the game's assemblies (on IL2CPP, from `GameAssembly.dll` + the
  metadata; untested here). That JSON is where tuning values and ScriptableObject data sit.
- `StreamingAssets/` is read as plain files: read text data in place (ONI: YAML); Addressables bundles show up in the
  inventory with their container paths.
- Game-specific binary data usually ships as `TextAsset` bytes: export, then the study-formats loop (`gst fmt id`).
- UnityDataTool 2.4.0 is installed without a gst wrapper (untested on player data): run the exe that
  `gst tools path unitydatatool` prints with `analyze "<install>\<Game>_Data" -o assets/unitydatatool.db`.

## Runtime

- Mono JIT: `gst trace watch` hits read "outside any module (JIT code, heap or stack)" → find the field in the C#
  (`gst q refs <Field>`). Unity's Boehm GC does not move objects: a found address stays valid while the object lives.
- IL2CPP: hits name `GameAssembly.dll+0xRVA` → look the RVA up in the stubs.
- Transforms, physics and rendering state live in `UnityPlayer.dll` memory (no PDB, symbol servers off): hits there
  carry only module+RVA; follow the managed caller instead.
- `gst trace snapshot before` / `after` + `gst trace diff`: the default folders are recon's user data (`--path` for others).
- UnityExplorer (through BepInEx or MelonLoader) shows the live hierarchy, components and field values: only inside
  `gst sandbox`, offline, installed there by the user (guided).

## Graphics

- `Player.log` names the API: D3D11 or D3D12 by default, Vulkan or OpenGL core when the build includes them. Force one
  with `gst gfx capture --args "-force-d3d11"` (or `-force-d3d12`, `-force-vulkan`, `-force-glcore`).
- `gst gfx capture` runs the sandbox copy (`gst sandbox create` first). RenderDoc covers all four APIs above.
- No `render_pipeline` detail = Built-in (forward or deferred); URP (forward, forward+, deferred); HDRP (deferred,
  heavy compute).
- Constant buffers keep Unity names (`UnityPerDraw`, `UnityPerMaterial`, `$Globals`, `unity_ObjectToWorld`,
  `unity_MatrixVP`, `_Time`): match them with the materials and shaders of the AssetRipper export.
- Steam games may relaunch through Steam from the original install, so the capture sees no device (ONI) → `--manual`,
  or study materials and shaders statically.

## Gotchas

- `gst q hier MonoBehaviour` → `no type named` → engine assemblies are not decompiled → use the game's base class
  (ONI `KMonoBehaviour`), `gst q grep ":\s*MonoBehaviour\b" --glob "*.cs"`, or `gst decompile dotnet UnityEngine.CoreModule`.
- A public method with no callers → wired by name: UnityEvent persistent calls, animation events, `SendMessage`,
  `Invoke("Name")` → `gst q strings "<Method>"`, then `rg` over `assets/assetripper/project/`.
- A `<Name>d__12` class with `MoveNext` → a coroutine or async state machine ILSpy could not fold back → read `MoveNext`;
  the outer method's name is in the class name.
- `code.il2cpp.dummydll` unsupported → no catalog tool knows the metadata version (`decomp/il2cpp/metadata.json`
  `tools.supporting`); failed with Cpp2IL → `gst study --opt il2cpp.tool=il2cppinspector`. No `AF 1B B1 FA` at all →
  see Protections.
- MonoBehaviour export fails → script outside `Managed/` or a stripped type → export TextAssets or read the code.
- `gst assets project` refuses → the output folder is not empty → remove `assets/assetripper/<mode>/`.
- AssetRipper run by hand hangs or rejects arguments → `/Export/*` blocks until done and `--log false` breaks its
  parser → use `gst assets project`.
- `gst map` puts a base class in the wrong system → keyword hit on a generic name → `notes/map-keywords.json`.
- Errors in `decomp/cs/<Assembly>.log` → the method carries the error text → `ilspycmd -il -t <Type> <dll>` shows the IL.

## Protections

- Multiplayer Unity games often ship EAC or BattlEye → `no-runtime`. Photon or Mirror alone don't change the verdict
  (PEAK uses Photon and is `offline-ok`: Steam lists it as single-player). recon writes each finding to
  `recon/protections.md`.
- Obfuscators (Beebyte, ConfuserEx; recon `obfuscation` hint): unreadable names, encrypted strings, verdict unchanged.
  `de4dot -d <dll>` (de4dotEx, optional, not in the catalog) names the obfuscator.
- Custom encryption: `global-metadata.dat` without `AF 1B B1 FA`, bundles without the `UnityFS` header. Diagnose with
  `gst fmt hex` and `gst fmt entropy`, record it in STUDY.md, and fall back to StreamingAssets text, configs and public
  docs.
- Keys: only community-published keys with their source recorded; never extracted from the exe or memory, never
  brute-forced.
- Limits: `plugins/gst/guardrails.md`.

Adapted from universal-modder `skills/mod-any-game/references/engines/unity.md` (MIT, Copyright (c) 2026 Rehan and
universal-modder contributors).
