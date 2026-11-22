# game-study-tool (`gst`)

`gst` is for **studying in depth how the games you own are made**, through reverse engineering. It does two things:

- it extracts from the installed game everything that is technically possible: code, assets, data, audio, maps and
  analyses. There is no cap on size, count or time, and the work can be resumed later;
- the Claude Code plugin turns what was extracted into notes on systems, mechanics and assets, an
  `ARCHITECTURE.md` with evidence, and comparisons between games.

It works with Claude Code ("analyze this game") and on its own, through the `gst` command.

Supported engines: Unity (Mono and IL2CPP), Godot 3/4 (+C#) and Unreal 4/5. It also handles .NET, Java, Lua and
native code (Ghidra with PDB).

> Derived from `kn04nd/game-study-toolkit@2505b70`, with its history preserved. If you used `gs`, see
> [`docs/MIGRATION.md`](docs/MIGRATION.md). What is still missing or unverified:
> [`docs/KNOWN-LIMITATIONS.md`](docs/KNOWN-LIMITATIONS.md).

## Limits (guardrails)

This is personal study of games you own. "No limit" applies to size, count and time, not to the rules. `gst` never:

- bypasses anti-cheat or DRM, or touches online services;
- runs anything in the game outside the offline/sandbox mode. Runtime and graphics need the `offline-ok` verdict
  from recon;
- decrypts without a published key (with its source URL);
- writes into the game's install folder, which is always read-only;
- puts derived content in git. What is extracted stays in `GS_HOME` only (default `D:\game-study`).

The rules are written in [`plugins/gst/guardrails.md`](plugins/gst/guardrails.md). The code enforces them too:
`policy.py`, `guard.py` and the plugin hook. A job the policy stops stays `blocked-policy`, with the reason.

## Installation

Requirements:

- Windows 10/11 x64;
- Python ≥ 3.12, or `uv` so the installer can download one;
- free disk space;
- VS Code with the Claude Code extension, to use the plugin.

In the repository folder, in a regular PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -DryRun   # shows what it would do, changes nothing
powershell -ExecutionPolicy Bypass -File .\install.ps1           # venv, gst.exe on the PATH, gst doctor
```

The installer:

1. finds a Python ≥ 3.12;
2. creates or reuses the repository's `.venv`;
3. runs `pip install -e .[full]`;
4. copies the venv's `gst.exe` to `%USERPROFILE%\.local\bin`.

You can run it again at any time. It changes nothing else without the matching option:

| Option | What it does |
| --- | --- |
| `-Tools` (`-Profiles core,unity,unreal`) | `gst tools install <profiles>`: Ghidra, ilspycmd, GDRE, vgmstream, uas... (profiles: `core`, `unity`, `unreal`, `jvm-lua-py`, `indie`, `native`, `runtime`, `graphics`, `formats`) |
| `-Plugin` | registers the `gst-local` marketplace and the `gst@gst-local` plugin in Claude Code; only **prints** the commands to disable the legacy `gs` plugin |
| `-EnableLongPaths` | turns `LongPathsEnabled` on (asks for administrator rights through UAC). Without it, the installer only shows the state and suggests it |
| `-System` | `-EnableLongPaths` + a Defender exclusion for the studies folder only |
| `-GsHome <folder>` | changes the studies folder (`gst config set gs_home`) |
| `-AddToPath` | adds `.local\bin` to the user PATH when missing (without it, it only warns) |
| `-Python`, `-Venv`, `-BinDir`, `-Extras` | pick the Python, the venv, the `gst.exe` folder and the pip extras |
| `-DryRun` / `-WhatIf` | change nothing, only list the steps |

Then check the environment:

```powershell
gst doctor     # tools, LongPaths, plugins (the new one and the gs one), running anti-cheat, GS_HOME
gst selftest   # environment; --tools runs the pipeline on the sample game (minutes)
```

## `gst study`: extract everything from a game

```powershell
gst study "D:\SteamLibrary\steamapps\common\Pratfall" --dry-run   # plan and the time of each asset level; writes nothing
gst study "D:\SteamLibrary\steamapps\common\Pratfall" --assets normal   # runs; repeat to resume
gst status pratfall --watch                                       # progress (jobs/STATUS.md)
gst coverage pratfall                                             # jobs/COVERAGE.md per category
```

`gst study`:

1. resolves or creates the workspace `GS_HOME/<slug>`;
2. runs recon (engine, protections, verdict);
3. plans the engine's extractors and runs everything as resumable jobs (`jobs/jobs.db`).

Job order: loose files, code (decompile, index, REA), asset inventory, export of every type, analyses
(`analysis/*.json`) and, last, the heavy ones (AssetRipper project, Ghidra).

Code and assets run as two tracks side by side: the code notes can start while the assets still export. How
much of the assets to study is asked once per workspace (`--assets`, then kept in game.json; a run without it stops
and prints the estimate of each level):

- `none`: code only;
- `basic`: asset inventory, map list and overview analyses;
- `normal`: every export and asset analysis except the map/level containers (Unity scene bundles);
- `full`: everything, maps and the AssetRipper project included.

Every 5 minutes `gst study` prints a `status:` line per track and updates `jobs/STATUS.md`.

Options:

- `--assets none|basic|normal|full`;
- `--only` and `--skip`: extractors, prefixes (`code`) or categories;
- `--jobs N`;
- `--retry-failed`;
- `--force <extractor>`;
- `--opt key=value`;
- `--timeout`: optional; there is no default timeout;
- `--min-free 20GB`: pauses when free disk space drops below it, never cuts.

When a new build of the game ships (different buildid), the study goes to a sibling workspace `<slug>@<buildid>`, so
the citations of the old study stay valid. `--dry-run` only says it would create the sibling.

Helper commands:

- `gst jobs list|show|retry|reset|estimate` (`estimate`: CPUs, RAM, time per job and to finish, recommended `--jobs`);
- `gst analyze <name>|--all|--list`.

## Main commands

The commands below run inside a game's workspace, or with the slug.

| Layer | Commands |
| --- | --- |
| Workspaces | `gst new "<game>"`, `gst status [slug]`, `gst workspace` (lists GS_HOME), `gst migrate`, `gst refresh --all` |
| Recon | `gst recon` (engine, protections, verdict, network/voice middleware), `gst recon --list` |
| Code (.NET, GDScript, Java, Lua, IL2CPP, native) | `gst decompile [native\|il2cpp\|java\|lua]`; `gst q def\|outline\|show\|callers\|callees\|refs\|hier\|strings\|grep\|sym <target>` |
| Assets (Unity, Godot, Unreal 4/5) | `gst assets list`, `gst assets extract --types <types> --name "<regex>"`, `gst assets project`, `gst assets to-legacy` (Unreal IoStore) |
| Audio | `gst assets audio`, `gst audio index\|pcm\|check\|page` |
| Maps and levels | `gst maps list`, `gst maps raster <file> ...` |
| Analyses | `gst analyze --list`; prefabs, mono_values, catalogs, animation, controller, avatar, rig, joints, shaders, textures, audio_refs, audio_catalog, dcc, gltf_summary, godot_*, unreal_assets, unreal_values, unreal_animation, systems |
| Formats (saves, data) | `gst fmt id\|entropy\|hex\|strings\|diff\|scan`, `gst fmt new-reader`, `gst fmt roundtrip` (byte-exact proof) |
| Runtime (`offline-ok` only) | `gst trace scan\|next\|watch\|snapshot\|diff`, `gst sandbox create` |
| Graphics (`offline-ok` only) | `gst gfx capture\|export` (RenderDoc from the install copy) |
| Notes and report | `gst map`, `gst notes new\|templates\|index\|search\|check`, `gst report --check` |
| Tools and health | `gst tools list\|install\|path\|info`, `gst doctor`, `gst selftest`, `gst config show\|set` |

Every command accepts `--json` and `--help`.

## Claude Code plugin (`gst@gst-local`)

Register the plugin with `.\install.ps1 -Plugin`. To try it without installing: `claude --plugin-dir plugins/gst`.

- **Skills** (`gst:*`):
  - `game-study` is the entry point: "analyze this game", "study Pratfall" (pt-BR requests work too);
  - `study-recon`, `study-decompile`, `study-assets`, `study-audio`, `study-maps`, `study-formats`,
    `study-logic`, `study-runtime`, `study-graphics` and `study-report`;
  - `mechanics`, `dossier` and `compare`.
- **Agents**: `recon`, `code-navigator`, `asset-analyst`, `mechanics-analyst`, `runtime-observer`,
  `synthesizer` and `comparer`. Each one writes its deliverable early and closes its "Não verificado" (not verified)
  section; a `SubagentStop` hook enforces it. Study notes are written in Brazilian Portuguese.
- **Workflows**:
  - `/gst:study-all` studies everything after `gst study`, in waves, with your `focus` list first
    (`gst config set focus "ragdoll,co-op,..."`);
  - `/gst:map-subsystems` studies the game's systems;
  - `/gst:verify-claims` checks `ARCHITECTURE.md` against the code.
- **Note templates**: `gst notes templates` lists them. Examples: `sistema`, `mecanica`, `ragdoll-fisica`,
  `rede-voz`, `comparativo`. They come prefilled with tables from `analysis/*.json`.

After `gst new` or `gst study`, open the workspace folder in VS Code and accept the trust dialog. That is what
enables the workspace permissions: `gst`, skills and reading the game folder.

## Cross-game library

```powershell
gst lib build                       # GS_HOME/_library/library.db, from every workspace
gst lib build --legacy D:\game-study   # adds old gs homes, for search only
gst lib compare rigs peak repo pratfall safari-tv   # _library/compare/rigs-....md (pt-BR)
gst lib search "ragdoll"            # search notes, FACTS, ARCHITECTURE and analyses
gst lib status
```

Facets: rigs, animation, joints/ragdoll, audio, shaders, middleware, systems, dcc. What a game lacks shows up as a
gap, never as an error.

## Workspaces and migrating from `gs`

A workspace is the folder `GS_HOME/<slug>`. It holds:

- `game.json`, `recon/`, `decomp/`, `assets/`, `dumps/`, `traces/`, `notes/`, `index/` and `logs/`
  (the `gs` layout);
- `jobs/` and `analysis/` (what `gst` adds);
- `STUDY.md`, `FACTS.md` and `ARCHITECTURE.md`.

`gs` (v1) workspaces open directly in `gst`. To import them as jobs:

```powershell
gst migrate --all --dry-run --report rehearsal.md   # read-only
gst migrate --all
```

The migration is additive and idempotent, and `gs` keeps reading the workspaces. Step by step, switching the plugin
and rolling back: [`docs/MIGRATION.md`](docs/MIGRATION.md).

## Guides

- Tools and engines: [`docs/tools/ghidra.md`](docs/tools/ghidra.md) (Ghidra headless, PDB, IL2CPP names),
  [`docs/tools/unity.md`](docs/tools/unity.md) (Addressables catalogs, scene animators, humanoids, audio categories),
  [`docs/tools/godot.md`](docs/tools/godot.md) (PCK, binary resources, rig links, shaders),
  [`docs/tools/unreal.md`](docs/tools/unreal.md) (repak, retoc, uas, usmap, keys, values, animation),
  [`docs/tools/java-lua.md`](docs/tools/java-lua.md) (Vineflower, unluac) and [`docs/tools/rea.md`](docs/tools/rea.md)
  (REA jobs, optional IL2CPP patch);
- per engine, where code and data live and the pitfalls:
  [`plugins/gst/references/engines/`](plugins/gst/references/engines/). `RECON.md` points to the right guide;
- tool catalog and versions: [`plugins/gst/references/tools.md`](plugins/gst/references/tools.md);
- internal contracts (workspace v2, jobs, analyses, library, migration):
  [`docs/INTERFACES.md`](docs/INTERFACES.md);
- what is not done or not verified yet: [`docs/KNOWN-LIMITATIONS.md`](docs/KNOWN-LIMITATIONS.md);
- development: [`docs/DEV.md`](docs/DEV.md) and [`CLAUDE.md`](CLAUDE.md).

## Example with the sample game

[`samples/sample-game`](samples/sample-game/README.md) is our own unprotected game, for practicing the layers that
need the game running. To build it: `samples\sample-game\build.ps1`, which needs VS Build Tools with C++ and the
.NET SDK.

```powershell
gst new "<repo>\samples\sample-game\build" --slug sample-game
cd D:\game-study\sample-game
gst decompile native                 # SampleGame.exe + PDB
gst trace scan 100                   # with the game open: the player's health
gst trace next --decreased           # after taking damage
gst trace watch scan                 # who writes it (function name from the decompiled code)
gst sandbox create
gst gfx capture --after 3            # frame.md: passes, shaders, constant buffers
gst fmt new-reader sgsv --sample dumps\run.sgs
gst fmt roundtrip notes\formats\sgsv.py dumps\run.sgs
```

## Troubleshooting (Windows)

| Symptom | What to do |
| --- | --- |
| `gst` is not recognized | Restart VS Code or the terminal. `gst.exe` is in `%USERPROFILE%\.local\bin` (`install.ps1 -AddToPath` puts the folder on the PATH). |
| Claude asks permission for every `gst` | Open `GS_HOME\<game>` and accept the trust dialog. After updating the repository, run `gst refresh --all`. |
| `gst:*` skills do not show | `.\install.ps1 -Plugin` and restart the session. `gst doctor` shows the new plugin and the old one. |
| Extraction fails on a long path | `.\install.ps1 -EnableLongPaths` (UAC). `gst` already uses `\\?\` paths; third-party tools do not. |
| `gst trace` or `gst gfx` refuse the game | The verdict is not `offline-ok`. See `recon/protections.md` and study the files on disk. |
| `gst doctor` warns about running anti-cheat | It is only a warning. `gst` never stops or removes those services. |
| A job ended `failed` | `gst jobs show <id>` shows the full error. Fix it and run `gst study <slug> --retry-failed`. |
| Ghidra takes long on a big exe | The first analysis takes minutes (or hours on big IL2CPP games). See [`docs/tools/ghidra.md`](docs/tools/ghidra.md). |

## Development

```powershell
.venv\Scripts\python -m pytest                 # unit tests
.venv\Scripts\python -m pytest -m tools        # real tools on the sample game
.venv\Scripts\python scripts\check_no_derived.py
claude plugin validate plugins/gst --strict
```

Details: [`docs/DEV.md`](docs/DEV.md). Instructions for working on the code: [`CLAUDE.md`](CLAUDE.md).
