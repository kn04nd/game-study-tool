# Java and Lua in gst (Vineflower, unluac)

## What it does

Turns a game's Java bytecode and Lua scripts into readable source under the study workspace, then indexes it so
`gst q def/refs/callers/callees/outline` work across both languages (e.g. Project Zomboid: the `zombie.*` Java
classes and the `media/lua` UI/game scripts that call them).

| tool | catalog id | role |
| --- | --- | --- |
| [Vineflower](https://github.com/Vineflower/vineflower) (fork of Fernflower) | `vineflower` (+ the `temurin21` JDK) | `.class` -> `.java` |
| [unluac](https://sourceforge.net/projects/unluac/) | `unluac` | Lua 5.0-5.4 bytecode -> `.lua` (Kahlua / LuaJ chunks are 5.1) |

`gst tools install vineflower unluac` installs both (and the JDK). LuaJIT bytecode is diagnosed only (header, version):
no decompiler is pinned.

## Two entry points, one code path

| command | what runs |
| --- | --- |
| `gst study <game>` (jobs `code.java`, `code.lua`, then `code.index`) | one job per unit in a worker process; resumable through `jobs/jobs.db` |
| `gst decompile java [names] [--only P] [--all] [--force]`, `gst decompile lua [--force]` | the same extractors in this process (`gst.extractors.javalua.run_inprocess`), then `gst index` |

Both write the same outputs and per-unit manifest fragments, and both skip a unit whose fingerprint did not change
(`gst study` through `jobs.db`, `gst decompile` through the fragment). `--force` / `gst study --force code.java` redo it.

`code.java` and `code.lua` are registered for every engine: they apply whenever the install holds jars/classes or Lua
(Lua ships next to many engines). Recon lists them under "Script code" in `RECON.md` (`recon.json` `script_code`)
and adds `gst decompile java|lua` to the next steps when the engine's own backend is something else.

## Units

**Java** (`src/gst/decompile/java.py::units`):

- one unit per `.jar` outside bundled JREs (`jre*`, `jdk*`, `runtime`, `java`, `steamapps`), at most 3 folders deep;
  output `decomp/java/<jar stem>/`, log `decomp/java/<jar stem>.log`;
- one unit per loose class tree: each folder's classpath root comes from the package of its first class file (read
  from the constant pool, so `bin/classes/com/x/Y.class` has root `bin/classes`); unit = root + top-level package,
  output `decomp/java/classes/[<root>/]<package>/` (package path kept). The classes are zipped (stored) into the
  staging folder first, so Vineflower never copies unrelated files of the install.
- What is decompiled by default: in a jar, the game's package roots (Main-Class root + top-level packages that are not
  well-known library namespaces, `LIBRARY_ROOTS`); library-only jars and class trees are `skipped` with the reason.

**Lua** (`src/gst/decompile/lua.py::units`): one unit per script folder, the file's folder cut at 3 levels
(`media/lua/client`, `media/lua/shared`...). A unit at that depth owns its subtree and is replaced as a whole; a
shallower folder (`.`, `media/luaexamples`) owns only its own files (stale ones are removed on rerun). Plain scripts are
copied (mtime kept, so the index does not reparse unchanged files); compiled chunks go through unluac when the policy
allows decompiling (`<name>.lua`, or `<name>.decompiled.lua` when a plain twin exists; a failed run keeps
`<name>.partial.lua`). A unit with only bytecode that cannot be decompiled is `unsupported` (full unluac stderr in the
job error) or `blocked-policy`.

## Options

| `gst study --opt` | `gst decompile java` | effect |
| --- | --- | --- |
| `java.filter=zombie/core/,zombie/ai/` | `--only zombie/core/` (repeatable) | Vineflower `--only` prefixes for every unit; units with no class under them are skipped |
| `java.all=1` | `--all` | every package of every jar and class tree, libraries included |
| `java.units=<jar or package>,...` | positional names | decompile these even when they look like libraries |
| `java.threads=N` | - | Vineflower threads (default: every CPU) |

The options are part of each Java unit's fingerprint: changing them reruns the affected units (and the index).

## Outputs and manifests

- `decomp/java/_manifest/<out>.json`, `decomp/lua/_manifest/<unit>.json`: one fragment per unit (source, prefixes,
  classes matched, files, exit code, seconds, exact command, fingerprint, tool version).
- `decomp/java/MANIFEST.json` (`jars: {<out>: fragment}`) and `decomp/lua/MANIFEST.json` (`units`, `plain_files`,
  `bytecode`): merged by each extractor's `finalize` job.

## Cost (Project Zomboid build 26486631, `gst study --only code.java,code.lua,code.index`, 12 threads)

| unit | input | output | time |
| --- | --- | --- | --- |
| `code.lua`, 19 folders (1,430 scripts, `stdlib.lbc` diagnosed: Kahlua chunk, unluac stops, partial kept) | 20 MB | 24 MB | ~10 s in total |
| `projectzomboid.jar` with `java.filter=zombie/characters/` (325 of 23,692 classes) | 66 MB | 203 files | 28 s |
| `projectzomboid.jar`, default game roots (5,343 classes) | 66 MB | 3,298 files, 34 MB | 154 s |
| `ZombieBuddy.jar` (a mod loader in the install root; roots `zb/`, `nonapi/`) | 13 MB | 2,598 files | 45 s |
| `code.index` over Java + Lua | - | 7,327 files, 170,809 symbols, 158 MB `code.db` | 43 s |

A rerun with nothing changed plans 0 jobs; `gst decompile java|lua` then reuses every unit too. Vineflower runs with
`--skip-extra-files` (the jar's resources are not code: without it the same jar copied 55 MB of library files).
