# Unity: catalogs, scenes, humanoids, audio categories

How gst reads the Unity data that has no third-party tool behind it. Everything is stdlib or UnityPy, read-only on
the install.

## Addressables catalogs (`engines/unity/export/addressables.py`)

`StreamingAssets/aa/` holds the content catalog the game loads (`settings.json` -> `m_CatalogLocations`, usually
`catalog.bin` on Addressables 1.21+/2.x, `catalog.json` before). Both formats decode to one summary
(`decode_catalog(path)`):

| key | what |
| --- | --- |
| `format`, `version` | `json` or `binary` (binary header version, 2 on Addressables 2.x) |
| `locator_id`, `build_version` | `AddressablesMainContentCatalog`, build result hash |
| `provider_ids`, `internal_ids`, `resource_types` | as in `catalog.json` (binary: distinct values in location order) |
| `bundles` | internal ids ending in `.bundle` |
| `keys` | `[{key, type, locations: [index]}]`: addresses, GUIDs, labels, bundle names |
| `locations` | `[{primary_key, internal_id, provider, type, dependencies: [index], bundle?}]`; `bundle` = `AssetBundleRequestOptions` (`hash`, `bundle_name`, `crc`, `bundle_size`) |
| `assets` | per non-bundle location: `address` -> `bundle` (first dependency bundle) -> `dependencies` (other bundles), `keys` (GUID, labels), `type`, `internal_id` |

`compare_summaries(a, b)` lists what two catalogs share (internal ids, providers, addresses, bundles, types).

- `unity.addressables` (extractor) copies every file of `aa/` and writes `Addressables/<src>/catalog.decoded.json`
  and `catalog.bin.decoded.json` (`active: true` on the one `settings.json` loads).
- `gst analyze catalogs` (`data.addressables`): every catalog with its counts and `active`, `assets` = the asset ->
  bundle map of the active catalog, per `aa/` bundle `in_catalog` and the `addresses` the catalog puts in it, and
  `compare` when the folder has a second catalog.

Binary layout (module docstring has the full list): offsets everywhere, uint32 byte size before arrays and
strings, string ids with flags (bit 31 UTF-16, bit 30 = linked list of parts joined with the reader's separator:
`/` for internal ids and primary keys, `.` for type names, the stored separator for keys), keys =
`{key object, location array}` pairs, location = 7 x uint32. Format reference: the Unity Addressables package source
(Unity Companion License), reimplemented, not copied (THIRD_PARTY_NOTICES.md).

Checked on R.E.P.O. (Addressables 2.10.3): `catalog.bin` 1481 keys, 1299 locations, 29 bundles (all 29 exist with
the exact `bundle_size`), 1270 assets, every primary key is a key. The `catalog.json` next to it is a leftover of an
older build (May vs November, 9 bundles of which 8 no longer match on disk, 1367 `LegacyResourcesProvider`
entries): its 28 bundled assets are all in the binary catalog with the same address, internal id, type, GUID key
and bundle. PEAK and Safari-TV do not use Addressables.

## Scene animators (`analysis/unity/prefabs.py`, default `scenes=animators`)

Clips and controllers used only by scene objects (camera glitch, loading UI, result screen, cutscenes) had no
binding paths, because the path maps come from the detailed prefab trees and scenes were skipped. Now every
scene file (`levelN`, `BuildPlayer-*` CABs) that has an Animator or a legacy Animation is read one at a time
(Transform/GameObject graph of that file only) and every animated GameObject becomes a `data.scene_animators` row
with its own detail JSON (`analysis/prefabs/scenes__<file>/<name>__<go>.json`, the subtree under it). Scenes
without an animated object are skipped before their Transforms are read. `animation` and `avatar` read prefab
roots and scene animators (`prefabs.detailed_rows`). `scenes=all` keeps the old `scenes=1` (every scene root in
`prefabs`), `scenes=0` skips scenes. The prefab rows themselves do not change.

| game | prefabs time | scene animators | unresolved bindings (animation) |
| --- | --- | --- | --- |
| R.E.P.O. (3 scenes, 0.5 MB each) | 128 -> 140 s | 10 | 555 -> 64 (the 64 are curves to children the prefabs no longer have) |
| Safari-TV (6 scenes up to 344 MB) | 27 -> 45 s | 387 | 12290 -> 12203 |
| PEAK (`level0-4`, up to 184k GameObjects; the 2 GB map bundles not measured) | 15 -> 72 s | 812 | 4249 -> 34 |

