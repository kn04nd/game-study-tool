---
name: study-decompile
description: Decompile a game's code into readable source in the study workspace and navigate it through the gst code index - find where a system lives, how it is called, what it reads and writes - citing path:line evidence. Managed code (.NET/Mono Unity, Godot .NET, XNA/FNA and other .NET games, via ilspycmd), Godot GDScript (GDRE Tools), Java jars (Vineflower), Lua scripts or bytecode (unluac), native exe/dll (Ghidra headless with the shipped PDB: decompiled C per class with field layouts) and Unreal Blueprints as JSON with their bytecode; the index answers definitions, references, callers/callees, type hierarchies, string literals and regex searches.
when_to_use: The user asks to decompile or read a game's code, where a feature is implemented, or how some logic works in code. Examples - "decompile the game", "where is the save code", "find the function that computes X", "who calls SaveLoader.Save", "how does the AI pick tasks (in code)", "descompila o jogo", "onde fica o código de salvar", "acha a função que calcula X", "quem chama esse método", "como isso funciona no código".
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Study the code

Where you are: the session context names the study workspace, if any (`gst status` shows it).

Goal: answer a code question with evidence. Decompiled output is a reference to read, never to edit.
Reply in pt-BR; notes in pt-BR, identifiers as they are.

## 1. Decompile (once per game build)

```
gst decompile --list        # managed assemblies found; "game" marks the game's own
gst decompile               # by engine: .NET assemblies -> decomp/cs/<Assembly>/ (one .cs per type);
                           # Godot: GDScript -> decomp/godot/ (+ the C# of Godot .NET games); then indexes
gst decompile dotnet <Name> # add a specific assembly (a framework or library one, when the question needs it)
gst decompile godot         # only the GDScript of a Godot pack (GDRE Tools)
gst decompile java          # jars -> decomp/java/<jar>/ (Vineflower): the game's own packages by default;
                           # --only <prefix>/ for another package, --all for the whole fat jar
gst decompile lua           # plain .lua copied to decomp/lua/ (snapshot of this build); Lua 5.x bytecode
                           # through unluac, with a diagnosis (and the source twin, if any) when it fails
```

- Uses the game's PDB for variable names when one ships next to the assembly.
- The game's own assemblies come from `<Game>.deps.json` when present (projects, not NuGet packages).
- Re-running is cheap: unchanged assemblies (same sha256) are reused; after a game update it redoes what changed.
- Policy: `static-only` games decompile only unprotected modules; `no-runtime` games don't decompile client
  code at all. A refusal says why: offer the data on disk instead.
- Native modules (C/C++ exe and dll) go through Ghidra headless:

  ```
  gst decompile native --list              # native modules: +pdb, third-party, large
  gst decompile native                     # the game's own (those shipping a PDB; the exe of native games)
  gst decompile native Game.dll --max-functions 5000   # big binaries: named functions first, rest listed
  gst decompile native --functions "^Sim\."            # decompile more of an analyzed module (no re-analysis)
  ```

  Output: `decomp/ghidra/<module>/src/<namespace>.c`, one file per class or namespace. A class file
  opens with its layout from the PDB (field offsets, base classes): the offsets runtime tools need.
  Each function starts with `// <module>+0x<RVA> Name`; cite it in FACTS.md as `module+0xRVA @ build <id>`.
  Calls are exact (from the decompiler); imports read `kernel32.CreateFileW`, so
  `gst q callers CreateFileW` lists who opens files. Without a PDB names are `FUN_<address>`: record
  the meaning you find in `notes/renames.json`. Minutes for small DLLs, hours and GBs of RAM for
  100 MB executables (Unreal shipping exes run only when named).
- Interactive questions over the analyzed project (decompile one function, xrefs, strings): `gst mcp add ghidra`
  configures pyghidra-mcp (stdio, `--no-symbols`) and pre-approves its read-only tools; restart Claude Code
  in the workspace to load it (`gst mcp call ghidra <tool> '<json>'` tests it now). Not while
  `gst decompile native` runs: both lock the same Ghidra project.
