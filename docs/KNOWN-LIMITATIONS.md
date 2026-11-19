# Known limitations

What gst still cannot do, or does but has not verified on a real game. Everything not listed here works and is
covered by the unit tests and the guides in `docs/tools/`. Contracts: `docs/INTERFACES.md`.

## Pipeline (`gst study`, jobs)

- The RAM weight of a job comes from the size of its unit, not from the tool's real peak. Cpp2IL and
  Il2CppInspector peak at ~4.9 GB on a 184 MB unit; with the `heavy` pool at one job at a time this has not been a
  problem, but the runner does not know it.
- The watchdog only warns; there is no default timeout (by design). A stuck third-party tool keeps running until
  you stop it or pass `--timeout`.
- `gst audio index` looks at `assets/audio` by default. For Unity clips run `gst audio index assets/unity/AudioClip`
  (the two folders are not merged in `files.jsonl`).

## Unity

- The AssetRipper project export (`unity.assetripper_project`) has not been measured on a real game.
- A bundle whose single block is larger than half the free RAM (single-block LZMA) becomes `unsupported` with the note
  "fallback assetripper"; the per-bundle AssetRipper fallback itself is not implemented.
- Scene animators were measured on R.E.P.O., Safari-TV and PEAK's `level0-4`; PEAK's 2 GB map bundles
  (`StreamingAssets/maps`) were not measured for scene clips.
