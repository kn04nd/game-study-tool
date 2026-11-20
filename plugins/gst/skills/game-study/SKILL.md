---
name: game-study
description: Study how a game the user owns is built, end to end, with the Game Study Toolkit (`gst` CLI). Finds the install, creates the study workspace, runs recon (engine, protections, policy verdict), plans which layers to study (code, assets, data formats, runtime, graphics, game logic), drives gst and the third-party tools, keeps the study journal and the verified facts current, and consolidates everything into ARCHITECTURE.md. Entry point for any game-study request.
when_to_use: The user wants to understand how a game works or how it was made, or names an installed game to analyze. Examples - "analyze this game", "study Oxygen Not Included", "how was <game> made", "how does <game> work under the hood", "reverse engineer <game>", "analise este jogo", "estude o <jogo>", "como esse jogo foi feito", "como o <jogo> funciona por dentro", "engenharia reversa do <jogo>". Not for building mods, trainers or cheats.
argument-hint: "[game name | Steam AppID | install folder]"
allowed-tools: Bash(gst *) PowerShell(gst *) WebSearch
---

# Game study

Where you are: the session context names the study workspace, if any (`gst status` shows it).

You guide a deep, evidence-based study of a game the user owns: how its code, data, assets, runtime
and rendering fit together. The goal is understanding, not modification. The user speaks pt-BR: talk
to them in pt-BR and write STUDY.md, notes and ARCHITECTURE.md in pt-BR; keep code, paths and symbol
names as they are.

## Ground rules

- **Read the real thing, don't guess.** Every claim cites evidence: `path:line`, `module+0xRVA @ build <id>`,
  a capture/trace id, or a command and what it printed. Ideas without evidence are labelled hypotheses.
- **Three identical failures: stop.** Change approach and record the dead end in STUDY.md.
- **Everything derived stays in the workspace** (`GS_HOME/<slug>`: decomp/, assets/, dumps/, traces/, notes/).
- **Limits** are in `${CLAUDE_PLUGIN_ROOT}/guardrails.md`; read it once per study. Hooks enforce it. A denial
  names the reason and rule: don't retry variations, offer the alternative it points to.
- Routine analysis needs no confirmation: inside a workspace, `gst` commands are pre-approved.
- Bash finds `gst` directly. In PowerShell use the form given in the session context (`gst` or `& '<path>/gs.exe'`).

## 1. Find the game and its workspace

- The status line above names a game: you are in its workspace, go to step 2.
- Otherwise list what is installed and create the workspace:
  ```
  gst recon --list                      # Steam, Epic, GOG and Xbox installs, with AppIDs
  gst new "<name | AppID | folder>"     # GS_HOME/<slug>: game.json, CLAUDE.md, STUDY/FACTS/ARCHITECTURE.md, recon
  ```
  Then ask the user to reopen Claude Code in that folder (its settings, permissions and session
  context load there), or `cd` into it in the shell and continue now.

## 2. Recon

Run `gst recon` in the workspace (`--deep` adds Detect It Easy on the main executables), read
`recon/RECON.md` and `recon/protections.md`, and tell the user in a few lines: engine and version, code
type, protections, verdict and what it allows. For the manual checklist and the search for public
knowledge, use the **study-recon** skill or delegate to the `recon` agent (read-only, fast).

| Verdict | Meaning | What can be studied |
|---|---|---|
| `offline-ok` | no anti-cheat, DRM wrapper or protector; offline/solo play exists | every layer; runtime only offline/solo; injecting tools only in a sandbox copy |
| `static-only` | DRM wrapper, anti-tamper, launcher account or online backend | files on disk and unprotected modules; no runtime; never unpack the protected exe |
| `no-runtime` | anti-cheat (VAC included) or always-online | unencrypted data on disk; no runtime, no client-code decompilation |

When protection blocks a layer: say so plainly, record it in STUDY.md, and offer what remains (data on
disk, an offline version, public SDKs or source, official tools).

## 3. Plan the study

