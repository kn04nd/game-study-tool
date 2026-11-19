# gst module contracts

The contracts the modules of gst share: the extractor API, sidecars, the workspace layout, the analysis API and the
outputs of each engine backend, the plugin, the library and the migration. Section numbers are stable (code, tests
and `CLAUDE.md` cite them). What is still missing or unverified: `docs/KNOWN-LIMITATIONS.md`.

## 1. Extractor (`src/gst/extractors/base.py`)

```python
@dataclass
class Unit:      id, extractor, relpaths: list[str], size: int, fingerprint: str = "", meta: dict = {}
@dataclass
class Result:    outputs: list[str] = [], out_bytes: int = 0, status = "done", error: str | None = None,
                 tool: str = "", tool_version: str = "", notes: list[str] = []
@dataclass
class Context:   workspace: Path, game: dict, install_dir: Path, out_root: Path, policy: str,
                 log: logging.Logger, options: dict = {}

class Extractor(ABC):
    name: str                 # unique, "<engine>.<thing>", e.g. "unity.textures", "files.loose"
    category: Category        # code|texture|mesh|audio|animation|prefab_scene|material_shader|data|
                              # localization|font|video|map|config_save|other
    resource: Resource        # cpu|io|heavy  (heavy = one at a time: Ghidra, AssetRipper, ilspycmd of the whole game)
    policy_need: PolicyNeed   # static|decrypt-published-key|runtime-offline|none
    def applies(ctx) -> bool                  # optional, cheap
    def discover(ctx) -> Iterable[Unit]       # generator: never a whole list in RAM
    def run(unit, ctx) -> Result              # runs inside the worker (subprocess)
```

- `Unit.relpaths` are relative to `install_dir`, with `/`. `Unit.size` = input bytes (RAM weight and disk
  estimate). `Unit.fingerprint` = sha256 of `(relpath, size, mtime_ns)` of each input + buildid; the pipeline computes
  it when empty. `Unit.meta` is JSON only.
- `Result.status` ∈ `done | failed | unsupported | blocked-policy | paused-disk | skipped`. An expected failure
  (unsupported format, container encrypted without a published key) is a status with the reason in
  `error`/`notes`, not an exception. `error` is the full text, never cut.
- `Result.outputs` are paths relative to the workspace.
- Registration: `@register("unity-mono", "unity-il2cpp")` in `src/gst/extractors/registry.py`
  (`*` = every game). Each extractor module is one line of `PROVIDERS`.
- No caps: no `DEFAULT_LIMIT`, `timeout=<constant>` or `[:N]` on errors inside extractors. A limit only as an explicit
  option in `ctx.options`. Python subprocesses always with `-X utf8`.
- Writes: only under `ctx.out_root`; first into `<out_root>/.partial/`, atomic rename, sidecar last. Never into the
  game install.

## 2. Sidecar `<file>.gs.json` (unified schema)

The `.gs.json` suffix stays (the legacy gs reads the same workspaces). Keys:

| key | type | required | description |
|---|---|---|---|
| `file` | str | yes | name of the exported file |
| `size` | **int** | yes | bytes of the exported file (never a list) |
| `sha256` | str | yes | sha256 of the exported file (streamed) |
| `source` | str | yes | source file, relative to the install (`/`) |
| `path_id` **or** `res_path` | int / str | yes (one of them) | Unity: the object's `path_id`; Godot/Unreal/others: `res_path` (`res://...`, `/Game/...`, path in the container) |
| `build` | str | yes | game buildid (game.json `buildid`; `"?"` when unknown) |
| `tool` | str | yes | tool/library that exported it (`UnityPy`, `AssetRipper`, `gdre_tools`, `uas`, `vgmstream`...) |
| `tool_version` | str | yes | version of that tool |
| `exported_at` | str | yes | local ISO 8601 |
| `dimensions` | list[int] | no | `[w, h]` / `[w, h, d]` of textures and images (formerly in `size`) |
| `type`, `name`, `container`, `extractor`, `unit` | str | no | object type, full original name (no 80-character cut), container/bundle, extractor and unit that produced it |

`_folder.gs.json` (a whole folder produced by one tool, e.g. AssetRipper) follows the same schema with
`file = "."`, `size` = bytes of the folder and `sha256` optional. `assets/__init__.py::write_sidecar` is the only
writer.

## 3. Workspace v2 (additive over the gs v1 layout)

```
GS_HOME/<slug>/
  game.json            "gs_workspace": 1 stays; v2 adds "gst_workspace": 2
  recon/ decomp/ assets/ dumps/ traces/ notes/ index/ logs/   (v1 layout, unchanged)
  jobs/
    plan.json          chosen extractors, units, estimate (written by gst study; --dry-run writes nothing)
    jobs.db            SQLite WAL (table jobs: id, extractor, unit_id, relpaths, fingerprint, buildid,
                       status, attempts, started_at, duration_s, outputs, out_bytes, error, tool, tool_version)
    STATUS.md          readable progress
    COVERAGE.md        per category: discovered / extracted / failed / unsupported / blocked (+ reason, next step)
  analysis/
    <name>.json + <name>.md        outputs of `gst analyze <name>`
    systems.json
  logs/study-<ts>.jsonl            pipeline events
```

Extractor outputs per category (default; an extractor may create subfolders):
`decomp/<backend>/`, `assets/<engine>/<category>/<container>/...`, `assets/audio/...`, `dumps/rea/...`.
A new build (different buildid) = sibling workspace `<slug>@<buildid>`.

`gst migrate` only creates `jobs/` and `analysis/` and imports existing outputs as `done` jobs with
`tool = "legacy"`; `src/gst/compat/legacy.py` reads v1 workspaces. Details in section 12.

## 4. Shared registries and extension points

| what | rule |
|---|---|
| `extractors/registry.py::PROVIDERS` | one line per extractor module |
| `analysis.PROVIDERS` | one line per analysis module |
| `cli.py::GROUPS` | one line per new command group |
| `pyproject.toml` | dependencies on their own lines; heavy ones under the `full` extra |
| `extractors/base.py` | only compatible additions (`stage`, `after`, `finalize`, `out_subdir`, `after_earlier_stages`) |

Tests run with `.venv/Scripts/python -m pytest` (see `docs/DEV.md`) and must pass without games or tools installed.

## 5. Unity inventory (format 2)

Everything in `assets/unity/_inventory/` (the `out_root` of the Unity extractors is `<ws>/assets/unity`):

- `<relpath with / -> __>.jsonl.gz`: gzip JSONL, streamed by `unity.inventory` (one unit per serialized file or
  bundle). Line 1 = header `{"_header": 1, "file": <relpath>, "kind"}`; in a bundle, a `{"_cab": <name>}` line opens
  the lines of each CAB; the other lines are objects `{"type", "class_id", "name", "path_id", "size"}` (+ `container`,
  `script` when known). `file`/`cab` are not repeated per line (a PEAK map bundle has ~21 M objects).
- `<relpath with __>.summary.json`: count and bytes per type, scripts, externals, CABs, `table`, `table_bytes`.
  The export extractors use the summary to skip files without their types.
- `INDEX.json` (`"format": 2`): per file, objects, types, bytes and the table path. There is **no** concatenated
  `assets/inventory.jsonl`; `assets/INVENTORY.md` is the readable summary.
- Reading: always through `gst.engines.unity.inventory.iter_rows(<_inventory>, files=, types=, name=)` (returns full
  rows with `file`/`cab`/`container`, without loading a whole table); `monoscripts()` replaces the old `mono_name` via
  `inventory.jsonl`. It also reads old `.jsonl` tables (one line with `file` per object).
- `unity.inventory` has `finalize(ctx)`: rebuilds `INDEX.json` + `INVENTORY.md` (idempotent) and the pipeline runs it
  as a `<finalize>` job after every file unit (see below).
- Order: `unity.inventory` `stage = 40`; exports `stage = 50`, `after = ("unity.inventory",)`;
  `unity.assetripper_project` `stage = 70`.
- Finalize: `Extractor.finalize(ctx) -> Result | None` (optional) runs **once** after every unit of the extractor, as
  its own job (`unit_id = "<finalize>"`, `gst.pipeline.worker.FINALIZE`): the runner starts it only when no other unit
  of the extractor is `pending`/`running`; whoever has the extractor in `after` also waits for the finalize; the plan
  reruns it whenever a unit of the extractor reruns (fingerprint = those of the units). `None` becomes `skipped`.
  `unity.inventory` uses it for `INDEX.json` + `INVENTORY.md` (the `_merge` unit is no longer generated; `run` still
  accepts a `_merge` from an old jobs.db).
- `Extractor.out_subdir`: `ctx.out_root = <ws>/<out_subdir>` in the worker and the plan (`""` = the workspace).
  Unity: `assets/unity`. `Extractor.after_earlier_stages`: waits for every job of a lower `stage` and reruns when one
  of them reruns (used by the analysis stage).

## 6. Analyses (`gst analyze`)

Code: `src/gst/analysis/__init__.py` (registry, context, writing), `src/gst/analysis/stage.py` (`gst study` jobs),
`src/gst/cmd_analyze.py` (CLI). Modules: `analysis/unity/<name>.py` (Unity) and `analysis/<name>.py` (generic). Shared
Unity helpers: `analysis/unity/__init__.py` (`UnityFile`, `Resolver`, `ptr`, `is_scene`, `mesh_stats`, `builtin`) and
`analysis/unity/fields.py` (`FieldStats`, `describe`).

### API

