---
name: study-runtime
description: Observe a running game the user owns, offline - find where a value lives in memory with read-only scans, catch which function writes it with a hardware watchpoint (named through the decompiled code), and see what an action changes on disk with folder snapshots; tools that inject (Frida, mod loaders, overlays) only in a sandbox copy of the install. Only for games whose verdict is offline-ok; protected or online games are refused by policy.
when_to_use: The user wants to see the game working live. Examples - "find the health value in memory", "who changes the gold", "what does the game write when I save", "trace the game while it runs", "watch this address", "acha a vida na memória", "quem altera o ouro", "o que o jogo grava quando salva", "observa o jogo rodando", "quem escreve nesse endereço".
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Observe the running game

Where you are: the session context names the study workspace, if any (`gst status` shows it).

Goal: a fact confirmed live and tied to a symbol of the decompiled code, recorded in FACTS.md.
Reply in pt-BR; notes in pt-BR.

## 0. Before anything

- `gst status`: the verdict must be `offline-ok`. Otherwise stop: runtime tools are refused (guardrails
  rules 2-3); study the code and the data on disk instead.
- The game runs offline or solo, from its install (or from the sandbox). You cannot press keys in the
  game: ask the user to do the in-game action (take damage, spend gold, save) at the right moment.
- Observation only: nothing below writes to the game's memory. A memory write happens only as an
  experiment the user asks for explicitly, logged and undone (rule 6).

## 1. Find the value

```
gst trace ps                   # the game's processes (--pid when there are several)
gst trace scan 100             # the value the game shows now (int32; --type float for 37.5)
gst trace next 87              # after the value changed in the game; or --decreased/--increased/--changed
```

Repeat `gst trace next` until one or a few candidates are left. Few hits at once: change the value again,
or use a rarer value.

## 2. Who writes it

```
gst trace watch scan --hits 10      # the last candidate (scan:N picks one of several; or an address 0x...)
```

The output names each writer: the function (from the Ghidra export), `module+0xRVA`, the decompiled
`file:line` and the values written. Then read it: `gst q show <function>` and explain the logic (formula,
limits, timers). No function name: `gst decompile native <module>` first (the hint names the module).
Managed games (Unity Mono, .NET) write from JIT code outside any module: find the field in the decompiled
C# instead (`gst q refs <Field>`).

## 3. What an action changes on disk

```
gst trace snapshot before      # the save and config folders recon found (--path <folder> to choose)
gst trace snapshot after       # after the action in the game
gst trace diff before after    # files added, removed, changed, with the byte ranges
```

The changed bytes are the starting point for the study-formats skill.

## 4. Tools that inject

Frida, BepInEx/UnityExplorer, UE4SS, ReShade and other loaders run only in a copy of the install:

```
gst sandbox create             # sandbox/ in the workspace; start the game from there, offline
```

The original install is never modified (rule 5). `gst sandbox remove` deletes the copy.

## 5. Record

- FACTS.md: one row per fact: what, `module+0xRVA @ build <buildid>` (or the field offset in its class),
  the function, and how it was verified (`traces/watch-*.json`).
- `notes/runtime/<topic>.md`: the procedure and what it showed, with links to the code.

## Done when

The fact is confirmed live (a trace in `traces/`), tied to a symbol of the decompiled code, and recorded in
FACTS.md with the build id.

## Common problems

| Symptom | Fix |
|---|---|
| `runtime tools are not allowed` | verdict no-runtime or static-only: study code and data on disk |
| `is not running` / several processes | start the game; pass `--pid` |
| too many candidates | change the value again; a rarer value; `--type float` for fractions |
| watch ends with no writes | do the action in the game during the watch; raise `--seconds` |
| only `module+0xRVA`, no function | `gst decompile native <module>` (or re-run it: the export is from another build) |
| `outside any module` | JIT (managed) code: find the field in the decompiled code |
| 32-bit game | `gst trace watch` supports 64-bit processes only |
