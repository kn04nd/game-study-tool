# Unreal Engine 1-3 — study guide

Covers the `gst recon` engine key `unreal3`.

## Identify and tools

- Packages `.upk`, `.u`, `.umap`, `.xxx` under `<Game>/CookedPC*` (UE3), or `System/` and `Maps/` (UE1-2); texture
  caches `.tfc`. The package header carries FileVersion/LicenseeVersion; cooked packages may be compressed (LZO,
  zlib, LZX or Oodle).
- UnrealScript bytecode lives in the script packages: UELib / UE Explorer decompile it, umodel exports meshes and
  textures (none is in the gst catalog: get them with the user's OK). Native code in the exe: `gst decompile native`.
- Unknown parts of a package: `gst fmt id`, `gst fmt hex`, `gst fmt strings`; prove a reader with `gst fmt new-reader`
  and `gst fmt roundtrip`.
- Many UE3 executables are 32-bit: `gst trace watch` refuses WoW64 processes.

The rest is a field note from the parent repository's knowledge base (2026-10-03), kept as written minus two passages
about changing the game. It was written for Borderlands 2 modding, hence the hooks; the same offline reads explain how
the game works.

## Reading UE3 Gearbox games offline: LZO packages, function signatures and behavior graphs

> When you can't poke at a running game, these offline sources let an agent get exact UnrealScript
> signatures and decode how Gearbox's data-driven behaviours work before writing a hook. They're built for
> Borderlands 2 and should carry over to TPS/AoDK and other cooked UE3 games.

## When to use it
- **You need a function's exact parameters** before hooking or calling it from pyunrealsdk (or any UE3
  hook). Object dumps list properties, not functions.
- **You want to know what a projectile, skill, shield or interactive object actually does.** Gearbox drives
  these with `BehaviorProviderDefinition` graphs (events → behaviours → outputs), not code.
- **You can't run code in the game freely,** e.g. no live REPL, or every test needs a human playtest.

## How
**1. Object data: the OpenBLCMM-Data pack.**
- The [OpenBLCMM-Data](https://github.com/BLCM/OpenBLCMM-Data/releases) `.jar` is a zip containing:
  - `data/BL2/data.db` (SQLite: `class`, `object` with class and parent, `attr_name`);
  - `dumps/<Class>.dump.N` (text `obj dump` of every object of that class).
- Find objects by class in SQLite. Then print one object's non-default properties by scanning its dump
  file for `*** Property dump for object '<Class> <path>' ***`.
- It answers questions like "which part sets a gun's element?" or "what fuse delay does this projectile
  use?" without the game.

**2. Function signatures: the game's own packages.**
- **Decompress.** Cooked BL2 `.upk` files under `WillowGame/CookedPCConsole` are fully compressed:
  repeated chunks of `[tag 0x9E2A83C1][block size 0x20000][compressed size][uncompressed size]`, then a
  table of `(compressed, uncompressed)` block sizes, then LZO1X blocks.
  - `python-lzo` wouldn't build on Python 3.12/3.14 on Windows. A ~100-line pure-Python LZO1X
    decompressor works (the standard lzo1x_d state machine), with output sizes checked against the
    headers.
  - Decompress *copies* outside the game folder.
- **Read the header:** tag, version, header size, folder name (FString), package flags, then counts and
  offsets for names, exports and imports.
  - **Names:** FString plus 8 bytes of flags each.
  - **Imports:** 28 bytes each.
  - **Exports (BL2):** class, super, outer, name (+ number), archetype ints; 64-bit object flags; serial
    size and offset; export flags; a net-object count plus that many ints; a 16-byte GUID; package flags.
- **List a class's functions:** exports whose outer is the class and whose class is `Function` (or `State`
  for states).
- **List parameters:** `*Property` exports whose outer is the function.
  - Each property's serial data starts with: NetIndex, a terminating `None` FName (8 bytes), `UField.Next`,
    `ArrayDim`, then 64-bit `PropertyFlags`.
  - Flags: `CPF_Parm` 0x80, `CPF_OptionalParm` 0x10, `CPF_OutParm` 0x100, `CPF_ReturnParm` 0x400.
  - **Export order is the reverse of declaration order:** reverse the parameter list.
- **Output:** lines like
  `void InitializeFromDefinitionData(Struct NewDefinitionData, Object InAdditionalQueryInterfaceSource, optional Bool bForceSelectNameParts)`.
  This matched the live signature exactly. Enum value names (e.g. `ZST_Zoomed`) can be grepped from the
  decompressed bytes.
- **Exe strings** (UTF-16 and ASCII, e.g. `AWillowWeaponexecOnAbortReload`) only show natives and some
  names. Use the packages for anything scripted.

**3. Behaviour graphs: decode a BehaviorProviderDefinition.**
- In the dump, each sequence is one `BehaviorSequences(i)=(...)` line holding:
  - `EventData2` (events with `OutputLinks`);
  - `BehaviorData2` (behaviours, each with `LinkedVariables` and `OutputLinks`);
  - `ConsolidatedOutputLinkData`, `ConsolidatedVariableLinkData`, `ConsolidatedLinkedVariables` and
    `VariableData`.
- **Packing:**
  - `ArrayIndexAndLength = (index << 16) | length`, pointing into the consolidated array.
  - `LinkIdAndLinkedBehavior = (outputLinkId << 24) | behaviourIndex` (into `BehaviorData2`).
  - `ActivateDelay` is a per-link delay in seconds.
- **Variables:** a behaviour's `LinkedVariables` index into `ConsolidatedVariableLinkData`
  (`PropertyName`, input/output, its own `ArrayIndexAndLength` into `ConsolidatedLinkedVariables`). Those
  index into `VariableData` entries (`Name`, `Type`, e.g. `BVAR_NamedVariable`).
- **Printing the graph** as "EVENT OnSpawn → [i] Behavior_X --linkId--> [j] Behavior_Y" plus
  "property ← variable" shows the real logic. Example: the Deliverance's thrown gun only fires when
  `TargetDot > 0.91`, ammo is left, and a damage-type check passes.
- **`Behavior_CompareFloat` output link ids:** 0 = A < B, 1 = A == B, 2 = A > B. Inferred from the Roid
  shield graph, which wires 0 and 1 to "triggered" for "shield ≤ 0".

## Gotchas
1. **Parameters print in reverse.** **Cause:** exports are stored in reverse declaration order. **Fix:**
   reverse them. Check the result against a call you already know works.
2. **`python-lzo` fails to build** (no wheels for recent Pythons on Windows). **Fix:** use a pure-Python
   LZO1X decompressor. It's slow (seconds per MB) but fine for a few packages.
3. **Output-link ids aren't named in the dumps.** **Fix:** infer from a graph whose meaning is obvious
   (shields, vending machines) before relying on a guess. A wrong guess can invert your logic.
4. **The data pack is from 2023-04-20.** Object names were stable for BL2 build 8639 here, but re-check
   anything critical in game (log a lookup on first use).
5. **Keep everything local:** decompressed packages, dumps and the `.jar` are game data.

Adapted from universal-modder `knowledge/techniques/reading-ue3-gearbox-games-offline.md` (MIT, Copyright (c) 2026
Rehan and universal-modder contributors).