```python
from gst.analysis import Report, UNITY, ANY, analysis, md_table, counted, stats

@analysis("animation",                      # name = `gst analyze animation` = analysis/animation.json + .md
          engines=UNITY,                    # engine.key values; ANY ("*") = every game
          needs=("unity.inventory", "unity.install"),   # required: missing -> status "skipped" with the reason
          uses=("analysis:prefabs",),       # optional; "analysis:<x>" also orders (x runs first)
          description="Clips: curves, CRC32 path_map, events", version=1,   # version++ when the output changes
          options={"clip": "regex on clip names"})                          # documentation (--list)
def animation(ctx) -> Report:
    for rel in ctx.inventory_files(("AnimationClip",)):          # files with the type (from the summaries)
        with UnityFile(ctx, rel) as uf:                          # one file at a time; records the input
            for sf in uf.files():                                # one CAB at a time
                for obj in list(sf.objects.values()): ...
    rows = ...                                                   # sort everything (deterministic output)
    ctx.write_side_json(per_clip, "<file>", "<clip>.json")       # per-item outputs in analysis/animation/
    return Report(data={"clips": rows}, markdown=md_table([...], [...]), summary={"clips": len(rows)})
```

Known requirements (`analysis.NEEDS`): `unity.inventory` (`assets/unity/_inventory/` with tables), `unity.install`
(install with `*_Data`), `code.index` (`index/code.db`), `dumps.rea` (JSON in `dumps/rea/`), and `analysis:<name>`
(`analysis/<name>.json`). A new requirement = an entry in `NEEDS` (function `(ws, game) -> (ok, reason, path)`).

