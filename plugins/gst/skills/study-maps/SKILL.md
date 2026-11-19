---
name: study-maps
description: Study how a game's maps and levels are made and loaded - find every level file (loose, packed, or built scenes), identify the container (compression, chunks, scene graph), prove a reader with a byte-exact round trip, decode what a map holds (terrain/heightmap, tiles and textures, placed objects and spawns, scripts and triggers, lighting, navigation data), render heightmaps as PNG, and follow the load flow in the code from menu to playable world. Part of every game study; any engine.
when_to_use: The user asks about maps, levels, missions or scenes, or a study reaches its maps layer (every study does). Examples - "how are the maps made", "what is inside a level file", "how does a mission load", "where are the spawn points", "arquitetura dos mapas", "como os mapas do jogo são feitos", "o que tem dentro de um mapa", "como uma fase é carregada", "estuda os níveis do jogo".
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Study the maps and levels

Where you are: the session context names the study workspace, if any (`gst status` shows it).

Goal, for every study: what a map **is** on disk, what it **contains**, how the game **loads** it and
how it connects to the other systems (navigation grid, scripts, AI, audio, rendering), with
`### Mapas e níveis` in ARCHITECTURE.md section 4 (`gst report --check` requires it). Notes in pt-BR.

## 1. Find the levels

```
gst maps list                   # level files in the install and in assets/**/inventory*.jsonl -> dumps/maps/levels.json
```

| Engine | Where levels are | First look |
|---|---|---|
| Unreal | `.umap` in the paks | `gst assets list --name "\.umap$"`, `gst assets extract --name "<map>\.umap$"` (JSON: actors, components) |
| Unity | built scenes `level0..N`, scenes in bundles | `gst assets project` (scenes as YAML: GameObjects, components, transforms) |
| Godot | `.tscn`/`.scn` | `gst assets extract --types scene --name "<regex>"` (text scenes: nodes, scripts, TileMap data) |
| Source / idTech / GoldSrc | `.bsp` | lumps (entities lump is text) |
| Custom engines | `.map`, `.lvl`, `.wld`, files inside archives | `gst fmt id`, `gst fmt entropy`, `gst fmt strings`, `gst fmt hex` |

No hits by name: the levels sit in a custom archive (list it with a reader first; study-formats) or are
built by code from data tables (find the loader in the code).

## 2. The container, proved

1. Compression envelope: magic + size + stream (zlib `78 9c`, LZ4, RefPack `10 fb`, Oodle...). Find the
   decompressor in the code or a public spec; port it to `notes/formats/tools/` and check that the
   declared size equals the decoded size on **every** map.
2. Structure: chunks/lumps/sections (id, version, size), a name table, nested blocks, or a scene graph.
3. Reader: `notes/formats/<map>.py`, proved with `gst fmt roundtrip notes/formats/<map>.py dumps/maps/*.bin`
   on all the maps (keep unknown payloads raw so the round trip holds while you decode).
4. Statistics over all maps (sizes, chunk versions, counts) in `dumps/maps/`: they show which fields vary.

## 3. What a map holds

Decode the parts with the code that reads them (`gst q def/callers/strings`; for chunked formats the
parser registered per chunk name). Usual parts:

| Part | Questions |
|---|---|
| Terrain / heightmap | grid size, border, cell size in world units, height scale (the formula to world Z); render it with `gst maps raster <file> --offset <n> --width <w> --height <h> --dtype u8 --flip-y` and look at it |
| Textures / tiles / blends | per-cell texture index, blending, cliffs, water |
| Placed objects | position, rotation **unit** (check the value range over all maps), template name, properties; count them by type |
| Players / sides / teams / spawns | start positions (named waypoints, spawn actors), how the player count is derived |
| Scripts and triggers | condition/action trees, trigger areas, what they can call (music, audio, objectives) |
| Lighting, weather, time of day | global lights, fog, skybox |
| Navigation | navmesh or grid; whether a map cell is a pathfinding cell |
| Sibling files | previews, per-map data overrides, strings, extra layers |

Validate a decoded layout on **all** maps: every payload must be consumed to its last byte.

## 4. The load flow

Delegate one `code-navigator` (it must write `notes/logic/mapas.md` early in its turns, then refine):
from the menu or mission choice to a playable world - metadata cache, file opening and decompression,
which reader takes which part (logic vs. rendering), object creation, scripts installed, navigation
built, players placed, music and ambience started.

## 5. Record

- `notes/formats/<map>.md`: envelope, container, the layouts proved, statistics, rendered heightmaps.
- `notes/logic/mapas.md`: semantics of each part and the load flow, with `path:line`, plus a Mermaid
  diagram of the load flow.
- `### Mapas e níveis` in ARCHITECTURE.md section 4; key numbers (cell size, height scale, counts) in FACTS.md.

## Done when

The reader round-trips every map, the main parts are decoded and checked against all maps, the load
flow is cited from the code, and `gst report --check` passes.
