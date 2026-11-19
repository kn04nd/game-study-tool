# game-study-tool (gst) - development notes

This repository is the **source** of the Game Study Toolkit: the `gst` CLI (`src/gst/`), the Claude
Code plugin `gst` (`plugins/gst/`), the optional LSP plugin, the installer and the
harmless sample game used for tests. The parent folder holds an unrelated project
(universal-modder); its instructions do not apply here (excluded via `claudeMdExcludes`).

Module contracts: `docs/INTERFACES.md`; what is still missing or unverified (by area):
`docs/KNOWN-LIMITATIONS.md`; dev environment: `docs/DEV.md`; tool guides: `docs/tools/` (Ghidra;
Unity: Addressables, scene animators, humanoids, audio categories; Unreal: repak/retoc/uas/usmap/keys, values and
animation; Godot; Java/Lua; REA and its optional IL2CPP patch); migration from the
legacy gs (workspaces and plugin): `docs/MIGRATION.md`.

## Layout

- `src/gst/` - Python package. One module per command group (`cmd_*.py` or a package), each
  exposing `register(sub, parent)`. Core = stdlib + pefile; heavy imports only inside functions.
- `src/gst/policy.py`, `guard.py` - the guardrails in code. Keep them stdlib-only and fast:
  the PreToolUse hook imports them whenever its screen finds something to check.
- `src/gst/hooks/__init__.py` - hook entry, the hot path of every tool call in every session.
  Imports only os/sys/_json (a test enforces it). Its `NEEDLES` screen must cover every rule in
  `guard.check_command`: add a sample to `RULE_SAMPLES`/`TRIGGER_SAMPLES` in `test_hooks.py` with
  any new rule. `handlers.py` holds the real work. `subagent-stop` (hooks.json matcher `^gst:`) is the
  deliver-first check: a task prompt line `DELIVERABLE: <path>` (first user message of `agent_transcript_path`)
  must exist with a non-empty "Não verificado" section, else `{"decision": "block"}` (at most `MAX_STOP_BLOCKS`
  per agent, counted in `logs/subagent-stop.json`, then a journal line).
- `plugins/gst/hooks/run.sh` - launcher; reads `%LOCALAPPDATA%/game-study-tool/hook.cfg`
  (`gst setup hooks`) to start the base Python with `-S -E`, else falls back to gst.exe.
- `src/gst/decompile/` - one module per backend (`dotnet` = ilspycmd, `godot` = GDRE, `java` = Vineflower,
  `lua` = copy + unluac, `native` = Ghidra headless); output in `decomp/<dir>/` with MANIFEST.json, then
  `gst.index.build`. Java and Lua are the `gst study` jobs `code.java` (unit per jar / loose class tree) and `code.lua`
  (unit per script folder) in `extractors/javalua.py`, for any engine; `gst decompile java|lua` runs the same
  extractors in-process (`run_inprocess`). Guide: docs/tools/java-lua.md; contract: docs/INTERFACES.md section 13. `native` runs the Java scripts in `src/gst/data/ghidra_scripts/` (Ghidra 12 has no
  Jython): `GsPdbPrescript` (local PDB only, Class Hierarchy layout, needs `-Dghidra.pdb.developerMode=true`)
  and `GsExport` (C per namespace + `.c.map.json` sidecars). Bump `SCRIPT_VERSION` when the export changes
  (re-export, analysis reused) and `ANALYSIS_VERSION` when analysis options change (re-analysis).
  Arguments reach Ghidra through a `.bat`: only gs-made paths, the regex goes in a file.
  `GstIl2cppNames` (`engines/il2cpp/ghidra_names.py`) applies `decomp/il2cpp/names.jsonl` to GameAssembly: `pre`
  (labels + entry points before analysis) and `post` (create/name the rest, plate comments); bump `NAMES_VERSION` when
  it changes what it applies (names re-applied on the saved analysis).
