---
name: mechanics
description: Turn one mechanic of a studied game into numbers and rules for the user's own game design - the tunable parameters serialized in ScriptableObjects/MonoBehaviours, Godot exported variables or Unreal DataTables (read from analysis/*.json), confirmed in the decompiled code, plus rules and formulas, game feel (timings, curves, feedback) and a mandatory "Relevância para o meu jogo" section, written into a note pre-filled with gst notes new --template mecanica.
when_to_use: The user wants the numbers or the feel of a mechanic, or wants to borrow it. Examples - "what are the movement values", "how does the jump feel so floaty", "what parameters does the ragdoll use", "how is the extraction loop tuned", "quais os valores do movimento do jogador", "como o pulo desse jogo é ajustado", "quais parâmetros o ragdoll usa", "como funciona o loop de extração", "o que posso aproveitar dessa mecânica no meu jogo".
argument-hint: "[mechanic, e.g. movimento do jogador]"
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# A mechanic as numbers

Where you are: the session context names the study workspace, if any (`gst status` shows it).

Goal: one note per mechanic that the user can prototype from: every parameter with its value, unit, effect and
source, the rules that use them, how it feels, and what fits their own game. Reply and write in pt-BR,
identifiers as they are. One mechanic per run; for several, one `mechanics-analyst` agent each.

## 1. Check what gst study produced

```
gst status                        # phase, jobs
gst coverage                      # what was extracted and analyzed, by category
gst analyze --list                # analyses that apply to this engine and whether they ran
```

The values usually already exist: Unity `analysis/mono_values.json` and `catalogs.json` (fields of every
MonoBehaviour/ScriptableObject class), Godot `analysis/godot_scenes.json` `data.tunables` (exported variables:
default in code vs value set in each scene) and `data_resources`, Unreal `analysis/unreal_assets.json`
`data.datatables`, and `analysis/systems.json` (systems with their key types and `path:line`).
A missing analysis: `gst analyze <name>` (only the one you need; `gst study` reruns everything).

## 2. Create the note first (deliver-first)

```
gst notes new logic/<mechanic> --template mecanica --system "<regex on the system label>" --match "<regex on script names>"
```

It is pre-filled with the system's key types and the matching tunables. Save it early and often; the rest of
this skill fills it. Bodies and ragdolls: `--template ragdoll-fisica`; a whole system: `--template sistema`.

## 3. Confirm in the code

- Where each parameter is read: `gst q refs <Type.Field>`, `gst q show <Type.Method>`.
- Defaults vs serialized values: the analysis gives both; the serialized one is what the game uses. Cite the
  analysis key (e.g. `analysis/godot_scenes.json` `data.tunables["res://.../Player.cs"].exports.Speed`) and the
  code line.
- Formulas: write them with the parameter names, units (m/s, s, N, degrees) and `path:line`.

## 4. Game feel

What makes it feel the way it does: acceleration and braking times, air control, coyote time, input buffering,
curves (AnimationCurve, Curve resources), camera response, sound and particles fired on each event (the audio
catalog says which), hit-stop, physics materials. Mark perceptions without code evidence as **[I]**.

## 5. Relevância para o meu jogo (mandatory)

What to borrow as an idea (not code or assets), starting values for a prototype, what to change for the user's
game, implementation cost, risks. The user's study priorities are `gst config show` -> `focus`.

## Done when

The note has the tunables table with sources, the rules and formulas with `path:line`, the game-feel section,
"Relevância para o meu jogo" and "Não verificado", and `gst notes check` reports no broken citation.
