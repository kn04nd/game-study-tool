---
name: dossier
description: Write a dossier note about one asset family or system of a studied game from a pt-BR template pre-filled with the counts and tables of analysis/*.json - 3D models and their origin (DCC), a character's meshes, rig and animations, the audio catalog, maps and levels, network and voice, ragdoll and physics, a system - then complete it with evidence (exported files with provenance, path:line in the code) and a "Não verificado" list.
when_to_use: The user wants a write-up of a part of a game. Examples - "write a dossier on the 3D models", "document the character rig and animations", "catalog the game's audio", "document how co-op and voice work", "faz um dossiê dos modelos 3D", "documenta o personagem, o rig e as animações", "cataloga o áudio do jogo", "dossiê de rede e voz", "como foram feitos os modelos desse jogo".
argument-hint: "[template] [topic]"
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Dossier from a template

Where you are: the session context names the study workspace, if any (`gst status` shows it).
Reply and write in pt-BR, identifiers as they are. One dossier per run; in parallel, one agent each
(`asset-analyst` for assets, `code-navigator` for systems, `mechanics-analyst` for mechanics).

## 1. Pick the template

```
gst notes templates
```

| template | for | pre-filled from |
|---|---|---|
| `modelos-3d-dossie` | models, origin (Blender/Maya...), rigs, props, shaders | `gltf_summary`, `dcc`, `prefabs`, `rig`, animation analyses |
| `personagem-modelos-animacoes` | one character: meshes, skeleton, controller, clips, body physics | the same + ragdolls |
| `audio-catalogo` | sound events, categories, who triggers them | `audio_catalog` |
| `mapa-niveis` | level files, generation, spawn rules, load flow | `godot_scenes` instance graph, `prefabs`, `unreal_assets` |
| `rede-voz` | co-op, authority, sync, proximity voice | `recon/recon.json` `net_voice`, `systems` |
| `ragdoll-fisica` | bodies, joints, ragdolls, physics control | `joints`, `godot_animation` ragdolls, tunables |
| `sistema` / `mecanica` | a system / a mechanic with its numbers | `systems`, tunables |

## 2. Create it first (deliver-first)

```
gst notes new assets/modelos-3d --template modelos-3d-dossie
gst notes new logic/rede-voz --template rede-voz --system "rede|network"
```

`--system` picks the system of `analysis/systems.json` (regex on its label), `--match` the scripts or tables
whose tunables go in. Missing analyses become a line saying which `gst analyze` to run. `gst study` must have
run (`gst status`, `gst coverage`).

## 3. Complete it

Every `<!-- preencher -->` gets text with evidence: an `analysis/<x>.json` key, an exported file in `assets/`
(its `.gs.json` sidecar is the provenance), or `path:line` in `decomp/` (`gst q strings`, `gst q refs`).
Measured = **[E]**, inferred = **[I]** (DCC origin is always inference). Save every few steps.

## Done when

No `<!-- preencher -->` left (`gst notes check` counts them and checks the citations), and "Não verificado"
lists what the data could not confirm.