- `src/gst/engines/il2cpp/` - Unity IL2CPP: `metadata.py` reads the `global-metadata.dat` header (stdlib;
  v24-v31 pairs, v38+ triples with counts), `cpp2il.py`/`il2cppdumper.py`/`inspector.py` wrap the catalog tools
  (picked by `metadata_versions`; Inspector = its legacy CLI, the new one never exits), `extractors.py` =
  `code.il2cpp.{metadata,dummydll,maps,decompile}` (stage 20; Unity exports wait for the dummy DLLs, which feed the
  typetree chain), `decompile.py` = ilspycmd -> `decomp/cs-il2cpp` + `il2cpp_addresses` table. Contract and paths
  for the Ghidra name maps: docs/INTERFACES.md section 7.
- `src/gst/engines/godot/` - Godot 3/4, stdlib: `pck.py` (PCK directory v1-v3, standalone or embedded in the
  exe; encrypted directory/files reported, never decrypted), `tres.py` (`.tscn`/`.tres` text -> Python/JSON with
  `Ctor`/`Ref`/`NodePath`/`StringName`), `scripts.py` (exported variables of GDScript/C# sources, C# bases by name),
  `animtree.py` (Animation tracks, AnimationTree graphs, Skeleton3D, PhysicalBone3D, joints), `rsrc.py` (binary
  RSRC/RSCC -> the tres document shape), `codecs.py` (FastLZ/Deflate/pure-Python Zstd/Gzip), `shader.py`. The analyses
  `godot_pck`, `godot_scenes` (tunables = exported values set in scenes vs code defaults), `godot_animation`
  (+ `characters`: scene -> model -> skeleton -> animation -> ragdoll via `analysis/godot/links.py`), `godot_shaders`
  live in `analysis/godot/` (requirements `godot.project`, `godot.pack`). Contract: docs/INTERFACES.md section 8;
  guide: docs/tools/godot.md.
- `src/gst/engines/unreal/` - Unreal 4/5: `paks.py` (stdlib: pak footer/index v1-v12 + v10-v12 directory
  listing, utoc header incl. `global.utoc`), `repak.py` (v1-v11; v12 goes to the stdlib lister), `retoc.py` (list,
  `to-legacy` into `dumps/unreal/legacy/`, script objects), `uas.py` (every argument in a response file `uas @x.rsp`,
  exact selections as a prefix-trie regex: one run per container and mode, never batches that delete each other),
  `keys.py`/`usmap.py` (published key with source URL / published or user `.usmap` only, else `blocked-policy`),
  `inventory.py`/`export.py` (what `gst assets` calls), `extractors.py` (`unreal.inventory`, `unreal.iostore`,
  `unreal.export|textures|models|audio|animations`). Analysis `unreal_assets` in `analysis/unreal.py`.
  `uasjson.py` (stdlib readers of uas JSON: Blueprint CDO/components/values, tables, curves, skeleton, mesh, anims,
  AnimBlueprint state machines, PhysicsAsset) feeds `unreal_values`/`unreal_animation` (`analysis/unreal_deep.py`,
  need `unreal.json`); `export.iostore_plan` decides plain/mixed/blocked IoStore conversion. Never call
  `retoc to-zen`/`repak pack` (modding, guard rule 1). Contract: docs/INTERFACES.md section 9; guide: docs/tools/unreal.md.
- `src/gst/rea/` - REA as a static tool through its JSON CLI: `runner.py` (request file in, raw Evidence out
  in `dumps/rea/evidence/<op>/<name>.json`, full stderr on failure, caps raised to 2 GiB, static allow-list: never
  Frida/runtime), `split.py` (per assembly/namespace JSON + MD), `import_index.py` (`rea_*` tables in `code.db`; `gst q
  callers/callees` merge them), `extractors.py` (`rea.*` jobs, stage 32-34). Catalog kind `npm`; `GST_REA_SPEC` picks
  a local build with `patches/rea/` (configurable IL2CPP caps, docs/tools/rea.md). Contract: docs/INTERFACES.md section 7 ("REA").
- `src/gst/index/` - code index: `<lang>.py` extractors run in worker processes and return
  plain tuples; `__init__.py` stores them in SQLite (FTS5 trigram); `query.py` answers `gst q`.
  New languages: an extractor + an entry in `EXTRACTORS`, with synthetic-source tests. `ghidra.py` reads
  the export's sidecars instead of parsing C (exact call edges; the RVA goes in the `body_hash` column).
- `src/gst/assets/` - engine backends (`unity` = UnityPy + AssetRipper, `godot` = GDRE, `unreal` = thin wrapper over
  `engines/unreal`) and `audio` (vgmstream); every export gets a `.gs.json` sidecar and `assets/INDEX.md`
  is rebuilt from the sidecars. uas deletes outputs outside a narrower `--filter`: `unreal.extract` always passes
  the union of the folder's filters (one response file) and keeps `--script-bytecode` once used.
- `src/gst/formats/` - `gst fmt`: signatures, entropy, bounded hexdump (512 B), strings, diff, scan, ImHex patterns;
  `readers.py` loads study readers (`notes/formats/<name>.py`, dissect.cstruct) and checks the byte-exact
  round-trip. Readers of public or own formats ship in `src/gst/data/readers/` (tests and `gst selftest`
  use them); readers recovered from a game's code stay in its workspace.
- `src/gst/runtime/` - `gst trace` / `gst sandbox`: ctypes over Win32 (`win.py`), no elevation, no injection.
  `target()` gates every live action (verdict + the process must run from the install or sandbox).
  `watch.py` is a Debug API loop with DR0/DR7: every exit path must freeze the game and clear the debug
  registers before `DebugActiveProcessStop`, or the game crashes on its next write. Tests that debug a
  child use `sys._base_executable` (a venv's python.exe is a launcher process).
- `src/gst/gfx/` - `gst gfx`: RenderDoc from the sandbox only. Its Python API runs inside
  `qrenderdoc --python <script>` (Python 3.8, before the UI opens; the scripts in `data/renderdoc_scripts/`
  call `os._exit`). `prepare_config()` turns analytics and update checks off: without it a first-run
  dialog blocks the scripted run.
- `src/gst/analysis/` - `gst analyze <name>`: functions registered with `@analysis(name, engines, needs, uses)`
  (modules listed in `analysis.PROVIDERS`), output `analysis/<name>.json` (header/summary/data) + `.md`; `stage.py`
  turns each into a `gst study` job `analysis.<name>` (stage 60). Contract and how to add one: docs/INTERFACES.md
  section 6. Unity analyses stream the inventory (`ctx.rows`) and open one file at a time (`analysis.unity.UnityFile`).
  Unity specifics (docs/tools/unity.md): Addressables `catalog.bin`/`catalog.json` decoder in
  `engines/unity/export/addressables.py`, scene animators in `prefabs` (default `scenes=animators`), hand-written
  humanoid tables in `analysis/unity/humanoid.py`, mixer groups in `audio_refs` -> kinds/categories in `audio_catalog`.
- `src/gst/mapping.py`, `notes.py`, `report.py` - `gst map` (systems by type-name keywords, ranked by use,
  overridable in `notes/map-keywords.json`), `gst notes` (template, index, citations) and `gst report`
  (`--check` is the definition of done for ARCHITECTURE.md: sections, systems with evidence, resolving
  `path:line` citations, a Mermaid diagram, the unverified list, FACTS rows with evidence).
- `src/gst/notetemplates.py` + `data/templates/notes/*.md` - `gst notes new <layer>/<topic> --template <name>`
  (`--system`, `--match`, `--games`): pt-BR dossier templates whose `{{auto:<block>}}` lines become small tables from
  `analysis/*.json` (each citing its JSON; a missing analysis says which `gst analyze` to run, never fails);
  `<!-- preencher -->` marks what the agent writes (`gst notes check` counts them). New template = a file + a
  `TEMPLATES` entry (default layer); new block = a function in `BLOCKS`.
- `src/gst/library/` + `cmd_lib.py` - `gst lib build|compare|search|status`: `GS_HOME/_library/library.db`
  rebuilt from scratch (partial file + rename) from every registry workspace (+ `--workspace` dirs and `--legacy` gs
  homes remembered in `_library/library.json`; legacy = search only). `facets.py`: one table per facet (rigs,
  avatars, constraints, clips, anim_graphs, joints, ragdolls, audio, audio_files, shaders, middleware, systems, dcc,
  asset_counts), every row with `source` (workspace-relative JSON) + `ref` (where in it); `FACETS` = analyses per
  facet and engine, a missing one is a `gaps` row, never an error. `docs` = FTS5 (unicode61, accents folded), one row
  per line of notes/FACTS/ARCHITECTURE/analysis .md. `compare.py` writes pt-BR tables to `_library/compare/` and feeds
  the `comparativo` template (`{{auto:library}}`). Read-only on workspaces. Contract: docs/INTERFACES.md section 11.
- `src/gst/compat/` - gs (v1) workspaces. `legacy.py` is read-only (`scan_home`, `plan_migration`, `stray_of`;
  jobs.db through `JobsDB.open_readonly`, an `immutable=1` URI that writes no -wal/-shm); `migrate.py::apply` is the
  only writer: `jobs/`, `analysis/`, `legacy.<folder>` jobs `done` with `tool=legacy`, COVERAGE.md, game.json
  `gst_workspace: 2` + `gst_migrated` (temp file + rename, gs keys kept). Idempotent; never moves, deletes or touches
  `registry.json`. `gst workspace` lists GS_HOME. Contract: docs/INTERFACES.md section 12; user guide: docs/MIGRATION.md.
- `gst study --dry-run` writes nothing at all (no workspace, `<slug>@<buildid>` sibling, recon, plan.json, registry
  entry): `pipeline/study.resolve(dry_run=True)` reconns in memory; keep it that way (`test_study_cli.py` snapshots
  GS_HOME).
- `install.ps1` (root, PS 5.1, ASCII, CRLF per .gitattributes): Python >= 3.12 venv, `pip install -e .[full]`, the venv's
  `gst.exe` launcher copied to `~/.local/bin`; tools, plugin, LongPaths (admin) and user PATH only behind their
  switches; `-DryRun`/`-WhatIf`. It never disables the legacy `game-study@game-study-local` plugin (prints the commands).
- `src/gst/recon/middleware.py` - network/voice/platform SDKs by file name (Photon, Dissonance, Vivox,
  Steamworks.NET, FishNet, Mirror, NGO, GodotSteam, EOS, Unreal ThirdParty...) -> `recon.json` `net_voice`,
  game.json `middleware`, the "Network/voice" line of RECON.md. `config.focus()` (`focus` setting, default
  `DEFAULT_FOCUS`) is the user's study priority list read by `/gst:study-all`.
- `plugins/gst/workflows/` - `map-subsystems.js`, `verify-claims.js`, `study-all.js` (checks `gst study`
  finished, plans one agent per system/mechanic/asset family with the focus list first, waves, synthesizer,
  verify-claims via `workflow()`, `gst report --check`) (plain JS, run as `/gst:<name>`; no file access in the
  script, agents do the work; `tests/unit/test_plugin.py` runs `node --check` on the body wrapped in an async
  function). Agent prompts carry `DELIVERABLE: <path>` lines.
- `plugins/gst/agents/` - every agent follows deliver-first (deliverable from a template in turns 1-3, saved every
  ~10 turns, "Não verificado" closed in the last 5, one system or asset family each, `maxTurns: 40`; recon is
  read-only and delivers its reply). New agent = an `AGENT_WRITE_SCOPES` entry in `guard.py` (a test checks).
- `src/gst/mcp.py`, `mcp_client.py` - `gst mcp`: verified templates only (`TEMPLATES`, tool names checked
  live), `audit_text` (rule 9: stdio or 127.0.0.1, no rejected servers, pyghidra-mcp with --no-symbols) is
  also run by the guard on any `.mcp.json` write; the stdlib client speaks JSON-RPC over stdio for
  `gst mcp tools/call`.
- `src/gst/selftest.py` - `gst selftest`: doctor findings; `--tools` runs every pipeline live on the sample
  game in `GS_HOME/_selftest-sample`; `--games` checks real studies read-only against `EXPECT` (by AppID).
- `plugins/gst/` - skills, agents, hooks, workflows, `guardrails.md`, evals, and `references/`:
  `engines/<file>.md` is the study guide `gst recon` points to (file names come from the ENGINES table in
  `recon/fingerprint.py`), `tools.md` the tool catalog with how gst drives each tool.
- `samples/sample-game/` - our own test target (C++ D3D11 + C#, `build.ps1`): native code, formats, runtime
  and graphics pipelines run against it before real games.

## Rules for working on the toolkit

- Never put derived game material (decompiled code, extracted assets, dumps, traces, captures)
  in this repo. Tests use synthetic fixtures or `samples/`; real-game tests (`-m games`) write
  only to `GS_HOME`.
- Windows first: UTF-8 everywhere (`gst.common.read_text/write_text`), LF line endings, paths
  compared with `gst.common.norm_path`/`is_under`, no `python -c` through pyenv shims.
- Every new command: `--json` output, short human summary, artifacts on disk, unit tests.
- Every tool wrapper: explicit output dir inside the workspace, timeout, never writes into the
  game install, checks `policy.check(...)` before runtime/injection/decryption.
- The plugin is enabled at user scope, so its hooks run in every project: no false positives on
  ordinary code (rule 7 uses strong signals only), quiet and fast outside study workspaces.
- Changed workspace templates or settings: `gst refresh --all` updates existing workspaces.
- Skills and agents may only cite `gst` commands that exist (`tests/unit/test_plugin.py`).
- Language: code, comments, CLI messages, docs and skill/agent instructions are English. The study output the user
  reads stays pt-BR: note templates, text gst writes into notes and compare tables, the "Não verificado" and
  "Relevância para o meu jogo" headings the hooks check, and the pt-BR trigger phrases of skills and evals.
- Something left undone or unverified goes to `docs/KNOWN-LIMITATIONS.md` (and out of it once fixed).
- The repo is public: no personal data in any file, commit message or patch.
  - No e-mail addresses. Commits use the GitHub noreply address: check `git config user.email` before committing.
  - No real names beyond the existing LICENSE/pyproject/plugin authorship.
  - No `Claude-Session:` or other session trailers in commit messages. The only trailer is the `Co-Authored-By` line.
  - No machine-specific paths: user profile folders (`C:\Users\<name>\...`), the maintainer's checkout locations,
    scratchpad or transcript paths. Use placeholders: `<path of your clone>`, `<game install>`, `%LOCALAPPDATA%`.
  - The documented defaults stay as they are: `GS_HOME` = `D:\game-study`, Steam paths like
    `D:\SteamLibrary\steamapps\common\<game>` in examples and tests.
  - No tokens, API keys, session links (`claude.ai/code/session...`), Steam IDs, IPs or hostnames of real machines,
    save files, or inventories of the maintainer's machine (installed games, folders).
  - Do not commit raw agent/research dumps or tool logs. Summarize what matters in the docs instead.
  - Strip the `From:` header, e-mail addresses and session trailers from `git format-patch` output before committing
    a patch.

## Commands

- Install: `install.ps1 -DryRun` first (see README); dev venv in docs/DEV.md.
- Tests: `.venv/Scripts/python -m pytest` (see docs/DEV.md) (fast unit tests; `-m tools` / `-m games`
  for the slow suites).
- Try the plugin without installing: `claude --plugin-dir plugins/gst`.
- Validate the plugin: `claude plugin validate plugins/gst --strict`.
- Skill evals: `claude plugin eval plugins/gst --judge-model sonnet --no-publish -j 4` (cases in
  `plugins/gst/evals/`; results stay local and are gitignored). The default haiku judge flips on our
  long PT-BR plans even when every rubric item is there; keep llm rubrics as short checklists.
  Health of an install: `gst selftest --tools --games oni`.