- Clip bindings to children that a prefab no longer has stay unresolved (R.E.P.O.: 64 curves).
- `joints`: ragdolls are detected by class name (`*Ragdoll*`, PuppetMaster...). Ragdolls built at runtime
  (Safari-TV's `RagdollAnimator2`) have no joints in the prefabs, so only the component is found.
- `dcc` verdicts are inferences from names and transforms, not proof of the authoring tool. Unreal package names are
  not a source.

## Unity IL2CPP

- Il2CppDumper (6.7.46, the latest release) does not read metadata v39; its path (`script.json`) is covered only by
  synthetic tests. Cpp2IL and Il2CppInspectorRedux cover v39.
- Safari-TV: 96 MonoBehaviours (11 scripts) have no typetree and 181 are read with a size that does not match the
  dummy DLL (`_gst_check_read: false`), probably `[SerializeReference]` or generics that TypeTreeGenerator lays out
  differently. Not investigated.
- `gst decompile il2cpp` always reruns Cpp2IL, whose DLLs are not byte-identical between runs, so ilspycmd redoes
  every assembly. `gst study` does not have this problem (the job reruns only when the inputs change).
- Assembly names with a dot (`UniTask.*`, `Sentry.*`) slip through the `NOT_GAME` filter of `decompile/dotnet.py` and
  are decompiled as game code (harmless, only more files).
- `gst doctor` has no IL2CPP-specific finding (the tools show in `gst tools list`).

## Godot

- Encrypted packs (`PACK_DIR_ENCRYPTED`) and encrypted files (`GDEC`) are only diagnosed, never decrypted; no real
  encrypted game was available, so this is covered by synthetic fixtures only.
- Godot 2 files (resource format 1-2, `Image` variant) are not supported.
- RSCC Zstandard falls back to a pure-Python decoder when neither `compression.zstd` nor `zstandard` is importable;
  it is slow (Pratfall: 2362 binary resources in 61 s).
- `scripts.py` finds exported variables by regex: exports declared through `_GetPropertyList` or in generated partial
  classes are not seen, and those properties land in `engine`/`unclassified`.
- Shader feature lists are keyword inferences, not a render test; VisualShader code is not generated.
- Which scene is "the" player comes from the instance graph only; scenes instanced from code look top level.

## Unreal

- No unencrypted UE5 IoStore game has been validated end to end. The only IoStore game available (Witch It) has every
  asset container encrypted; `retoc to-legacy` + uas were exercised with fake tools and on the plain `global.utoc`.
  The checklist to run is in `docs/tools/unreal.md` ("How to validate once an unencrypted UE5 game is installed").
- Not verified on a real game: whether retoc resolves every import when only some containers are mounted (mixed
  plain/encrypted folders).
- The pak v12 lister (UE 5.4+) and `.usmap` handling are tested on synthetic data only (DRG is UE4 and needs no
  mappings; Witch It is encrypted).
- Physics constraint values equal to the engine defaults are omitted from cooked packages; gst fills them from the
  engine constructors and lists them in `defaulted`, so those values are inferred, not read.
- State machine transition rules are Kismet bytecode: read them with `--bytecode`, they are not decoded into
  conditions.
- The real class of every package needs its JSON export; the inventory families come from name prefixes
  (`uas list --format Types` would give all classes but is slow, so it is not the default).
- Tunable values have no cross-engine library facet; they stay in each workspace's `unreal_values.json`.
- A full export of a large game (DRG: 52k packages x 4 modes) takes hours and tens of GB; it was never run on purpose.
  Narrow with `unreal.name` / `--name`.

## Java and Lua

- Kahlua's `stdlib.lbc` (Project Zomboid) is not decompiled: unluac stops on that chunk and gst keeps the partial
  output (`<name>.partial.lua`).
- LuaJIT bytecode is diagnosed only (header, version); no decompiler is pinned.

## Ghidra

- A full Ghidra analysis of a large IL2CPP `GameAssembly.dll` (Safari-TV, 160 MB, ~280k methods) did not finish: it was
  stopped after ~3 h at the 15 GB heap. The capability is validated on a system DLL (`-m tools`) and the `pre` names
  step on Safari-TV; see `docs/tools/ghidra.md` for how to run a big one.
- The `post` step's counts (`existing_method`/`already_named` versus `created_*`/`renamed_*` in `il2cpp_names.json`)
  have not been read on a real game.

## REA

- IL2CPP without the `*_BackUpThisFolder*` folder has no managed method bodies: the `rea_calls` of such a game come only
  from the dummy DLLs (constructor chains), not a real call graph. The real IL2CPP call graph comes from Ghidra with
  names.
- `rea.managed_graph` repeats the calls of `rea.managed_members` (only within the module); it stays as Evidence and the
  import uses the members.
- The official `rea-agents@6.9.0` refuses IL2CPP reports above 64 MiB (and metadata above 256 MiB). On a large
  IL2CPP game `rea.il2cpp_metadata` falls back to metadata only (no method addresses, no field/parameter types).
  The fix is optional: build REA locally with `patches/rea/` and install that tarball (`docs/tools/rea.md`). Building
  it needs Node 24.22+ (Node 26 breaks REA's verifier loader). Method addresses also come from Cpp2IL and Ghidra,
  which have no such cap.

## Plugin

- `/gst:study-all` has not been run end to end on a game, and the newer skill evals (`mechanic-numbers`,
  `compare-games`, `dossier-models`) have not been run (cost).
- The name a plugin workflow uses to call another (`workflow('gst:verify-claims')`, fallback `'verify-claims'`) is not
  confirmed; if neither resolves, study-all logs it and goes on.
- There is no `assets/families.json`: study-all plans the asset families from `gltf_summary`, `dcc`, `audio_catalog`,
  `godot_scenes`, `prefabs` and `unreal_assets`.

## Library (`gst lib`)

- "Bones per rig" is not the same measure across engines (Unity = SkinnedMeshRenderer bones, Godot = joints per skin,
  Unreal = reference skeleton bones).
- Godot rigs come from the exported `.glb` and the scene skeletons, without bindposes or an Avatar equivalent.
- An analysis that was not run in a workspace shows as a gap row, not a value: comparisons are only as complete as the
  analyses of each game.

## Migration and installer

- `install.ps1 -Plugin`, `-Tools`, `-EnableLongPaths`, `-System` and `-AddToPath` were checked in dry run only (they
  change the Claude configuration, the registry and the PATH). A real install into a throwaway venv passed.
- `claude plugin install gst@gst-local` after the marketplace rename was not run (`claude plugin validate` passes).
- The legacy `gs` CLI was checked against migrated workspaces with `gs status` and `gs report` only.

## Security

Threat model: the game files (and names inside them) and text the model reads during a study are untrusted. A
2026-11 review fixed: the hook launcher and gst's Python subprocesses importing modules from the current folder
(`-P`), batch-file argument injection (`gst.common.check_bat_args` on every `.bat`/`.cmd` call that can carry
game-derived text), Unity export names and store build ids escaping their folders, absolute/UNC paths in glTF
buffers and Godot `res://` paths, zip members with drive letters, three downloads without a pinned hash,
https-to-http redirects, `uas` response-file line breaks, and workspace rules that let a file written in the
workspace run with no prompt (`ask` rules for `gst fmt roundtrip`, `config set`, `tools install`, `setup`, `mcp`,
`rg --pre`, `ast-grep -U`). Still open, by choice:

- The plugin hooks fail open: an error in a hook allows the call (the normal permission flow still applies). The
  guard enforces study policy; it is not a sandbox.
- SessionStart and compaction put workspace text (journal lines, open questions, FACTS.md) back into the context.
  That text can quote game-derived names; the workspace CLAUDE.md and guardrails rule 11 say game content is data.
- npm installs run lifecycle scripts, and npm/uv/pip dependencies are pinned only at the top level (no lockfile).
- Paths written by third-party tools (GDRE, uas, retoc, Vineflower, AssetRipper, vgmstream) from entry names inside
  the game's containers are not checked by gst; they write into gst-made output folders.
- The `recon` agent can use WebFetch (with the normal permission prompt; the skill no longer pre-approves it).
