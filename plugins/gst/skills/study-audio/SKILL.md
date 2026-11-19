---
name: study-audio
description: Study a game's sound end to end - export every sound effect, voice, dialog line and music track into the workspace, inventory formats and durations, map each sound event to its files and to who triggers it (units, weapons, UI, map scripts), explain the sound engine (channels, priorities, limits, 2D/3D, shroud/occlusion, randomization, localization, music selection) from the code, and build a local page to browse and listen. Part of every game study; any engine.
when_to_use: The user asks about the game's sounds, effects, voices or music, or a study reaches its audio layer (every study does). Examples - "extract the sounds", "how are sound effects used", "which unit plays this voice", "how does the music change", "puxa os sons do jogo", "como os efeitos sonoros são usados", "quais vozes as unidades têm", "como a música do jogo funciona", "estuda o áudio do jogo".
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Study the audio

Where you are: the session context names the study workspace, if any (`gst status` shows it).

Goal, for every study: (1) all the game's audio exported and inventoried, (2) a catalog that ties each
sound **event** to its **files** and to **who triggers it**, (3) how the sound engine decides what plays,
(4) `### Áudio` in ARCHITECTURE.md section 4 (`gst report --check` requires it). Notes in pt-BR.

## 1. Export everything into assets/audio/

Games rarely play a file by name: the code asks for an **event** (a named sound with variations,
volume, priority, limits) and the event points to files. Export the files first, keeping their
in-game paths so the catalog can resolve them.

| Where the audio lives | How to export |
|---|---|
| FMOD `.bank`/`.fsb`, Wwise `.bnk`/`.wem`/`.pck`, CRI `.acb`/`.awb`, XACT `.xwb`... | `gst assets audio --list`, then `gst assets audio "<glob>" --all` |
| Unity `AudioClip`, Godot samples, Unreal `SoundWave` | `gst assets extract --types AudioClip --all` (Unity), `gst assets extract --types audio --name "<regex>"` (Godot), `gst assets extract --types audio --name "<regex>"` (Unreal) |
| Loose files or a custom archive (`.big`, `.pak`, `.dat`...) | read them in place, or write a reader (study-formats) and extract to `assets/audio/<virtual path>`. Apply the engine's override order when the same path is in several archives: keep the winning copy, put the losers under `assets/audio/overridden/` and give each file `"effective": false` in files.jsonl |

Then inventory and make everything listenable:

```
gst audio index                 # assets/audio/files.jsonl: format (PCM, IMA-ADPCM...), rate, channels, seconds
gst audio pcm                   # PCM copies of WAVs a browser cannot play (assets/audio/pcm/), originals untouched
```

## 2. The catalog: event -> files -> who uses it

Write `assets/audio/catalog.json` with a small script under `notes/formats/tools/` (the engine decides
where events and their users are):

| Engine | Events and their files | Who uses them |
|---|---|---|
| INI/text data (SAGE, many RTS) | `AudioEvent`/`DialogEvent`/`MusicTrack`-like blocks | object/weapon/FX blocks whose values name the event; map scripts |
| FMOD / Wwise | event names in `.strings.bank` / `SoundbanksInfo.xml` (or the bank) | `gst q strings "<event>"` in the code; scenes/prefabs/Blueprints that name it |
| Unity | `AudioClip` objects | `AudioSource` and MonoBehaviours in prefabs/scenes (`gst assets project`, then `rg` the clip's guid) |
| Unreal | `SoundCue`/`MetaSound` -> `SoundWave` | Blueprints and data assets that reference the cue (`gst assets extract --name ... --bytecode`) |
| Godot | `AudioStream` resources | `.tscn` nodes (`AudioStreamPlayer*`) and scripts |

Schema (one object per event; `gst audio check --help` repeats it): `name`, `kind` (sound, voice, dialog,
music, ui, ambient...), optional `category`, `defined_at` (`path:line`), `fields` (volume, priority,
limit, type/flags as the game names them), `files` (`[{"name", "file": "assets/audio/..." or null}]`),
`used_by` (`[{"where": "path:line", "block": "Object Tank", "key": "VoiceSelect"}]`).

Resolve names to files with the engine's own rule, read from the code: the folder per kind, the
extension, the **localized folder tried first**, and random or sequential variations. Search for users
in **every** data file that is actually loaded, and in map scripts too (study-maps).

```
gst audio check                 # missing files, unreferenced files, events nobody uses -> catalog_summary.json
gst audio page                  # assets/audio/INDEX.html: tabs by category, search, fields, who uses it, players
```

The page stays in the workspace: never publish or share it (guardrails rule 1).

## 3. The sound engine, from the code

Delegate one `code-navigator` with this checklist (it must write `notes/logic/audio.md` early in its
turns, then refine):
- the manager and device classes, when they update, channel budgets (2D/3D/streams), cache;
- every event field with its formula (pitch and volume shift, delay, loop count, attack/decay layers);
- an event's lifecycle: who may hear it (player/allies/everyone), shroud or occlusion, distance,
  per-event limit, priority and what gets dropped when channels run out;
- determinism: a separate audio RNG, or the logic RNG for sounds that affect gameplay timing;
- unit voices (which slot plays on select, move, attack...), announcer/EVA-style events and cooldowns;
- music: who picks the track (code, data, map scripts), per faction, menus and loading screens.

## 4. Record

- `notes/assets/audio-catalogo.md`: counts by kind, formats, totals in minutes, one worked example (every
  sound of one unit with the key that triggers it), music selection, and what does not fit (events whose
  file is missing, files no event uses, silent placeholder events).
- `notes/logic/audio.md`: the engine (section 3) with `path:line`.
- `### Áudio` in ARCHITECTURE.md section 4; key numbers in FACTS.md.

## Done when

`gst audio check` ran on a full catalog, `assets/audio/INDEX.html` plays every file it lists, both notes
exist with evidence and `gst report --check` passes.
