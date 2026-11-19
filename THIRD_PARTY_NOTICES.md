# Third-party notices

This toolkit adapts or vendors material from the projects below. Each keeps its own license.
Third-party *tools* (Ghidra, ILSpy, etc.) are downloaded by `gs tools install` from their
official sources and are not redistributed here.

## universal-modder (MIT)

Copyright (c) 2026 Rehan and universal-modder contributors.
Adapted: engine/anti-cheat fingerprinting and binary sniffers (`gst/recon/fingerprint.py`, from
`um/scan.py`), CLI dispatch pattern (`gst/cli.py`), shared helpers (`gst/common.py`), local notes
index (`gst/notes.py`, from `um/kb.py`), snapshot/diff (`gst/runtime/snapshot.py`, from
`um/backup.py`), derived-output patterns (`gst/guard.py`, from `um/publish.py`), engine reference
notes (`plugins/gst/references/engines/*.md`) and note templates.

```text
Permission is hereby granted, free of charge, to any person obtaining a copy of this software
and associated documentation files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions: The above copyright
notice and this permission notice shall be included in all copies or substantial portions of
the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
```

## SteamDB FileDetectionRuleSets (MIT)

Vendored as `src/gst/data/rules/steamdb_rules.ini` (pinned commit, see file header).
Copyright (c) SteamDB. <https://github.com/SteamDatabase/FileDetectionRuleSets>

## AreWeAntiCheatYet (MIT)

Vendored as `src/gst/data/rules/areweanticheatyet_games.json` (pinned commit, see
`src/gst/data/rules/SOURCES.md`). Copyright (c) 2021 Starz0r, Curve.
<https://github.com/AreWeAntiCheatYet/AreWeAntiCheatYet>

## REA - reverse-engineer anything (MIT)

Not vendored: `gst tools install rea` installs the npm package `rea-agents` (pinned 6.9.0) into gst's tools dir and
gst runs its JSON CLI (`src/gst/rea/`). Copyright (c) 2026 morluto. <https://github.com/morluto/rea>
`patches/rea/` holds an optional patch, applied by the user to a local REA checkout (`docs/tools/rea.md`), that only
makes the IL2CPP metadata input/report allowances configurable. The patch is distributed under REA's MIT license;
gst does not ship a modified REA.

## RenderDoc in-application API header (MIT)

Vendored as `samples/sample-game/native/third_party/renderdoc_app.h`, unchanged, with its license
in the file header. Copyright (c) 2015-2026 Baldur Karlsson. <https://github.com/baldurk/renderdoc>

## Unity Addressables package (Unity Companion License) - format reference only

Not vendored, no code copied. `src/gst/engines/unity/export/addressables.py` reads the binary content catalog
(`catalog.bin`, Addressables 1.21+/2.x) and the packed strings of `catalog.json` with an independent
reimplementation; the layout follows the package source (`ContentCatalogData`, `BinaryStorageBuffer`,
`SerializationUtilities`), used as a format reference only, and was checked against a real catalog. Unity Addressables
(`com.unity.addressables`) is Copyright (c) Unity Technologies, licensed under the Unity Companion License.
The humanoid name tables of `src/gst/analysis/unity/humanoid.py` are written by hand from Unity's public
scripting API documentation (`HumanTrait`, `HumanBodyBones`).

## Godot Engine (MIT)

Ported logic, no code vendored: the binary resource layout and variant encoding of `core/io/resource_format_binary.cpp`,
the `RSCC` block wrapper of `core/io/file_access_compressed.cpp`, the PackedScene `_bundled` layout of
`scene/resources/packed_scene.cpp`, `ResourceUID::id_to_text` and the `CompressedTexture2D` header, read in Python by
`src/gst/engines/godot/rsrc.py`; the FastLZ decoder in `src/gst/engines/godot/codecs.py` follows the format of Godot's
`thirdparty/misc/fastlz.c` (FastLZ, MIT, Copyright (C) 2005-2020 Ariya Hidayat). The Zstandard decoder there is written
from RFC 8878. Copyright (c) 2014-present Godot Engine contributors; (c) 2007-2014 Juan Linietsky, Ariel Manzur.
<https://github.com/godotengine/godot> (license text as for universal-modder above: MIT).