## Humanoid names (`analysis/unity/humanoid.py`)

Hand-written from the public scripting API: `BONE_NAMES` (the 55 `HumanTrait.BoneName`), `MUSCLE_NAMES` (the 95
`HumanTrait.MuscleName`) and `MUSCLE_BONE`. A humanoid clip curve's `attribute` indexes Mecanim's list: MotionT/Q
(0-6), RootT/Q (7-13), IK goals LeftFoot/RightFoot/LeftHand/RightHand T/Q (14-41), the 95 muscles (42-136), the
translation DoF of 21 bones (137-199). Checked on Safari-TV's 575 humanoid clips: MotionT.y always 0, RootT.y the
body height (0 to 1.07), foot goals' T.y negative, eye muscle pairs with matching ranges, TDoF curves tiny.

- `animation`: humanoid bindings get `attribute` (name), `group` (motion/root/goal/muscle/finger/tdof) and
  `index`; clips get `muscles_moved` and `humanoid_groups`; the report counts clips per muscle.
- `avatar`: per humanoid Avatar the 95 `muscles` (name, bone, skeleton node, curve index) and `hd_limits` (modified
  limits of `m_HumanDescription`). Two fixes found on the real Avatars: skeleton ids are CRC32 of the bone *name*
  (named from the `m_TOS` path whose last segment hashes to them: 0 unnamed nodes, before 87 of 90), and
  `m_HumanBoneIndex` indexes the human skeleton (`m_Human.m_Skeleton.m_ID` = path hashes, or
  `m_HumanSkeletonIndexArray`), not the avatar skeleton. Safari-TV `SK_MummyAvatar` 51/51 and
  `CHAR_ModularSausageAvatar` 54/54 bones agree with `m_HumanDescription` (before 0/51 and 1/53), which also
  confirms UpperChest at internal index 9.

## Audio categories (`audio_refs` + `audio_catalog`)

- `audio_refs` reads every `AudioMixerGroupController` (path `<mixer>/<group>/<child>`, `data.mixer_groups`) and
  gives each reference the group it plays through (`mixer_group`, `mixer_via`): an AudioSource's
  `m_OutputAudioMixerGroup`; a MonoBehaviour's AudioMixerGroup/AudioSource PPtr in the clip field's struct or an
  enclosing one (`Sound.Source` next to `Sound.Sounds[]`); else the AudioSources of its GameObject when they share
  one group. AudioSource typetrees are read only when a reference needs them.
- `audio_catalog` (Unity): `category` = the event's mixer group unless it is the game's default group (the one most
  references use), else the owner family; `kind` = music, ambient, voice, ui, footstep, impact, weapon or sound, from
  the deepest mixer group name that names one, else the field name, the class name, the clip names
  (`fields.kind_by`, `fields.mixer_group`). Godot keeps its `bus` in `fields` as before.

R.E.P.O. (4 mixers, 23 groups; `audio_refs` 120 -> 129 s): 4664 of 21162 clip references resolve a group; over
clip + holder references `Sound/Sound Master/Sound Effects` (the default) 6224, `Music/Music` 82,
`Spectate/Persistent Sounds` 23, `Music/Scare` 13, `Cutscene` 7. Kinds of the 2329 events (before: sound 2127, ui 79, music 64, ambient 46, voice 13): sound 1190,
impact 551 (1335 clips), voice 130, weapon 113, footstep 110 (274 clips), ui 98, music 91, ambient 46 (127 clips);
by mixer 33, component 944, clip name 162, default 1190. Against the hand-made study note
(`notes/assets/audio-catalogo.md`): ambience 44 events / 125 clips (gst 46 / 127), grab-impact 542 / 1322 (gst
impact 551 / 1335); gst's music also counts the instrument notes of musical valuables (the note files them under
valuables) and its footsteps include enemy footsteps.

## DCC names (`analysis/dcc.py`)

The Blender `.NNN` duplicate rule ignores decimal sizes: `0.NNN` after a separator or after `<n>x`
(`Floor Museum 0.125x0.125`, `Floor_1x0.125` in R.E.P.O.). `wall broken_1x1.001`, `stem 2.001` still count.