Ask what the user wants to understand, or propose 3-5 questions grounded in the recon ("how the
simulation ticks", "what the save file holds", "how the AI picks tasks", "how a frame is drawn").
**Every plan also includes two layers, even when the user did not ask:** audio (how sound effects,
voices and music are organized and used: **study-audio**) and maps/levels (format, content, load flow:
**study-maps**). `gst report --check` requires a `### Áudio` and a `### Mapas e níveis` system in
ARCHITECTURE.md section 4 (or a heading saying why one does not apply).
Write the plan in STUDY.md under `## Plano`: question → layer → tool → order. Check tools with
`gst doctor` and install what is missing with `gst tools install <id|profile>`.

| Layer | Look at | How (skill → commands) |
|---|---|---|
| Code: managed (.NET/Mono, Godot C#, XNA/FNA), GDScript, Java, Lua | game assemblies, Godot packs, jars, scripts | **study-decompile**: `gst decompile`, then `gst q def/outline/show/callers/callees/strings` |
| Code: Unreal Blueprints | `BP_*` assets in the paks | **study-assets**: `gst assets extract --name <path> --bytecode` (JSON with Kismet bytecode) |
| Code, native | exe/dll, `.pdb` when shipped | **study-decompile**: `gst decompile native` (Ghidra headless; C per class with field layouts), then `gst q` |
| Assets and data | textures, audio, models, scenes, data assets, configs | **study-assets**: `gst assets list/extract` (Unity, Godot, Unreal), `gst assets project` (Unity, Godot), `gst assets audio` (any engine); plain data files read in place |
| Binary formats | saves, data files, custom containers | **study-formats**: `gst fmt id/entropy/hex/diff`, then a reader proved by `gst fmt roundtrip` |
| Runtime | memory, writers, save folders (only `offline-ok`) | **study-runtime** (agent `runtime-observer`): `gst trace scan/next/watch`, `gst trace snapshot/diff`, `gst sandbox` for injectors |
| Graphics | passes, render targets, shaders (only `offline-ok`) | **study-graphics**: `gst sandbox create`, `gst gfx capture` -> `frame.md` |
| Audio (always) | sound events, files, who triggers them, sound engine, music | **study-audio**: export, `gst audio index/pcm`, catalog.json, `gst audio check/page` |
| Maps and levels (always) | level files, container, terrain, placed objects, scripts, load flow | **study-maps**: `gst maps list`, a reader proved by `gst fmt roundtrip`, `gst maps raster` |
| Logic | systems, formulas, state machines | code + data → specs in `notes/logic/` (delegate one subsystem per `code-navigator` agent) |

Extract first, then read: `gst study` runs every extractor and analysis of the engine as resumable jobs
(`gst status`, `gst jobs list`, `gst coverage`); the notes start from `analysis/*.json`. Before a long run, or when
the user asks why it is slow, run `gst jobs estimate` (needs a planned study; `--json` for the numbers): with
`decision: auto` start `gst study <slug> --jobs <recommended>`; with `decision: ask` show the user the time per
`--jobs` value and the reasons, and let them choose. Changing `--jobs` of a running study means stopping it (its
running jobs restart). Once it finished, the
workflow `/gst:study-all` plans one agent per system, mechanic or asset family (the user's `focus` list from
`gst config show` first), runs them in waves, consolidates ARCHITECTURE.md, runs verify-claims and
`gst report --check`.

Parallel work by hand: one `code-navigator` per subsystem, one `mechanics-analyst` per mechanic (numbers,
game feel, relevance to the user's game: **mechanics** skill), one `asset-analyst` per asset family (dossiers:
**dossier** skill), the `comparer` across studied games (**compare** skill). Agents follow deliver-first: put a
line `DELIVERABLE: notes/<layer>/<topic>.md` in each task; the agent writes that file from a pre-filled template
(`gst notes new <layer>/<topic> --template <name>`, list: `gst notes templates`) in its first turns, saves it
every ~10 turns and closes "Não verificado" at the end; a hook keeps it from stopping without the file.

Until a dedicated `gst` command exists for a layer: run the tool yourself with output under the
workspace, never next to the game files, and note the exact command in STUDY.md.

## 4. Record as you go

- **STUDY.md**: the hooks log `gst` and reverse-engineering commands automatically. You add decisions,
  hypotheses, dead ends and open questions (`## Perguntas abertas`).
- **FACTS.md**: one table row per verified fact (what, where, evidence, how verified). Keep it under 4 KB:
  it is re-injected after context compaction.
- **notes/<layer>/<topic>.md**: the long form, one topic per file.
- **ARCHITECTURE.md**: the consolidated result, filled section by section as layers finish.

Large outputs stay on disk: read indexes and slices (`Read` with offset/limit, `rg`), not whole trees.

## 5. Done

A study round is done when every question in `## Plano` has an answer in notes/ with evidence,
FACTS.md holds the key facts, ARCHITECTURE.md covers the studied systems and lists what was **not**
verified, and STUDY.md records what was tried.

## Common problems

| Symptom | Fix |
|---|---|
| `game not found` | `gst recon --list`; pass the install folder or the AppID |
| `workspace ... already exists` | it is there: `cd` into it (`gst status`), or `--slug` for a second study |
| `GS_HOME ... inside the git repository` | `gst config set gs_home D:\game-study` (outside any repo) |
| tool missing or another version | `gst doctor`, then `gst tools install <id>` |
| `gst` not found in PowerShell | Bash tool, or the `& '<path>/gs.exe'` form from the session context |
| a hook denied a command | read reason and rule; propose the legitimate alternative instead |
| workspace settings look outdated after a toolkit update | `gst refresh` |
