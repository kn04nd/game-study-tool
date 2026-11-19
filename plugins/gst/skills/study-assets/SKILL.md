---
name: study-assets
description: Inventory and extract a game's assets and data into readable files in the study workspace - textures and sprites to PNG with thumbnails, audio banks and clips to WAV, text/binary data assets, fonts, meshes to OBJ, Unity MonoBehaviour/ScriptableObject data to JSON, Godot scenes to .tscn text, Unreal assets and Blueprints to JSON - with provenance sidecars and an INDEX.md, plus plain data files read straight from the install. Unity, Godot and Unreal 4/5 (encrypted paks are diagnosed and listed, never opened); audio containers (FMOD, Wwise, CRI...) for any engine.
when_to_use: The user wants to see or understand game content and data rather than code. Examples - "extract the textures", "what sounds does the game have", "list the assets", "where are the item stats stored", "how is the data organized", "extrai as texturas", "quais sons tem no jogo", "lista os assets", "onde ficam os dados dos itens", "como os dados do jogo estão organizados".
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Study the assets and data

Where you are: the session context names the study workspace, if any (`gst status` shows it).

Goal: know what content the game ships, how it is organized and how the code consumes it, with
provenance for every exported file. Reply in pt-BR; notes in pt-BR.

## 1. Inventory before exporting

```
gst assets list                                        # Unity: every object -> assets/INVENTORY.md + inventory.jsonl
gst assets list --types Texture2D,TextAsset --name "^hatch_" --limit 20   # look at a slice first
gst assets audio --list                                # any engine: audio containers, stream counts, minutes
```

Read `assets/INVENTORY.md` (types, sizes, biggest objects). Search `assets/inventory.jsonl` with `rg` for
names; it has the type, source file, path_id and container of every object.

## 2. Extract selectively

```
gst assets extract --types Texture2D,Sprite --name "<regex>" --limit 50      # PNG + 256 px thumbnails
gst assets extract --types TextAsset --name "<regex>"                         # text or raw bytes as shipped
gst assets extract --types MonoBehaviour --file "resources.assets" --limit 20 # data objects as JSON (typetrees from the game's assemblies)
gst assets extract --types AudioClip,Font,Mesh --limit 30
gst assets audio "<file or glob relative to the install>" --name "<regex>" --limit 20   # WAV per stream
```

For scenes, prefabs and how objects are wired together, reconstruct the whole Unity project once
(several minutes; output can be GBs): `gst assets project` (YAML scenes/prefabs under
`assets/assetripper/project/`), or `gst assets project --mode primary` for content only.

Godot games work the same way, through GDRE Tools: `gst assets list` inventories the pack,
`gst assets extract --types scene,texture,audio,text --name "<regex on the res:// path>"` recovers files
already converted (binary scenes to `.tscn` text, `.ctex` to PNG, samples to WAV) into
`assets/godot/extract/`, and `gst assets project` recovers the whole project. A `.tscn` lists the
scripts attached to each node: the quickest map of how a player, enemy or level is composed.

Unreal 4/5 games are read straight from the paks (repak, UnrealAssetScout); there is no whole-project
export, since one pak holds 100k+ files:

```
gst assets list                                         # containers (version, encrypted, signed) + every packed path
gst assets list --name "/BP_.*Player"                   # search packed paths (regex)
gst assets extract --name "Character/BP_PlayerCharacter\.uasset$" --bytecode   # JSON (+ Kismet bytecode)
gst assets extract --types textures --name "UI/.*/Icon_.*"                     # PNG; also audio, models, raw
```

- JSON exports land in `assets/unreal/json/<packed path>.json`: properties, components (`SCS_Node`), the
  C++ parent (`SuperStruct`) and, with `--bytecode`, each Blueprint function as `EX_*` statements.
- Every export of a mode is re-run with the union of the filters used so far (the tool deletes what a
  narrower filter leaves out); `--bytecode` stays on for the folder once used.
- Encrypted containers: `gst assets list` takes the paths from `Manifest_UFSFiles_*.txt` and stops there
  (guardrails rule 4); the folder structure and names still say a lot about how the game is organized.
- Wrong engine version for CUE4Parse: `--ue 4.27` / `--ue 5.4` overrides the one recon found.

- Default limit: 200 objects (audio: 50 streams) per run; `--all` lifts it. Narrow with `--name`,
  `--container`, `--file` instead of exporting everything.
- Re-runs are safe: audio skips streams already converted; Unity exports add `__<path_id>` on name clashes.
- Results: `assets/unity/<Type>/`, `assets/godot/`, `assets/unreal/<mode>/`, `assets/audio/<container>/`, with
  `.gs.json` sidecars (source, object id, tool, sha256; one per folder for Unreal); `assets/INDEX.md`
  summarizes them (`gst assets index` rebuilds it).

Audio and levels have their own skills, part of every study: **study-audio** (catalog of sound events,
who triggers them, the sound engine, a listening page: `gst audio index`) and **study-maps** (level
files, container, contents, load flow: `gst maps list`).

## 3. Look and connect

- View textures through their thumbnails (`assets/unity/_thumbs/...`): Read a PNG to see it.
- Plain data files on disk (YAML, JSON, XML, CSV, INI, Lua) are often the real game data: read them in
  place in the install folder (read-only) and cite their paths.
- Connect content to code: `gst q strings "<asset or file name>"` finds where the code loads it.
- Unknown binary formats: inspect a small sample with ImHex (`gst tools path imhex`) or a hexdump of at
  most 512 bytes; describe the layout in `notes/formats/<format>.md`.

## 4. Record

`notes/assets/<topic>.md`: what exists (counts, naming conventions, folders), formats, how the game loads
and uses it (with `path:line` from the code), and open questions. Key facts go to FACTS.md.

## Done when

`assets/INDEX.md` lists the exported sets, the notes explain organization and formats with evidence,
and the question that motivated the export is answered.

## Common problems

| Symptom | Fix |
|---|---|
| `handle Unity, Godot and Unreal 4/5 games in this version` | other engines: read data files in place; audio works for any engine |
| `encrypted with an unpublished key` | diagnosis only (rule 4): study the paths in `assets/INVENTORY.md`, configs on disk, public docs |
| `no packed asset matches` | find the real path with `gst assets list --name <part>` |
| Unreal JSON fails or looks wrong | engine version mismatch: pass `--ue <major.minor>` |
| `UnityPy is not installed` | `gst tools install unitypy` |
| MonoBehaviour export fails | the script may live in an assembly that is not in `Managed/`; export TextAssets or read the code instead |
| empty Texture2D / no PNG | some textures are empty placeholders; check `size` in the inventory |
| `stopped at the limit` | narrow the filter or raise `--limit` |
