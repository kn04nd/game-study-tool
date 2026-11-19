# Java — study guide

Covers the `gst recon` engine key `java` (LWJGL, libGDX, custom engines). Tested on Project Zomboid (PZ): a fat jar
plus Lua run by Kahlua, a Lua VM written in Java.

## Identify

- Jars (recon: `jars`, `main_jar` = the biggest), a bundled JRE (`jre*/release` → `java_version`, `implementor`), and
  often a launcher exe with a JSON file (main class, classpath, JVM options).
- Confidence 80 with a bundled JRE, 60 with at most 12 jars and no JRE.
- PZ: `ProjectZomboid64.exe` + `ProjectZomboid64.json` (mainClass `zombie/gameStates/MainScreenState`, classpath `.` +
  `projectzomboid.jar`, `-Xmx3072m`, ZGC), `projectzomboid.jar` (64.5 MB fat jar), `jre64/` (Azul Zulu 25.0.1).
  SteamDB rules: JVM, Lua, FMOD, Bink; no engine rule.
- Lua beside a Java game (plain `.lua`, or bytecode starting `\x1bLua`) makes `gst decompile` add the `lua` backend.
- User data: PZ `%USERPROFILE%\Zomboid\` (`Saves/`, `console.txt` = the log); other games use `%APPDATA%` or
  `Documents`.

## Code

- `gst decompile` → `java` (+ `lua`) → `decomp/java/<jar>/` (Vineflower) and `decomp/lua/`, and indexes both.
- Only the game's package roots by default: the Main-Class root plus every root that isn't a library namespace. PZ:
  `zombie/`, `generation/`, `astar/`, `fmod/`, `se/` → 3,422 files in 153 s. Skipped library roots are listed:
  `--only <prefix>/` (repeatable) adds one, `--all` takes the whole jar, `--force` redoes it.
- Second opinion: jadx from the tool table (`jadx -d <out> --no-res`; not in the gst catalog).
- Lua: plain files are copied as a snapshot of this build (PZ: 1,436); Lua 5.x bytecode goes through unluac with a
  header diagnosis; LuaJIT bytecode is only diagnosed.
- One index for both languages (PZ: 133,360 symbols, 687,073 references in 58 s). Lua classes come from
  `X = Base:derive("X")`, `class(Base)` and plain tables; `function X:m()` becomes the member `X.m`.

Where to start (PZ):

- Entry: the launcher JSON's main class → the game states in `zombie.gameStates` (`gst q sym gameStates --kind class`).
- World and actors: `IsoWorld`, `IsoCell`, `IsoGridSquare`; `IsoPlayer`
  (`decomp/java/projectzomboid/zombie/characters/IsoPlayer.java`), `IsoZombie`.
- Lua ↔ Java: `LuaManager` (package `zombie.Lua`) exposes Java classes and global functions to Lua (Kahlua's
  `@LuaMethod`). Events fire from Java (`LuaEventManager.triggerEvent("OnTick")`) into Lua handlers
  (`Events.OnTick.Add(...)`): `gst q strings OnTick` shows both sides.
- Lua UI: `gst q hier ISPanel` lists hundreds of derived classes (`ISInventoryPane` among them).
- The Lua VM itself (Kahlua) is decompiled with the game, under `se/krka/kahlua/`.
- Item, recipe and vehicle definitions: `media/scripts/*.txt`, a text DSL parsed by `ScriptManager`; not indexed, so
  search it with `rg`.

## Data and assets

- `gst assets list` and `gst assets extract` don't cover Java games (gap): read `media/` in place, read-only (`lua/`,
  `scripts/`, `maps/`, `texturepacks/`, `sound/`; translations in `media/lua/shared/Translate/`).
- Audio: FMOD banks → `gst assets audio --list`, then `gst assets audio "<glob>" --name "<regex>"`.
- Binary formats (texture `.pack` files, map `.lotheader`/`.lotpack`): `gst fmt scan media/texturepacks`, `gst fmt id`;
  the Java reader is the spec (`gst q strings ".pack"`, then `gst q show` on the reader).
- PZ saves (`Zomboid\Saves\<mode>\<world>\`): SQLite `.db` files (`gst fmt id` says so) and `.bin` chunks →
  `gst trace snapshot before` / `after` + `gst trace diff` around an action, then study-formats.

## Runtime

- HotSpot JIT code: `gst trace watch` hits read "outside any module" → find the field in `decomp/java` (`gst q refs`).
- The GC moves objects (PZ runs ZGC): an address from `gst trace scan` can go stale → scan again right before
  `gst trace watch` and keep the window short (`--seconds`).
- Java fields are primitives (`--type int32`, `float`, `double`). Kahlua stores Lua numbers as boxed, immutable
  `Double` objects: a Lua-side value can't be followed by scans → read the Lua instead.
- `gst trace snapshot` defaults to the user-data folder recon found (`%USERPROFILE%\Zomboid` for PZ); `gst trace diff`
  compares two snapshots.

## Graphics

- LWJGL usually means OpenGL; libGDX defaults to a GL 2.0-level context. RenderDoc captures only OpenGL 3.2+ core
  contexts (plus D3D11/12 and Vulkan), so older or compatibility contexts fail; PZ is untested.
- `gst gfx capture` runs the sandbox copy (`gst sandbox create` first). When it can't capture: study textures and shaders
  on disk, or apitrace from the tool table (not in gst; it traces legacy GL, from the sandbox).

## Gotchas

- `stdlib.lbc` NOT decompiled → Kahlua's big-endian Lua 5.1, whose `for` loops unluac misreads → read the source
  twin `stdlib.lua` that gst names, or the `.partial.lua`.
- A package is missing from `decomp/java/` → library roots are skipped → `--only <prefix>/` or `--all`.
- A global Lua function with no Lua definition → it lives in Java → `gst q grep 'name = "<fn>"' --glob "*.java"`
  (the `@LuaMethod` name).
- `gst q hier` misses a Lua class → declared in a pattern the indexer doesn't map → `gst q grep "<Name>\s*=" --glob "*.lua"`.
- Classes named `a`, `b`, `c` → ProGuard or Allatori → anchor on strings, Lua bindings and file names; record names
  in `notes/renames.json`.
- A method reads wrong in Vineflower → decompilers disagree on odd bytecode → compare with jadx.
- `media/scripts` blocks missing from `gst q` → the DSL isn't indexed → `rg`.

## Protections

- Usually none: PZ is `offline-ok`. Multiplayer servers belong to others: runtime work only in solo play or on a
  server the user hosts on their own LAN.
- Obfuscation (ProGuard, Allatori) is informational: the verdict doesn't change. recon records anything it finds in
  `recon/protections.md`.
- Limits: `plugins/gst/guardrails.md`.