`Context` (`ctx`): `workspace`, `game`, `install_dir`, `options`, `out_dir` (= `<ws>/analysis`), `opt(k, default)`,
`opt_list(k)` (repeated or comma-separated), `use(label, path)` (input in the header), `tool(name, version)`,
`log(msg)`, `rows(files=, types=, name=)` (= `inventory.iter_rows`, streaming), `inventory_files(types)`,
`inventory_dir()`, `open_unity(relpath)`, `load(name)` (JSON of another analysis, recorded as input),
`side_path(*parts)`, `write_side_json(data, *parts)`, `side_dir()`. `UnityFile(ctx, relpath)`: `files()`,
`script(obj)` (ScriptInfo from the raw `m_Script`, without typetree), `mono(obj, info)` (typetree through the source
chain, read from the object's bytes), `resolve(sf, pptr)`, `catalog`, `generator()`.

Running: `analysis.run(ws, game, name, options)` -> `{"name", "status": done|skipped|failed, "reason", "json", "md",
"outputs", "summary", "seconds"}` (never raises for a broken analysis; the traceback goes in `reason`);
`analysis.run_all(ws, game, options)` runs the applicable ones in dependency order; `options_for(name, opts)` gives each
analysis the `"<name>.<k>"` keys (they win) and the shared ones without a dot.

### Output contract

- `analysis/<name>.json` = `{"header": {...}, "summary": {...}, "data": ...}` (+ `"notes"`). `analysis/<name>.md` =
  title (`description`), the header as a list and the `Report` Markdown (tables for humans; the JSON has everything).
- `header`: `analysis`, `version`, `game`, `slug`, `build`, `engine`, `engine_version`, `inputs` (label, path relative
  to the workspace or `<install>/...`, bytes, mtime), `tools` (gst, UnityPy, typetree source...), `options`,
  `generated_at`, `seconds`.
- Deterministic: two runs over the same inputs differ only in `generated_at`/`seconds` (sort lists; `counted()` sorts
  counters).
- Per-item outputs: `analysis/<name>/...` (the analysis deletes the folder at the start of a new run).
- Atomic writes (`.partial-<pid>` + rename); strings with lone surrogates become `\udcxx` escapes.
- Streaming: never load every object of the game; one file/CAB at a time; no caps (a limit only as an option).

### `gst analyze` and `gst study`

- `gst analyze --list [--game slug] [--json]`; `gst analyze <name> [--opt k=v] [--script X] [--marker X]
  [--file REGEX] [--name-filter REGEX]`; `gst analyze --all`. `--json` output = the dict of `run`.
- `gst study`: each analysis becomes the extractor `analysis.<name>` (stage 60, `after_earlier_stages`, `after` = the
  analyses of `analysis:<x>`), one job each. A missing requirement = job `skipped` with the reason. Options:
  `gst study --opt prefabs.marker=EnemyParent` (or a shared `--opt file=...`).

### How to add an analysis

1. Create `src/gst/analysis/unity/<name>.py` (or `analysis/<name>.py`) with the decorated function; heavy imports
   (UnityPy) only inside the function (the registry is imported by `gst study` and by `--list`).
2. Add the module to `analysis.PROVIDERS` on its own line.
3. Logic over plain dicts, tested with synthetic objects in `tests/unit/test_analysis_<name>.py` (see
   `test_analysis.py`: `FakeObj`, `_write_inventory`, `fake_analyses`); no derived game material in the repo.
4. Never write outside `analysis/` (e.g. `mapping.compute` instead of `mapping.build`).

Core Unity analyses: `prefabs` (roots + tree per prefab; `marker`, `detail=auto|all|none`, `scenes`, `file`, `name`),
`mono_values` (`script`, `file`, `values=0`), `catalogs` (`script`, `file`, `values=1`) and `systems` (code.db +
`analysis:prefabs` + `dumps.rea`). `prefabs.json` `data.scripts` (`{class: {prefabs, components}}`) and
`data.prefabs[*]` (`file`, `cab`, `go`, `transform`, `name`, `container`, `scripts`, `markers`, `smr`, `tris`,
`animators`, `detail` -> `analysis/prefabs/<file>/<name>__<go>.json` with `nodes`) are the base for rig, joints and
animation.

### Animation, rig and physics analyses (Unity)

Modules in `analysis/unity/`: `animcore.py` (pure helpers: `crc`, `path_map`/`animator_maps`, `quat_to_euler`/
`euler_to_quat` in Unity's convention (Z, then X, then Y), `matrix_scale_rotation`, `decode_clip`,
`clip_bindings`/`summarize_bindings`, `iter_typed`, `clip_key`), `controller.py`, `animation.py`, `avatar.py`,
`rig.py`, `joints.py`. Order: `prefabs` -> `controller`, `rig`, `avatar`, `joints` -> `animation`.

- `controller` (`uses` code.index, dumps.rea; `bruteforce=0|1|2`, `name`, `file`): `data.controllers[*]` = `file`,
  `cab`, `path_id`, `key` (`<serialized file in lower case>:<path id>`), `name`, `kind` (`controller`|`override`),
  `params` (`name`, `hash`, `type`, `default`, and when the name did not come from its own `m_TOS`: `name_source`
  tos_other|code|rea|clip|bruteforce, `confidence`, `evidence` path:line, `alternatives`), `layers` (`states` with
  `motion` = clip or blend tree, `clips`, `transitions`; `any_state`), `clips` (`[{key, name}]`; override = the base
  clips with the swapped ones), `counts`. `data.hash_params` lists the parameters outside the TOS.
- `animation` (`uses` analysis:prefabs, analysis:controller, analysis:rig; `clip`, `file`, `bindings=0`):
  `data.clips[*]` = `key`, `name`, `length`, `fps`, `loop`, `legacy`, `compressed`, `kind`
  (humanoid|generic|blendshape|properties|empty), `curves`, `bindings`, `by_kind`, `by_class`, `transforms_moved`,
  `bones_moved`, `unresolved`, `resolved_global`, `mapped_by` (controller|animation|legacy|global|none),
  `controllers`, `root`, `events`, `detail` -> `analysis/animation/<file>/<clip>__<pid>.json` (`bindings`:
  `path`, `class`, `kind`, `keys`, `const`, `range`, `attribute`). Path maps: Animators of the detailed prefabs
  (path relative to the Animator's GameObject, CRC32).
- `avatar` (`uses` analysis:prefabs): `data.avatars[*]` = `skeleton`, `humanoid`, `mapping` (Mecanim internal order,
  25 bones + fingers), `root_motion_bone`, `settings`, `human_description` and `mapping_check` when there is a
  `m_HumanDescription`, `animators`.
- `rig` (`needs` analysis:prefabs; `name`, `file`): `data.smr[*]` (per SMR of the detailed prefabs: `bones`,
  `root_bone`, `mesh` with `max_influences`/`influences_hist`/`bindpose_scales`/`bindpose0`, `deform_bones`,
  `helper_bones`, `blendshape_names`, `hash_check`, `geometry_check`, `mapping_ok`), `data.meshes`,
  `data.constraints` (Animation Rigging, Final IK, Unity constraints, PPtrs named by their path in the prefab),
  `data.blendshape_names`; per prefab `analysis/rig/<file>/<prefab>__<go>.json` with `bones_detail`.
- `joints` (`uses` analysis:prefabs; `scenes`, `file`, `ragdoll`): `data.prefabs[*]` (root, `family`, `joints` with
  type, path, connected body, limits/springs/drives/motor, `body` = Rigidbody of the same GO, `scripts`),
  `data.families`, `data.ragdolls` (classes `*Ragdoll*`/PuppetMaster from `prefabs.json`, dump in
  `analysis/joints/<file>/<prefab>__<go>.json`).

### Asset analyses

| analysis | engines | reads | output (`data`) |
| --- | --- | --- | --- |
| `dcc` | all | Unity inventory (names of Mesh/AnimationClip/Material/Texture2D/GameObject), raw Transforms, glTF in `assets/`, Godot `.tscn`/`.import` | `rules` (evidence per rule: objects, unique names, sample, top), `families` (verdict = **inference**: present/weak/ambiguous/absent), `transforms` per file (rot ±90 X, scale 100/0.01, per type), `gltf` (generators, node signatures), `godot` (`import_sources`), `caveats` |
| `gltf_summary` | all (stdlib) | every `.glb`/`.gltf` under `assets/` (`root=`) | `files` (tris, verts, meshes, materials, skins, influences, anims with channels/keys/dur/dt_min, bbox, IBM scale, signatures), `families` (folder under the common root, `family_depth=`), `generators`, `influences_hist`; `detail=REGEX` writes the summary with bone names in `analysis/gltf_summary/` |
| `shaders` | Unity | Shader (`m_ParsedForm`), Material (builtin typetree) | `shaders` (props, types, keywords, subshaders/passes, LightMode, fallback, amplify/shader_graph/... signatures, materials using them), `families` (name before the first `/`), `shader_index` (`file\|pid` -> name, used by `textures`) |
| `textures` | Unity | Texture2D, Material, `analysis:shaders` | `textures` (size, format, mips, streamed, materials/slots, normal by slot/name), `sizes`, `formats`, `families` (by shader family), `atlas_hints` (shared by >= `atlas_min` materials; name *atlas*) |
| `audio_refs` | Unity | AudioClip (inventory), MonoBehaviour/AudioSource (raw bytes + typetree) | `clips` (`file\|pid`, refs), `refs` (class, GO, root, field = typetree path or `@0xOFF` without typetree; a raw match the typetree does not confirm is dropped), `holders`/`holder_refs` (ScriptableObjects holding clips and who references them, `depth=2`), `scan` |
| `audio_catalog` | all | `analysis:audio_refs` + `.gs.json` sidecars of the exported clips (Unity) or the Godot project's `.tscn`/`.tres`; sources in `decomp/` and project scripts | `events` in the `assets/audio/catalog.json` schema (`gst audio check/page`); publishes to that file when absent or previously written by gst (marker `assets/audio/.catalog-by-gst`; `publish=0\|1`): the only analysis that writes outside `analysis/` |

### Unity catalogs, scene animators, humanoids, audio categories (guide: docs/tools/unity.md)

- Addressables: `engines/unity/export/addressables.decode_catalog(path)` decodes `catalog.json` and the binary
  `catalog.bin` (1.21+/2.x) to one summary: `format`, `version`, `locator_id`, `build_version`, `provider_ids`,
  `internal_ids`, `bundles`, `resource_types`, `keys` (`{key, type, locations}`), `locations` (`{primary_key,
  internal_id, provider, type, dependencies, bundle?}`), `assets` (`{address, internal_id, type, provider, bundle,
  dependencies, keys, location}`). `unity.addressables` writes `catalog[.bin].decoded.json` (+ `active`);
  `catalogs` v2: `data.addressables` = `catalogs` (+ `active`, `keys`, `locations`, `assets`), `active`, `assets`
  (asset -> bundle map of the active catalog), `bundles[*].in_catalog`/`addresses`, `compare`.
- `prefabs` v2: option `scenes` = `animators` (default) | `all` | `0`; `data.scene_animators` (rows like
  `prefabs` plus `scene`, `scene_path`, `detail` under `analysis/prefabs/scenes__<file>/`); `summary.scene_animators`.
  `prefabs.detailed_rows(doc)` = prefab rows + scene animators with a detail (read by `animation` and `avatar`).
- `analysis/unity/humanoid.py`: `BONE_NAMES` (55), `MUSCLE_NAMES` (95), `MUSCLE_BONE`, `clip_attribute(index)`,
  `muscle_table(mapping)`. `animation` v2: humanoid bindings `attribute` (name), `group`, `index`; clips
  `muscles_moved`, `humanoid_groups`. `avatar` v2: `muscles`, `hd_limits`; skeleton names by name hash, human bones
  through the human skeleton.
- `audio_refs` v2: `data.mixer_groups` (`key -> path`), `refs[*].mixer_group`/`mixer_via`
  (audiosource|field|gameobject), `refs_by_mixer_group`, `summary.mixer_groups`/`refs_with_mixer_group`.
  `audio_catalog` v2 (Unity): `category` = mixer group unless the default one, else owner family; `kind` in
  music|ambient|voice|ui|footstep|impact|weapon|sound; `fields.kind_by` (mixer|component|clip name|default),
  `fields.mixer_group`; `summary.kind_by`.
- `dcc`: `dup_suffix_.NNN` skips decimal sizes (`0.NNN` after a separator or `<n>x`).

## 7. IL2CPP and REA

### IL2CPP

Code: `src/gst/engines/il2cpp/` - `metadata.py` (`global-metadata.dat` header in stdlib + basic PE through pefile),
`tooling.py` (tool choice by version, run without timeout in an empty cwd made by gst), `cpp2il.py`,
`il2cppdumper.py`, `inspector.py` (Il2CppInspectorRedux, legacy CLI), `decompile.py` (ilspycmd over the dummy DLLs +
address table) and `extractors.py`. Catalog: `cpp2il`, `il2cppinspector`, `il2cppdumper` in `data/catalog.toml`, each
with `metadata_versions` (`"39"`, `"24-29.1"`); `tooling.supporting(version)` returns the ones covering the version, in
order of preference (Cpp2IL, Il2CppDumper, Il2CppInspector).

Extractors (`unity-il2cpp`, stage 20, inputs = `GameAssembly.dll` + `global-metadata.dat`, fingerprint from their
size/mtime + buildid):

| extractor | resource | output |
| --- | --- | --- |
| `code.il2cpp.metadata` | cpu | `decomp/il2cpp/metadata.json` |
| `code.il2cpp.dummydll` (`after` metadata) | heavy | `decomp/il2cpp/dummydll/*.dll` + `MANIFEST.json`; raw output in `decomp/il2cpp/<tool>/` |
| `code.il2cpp.maps` (one unit per map tool covering the version) | heavy | `decomp/il2cpp/il2cppinspector/`, `decomp/il2cpp/il2cppdumper/` |
| `code.il2cpp.decompile` (`after` dummydll) | heavy | `decomp/cs-il2cpp/<Assembly>/**.cs` + `MANIFEST.json`, index, `il2cpp_addresses`, `decomp/il2cpp/addresses.tsv` |

- A version with no tool in the catalog = `unsupported` with the reason (not `failed`); a magic other than `FAB11BAF`
  (encrypted/obfuscated metadata) = `unsupported`, never decrypted. Tool not installed = `failed` with
  `gst tools install <id>`.
- The Unity exports (`UnityFileExtractor.after`) wait for `code.il2cpp.dummydll`; in a Mono game the dependency has no
  jobs and does not block. The typetree chain (`engines/unity/typetree.py`) reads `decomp/il2cpp/dummydll/` and skips
  the `il2cpp` source (TypeTreeGenerator) when `metadata.json` has `typetree_il2cpp.ok = false`.
- Options: `il2cpp.all=1` (every assembly in ilspycmd), `il2cpp.tool=<id>` (forces the dummy DLL tool),
  `il2cpp.maps=0` (no maps), `il2cpp.probe_typetree=0`, `timeout` (only when given).
- CLI: `gst decompile il2cpp [--all] [--maps] [--force]` runs the same sequence in the current process and indexes.

`decomp/il2cpp/metadata.json` (`format: 1`): `inputs` (`{relpath: [size, mtime_ns]}`), `metadata_file`,
`binary_file`, `version_key` (`"39"`, `"24.2"`...), `metadata` (`magic`, `version`, `standard`, `layout`
`offset-size`|`offset-size-count`, `header_size`, `sections[] {name, offset, size, count?}`, `subversion
{version, candidates, how}`, `counts {images, types, methods, fields, properties, events, parameters,
string_literals, assemblies, generic_containers, nested_types, strings: {count, exact}}`, `images[] {index, name,
type_start, type_count}`), `binary` (`format`, `machine`, `image_base`, `timestamp`, `sections`, `exports`,
`il2cpp_exports`), `tools {supporting, catalog}`, `typetree_il2cpp {ok, error?, seconds}`.

`MANIFEST.json` of each `decomp/il2cpp/<tool>/` (and a copy in `dummydll/`): `tool`, `tool_version`, `role`
(`dummydll`|`maps`), `ok`, `command`, `exit_code`, `seconds`, `metadata_version`, `facts` (from the tool's log:
`code_registration`, `metadata_registration`, `unity_version`, `errors`...), `dlls`, `outputs`, `inputs` (as in
`metadata.json`: an output is current when `inputs` matches and `ok` is true), `game_build`, `generated_at`. Full log
in `<tool>.log`.

**Name maps for Ghidra** - fixed paths, relative to the workspace:

| file | tool / versions | content |
| --- | --- | --- |
| `decomp/il2cpp/il2cppdumper/script.json` | Il2CppDumper, metadata <= 31 | `ScriptMethod[] {Address (RVA, int), Name, Signature, TypeSignature}`, `ScriptString[]`, `ScriptMetadata[]`, `ScriptMetadataMethod[]`, `Addresses[]` (Il2CppDumper format) |
| `decomp/il2cpp/il2cppdumper/il2cpp.h`, `dump.cs.txt` | same | C structs of the classes; C# with RVAs in comments (`dump.cs` renamed: the index does not read the same types twice) |
| `decomp/il2cpp/il2cppinspector/metadata.json` | Il2CppInspectorRedux, metadata 24-39 (Safari-TV v39: 472 MB) | `addressMap.methodDefinitions[] {virtualAddress ("0x...", VA with the image base), name, signature (C), dotNetSignature, group}`, `constructedGenericMethods`, `methodInvokers`, `stringLiterals`, `typeInfoPointers`, `functionAddresses`... |
| `decomp/il2cpp/il2cppinspector/cpp/appdata/*.h` | same | C++ types (`il2cpp-types.h`, `il2cpp-functions.h`...); the rest of the C++ project (injection scaffold) is deleted |
| `decomp/il2cpp/il2cppinspector/il2cpp_ghidra.py` | same | the Inspector's Ghidra script (no C# output: the stubs come from `decomp/cs-il2cpp`) |
| `decomp/il2cpp/addresses.tsv` and the `il2cpp_addresses` table of `index/code.db` | Cpp2IL (`[Address]`) via `decomp/cs-il2cpp` | `rva va offset length token field_offset kind qname decl path line`: RVA (without the image base) per method/accessor, token per type/field/method, offset per field; `qname` = index symbol (`gst q def <qname>`) |

The image base is in `decomp/il2cpp/metadata.json` (`binary.image_base`); `VA = image_base + RVA`.

### REA

Code: `src/gst/rea/` (`runner.py`, `evidence.py`, `split.py`, `import_index.py`, `extractors.py`). Catalog tool `rea`
(kind `npm`): `rea-agents@6.9.0` by default; `GST_REA_SPEC` (or `gst config set rea_spec ...`) picks another npm/GitHub
spec or a local `.tgz` (e.g. REA built with `patches/rea/`, see `docs/tools/rea.md`), installed in
`<tools_dir>/rea/custom-<hash>/`. `GST_REA_EXE` points at a specific `rea` (tests: a fake Python script).

**Runner** (`gst.rea.runner.run(ws, op, name, path=, options=, request=|request_file=)`): static commands only
(`STATIC_COMMANDS`; Frida, debugger, capture, adb, browser... are refused before starting), `D:/...` paths (UNC
refused), no timeout, caps that REA lets callers raise set to 2 GiB when the installed REA's `--help` lists them
(`--max-snapshot-bytes`, `--max-metadata-representation-bytes`; with `patches/rea/` the IL2CPP `--max-input-bytes`/
`--max-output-bytes`; `--artifact-limits` with `expanded_bytes` 2 GiB and the Godot directory at Node's string
ceiling). Files in `dumps/rea/evidence/<op>/` (`op` = MCP name, e.g. `inspect_managed_members`):

| file | content |
| --- | --- |
| `<name>.json` | the **raw** output of `rea <cmd> --json` (one Evidence record: `evidence_id`, `operation`, `subject`, `parameters`, `normalized_result`...) |
| `<name>.request.json` | the request in the MCP argument format; for commands with `inputJson` it is the input itself (the graph's becomes a stub `{"managed_members": {"inlined_from": ...}}` after the run) |
| `<name>.run.json` | argv, exit code, seconds, peak working set, bytes, REA version, caps used |
| `<name>.error.json` + `<name>.stderr.txt` | only on failure: REA's error JSON and the full stderr (never cut); a `<name>.json` from an earlier successful run stays (a failure never deletes Evidence); the next success removes the error files |

**Contract for Ghidra with IL2CPP names:** the IL2CPP metadata Evidence is at
`dumps/rea/evidence/inspect_il2cpp_metadata/GameAssembly.json` when the binary was linked (name = stem of the binary:
`GameAssembly`, `libil2cpp`...) and at `.../global-metadata.json` when only the metadata was read (fallback; a later
success with the binary deletes that fallback). It is REA's raw JSON: `normalized_result.images[]` (`name`,
`type_start`, `type_count`), `types[]` (`name`, `namespace`, `method_start`, `method_count`, `declaring_type` = index
or -1), `methods[]` (`name`, `declaring_type`), `method_addresses[i]` (same index as `methods`; `state` = code |
no-body | unknown, `address` and `rva` in hex), `fields[]` + `field_types[i]` (`type`, `name`), `parameters[]` +
`parameter_types[i]`, `return_types[i]`, `binary` (`artifact`, `image_base`, `code_gen_modules`...),
`string_literals[]`. Full type names: `gst.rea.split.il2cpp_type_name(types, i)` (`Ns.Outer/Inner`). The `run.json`
says whether the caps were raised.

**Split** (`dumps/rea/split/<assembly or image>/`): `INDEX.md` + per namespace `<ns>.json` (`{type: {...}}`) and
`<ns>.md` (`_global` = no namespace). Managed: methods, fields with type, engine APIs and game types called (counted),
field accesses, strings (`ldstr`). IL2CPP: methods with `state`/RVA and fields with type.
`split.split_bundle(bundle, out)` splits a bundle (`{"records": [...]}`) by artifact SHA-256.

**code.db** (`gst.rea.import_index.import_workspace(ws)`, job `rea.import`): rebuilds `rea_calls(caller, callee,
kind, assembly, evidence, caller_type, caller_method, callee_type, callee_method, il_offset, artifact_sha256)`,
`rea_fields(type, field, field_type, assembly, evidence, flags, artifact_sha256, source)`, `rea_methods(type,
method, assembly, rva, address, state, token, evidence, artifact_sha256, source)`,
`rea_field_accesses(method, field, field_type, kind, assembly, evidence)` and `rea_sources` from every JSON in
`dumps/rea/` (`evidence/` first; an (operation, SHA-256) pair already imported is skipped; the graph's `calls` edges
only go in for an artifact without a members record). `source` = `managed` | `il2cpp`; `evidence` = path relative to
the workspace. `gst q callers` merges `rea_calls` into the index results (without duplicating the same `Type.Method`;
rows marked `[REA call graph]`, location `rea:<assembly>:IL_xxxx`); `gst q callees` falls back to `rea_calls` when the
index lacks the symbol or its edges. `gst index --rebuild` deletes code.db: the next `gst study` redoes `rea.import`.

**Jobs** (all `policy_need = static`):

| extractor | engines | stage / resource | unit | output |
| --- | --- | --- | --- | --- |
| `rea.managed_members` | all (applies when there are managed assemblies) | 32 / heavy | one assembly (game, then third party, then framework; framework only with `--opt rea.framework=1`) | Evidence + split |
| `rea.managed_graph` | same, `after` members | 32 / heavy | one assembly | `project_managed_application_graph` |
| `rea.il2cpp_metadata` | unity-il2cpp | 32 / heavy | metadata + GameAssembly | Evidence with the binary (or `unsupported` + metadata-only fallback on a REA without the caps) + split; `--opt rea.binary=0` skips the binary |
| `rea.artifact` | godot | 32 / heavy | the `.pck` | `inspect_artifact` + `dumps/rea/artifact/<pck>.crosscheck.json` (REA's entries, sizes and offsets against gst's read of the PCK directory); `--opt rea.extract=1` runs `extract_artifact` into `dumps/rea/extract/<pck>/` |
| `rea.import` | all | 34 / cpu, `after` rea.* and `code.index`, `after_earlier_stages` | `code.db` | `rea_*` tables, `dumps/rea/import.json` |

The fingerprint includes the REA version and whether it accepts the IL2CPP caps: installing a patched REA reruns
`rea.il2cpp_metadata`. `gst analyze systems` reads `dumps/rea` in both formats (single record and bundle), once per
artifact.

### Ghidra with IL2CPP names

Code: `src/gst/engines/il2cpp/names.py` (name map), `src/gst/engines/il2cpp/ghidra_names.py` (Ghidra run +
`code.il2cpp.native` extractor), `src/gst/data/ghidra_scripts/GstIl2cppNames.java`.

**`decomp/il2cpp/names.jsonl`** (built by `names.build(ws, install)` on every run, from every source present):

- Line 1 = header `{"_header": 1, "format": 1, "game_assembly": {"file", "size", "sha256", "image_base"},
  "precedence", "sources": {source: [{"path", "rows"}]}, "counts": {"entries", "by_kind", "rows_by_source",
  "won_by_source", "conflicts", "conflict_pairs", "folded"}, "notes", "generated_at", "seconds"}`. The same summary
  (+ `path`, `entries_sha256`) goes to `decomp/il2cpp/names.manifest.json`.
- The other lines, one per `(rva, kind)`, sorted by `(rva, kind)`:

  | key | type | description |
  |---|---|---|
  | `rva` | int | address relative to the GameAssembly image base |
  | `name` | str | `Namespace.Type$$Method` (Il2CppDumper style), no spaces/control characters, up to 2000 characters (Ghidra's limit; a longer name gets a hash) |
  | `signature` | str \| null | C# signature (Cpp2IL/REA/Inspector) or C prototype (Il2CppDumper) |
  | `kind` | str | `method`, `method_info`, `type_info`, `string_literal`, `field`, `metadata`, `function` (unnamed code address: Il2CppDumper `Addresses`, Inspector `functionAddresses`) |
  | `type` | str \| null | owning type (`Namespace.Outer.Inner`) |
  | `source` | str | `il2cppdumper`, `cpp2il`, `inspector` or `rea` (the source that won) |
  | `ns`, `fn` | list[str], str | only on `method`: Ghidra namespace path (C# namespace parts + nested types, whole generics) and the name inside it |
  | `also` | list[str] | other names at the same address (identical code folding), from any source |
  | `value` | str | text of a `string_literal` |
  | `size`, `method_rva` | int | code bytes (Cpp2IL `Length`); RVA of the method of a `method_info` (Il2CppDumper) |

- Sources (all optional) and where they are looked for: Il2CppDumper `decomp/il2cpp/il2cppdumper/script.json`;
  Il2CppInspector(Redux) JSON with `addressMap` in `decomp/il2cpp/il2cppinspector/` or `decomp/il2cpp/inspector/`
  (VAs; the image base comes from the PE or from `decomp/il2cpp/metadata.json` `binary.image_base`; method `name` is
  Itanium-mangled, so the name comes from `group` (type, `+` = nested) + `dotNetSignature` (method; no return type =
  constructor, `.cctor` from the `__cctor` of the C signature); TypeInfo/MethodInfo/TypeRef demangled to
  `Type_TypeInfo`, `Method$Type.Method`, `Type_var`); Cpp2IL `*.cs` with `[Address(RVA = ..)]` (diffable-cs with
  `--use-processor attributeinjector`, or the C# of the dummy DLLs) in `decomp/il2cpp/cpp2il/` and
  `decomp/il2cpp/cpp2il-diffable/`, and `decomp/il2cpp/addresses.tsv` (method/ctor/dtor/operator rows; the
  property/event rows do not say which accessor and are left to the other sources); REA
  `dumps/rea/evidence/inspect_il2cpp_metadata/*.json` (raw Evidence from the REA runner: `GameAssembly.json` with the
  binary, `global-metadata.json` metadata only, without RVAs; the `*.request/run/error.json` files next to them are not
  sources; Evidence linked to another GameAssembly, by sha256, is ignored with a note) and `dumps/rea/**/*seeds*.txt`
  (`--ghidra-seeds`).
- Precedence per `(rva, kind)`: `il2cppdumper` > `inspector` > `cpp2il` > `rea`. Conflict = the winning source has a
  name another source does not have at the same address (counted per source pair).

**`code.il2cpp.native`** (`unity-il2cpp`, `heavy`, stage 70, `after` = `code.il2cpp.{metadata,dummydll,maps,decompile}`
and `rea.il2cpp_metadata`, which may not exist): `names.build`; with no source at all, runs Cpp2IL diffable-cs into
`decomp/il2cpp/cpp2il-diffable/` when Cpp2IL is installed; still without names, Ghidra runs without names (note on the
job). Output in the `gst decompile native` layout: project `decomp/ghidra/_projects/GameAssembly.dll/`, export
`decomp/ghidra/GameAssembly.dll/` (GsExport, every function, no time limit), entry `GameAssembly.dll` in
`decomp/ghidra/MANIFEST.json` (with `names_sha256`, `names_sources`, `il2cpp_names` = the `post` counts),
`il2cpp_names.json`/`il2cpp_names.pre.json` in the export; then `gst index`. Only the names changed (same sha256, same
`ANALYSIS_VERSION`): reuses the saved analysis and runs only `post` + export. Options: `il2cpp.flat=1`
(`Namespace.Type$$Method` names in the global namespace), `il2cpp.reanalyze=1`, `il2cpp.force=1`. The job fingerprint
includes the state of the name sources (a new source = the job reruns). `code.native` skips `GameAssembly.dll` in
`unity-il2cpp` games (`NativeCode.handled_elsewhere`); `code.index` runs after `code.il2cpp.native`; `gst q` accepts
`Type$$Method` (becomes `Type.Method`).

## 8. Godot

Code: `src/gst/engines/godot/` (stdlib parsers, testable with synthetic text) and `src/gst/analysis/godot/` (the
analyses; `__init__.py` = `project_root`, `iter_files`, `Docs`, `binary_leftovers`, `binary_type`, and the
requirements `godot.project` and `godot.pack`). GDRE stays in `assets/godot.py` / `decompile/godot.py`; the C# of
Godot .NET games (`data_<game>_*/` with `GodotSharp.dll`) is `code.dotnet` (ilspycmd) in `decomp/cs/`.

### Parsers (`gst.engines.godot`)

- `pck.py`: `read(path)` -> `{container, where (pck | embedded in exe), pck_start, pack_format (1 = Godot 3, 2 = 4.0-4.3,
  3 = 4.4+), version, flags, dir_encrypted, rel_filebase, sparse_bundle, file_base, file_count, files: [{path (res://),
  offset (absolute in the container), pck_offset, size, md5, encrypted, removal}], encrypted_files, removal_files, bytes,
  errors}`. Encrypted directory: `files == []`, `file_count` from the plain header and an explaining error (rule 4:
  never decrypts). `find(install)` = `*.pck` + exes with an embedded pck; `cross_check(files, paths)` compares with
  another listing; `build(entries, fmt=)` builds synthetic packs (tests only).
- `tres.py`: `parse(text)` -> `{kind (scene|resource), header, ext_resources: {id: {type, path, uid, line}},
  sub_resources: {id: {type, props, line}}, sub_order, nodes: [{name, type, parent, path ("." = root), instance, groups,
  unique_id, props, line}], connections, editable, resource}`. Values: `Ctor(name, args)` (Vector3, Transform3D, Color,
  Packed*Array...), `Ref(kind ext|sub, id)`, `NodePath`, `StringName` (str), lists, dicts (keys become str),
  `inf`/`nan`; `Array[T](...)`/`Dictionary[K,V](...)` become list/dict; Godot 3 (`format=2`, `ExtResource( 1 )`) too.
  Keys with spaces come quoted (`"states/Throw Right/node"`). `to_json(v, packed_max=)` (Packed*Array larger than
  `packed_max` become `{type, count, head}`; None = everything), `short(v, doc)` (one-line text, uncut; with `doc`,
  ExtResource shows the `res://`), `ref_path`, `ref_type`, `iter_refs`, `is_binary` (RSRC/RSCC).
- `scripts.py`: `ScriptIndex(project, roots).exports(res)` -> `{source, language, class, base, exports: {name: {type,
  default (initializer text in the code), declared_in}}}`; C# follows the base class by name (`class A : B`) in
  `decomp/cs`; GDScript `@export*`/`export(...)` and `extends "res://..."`.
- `animtree.py`: `animation(doc, props)` (tracks per type; keys: dict = `len(times)`, packed = array/stride with
  position 5, rotation 6, scale 5, blend_shape 3, transform (Godot 3) 12; method track calls, audio track streams),
  `libraries`, `graph(doc, type, props)` (state machine: states + transitions with advance_mode (default 1),
  advance_condition, advance_expression, xfade_time, switch_mode, priority; blend tree: nodes + connections
  `{to, port, from}`; blend space: points; `animation` leaves; other nodes: `filters` (count) and `settings`),
  `graph_stats`, `skeleton`, `physical_bone` (joint_type 0-5 -> none/pin/cone/hinge/slider/6dof), `joint` (node_a/node_b
  resolved to scene paths), `resolve_node_path`.

### Analyses (engine `godot`)

| analysis | requirements | category (COVERAGE) | output (`data`) |
| --- | --- | --- | --- |
| `godot_pck` | `godot.pack` (install with a .pck or an exe with an embedded pck) | data | `packs` (header, flags, `by_ext`, `bytes_by_ext`, `by_folder`, `md5_checked`, `md5_mismatch`), `cross_check` with `assets/inventory.jsonl` (GDRE); full list in `analysis/godot_pck/<container>.files.json`. `md5=1` rehashes every file (default: first and last) |
| `godot_scenes` | `godot.project` (`assets/godot/**/project.godot`, else `assets/godot/extract`) | prefab_scene | `files` (per `.tscn`/`.tres` outside `.godot/`: type, nodes, root, root script, `inherits`, `instances`, `scripts`, connections, sub/ext resource types), `node_types` (`(instance)`; `(override)` = node of an instanced scene with only changed props), `scripts` (script -> nodes, scenes, types), `connections`, `signals`, `instance_graph` (`edges`, `most_instanced`, `top_level`), `tunables` (script -> `exports` {prop: {type, code default, declared_in, values: {value: {count, where: ["scene::node"]}}}}, `engine` (engine props on the nodes with the script), `unclassified` (script without source)), `data_resources` (Resources with a script: `.tres` and sub-resources, with values), `parse_errors`. Per file: `analysis/godot_scenes/<res path>.json`. Options: `name=REGEX`, `packed=N\|all` (default 256 items per Packed*Array in the per-file JSON), `files=0` |
| `godot_animation` | `godot.project` | animation | `animations` (Animation in a sub-resource or `.tres`: length, loop_mode, tracks, keys, by_type, methods, audio), `players` (AnimationPlayer/AnimationLibrary, animations per library), `trees` (AnimationTree: local `tree_root` -> `graph` + `stats`, or `tree_resource` = external `.tres`; `parameters/*`; AnimationNode* saved as `.tres` -> `graph`, `stats`, `sub_resource_types`), `tree_node_types`, `conditions`, `skeletons` (Skeleton3D with `bones/N/name` in the scene), `attachments` (BoneAttachment3D, IK, modifiers, PhysicalBoneSimulator3D), `ragdolls` (per scene: PhysicalBone3D with bone_name, joint_type, mass, damping, layers; child joints; total mass), `joints` (every Joint3D/2D) |

Animations embedded in imported models (glb) stay in `gltf_summary`; `audio_catalog`, `dcc` and `gltf_summary` run in
the same `gst study` of a Godot game. `@analysis(..., category=)` (default `other`) picks the COVERAGE.md row the
`analysis.<name>` job counts in.

### Binary resources, rig links, shaders

These replace the old `binary_left*` / `binary_animations` outputs (binary files used to be only listed).

- `engines/godot/rsrc.py`: `read(path | read_bytes(data), bulk=None, skip_types=())` -> the `tres.parse` document
  (`line` None, `offset` per resource) + `binary` {big_endian, ver_major, ver_minor, ver_format, type, flags, uid,
  script_class, compression, bytes[, blocks, blocks_decoded]}; PackedScene unpacked into `nodes`/`connections`/
  `editable`. `header(path)`, `uid_text(int)`, `ctex_header(head)`, `RsrcError`. `tres.Bulk(name, count)` = packed array
  left undecoded (`to_json` -> `{type, count, decoded: false}`). `engines/godot/codecs.py`: `decompress(mode, data,
  size)`, `ZstdDecoder`, `fastlz_decompress`. `engines/godot/shader.py`: `parse(code)`, `visual(props, sub_types,
  sub_props)`.
- `analysis/godot/__init__.py`: `SCENE_EXTS`, `IMPORT_DIRS`, `BULK` (4096), `GEOMETRY_TYPES`, `bulk_option(ctx)`,
  `binary_files(root)`, `Docs(root, bulk=, skip=)` (text + binary; `GDEC` = error "not decrypted"; `binary` = res read
  from binary). `analysis/godot/links.py`: `Project(root, docs)` with `import_info`, `loadable`, `is_model`, `walk`,
  `chains`, `instances`.
- `godot_scenes` v2: rows of binary files carry `binary`; rows carry `models` [{node, model, imported, importer}];
  `data.binary`, `binary_types`, `models`, `imported` {files, by_ext, binary, parsed, mode, binary_types, versions,
  compression, textures, texture_sizes, errors}; summary `binary_files`, `binary_errors`, `models_instanced`,
  `imported_files`, `imported_binary`, `imported_parsed`, `imported_errors`. Options `bulk=N|all`,
  `imported=headers|parse|0`.
- `godot_animation` v2: `data.characters` [{scene, top_level, skeleton, skeleton_defined_in, bones, roots, bone_names,
  model, model_node, imported, skinned_meshes, meshes, animation_players [{node, defined_in, root_node, libraries,
  animations, names}], animation_trees [{node, root_node, anim_player, root_type, tree_resource, states, transitions,
  ...}], ragdoll {bodies, bones, simulators, defined_in, joint_types, mass_total}}], `binary_files`; summary
  `characters`, `characters_animated`, `characters_ragdoll`, `characters_max_bones`, `binary_files`, `parse_errors`.
- `godot_shaders` (`godot.project`, category material_shader): `data.shaders` [{name, kind (file | include |
  embedded | resource | visual_shader), file, shader_type, render_modes, uniforms [{name, type, scope, hints, default,
  group[, precision, array]}], includes, functions, features, materials, used_by[, included_by, node_types]}],
  `materials` [{where, file, embedded, shader, params, used_by[, next_pass]}], `shader_types`, `kinds`, `features`,
  `uniform_hints`, `shaders_not_found`, `parse_errors`.
- Library: `rigs` column `links` (JSON) and kind `godot-character` (from `characters`: top level or driven by a
  player/tree/ragdoll); `shaders` column `uniforms`; facet shaders/godot = `godot_shaders`.

## 9. Unreal

Code in `src/gst/engines/unreal/`; `gst.assets.unreal` and `gst.extractors.unreal` stay as thin re-exports.
Usage guide and cost: [`docs/tools/unreal.md`](tools/unreal.md).

### Modules

- `paks.py` (stdlib): `paks_dir(install)`, `containers(paks)` -> `[{name, kind (pak|iostore|global), size, version,
  encrypted, signed, key_guid, entries, mount_point|container_id, flags, readable}]`, `pak_footer`/`pak_info` (FPakInfo
  v1-v12; plain index: mount point, entries, hash index and full directory v10+), `list_pak` (full directory
  v10-v12, UTF-8 names in v12), `utoc_header` (144-byte FIoStoreTocHeader), `manifest_paths`, `ue_game`
  (CUE4Parse EGame), `retoc_version`.
- `repak.py`: `list_paths(pak, key=None) -> (paths, "repak"|"repak+aes"|"stdlib")`; v12 never reaches repak.
- `retoc.py`: `list_paths(utoc)`, `to_legacy_commands`/`to_legacy(source, out.pak, log, filters, version, key)`
  (`-f` = substring contained in the path; batches of 30,000 characters, each batch into its own `.pak`),
  `script_objects` + `parse_script_objects` + `script_modules`.
- `uas.py`: `export(paks, out, mode, game_enum, log=, rsp=, flt=, bytecode=, key_file=, usmap=)` and `list_paths` run
  `uas @<rsp>`; `trie_regex`/`exact_filter` (exact packages + `.uexp/.ubulk` companions), `union_filter`.
- `keys.py`: `load(ws, game)` (game.json `keys` + `notes/keys.json`; only 256-bit keys with an http(s) `source`),
  `key_for(ws, game, guid) -> (key|None, reason)` (goes through `policy.check(..., "decrypt")`), `require` (GsError
  code 3), `key_file` (`dumps/unreal/keys/<guid>.key`).
- `usmap.py`: `find(ws, game)` (game.json `usmap.path` or `notes/*.usmap` with magic 0x30C4), `needs_mappings(text)`,
  `NEEDS_REASON`.
- `inventory.py`: `inventory(ws, install, game)`, `matching`, `search`, `rows_by_container`.
- `export.py`: `extract(...)` (`gst assets extract`), `to_legacy(...)` (`gst assets to-legacy`), `access` (key and
  usmap of a folder).

### Outputs

| path | written by |
| --- | --- |
| `assets/inventory.jsonl` (`path, ext, folder, container`; `container` = container name, `manifest` or `uas`), `assets/INVENTORY.md`, `assets/unreal/containers.json` | `unreal.inventory` / `gst assets list` |
| `assets/unreal/<mode>/` + `_folder.gs.json` (`filters`, `exact`, `bytecode`) | `gst assets extract` |
| `assets/unreal/containers/<container>/<mode>/` + `_folder.gs.json` | jobs `unreal.export/textures/models/audio/animations` |
| `dumps/unreal/legacy/*.pak`, `assets/unreal/iostore/<mode>/` | `gst assets to-legacy`; the `unreal.iostore` job uses `dumps/unreal/legacy/study/` and `assets/unreal/iostore/json/` |
| `analysis/unreal_assets.json/.md`, `analysis/unreal_assets/datatables/<table>.json`, `analysis/unreal_assets/script_objects.json` | `gst analyze unreal_assets` |

### Jobs (`gst study`, engine `unreal`)

| job | stage | category | unit | options |
| --- | --- | --- | --- | --- |
| `unreal.inventory` | 40 | data | the paks folder | - |
| `unreal.iostore` | 45 | data | the `.utoc/.ucas` | `unreal.filter=a,b` (substrings; empty = everything) |
| `unreal.export` / `unreal.textures` / `unreal.models` / `unreal.audio` / `unreal.animations` | 50 | data / texture / mesh / audio / animation | one container (except `global.utoc`) | `unreal.modes` (default `json,textures,models,audio`), `unreal.name=<regex>` |

`blocked-policy`: container encrypted without a published key; packages that need a `.usmap` without one. The
fingerprint includes the mode, `unreal.name`/`unreal.filter` and whether a key/usmap is present.

### Analysis `unreal_assets` (requirement `unreal.inventory`, category data)

`data`: `by_folder` (families per folder, depth `depth`, default 3), `guessed` (classes by name), `containers`,
`exported` (`types` = class of the main export of each uas JSON, `by_folder`), `datatables` (`package`, `file`,
`row_struct`, `rows`, `columns` {column: {min, max, rows}}; each row's numbers in the per-table file),
`blueprints` (`class`, `parent`, `parent_path`, `native_parent`, `functions`, `components`, `variables`),
`blueprint_parents`, `script_modules` ({`/Script/<Module>`: {objects, classes}} of a plain `global.utoc`; class list
in `analysis/unreal_assets/script_objects.json`). Options: `depth=N`, `script_objects=0`.

### uas JSON readers, `unreal_values`, `unreal_animation`, IoStore plan

- `engines/unreal/uasjson.py` (stdlib, plain dicts): `ref_text` (`Class'Package.Object'`), `simplify`, `leaves`
  (`(path, value, kind)`), `values_of` (numbers/flags/enums only), `blueprint(exports, package)` (class, parent,
  `components` [{name, class, origin native|scs|inherited, attach|owner}], `defaults`, `component_defaults`, `values`
  {`Prop`/`Component.Prop`: v}, `curves`, `timelines`), `datatable`, `curvetable`, `columns`, `skeleton`, `skeletal_mesh`,
  `anim_sequence`, `anim_montage`, `blend_space`, `anim_blueprint`, `physics_asset` (`bodies`, `constraints` with
  `type` fixed|hinge|ball|cone-twist|mixed and `defaulted` = fields filled from the engine struct defaults).
- Requirement `unreal.json` (any uas JSON under `assets/unreal/`). Analyses (module `analysis/unreal_deep.py`):
  `unreal_values` (category data; `data.blueprints[*]` {package, class, type, parent, parent_class, parent_path,
  native_parent, cdo, components, values, file, curves, timelines, functions, variables, detail}, `blueprint_parents`,
  `datatables` (+ rows in `analysis/unreal_values/datatables/`), `curve_tables`, `curves`, `data_assets` {class, values,
  detail}) and `unreal_animation` (category animation; `data.skeletons` (bone list in the side file `detail`), `meshes`
  (+ `skeleton_bones`), `sequences`, `montages`, `blendspaces`, `anim_blueprints`, `physics_assets`). Option
  `name=<regex>` on the package path. Side files mirror the package path.
- uas JSON mode skips SkeletalMesh/AnimMontage by default: `gst assets extract --all-types`, `gst assets to-legacy
  --all-types`, study option `unreal.all_types=1` pass `--no-skip-types` (sticky per folder, in the fingerprint).
- Library: `FACETS` rigs/animation/joints for unreal = (`unreal_animation`, `unreal_assets`); rows `rigs`
  `unreal-skeleton`/`unreal-skelmesh`, `clips` `unreal` (`sequence`/`montage`), `anim_graphs`
  `unreal-abp`/`unreal-blendspace`, `joints` `unreal` (one per constraint), `ragdolls` `unreal-physics-asset`.
  Note block `tunables` lists `unreal_values` values (`--match` on package, class or value path).
- IoStore: `export.iostore_plan(ws, game, containers)` -> {convert, blocked, key, mixed, global} or GsError code 3
  (global encrypted, or nothing convertible); `export.mount_folder` (mixed: `dumps/unreal/mount/<name>/` with the
  convertible `.utoc/.ucas` + global, symlinks else copies). `retoc.to_legacy_commands(..., shaders=False)` adds
  `--no-shaders`; `retoc.to_legacy` returns `extracted: None` when retoc printed no count but wrote a pak.

## 10. Plugin

### Deliver first (agents + `SubagentStop` hook)

- Declaration: the task prompt of a plugin agent (`gst:<agent>`) has one or more lines
  `DELIVERABLE: <path relative to the workspace>` (e.g. `notes/logic/movimento.md`, `ARCHITECTURE.md`). Without such
  a line the hook does nothing.
- Hook: `hooks.json` `SubagentStop` with matcher `^gst:` -> `gst hook subagent-stop` (`handlers.subagent_stop`). Reads
  the first user message of `agent_transcript_path` (JSONL; `message.role == "user"`, `content` text or `text`
  blocks). Blocks (`{"decision": "block", "reason": ...}`; the `reason` goes back to the agent as an instruction) when a
  deliverable does not exist, is empty, or is a `.md` without a "Não verificado" section (a `##`-`####` heading with
  "não verificado" or "não foi verificado") or with that section empty (only `-`/comments). At most `MAX_STOP_BLOCKS`
  (3) blocks per `agent_id` (`logs/subagent-stop.json`); then it lets the agent stop and writes a line to STUDY.md.
- Agents: deliverable created from the template in turns 1-3, saved every ~10 turns, "Não verificado" closed in the
  last 5; one system or one asset family per agent; `maxTurns: 40` (recon: 25, read-only, delivers its reply). Notes
  are written in pt-BR.

### Note templates (`gst notes new <layer>/<topic> --template <name>`)

The templates and their prefilled text are pt-BR (the study output the user reads).

| template | default layer | prefilled blocks |
| --- | --- | --- |
| `sistema` | logic | `system` (the system's types from `systems.json` by `--system`), `tunables` |
| `mecanica` | logic | `system`, `tunables` (`--match`) |
| `ragdoll-fisica` | logic | `ragdoll` (`godot_animation` `data.ragdolls`, `joints`), `tunables` |
| `mapa-niveis` | logic | `maps` (`godot_scenes` `instance_graph`, `prefabs`, `unreal_assets`), `tunables` |
| `rede-voz` | logic | `middleware` (`recon/recon.json` `net_voice`), `system` |
| `modelos-3d-dossie` | assets | `models` (`gltf_summary`/`dcc` `families`, `prefabs`, `rig`...), `animation` |
| `personagem-modelos-animacoes` | assets | `models`, `animation`, `ragdoll` |
| `audio-catalogo` | assets | `audio` (`audio_catalog` summary and `categories`) |
| `comparativo` | compare | `workspaces` (GS_HOME workspaces, `--games a,b`: engine, `middleware`, analyses) |

`tunables` = Godot `godot_scenes.json` `data.tunables[*].exports` (type, code default, values in scenes), Unity
`mono_values`/`catalogs` `data.scripts` (fields), Unreal `unreal_assets` `data.datatables` and `unreal_values`; up to
25 rows. `--json` output: `{note, template, unfilled}`; `gst notes check --json` has `unfilled` (`{note: n}` of
`<!-- preencher -->`). `gst notes templates [--json]` lists `{name, layer, title}`.

### Recon: network and voice

`recon/recon.json` `net_voice` = `[{name, kind (network|voice|platform), evidence: [up to 3 paths]}]`
(`gst.recon.middleware.detect(files, folders)`); game.json `middleware` = `[{name, kind}]` (written by `gst new` and
`gst recon`). `middleware` (list of `[SDK]` names from the SteamDB rules) is unchanged.

### `focus` setting and the `study-all` workflow

`gst config set focus "ragdoll,co-op"` (or `GST_FOCUS`); default `config.DEFAULT_FOCUS` (ragdoll, proximity voice,
procedural map, co-op, extraction loop, inventory, physics); `gst config show` shows `focus`. `/gst:study-all`
(`args`: `{focus, max: 12, wave: 4, verify: 20}`): Check (`gst status`/`gst jobs list`/`gst coverage`; exits when a
job is pending) -> Plan (tasks `{slug, label, kind, template, question, system, match, focus, priority}`) -> Study
(waves of `wave` agents, `DELIVERABLE: notes/<layer>/<slug>.md`) -> Consolidate (synthesizer,
`DELIVERABLE: ARCHITECTURE.md`) -> Verify (`workflow('gst:verify-claims')`) -> Report (`gst report --check`,
`gst notes check`).

## 11. Cross-game library

Code: `src/gst/library/` (`__init__.py` = paths, `library.json`, workspace discovery, `resolve_games`;
`facets.py` = tables, extractors per analysis, gaps; `build.py`; `compare.py`; `search.py`) and `src/gst/cmd_lib.py`.
It only reads workspaces; it writes only under `GS_HOME/_library/`.

### Files

| path | content |
| --- | --- |
| `_library/library.db` | SQLite rebuilt from scratch on every `gst lib build` (writes `library.db.partial-<pid>` and renames at the end: idempotent; a failed build leaves the previous one) |
| `_library/library.json` | `{"workspaces": [...], "legacy": [...]}`: extra folders (`--workspace`, a workspace or another GS_HOME) and gs v1 homes (`--legacy`, search only) remembered between builds; `--forget <folder>` removes one |
| `_library/compare/<facet>-<key>_<key>....md` | output of `gst lib compare` |

### Workspaces

Those in the GS_HOME `registry.json` whose folder has a `game.json` (without a registry: `GS_HOME/*/game.json`, except
`_*`), then the extras, then the legacy ones. `key` = slug; on a collision `<slug>@<parent folder>`; legacy =
`legacy:<slug>` (search index only, no facets nor gaps). `--games a,b` filters by slug/name. Game names the commands
accept: key, slug or name without punctuation (`repo` finds `r-e-p-o`/`R.E.P.O.`); unknown = error with the list.

### Tables (`library.db`)

Every facet table has `id`, `game` (key), the columns below, `source` (file relative to the workspace) and `ref`
(where in the JSON: `data.smr[12]`, `data.files[3].skins[0]`, `net_voice[1]`).

| table | columns | from (engine) |
| --- | --- | --- |
| `games` | `key, slug, name, origin (registry\|extra\|legacy), engine (unity\|godot\|unreal\|...), engine_key, engine_label, engine_version, buildid, workspace, home, analyses (JSON), middleware (JSON from game.json), docs` | `game.json` |
| `sources` | `game, name, path, version, generated_at, bytes` | header of each JSON read |
| `gaps` | `game, facet, analysis, kind (missing\|partial\|no-analysis), reason` | `facets.gaps`: missing analysis (with the `gst analyze` that would make it), what an engine's analysis does not measure (`PARTIAL`), recon without `net_voice` |
| `rigs` | `kind (unity-smr\|gltf-skin\|godot-skeleton\|godot-character\|unreal-skeleton\|unreal-skelmesh), item, node, bones, deform_bones, root_bone, bindposes, max_influences, verts, tris, blendshapes, mesh, detail, links` | Unity `rig` `data.smr`; Godot `gltf_summary` `files[*].skins`, `godot_animation` `skeletons`/`characters`; Unreal `unreal_animation` |
| `avatars` | `name, humanoid, skeleton_nodes, human_bones, root_motion_bone, animators` | Unity `avatar` |
| `constraints` | `item, node, type` | Unity `rig` `data.constraints` |
| `clips` | `kind (unity\|godot\|gltf\|unreal), name, owner, length, fps, loop, events, tracks, keys, clip_kind, root_motion` | Unity `animation`; Godot `godot_animation` `animations` and `gltf_summary` `anims` (fps = 1/`dt_min`); Unreal `unreal_animation` |
| `anim_graphs` | `kind (unity-controller\|unity-override\|godot-tree\|unreal-abp\|unreal-blendspace), name, file, params, layers, states, transitions, blend_trees, clips` | Unity `controller`; Godot `godot_animation` `trees`; Unreal `unreal_animation` |
| `joints` | `kind, family, item, path, type, connected, mass, settings (JSON: limits, drives, springs)` | Unity `joints` `prefabs[*].joints`; Godot `godot_animation` `joints`; Unreal `unreal_animation` `physics_assets` |
| `ragdolls` | `kind, item, file, bodies, joints, joint_types (JSON), mass_total, classes (JSON), detail` | Unity `joints` `ragdolls` + the prefab with the same `file`/`root`; Godot `godot_animation` `ragdolls`; Unreal PhysicsAssets |
| `audio` | `name, kind, category, clips, files (with an exported file), users, defined_at` | `audio_catalog` `events` (every engine) |
| `audio_files` | `ext, format, channels, rate, files, seconds, bytes` | `assets/audio/files.jsonl` (`gst audio index`), grouped |
| `shaders` | `name, family, materials, passes, keywords, signatures (JSON), uniforms` | Unity `shaders`; Godot `godot_shaders` |
| `middleware` | `name, kind (sdk\|network\|voice\|platform), evidence (JSON)` | `recon/recon.json` `middleware` (SteamDB rules, kind `sdk`) and `net_voice` |
| `systems` | `label, types, files, prefabs, score, top (JSON: name, path, line, refs)` | `systems` |
| `dcc` | `family, verdict, strong, weak, ambiguous, rules (JSON)` | `dcc` `data.families` (inference) |
| `asset_counts` | `type, count` | Unreal `unreal_assets` `data.guessed` (packages counted by name) |
| `docs` (FTS5, `unicode61 remove_diacritics 2`) | `text` (indexed), `heading, game, slug, kind (notes\|facts\|analysis), path, line` | one row per non-empty line of `notes/**/*.md` (except `INDEX.md`) and `ARCHITECTURE.md` (notes), `FACTS.md` (facts), `analysis/*.md` (analysis); table separators are left out |
| `meta` | `key, value (JSON)`: `schema_version, built_at, home, games, extra, legacy, filter, seconds` | the build |

Facet -> analyses per engine (`facets.FACETS`): rigs (Unity `rig`, `avatar`; Godot `gltf_summary`, `godot_animation`;
Unreal `unreal_animation`, `unreal_assets`), animation (`animation`, `controller` / `godot_animation`, `gltf_summary` /
`unreal_animation`, `unreal_assets`), joints (`joints` / `godot_animation` / `unreal_animation`, `unreal_assets`), audio
(`audio_catalog`; Unreal + `unreal_assets`), shaders (`shaders` / `godot_shaders` / `unreal_assets`), middleware
(`recon`), systems (`systems`), dcc (`dcc`). A new analysis in a facet = an extractor in `facets.EXTRACTORS` + the
analysis in `FACETS` + columns in `TABLES` when needed.

### Commands

- `gst lib build [--games a,b] [--workspace DIR]... [--legacy [HOME]] [--forget DIR]...` (`--legacy` without a value =
  the `gs_home` of the gs config). `--json`: `{db, games: [{key, origin, engine, analyses, rows, docs}], tables, gaps,
  warnings, seconds, settings}`.
- `gst lib compare <facet> [game...] [--print]`: facets `rigs, joints, animation, audio, middleware, systems,
  shaders, dcc` (aliases: `ragdoll`, `rig`, `clips`, `network`...); no games = all (except legacy). pt-BR Markdown:
  title, header with the command and the build, `## Tabela` (`engine` row, one row per dimension, rows no game has are
  omitted, `fontes` row), `## Por jogo` (engine, build, workspace, sources with version and date, highlights with
  `` `<slug>:<file>` `ref` ``), `## Não verificado / lacunas` (the facet's gaps, plus those of `dcc` in the rigs one,
  and the facet's warnings). `--json`: `{path, facet, games, gaps}`.
- `gst lib search "<query>" [--game slug]... [--kind notes|analysis|facts] [--limit N]`: FTS5 syntax (`"phrase"`,
  `rag*`, `a AND b`); text that is not FTS5 becomes quoted words. Output `<absolute file>:<line>  <snippet>` (bm25);
  `--json`: `[{game, slug, kind, path, file, line, heading, text, snippet}]`. No limit by default.
- `gst lib status [--json]`: build, games, rows per facet, gaps.
- `gst notes new compare/<topic> --template comparativo --games a,b [--facet <facet>]`: the `{{auto:library}}` block
  puts in the `gst lib compare` table (facet = `--facet` or the topic) when `library.db` exists; without a library, a
  line saying to run `gst lib build`.

## 12. Migration, installer, plugin registration

Code: `src/gst/compat/legacy.py` (reading: `scan_home`, `plan_migration`, `stray_of`, `tree_stats`; never writes,
`jobs.db` opened with `JobsDB.open_readonly` = URI `immutable=1`), `src/gst/compat/migrate.py` (`apply`,
`report_markdown`), `src/gst/cmd_workspace.py` (`gst migrate`, `gst workspace`). User guide: `docs/MIGRATION.md`.

### `gst migrate <slug> | --all [--dry-run] [--report FILE]`

- `--all` = every entry of `GS_HOME` (`scan_home`): folder with `game.json` = workspace (including `_selftest-*`);
  a `.zip/.7z/.rar/.tar/.gz` file = `ignored` "archive"; `registry.json`, `INDEX.md`, other files, `_library` and
  folders without `game.json` = `ignored` with the reason. Invalid `game.json` = status `error` (exit code 1; the
  others migrate).
- Unit = each top-level entry of `decomp/ index/ assets/ dumps/ traces/` (folder or file) and the whole `recon/`;
  `.partial` and empty ones are left out; `notes/ logs/ sandbox/` and the documents never go in. Job: extractor
  `legacy.<folder>`, `unit_id` = relative path (`decomp/cs`), `status = done`, `tool = legacy`, `tool_version =
  "gs v1"`, `resource = io`, `stage` (recon 0, decomp 20, index 25, assets 40, dumps 50, traces 70), `inputs =
  [[relpath, bytes, newest mtime_ns]]`, `outputs = [relpath]`, `out_bytes`, `meta = {"legacy": true, "files": n}`
  (`COVERAGE.md` counts `files` as output files), `fingerprint` = sha256 of `[relpath, files, bytes, mtime]`.
  Category: `decomp`/`index` = `code`, `recon` = `other`, the rest by a word at the start of a token of the name
  (`legacy.CATEGORY_WORDS`: `audio_scan.json` = audio, `unreal` is not `rea`).
- Action per unit against `jobs.db`: `new`, `changed` (fingerprint changed: gs wrote more) or `kept`. Workspace status:
  `would-migrate` (dry run) / `migrated`, `up-to-date` (nothing to do: a second `migrate` writes nothing), `stray`,
  `error`.
- `stray` (`stray_of`): a `<slug>@<build>` folder whose `<slug>` exists with the same `install_dir`, without `jobs.db`
  and with empty `decomp assets dumps traces index notes analysis`. Not migrated; the problem explains it is a leftover
  of an old `gst study --dry-run` and that deleting it is the user's call.
- Workspace with non-legacy jobs in `jobs.db` (`gst study` already ran): no unit imported; at most the v2 mark is
  added.
- Reported problems (they do not stop the migration): missing or nonexistent `install_dir`, `game.json` slug different
  from the folder, no `engine.key`, unreadable files, paths of 260+ characters with `LongPathsEnabled` off.
- What `apply` writes: `jobs/`, `analysis/` (the missing ones), the jobs, `jobs/COVERAGE.md` (only when something was
  imported), `game.json` with `"gst_workspace": 2` and `"gst_migrated": {"at", "from_version", "gst"}` (temp file +
  rename; the gs keys stay), one line in `STUDY.md`. Never: moves/deletes, `registry.json`, files outside the
  workspace.
- `--report FILE`: Markdown (one section per workspace, table of units, problems) or JSON when it ends in `.json`
  (the same object as `--json`). It is the only file `--dry-run` writes.
- `--json`: `{dry_run, home, at, totals: {status: n}, ignored: [{name, reason}], plans: [{workspace, slug, version,
  engine, status, create_dirs, mark_game_json, import_as_legacy_jobs, import_files, import_bytes, units: [{relpath,
  extractor, category, files, bytes, action}], problems, notes, written?: {created, imported, updated, game_json,
  coverage}}], report?}`.

### `gst workspace`

Read-only list of `GS_HOME`: `{home, workspaces: [{folder, path, slug, name, version, migrated, engine, buildid,
phase, install_found, registry, stray_of, jobs_db}], ignored}`. `v2m` in the text = migrated from v1.

### `gst study --dry-run`

Writes nothing: no workspace, no `<slug>@<buildid>` sibling, no `recon/`, `jobs/plan.json`, `jobs.db` or
`registry.json` entry. Missing recon runs in memory; `plan.build(write=False)` reads an existing `jobs.db` with
`open_readonly`. The result carries `would_create: {create: <folder>|null, sibling_of: <slug>|null, recon: bool}`.

### `install.ps1` (root, PowerShell 5.1, ASCII, CRLF)

Steps: Python >= 3.12 (`-Python`, `py -0p`, `uv python find`, PATH; a python from another venv becomes its base;
pyenv shims and the Store stub ignored; with none, `uv python install 3.13` when uv exists) -> venv (`-Venv`, default
`<repo>\.venv`, reused; < 3.12 = error, never deleted) -> `pip install -e .[<Extras>]` (`uv pip` when the venv has no
pip) -> copy of the `Scripts\gst.exe` launcher to `-BinDir` (default `~/.local/bin`, where hooks and `GST_EXE` look;
the launcher points at the venv's python) -> `-GsHome` -> `-Tools` (`gst tools install <Profiles>`) ->
`LongPathsEnabled`: always read; changed only with `-EnableLongPaths` (admin: directly; else UAC for
`scripts/system-setup.ps1 -LongPathsOnly`) or `-System` (+ Defender exclusion) -> `-Plugin` (`gst setup plugin`;
prints the commands to switch from/back to the legacy plugin, never runs them) -> `gst doctor` (`-NoDoctor` skips).
`-DryRun` and `-WhatIf` only describe. User PATH only with `-AddToPath`. Log:
`%LOCALAPPDATA%\game-study-tool\install.log` (not in a dry run).

### Plugin and doctor

- `.claude-plugin/marketplace.json`: the plugin entry is named `gst` (it used to be `game-study`, which prevented
  `claude plugin install gst@gst-local`); `test_setup.py` ties the entry names to the `plugin.json` files.
- `doctor.plugin_state()` reads `~/.claude/settings.json` (`enabledPlugins`) and `~/.claude/plugins/installed_plugins.json`:
  `{installed, enabled, key, legacy: {key, installed, enabled}, claude_cli}`; findings: both enabled, or only the old
  one. `LongPathsEnabled` stays in the report and the findings.
- `requires-python >= 3.12` (the code uses `shutil.rmtree(onexc=...)`).

## 13. Java and Lua in `gst study`

### Extractors (`src/gst/extractors/javalua.py`, registered for every engine, stage 20)

- `code.java` (`resource = heavy`): one unit per jar (id = install-relative path, `decomp/java/<stem>/`) and per loose
  class tree (id = `[<classpath root>/]<top package>/`, `decomp/java/classes/[<root>/]<package>/`). The classpath root
  comes from the first class file of each folder (`java.class_name`, constant pool). Library-only units are `skipped`.
- `code.lua` (`resource = io`): one unit per script folder, the file's folder cut at `lua.UNIT_DEPTH` (3) levels; a
  unit at that depth owns its subtree, a shallower one only its own files. Bytecode-only units that cannot be decompiled
  end `unsupported` (full unluac stderr) or `blocked-policy`.
- Both: `Unit.fingerprint` = inputs + buildid + the options that change the output (`java.filter`, `java.all`,
  `java.units`, tool version; for Lua bytecode the unluac version and the verdict). One fragment per unit in
  `decomp/<java|lua>/_manifest/` (with that fingerprint); `finalize` merges them into `decomp/<java|lua>/MANIFEST.json`.
- `code.index` runs `after` both and takes their inputs into its own fingerprint.
- Options: `java.filter=<prefix>,...`, `java.all=1`, `java.units=<name>,...`, `java.threads=N`.

### `gst decompile java|lua`

`java.decompile()` / `lua.decompile()` call `javalua.run_inprocess(ws, game, cls, options, select=, force=)`: the same
`discover`/`run`/`finalize` in the calling process, reusing a unit whose fragment fingerprint is unchanged. Return
shapes kept: java `{out, jars: {<out>: fragment + reused + status}, skipped, failed}`; lua `{out, copied, bytecode,
units, reused, seconds}`. `cmd_decompile.backends_for` adds `lua` (and `java` with a game jar) next to any engine.

### Recon

`recon.json` `script_code`: `{java: {jars, class_files, jar_files}, lua: {scripts, compiled}}` (bundled JREs and
`steamapps/` left out); RECON.md "Script code" line; `gst decompile java|lua` in the next steps when the engine's backend
is another one.

### Ghidra export without caps (`GsExport.java`, `SCRIPT_VERSION = 4`)

`strings.jsonl` is streamed with every defined string (full value, every referencing function, no 300k cap); class
layouts list every field; the per-function decompiler limit defaults to 0 = none (`gst decompile native
--function-timeout S`, `--opt native.function_timeout=S`). `GstIl2cppNames` reports every apply error.
