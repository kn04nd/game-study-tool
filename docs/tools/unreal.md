# Unreal 4/5 in gst (repak, retoc, uas, usmap, keys)

## What it is

A packaged Unreal game keeps its content (Blueprints, DataTables, meshes, textures, sounds, maps) in containers
inside `<Project>/Content/Paks/`:

| container | files | engine | holds |
| --- | --- | --- | --- |
| legacy pak | `*.pak` (+ `.sig` when signed) | UE4 and UE5 | `.uasset/.uexp/.ubulk` packages, `.umap`, configs, `.locres`; versions 1 to 11 up to UE 5.3, **12** (`Utf8PakDirectory`) from UE 5.4 on |
| IoStore (Zen) | `*.utoc` + `*.ucas` | UE 4.26+ and almost every UE5 game | the same packages in Zen format (no legacy `.uasset`); a small `.pak` next to them holds what is not a package |
| global | `global.utoc` + `global.ucas` | IoStore | only the *script objects*: the native C++ modules and classes (`/Script/<Module>.<Class>`) |

gst reads the headers with the Python standard library and calls three third-party tools, always writing inside the
workspace (`GST_HOME/<slug>`), never in the install:

| tool | version (catalog) | license | role in gst |
| --- | --- | --- | --- |
| [repak](https://github.com/trumank/repak) | 0.2.3 | MIT/Apache-2.0 | lists legacy paks v1-v11 |
| [retoc](https://github.com/trumank/retoc) | 0.1.5 | MIT | lists IoStore, converts Zen -> legacy (`to-legacy`), reads the script objects of `global.utoc` |
| [UnrealAssetScout (uas)](https://github.com/AndrewSav/UnrealAssetScout) | 0.5.0 | MIT | CUE4Parse on the command line: JSON (with Kismet bytecode), textures, models (`.uemodel` + materials as JSON), animations, audio, straight from `.pak`/`.utoc` |

Install: `gst tools install unreal` (all three). An already installed legacy gs copy is reused.

## When each one runs

| situation | what runs |
| --- | --- |
| any Unreal game | `paks.py` (stdlib): pak version, encrypted index or not, mount point, entry count; `.utoc` header (version, container id, encrypted/signed/indexed flags) |
| plain legacy pak v1-v11 | `repak list` (DRG: 109,939 paths in 1.4 s) |
| plain pak v12 (UE 5.4+) | stdlib lister of the full directory (repak 0.2.3, the latest release, stops at v11); also the fallback when repak fails on a v10/v11 |
| plain IoStore | `retoc list --path` per container (knows which container holds each path) |
| plain `global.utoc` | `retoc print-script-objects`: native modules and classes, even when everything else is encrypted |
| export packages | `uas export <mode>` once per container and mode, the filter in a *response file* |
| tools that only read legacy packages (UAssetGUI, kismet-analyzer) or a cross-check | `retoc to-legacy` into `dumps/unreal/legacy/*.pak`, then uas on that pak |
| unversioned properties (UE5 without mappings) | a published or user `.usmap` (`--usmap`) |
| encrypted container | a published AES key with its source URL (`--aes-file`/`-a`); without one, `blocked-policy` |

## How gst runs them

- `gst assets list`: inventory (`assets/inventory.jsonl` with each path's container, `assets/INVENTORY.md`,
  `assets/unreal/containers.json` with the headers and how each container was listed). Encrypted container without a
  key: the paths come from the `Manifest_UFSFiles_*.txt` the engine ships next to the game (container `manifest`).
- `gst assets extract --name <regex> --types json,textures,models,audio,animations [--bytecode] [--all-types] [--limit N]`:
  one uas call per mode into `assets/unreal/<mode>/`. `--types` repeats (`--types json --types models`); `--limit N`
  exports the first N packages (alphabetical). uas deletes outputs outside a narrower filter, so gst always passes the
  union of every filter already used in that folder (kept in `_folder.gs.json`), in a response file
  (`uas @logs/uas-<mode>.rsp`): no command-line limit. `--bytecode` and `--all-types` are sticky per folder the same way.
- `--all-types` (JSON only): uas skips "specialized export" classes in JSON mode by default (`SkeletalMesh`,
  `AnimMontage`; its log says `[SKIPPED] ... (specialized export asset)`); this passes `--no-skip-types` so they are
  exported too (a SkeletalMesh JSON holds LODs, sections, materials, morph targets, bounds; no vertex buffers).
- `gst assets to-legacy --filter <path substring> [--types json] [--all-types]`: retoc converts the packages whose path
  contains one of the substrings (no filter = everything) into `dumps/unreal/legacy/legacy-<hash>.pak`, and uas exports
  every legacy pak of that folder into `assets/unreal/iostore/<mode>/`.
- `gst study`: jobs `unreal.inventory` (stage 40), `unreal.iostore` (45; option `unreal.filter=a,b`), `unreal.export`
  (JSON, category Data), `unreal.textures`, `unreal.models` (Meshes), `unreal.audio`, `unreal.animations` (50), one job
  per container and mode in `assets/unreal/containers/<container>/<mode>/`. Options: `unreal.modes=json,models`
  (default `json,textures,models,audio`), `unreal.name=<regex>` to narrow, `unreal.all_types=1` (JSON with the classes
  uas skips). The fingerprint covers mode, filter, `all_types` and whether there is a key/usmap: adding a published key
  or a `.usmap` reruns only the blocked jobs.

## Analyses

| analysis | needs | what it gives |
| --- | --- | --- |
| `unreal_assets` | `unreal.inventory` | families per folder (from the `BP_`, `DT_`, `M_`/`MI_`, `SK_`, `SKEL_`/`_Skeleton`, `ABP_`, `PA_`/`_PhysicsAsset`, `_Montage`, `_BlendSpace`, `SC_`/`SW_`... naming), the real class of each exported JSON, DataTable numeric columns, Blueprint parents, the script objects of a plain `global.utoc` |
| `unreal_values` | `unreal.json` (any uas JSON under `assets/unreal/`) | the tunable numbers (below) |
| `unreal_animation` | `unreal.json` | skeletons, skeletal meshes, clips, anim graphs and ragdolls (below) |

### `unreal_values`: default values

Per Blueprint (`BlueprintGeneratedClass`, widget and anim Blueprints too): class, parent (native `/Script/<Module>` or
another Blueprint), and its components with their origin:

- `native`: default subobjects of the class default object (CDO, `Default__<Class>`) that this Blueprint configures,
  e.g. `CharMoveComp` (the character movement component of a `Character`);
- `scs`: components added by the Blueprint's construction script (SCS nodes, template `<Name>_GEN_VARIABLE`), with the
  component they attach to;
- `inherited`: components of a parent Blueprint whose template this one overrides (`InheritableComponentHandler`).

`values` = every number, flag and enum the CDO and the component templates set, as `{"Prop": v,
"Component.Prop": v}` (structs flattened `Struct.Field`, arrays `List[0]`, fixed arrays `Name[1]`); the full trees
(references as `Class'Package.Object'`, soft references as `/Game/...` paths, texts as their localized string) go to
`analysis/unreal_values/blueprints/<package path>.json` (`defaults`, `component_defaults`, `curves`, `timelines`,
`functions`, `variables`). An AnimBlueprint's values are its variables (the anim graph nodes go to
`unreal_animation`). Also: DataTables as rows (numeric columns min/max in the main JSON, every row in
`analysis/unreal_values/datatables/`), CurveTables and curve assets (`CurveFloat` and friends, also the timeline
curves inside Blueprints) as `[[time, value], ...]`, and data assets (any other asset object with properties, e.g. a
`PrimaryDataAsset` subclass) as values plus `analysis/unreal_values/data_assets/`. Option `name=<regex>` narrows by
package path.

Cooked packages keep only what differs from the parent class (C++ constructor or parent Blueprint): an absent value is
inherited, not zero. `gst notes new logic/<topic> --template mecanica --match "<regex>"` lists these values in the
"Parâmetros tunáveis" section (`--match` is tried on the package, the class and the value path, e.g.
`--match "MaxWalkSpeed|JumpZVelocity"`).

### `unreal_animation`: skeleton, animation, physics

| asset | read from the uas JSON |
| --- | --- |
| `Skeleton` | bones (`ReferenceSkeleton.FinalRefBoneInfo`: name + parent), root, depth, leaves, sockets (bone, relative transform), virtual bones, slot groups, animation curve names; the bone list in `analysis/unreal_animation/skeletons/<package path>.json` |
| `SkeletalMesh` (needs `--all-types`) | skeleton, PhysicsAsset, materials per slot, LODs (sections, vertices, triangles, max bone influences per section), morph targets, bounds; bones from its own reference skeleton when present, else from its exported skeleton (`skeleton_bones`) |
| `AnimSequence` | length, sampled keys, frame rate (UE5 `SamplingFrameRate`; UE4 `(NumFrames - 1) / SequenceLength`), tracks, notifies (name, class, time, duration), root motion, additive type, compression |
| `AnimMontage` (needs `--all-types`) | length, sections, slots with their segments (animation, start/end, rate, loops), notifies, blend in/out (engine default 0.25 s when absent, listed in `defaulted`) |
| `BlendSpace`, `BlendSpace1D`, `AimOffsetBlendSpace*` | axes (name, min, max, grid), samples (animation, position, rate scale), interpolation times |
| AnimBlueprint | parent, target skeleton, baked state machines (states, initial state, conduits, transitions with crossfade and blend mode), node types of the anim graph, animation assets referenced by the nodes, the variables' defaults, sync groups |
| `PhysicsAsset` (the ragdoll) | bodies per bone (shape counts: capsule, sphere, box, convex, tapered capsule; mass when `bOverrideMass`; physical material), constraints (bones, swing1/swing2/twist motion and limit, linear motion and limit, drives, type: `fixed`, `hinge`, `ball`, `cone-twist`, `mixed`) |

Cooked constraints omit the values equal to the engine's struct defaults; gst fills them from the constructors
(`Swing1/Swing2/TwistMotion` free, limits 45 degrees, linear locked with limit 0) and lists each filled field in
`defaulted`. State machine conditions (the transition rule graphs) are Kismet bytecode, not state data: read them
with `--bytecode` in the Blueprint JSON.

## Library (`gst lib`)

`facets.FACETS` feeds rigs, animation and joints of Unreal games from `unreal_animation` (and the name counts of
`unreal_assets`): `rigs` rows `unreal-skeleton` (bones, root) and `unreal-skelmesh` (bones, LOD0 vertices/triangles,
max influences, morph targets as blendshapes); `clips` (`unreal`, clip kind `sequence`/`montage`); `anim_graphs`
(`unreal-abp`: machines as layers, states, transitions, blend nodes, referenced assets; `unreal-blendspace`);
`joints` (one per constraint, family `PhysicsAsset`) and `ragdolls` (one per PhysicsAsset). `gst lib compare rigs`
then shows Unreal bone counts next to Unity and Godot. Tunable values have no library facet yet (no cross-engine
table fits them); they stay in each workspace's `unreal_values.json`.

## IoStore (UE5) path

`gst assets to-legacy` and the `unreal.iostore` job share one plan (`export.iostore_plan`):

1. `global.utoc` encrypted without its published key: nothing is converted (code 3, `blocked-policy`); without its
   script objects no import resolves.
2. Every `.utoc` encrypted without a usable published key: code 3 with every container named and the reason; the
   plain `global.utoc` script objects are still read by `gst analyze unreal_assets`.
3. Some plain, some encrypted (`mixed`): retoc opens every `.utoc` of a folder and stops at the first one it cannot
   decrypt, so gst builds `dumps/unreal/mount/<name>/` (the job: its staging folder) with only the convertible
   containers and `global.utoc/.ucas` (symbolic links when Windows allows them, else copies; the install is never
   written) and converts that. The left-out containers are listed in the result and in `_folder.gs.json` (`blocked`).
4. All convertible: the Paks folder is the retoc input. retoc takes one AES key per run; containers that need a
   second key are left out with that reason.

retoc runs with `--no-shaders` (uas reads packages; a shader library that fails to convert would stop the whole run)
and `--version UE5_x` from the engine version. When retoc prints no `Extracted N (M failed)` line (another retoc
version) but wrote a pak, the count is unknown (`extracted: null`) and uas still runs. uas then reads every legacy
pak of `dumps/unreal/legacy/` in one run (it deletes outputs of packages missing from a later run).

### Measured on Witch It (UE 5.x, encrypted, `no-runtime`)

| step | result |
| --- | --- |
| `gst assets list` | 53 containers, 54,619 paths from the manifest (2.4 s) |
| `gst analyze unreal_assets` | `global.utoc` is plain: 48,699 script objects in 519 native modules (2.9 s) |
| `gst assets to-legacy --filter Character/` | exit 3: "every IoStore container is encrypted without a usable published key (26: ...)", plus the hint that the script objects are still read |
| `gst study <slug> --only unreal.inventory,unreal.iostore` | `unreal.inventory` done, `unreal.iostore` `blocked-policy` (3.6 s, no tool started for the encrypted containers) |

### How to validate once an unencrypted UE5 game is installed

No unencrypted UE5 IoStore game was available when this path was built; the steps below are the check to run (short,
on a filtered set; never a full conversion):

1. `gst new "<install folder>" --slug <slug>` (recon) and `gst assets list`: `assets/unreal/containers.json` must show
   the `.utoc` containers with `encrypted: false` and `how: retoc list`; `assets/INVENTORY.md` lists their paths.
2. `retoc to-legacy --dry-run --no-shaders --version UE5_x "<Paks>" <scratch>/x.pak` once by hand: it must end without
   "missing encryption key" (dry run: writes nothing).
3. `gst assets to-legacy --filter <one small folder, e.g. Characters/Hero/> --types json --all-types`: expect
   `dumps/unreal/legacy/legacy-<hash>.pak`, the `Extracted N (0 failed)` count in `logs/retoc-legacy-<hash>.log`, and
   JSON files in `assets/unreal/iostore/json/`. If uas reports unversioned properties, the run stops with
   `blocked-policy` asking for a `.usmap`: add a published one (`game.json` `usmap: {path, source}`) or the user's in
   `notes/*.usmap`, then rerun.
4. `gst analyze unreal_values` and `gst analyze unreal_animation` read the IoStore JSON like the pak JSON
   (`assets/unreal/iostore/json/` is under `assets/unreal/`); check one Blueprint's `values` and one skeleton's bones.
5. `gst study <slug> --only unreal.inventory,unreal.iostore --opt unreal.filter=<same folder>`: the job must end
   `done` with the same counts, its JSON in `assets/unreal/iostore/json/`.
6. A game with some containers encrypted and some plain: step 3 must list the left-out containers (`left out
   (encrypted without a usable published key): ...`) and convert the rest from `dumps/unreal/mount/<name>/`.
   Not verified on a real game yet: whether retoc resolves every import when only some containers are mounted.

## Policy limits

- **AES keys**: only community-published keys with their source URL, in `game.json` (`keys`) or the user's file
  `notes/keys.json`:

  ```json
  {"keys": [{"key": "0x<64 hex>", "guid": "<32 hex, optional>", "source": "https://where-it-was-published"}]}
  ```

  An entry without an http(s) `source` is ignored (and the reason shows in the error). Never brute force, never key
  extraction from the exe or from memory (`policy.check(..., "decrypt")`, guardrails rule 4). The key reaches uas
  through a file (`dumps/unreal/keys/<guid>.key`), never the command line; the retoc log shows it as `<key>`.
- **`.usmap`**: only from a published source (`game.json` `usmap: {path, source}`) or a file the user puts in
  `notes/*.usmap`. gst does not generate mappings at runtime (that injects into the game; even for `offline-ok` it
  stays out of gst). An export that needs a `.usmap` and has none becomes `blocked-policy` with the reason.
- **Nothing is built**: `repak pack`, `retoc to-zen`/`pack-raw`, UAssetGUI `fromjson` are modding and the guard blocks
  them (rule 1). gst only lists, converts into the workspace and exports.
- `no-runtime` games (anti-cheat, e.g. Witch It/VAC) keep their on-disk data open: inventory, headers, the script
  objects of a plain global. Encrypted content without a published key stays closed.

## Cost (measured)

| operation | game | time | output |
| --- | --- | --- | --- |
| headers + stdlib lister | DRG (pak v11, 2.3 GB, 109,939 entries) | 0.14 s | - |
| `repak list` | DRG | 1.4 s | - |
| `gst assets list` | DRG | 4.3 s | inventory 2.4 MB |
| `gst assets list` | Witch It (53 containers, encrypted) | 1.9-2.4 s | 54,619 manifest paths |
| uas: mount the pak and plan | DRG | 4-20 s per call (fixed) | - |
| uas JSON of 92 packages (Grunt family + DataTables + BP_PlayerCharacter, with bytecode) | DRG | 9 s | 89 JSON, 2.5 MB |
| uas JSON, union of filters + 10 player skeleton/animation/physics packages, `--all-types` | DRG | 8.4 s | 102 JSON, 3.6 MB |
| `gst analyze unreal_values` / `unreal_animation` over 102 JSON | DRG | 0.7 s / 1.8 s | - |
| uas models+textures+animations of the Grunt family | DRG | 18 s (3 modes) | 36 files, 3.6 MB |
| `gst study --only unreal --opt unreal.name=Enemies/Spider/Grunt/ --opt unreal.modes=json,models` | DRG | 21 s (4 jobs); again: 0 jobs | 4.4 MB |
| `retoc print-script-objects` | Witch It `global.utoc` | ~1 s | 48,699 objects, 519 modules |

The fixed uas cost (mount and plan) is why it runs **once** per container and mode with the whole filter in a
response file, instead of batches. A full DRG export (52k packages x 4 modes) takes hours and tens of GB: narrow with
`unreal.name`/`--name` when everything is not needed.

### DRG player character (UE 4.27, filtered export)

`--name` regex: `CharacterStructure/Rig/(3P_Dwarf_Rig_Skeleton|3P_Dwarf_Rig_PhysicsAsset|SK_3P_Dwarf_Rig)\.uasset$`,
four `Gear_Unarmed/TP/TP_Unarmed_*` sequences and one montage, `Gear_Pistol/TP/3P_Pistol_Grappling_Blendspace`,
`Character/ABP_TP_Dwarf`, plus the earlier `Character/BP_PlayerCharacter` and the DataTables. Results:
`3P_Dwarf_Rig_Skeleton` 161 bones (root `Dwarf_RootBone`, depth 15, 56 sockets); `SK_3P_Dwarf_Rig` 6,862 vertices,
9,054 triangles, up to 8 influences; 4 sequences at 30 fps (0.2-0.67 s), 2 montages, 1 blend space (2 axes, 5
samples); `ABP_TP_Dwarf` state machines `Locomotion` (15 states, 29 transitions) and `AdditiveTurns` (3 states);
`3P_Dwarf_Rig_PhysicsAsset` 15 bodies (10 capsules, 5 spheres) and 14 constraints (10 ball, 4 mixed: the beard);
`BP_PlayerCharacter` parent `/Script/FSD.PlayerCharacter`, 95 components, `CharMoveComp.MaxWalkSpeed` 300,
`CharMoveComp.JumpZVelocity` 500, `CharMoveComp.AirControl` 0.35, `RunningSpeed` 435, `Health.MaxHealth` 110.

## When a gst-cue4 (.NET + CUE4Parse) would be needed

`tools/gst-cue4/` was reserved for something uas could not cover. It has not been needed: uas already
exposes what CUE4Parse does (JSON with bytecode and with every class through `--no-skip-types`, textures, models,
animations, audio, `--aes-file`, `--usmap`, type filters through `list --format types` + `--expression`, response
files), and the skeleton, animation and physics data above come from its JSON. It would be justified for something
uas does not export (e.g. meshes as glTF instead of `.uemodel`, the reference pose of a SkeletalMesh's own skeleton, or
reading packages straight into the index without files).