- Unreal: gameplay lives in C++ (the `*-Win64-Shipping.exe`, native) and in Blueprints inside the paks.
  `gst assets extract --name "<path regex of a BP_ asset>" --bytecode` exports a Blueprint to JSON with
  its Kismet bytecode per function (`EX_*` statements); `SuperStruct` names the C++ class it extends,
  `SCS_Node` entries are its components. Find Blueprints with `gst assets list --name "/BP_"`.

## 2. Navigate with the index (not by opening huge files)

```
gst q def SaveLoader                 # where a type/member is defined (also Type.Member or Ns.Type.Member)
gst q outline SaveLoader             # its members with signatures and lines
gst q show SaveLoader.Save           # the source of one member (capped; --lines N, --context N)
gst q show decomp/cs/X/Foo.cs:120    # the symbol around a line
gst q callers SaveLoader.Save        # who calls it (a type: who constructs it)
gst q callees Game.Update            # what it calls, resolved through field types and singletons
gst q refs ElementLoader             # every use of a name
gst q hier KMonoBehaviour            # bases and derived types
gst q strings "save file"            # string literals (UI text, file names, keys) and where they live
gst q grep "Serialize\(" --glob "*.cs"   # regex over decomp/ with the enclosing symbol per hit
gst q sym Chore --kind class         # fuzzy search over qualified names
```

`callees` marks targets matched by name only as `[by name only]`: confirm those by reading the call site.

## 3. Reading strategy

1. Start from what the question names: a string the user sees (`gst q strings`), a file the game writes,
   a type name. In Unity, managers and singletons (`Game`, `*Manager`, `*Loader`, `Instance`) and the
   `Awake/Start/Update` methods of MonoBehaviours are the usual entry points.
2. `outline` before `show`; follow `callers` up to the trigger and `callees` down to the data.
3. Data often lives outside code: configs, YAML/JSON tables, ScriptableObjects. Use the study-assets
   skill for those and connect them back with `gst q strings <file or key name>`.
4. Write down as you go; three dead ends on the same path means changing angle (and noting it).

## 4. Code architecture (study material)

`gst analyze code_architecture` (also a `gst study` job) turns the index into `analysis/code_architecture.json`/`.md`:
modules and namespaces, the third-party libraries the game's code uses and where, entry points and managers,
lifecycle and the longest per-frame methods, and a catalog of design patterns (events, singletons, service locator,
state machines, ScriptableObject data, coroutines/async, Jobs/Burst, RPCs) and performance techniques (pooling,
NonAlloc, cached components, precomputed ids, spread-out updates, LOD/culling) plus costs inside per-frame methods,
each with counts and `path:line` examples (C# sources; option `modules` picks the game's modules). Write it up with
`gst notes new code/arquitetura-codigo --template arquitetura-codigo` and confirm each pattern from its examples:
the counts are regex signals.

## 5. Record

- `notes/code/<topic>.md`: the explanation in your own words, with `decomp/...:line` citations, tables,
  formulas and state machines instead of long code dumps (short excerpts are fine).
- `notes/renames.json`: names you give to obfuscated or generic symbols: `{"<qname>": "<meaning>"}`.
- `FACTS.md`: one row per verified fact (what, where `path:line`, how verified).
- A claim you could not verify stays marked as such.

## Done when

The question has an answer in `notes/code/` backed by `path:line` evidence, the key symbols are listed
with their roles, and FACTS.md holds what was verified.

## Common problems

| Symptom | Fix |
|---|---|
| `no .NET assemblies found` | not a managed game: `gst decompile native --list` |
| `no decompile backend for unreal` | Blueprints through `gst assets extract --bytecode`; C++: `gst decompile native <Game>-Win64-Shipping.exe` |
| `no native game module recognized` | name the module: `gst decompile native <name.dll>` (see `--list`) |
| native run failed or slow | `logs/ghidra-<module>.log`; analyze one module at a time, lower `--max-functions` |
| `ilspycmd is not installed` | `gst tools install ilspycmd` |
| errors in `decomp/cs/<name>.log` | the affected method carries the error text; read the IL with `ilspycmd -il -t <Type>` |
| `no code index yet` | `gst index` (or re-run `gst decompile`) |
| a definition you expect is missing | `gst q sym <part>`; it may be in an assembly not decompiled yet (`gst decompile --list`) |
| path too long errors | `install.ps1 -EnableLongPaths` enables LongPathsEnabled (admin, asks via UAC) |
