# Godot 3/4 in gst (stdlib readers, GDRE, analyses)

## What gst reads

A Godot game ships a pack (`*.pck`, or embedded in the exe). GDRE Tools (`gst assets project`) recovers it into
`assets/godot/project/`, converting most binary scenes/resources back to `.tscn`/`.tres` text. Everything else gst reads
with the Python standard library (`src/gst/engines/godot/`):

| module | reads |
| --- | --- |
| `pck.py` | PCK directory v1-v3 (encrypted directory/files reported, never decrypted) |
| `tres.py` | `.tscn`/`.tres`/`.escn` text -> Python/JSON (`Ctor`, `Ref`, `NodePath`, `StringName`; `Bulk` = packed array not decoded) |
| `rsrc.py` | binary resources `RSRC`/`RSCC` (`.scn`, `.res`, imported `.scn`/`.res`/`.mesh`/`.anim`/`.sample`...) -> the same document shape as `tres.parse`; `.ctex` headers (`ctex_header`) |
| `codecs.py` | RSCC block codecs: FastLZ, Deflate, Zstandard (pure Python, RFC 8878; `compression.zstd`/`zstandard` used when importable), Gzip, Brotli (needs the `brotli` package) |
| `scripts.py`, `animtree.py`, `shader.py` | exported script variables; animation/tree/skeleton/ragdoll structures; shader text (uniforms, render modes, features) |

## Binary resources (`rsrc.py`)

Port of `core/io/resource_format_binary.cpp` (Godot, MIT; see THIRD_PARTY_NOTICES): format versions 1-6 (Godot 3.x =
3, Godot 4.x = 4/5/6), little and big endian, `real_t` double flag, string table, external resources (with UIDs as
`uid://` text), internal resources (`local://<id>`), every variant type incl. packed arrays (Godot 3 `Pool*Array` names),
NodePath, StringName, dictionaries and arrays (typed containers are stored untyped in this format). A `PackedScene`
main resource is unpacked from its `_bundled` SceneState into `nodes` (path, type, parent, instance, groups, index,
owner, unique_id, props) and `connections` exactly like a `.tscn`.

- `rsrc.read(path, bulk=N, skip_types=(...))`: `bulk` leaves packed float/vector/byte arrays of N bytes or more
  undecoded (type + value count); `skip_types` leaves whole sub-resources unread (e.g. `ArrayMesh`). With RSCC the
  4 KiB blocks holding only skipped data are never decompressed. The analyses use `bulk=4096` (option `bulk=all`).
- `rsrc.header(path)`: type, versions, flags, uid, compression only.
- Encrypted files (`GDEC`) are reported as errors ("not decrypted"); nothing is ever decrypted.

Validation on Pratfall (Godot 4.6, format 6): all 2362 binary resources of the recovered project (55 in the project,
955 in `.godot/imported`, 1352 in `.godot/exported`) read with 0 errors, fully decoded (61 s with the pure-Python
zstd). The 1352 exported binaries match their GDRE text twins (paired by `md5(res path)` in the export file name) in
node paths, types, property names, connections and sub-resource classes; the only differences are an explicit
`script = null` the text omits (262), ext lists where GDRE dropped an unused script (93) and float32 rounding (10).

## Import remaps and rig links (`analysis/godot/links.py`)

`Project.import_info(res)` reads `<file>.import` (`[remap] path`, `importer`, `uid`); without it, the Godot naming rule
`.godot/imported/<file>-<md5(res path)>.<ext>` finds the imported file. `Project.walk(scene)` follows instances
recursively (imported model scenes included) and `Project.chains(scene)` gives one link per Skeleton3D: scene ->
model file -> imported scene -> skeleton (bones) -> skinned meshes -> AnimationPlayer/AnimationTree whose tracks
reach it (track paths resolved from the player's/tree's `root_node`) -> PhysicalBone3D bodies under it.

## Analyses

| analysis | what it adds |
| --- | --- |
| `godot_scenes` (v2) | binary `.scn`/`.res` parsed like text (`binary`, `binary_types`); per file `models` (instance -> model -> imported); `imported` = the import cache by class/version/compression and `.ctex` formats (`imported=parse` reads every file fully) |
| `godot_animation` (v2) | text + binary; `characters` (one link per skeleton, `top_level` flag) |
| `godot_shaders` (new) | shaders (files, includes, embedded, visual), uniforms with hints/defaults/groups, ShaderMaterials with parameters and users, keyword features |

Library: `rigs` gets `godot-character` rows (with `links` JSON: model, animations, trees, ragdoll bodies) and the
compare measures bones on them when present; `shaders` reads `godot_shaders` (column `uniforms`).

## Limits

- Feature lists of shaders are keyword inferences, not a render test; VisualShader code is not generated.
- Godot 2 files (format 1-2, `Image` variant) are not supported.
- Which scene is "the" player comes from the instance graph only; scenes instanced from code look top level.
