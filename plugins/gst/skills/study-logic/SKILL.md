---
name: study-logic
description: Reconstruct how a game system works - from the system map to the code and the data tables - and write it as a specification - numbered rules in plain language, formulas with their constants and units, state machines, data tables - each backed by path:line evidence, then confirm the key ones against the running game or a save when possible, listing what was not verified.
when_to_use: The user wants to know how a mechanic or system works. Examples - "how does the AI pick tasks", "how is damage calculated", "how does oxygen spread", "explain the economy", "map the game's systems", "como a IA escolhe tarefas", "como o dano é calculado", "como funciona o oxigênio", "explica a economia do jogo", "quais são os sistemas do jogo".
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Reconstruct a system

Where you are: the session context names the study workspace, if any (`gst status` shows it).

Goal: a specification of the system that someone could reimplement from, with evidence for every rule.
Reply in pt-BR; notes in pt-BR, identifiers as they are.

## 1. Locate

```
gst map                         # notes/MAP.md: systems, their most used types, who depends on whom
gst q outline <Type>            # members of a key type
gst q hier <Type>               # its bases and the types deriving from it
```

The map is keyword-based: confirm membership by reading. Refine it for this game in
`notes/map-keywords.json` (`{"Label": "regex on type names"}`), then `gst map` again.

## 2. Follow the flow

- Entry points: the per-frame or per-tick updates (`Update`, `Sim200ms`, `Tick`, `Step`), event handlers
  and callbacks. `gst q callers <Type.Method>` climbs to the trigger; `gst q callees` goes down to the data.
- Constants and tunables: fields initialized in code, config classes, data tables on disk (YAML, JSON,
  ScriptableObjects: the study-assets skill), native code (`gst decompile native`).
- Cross-language calls: managed code calling a native DLL shows up as `extern`/DllImport methods; the
  native side is in `decomp/ghidra/<module>/`.

## 3. Write the specification

`gst notes new logic/<system>` creates `notes/logic/<system>.md`. Fill it with:

- the rules, numbered, in plain language;
- each formula with its constants, units and the `path:line` it was read from;
- the state machine as a Mermaid `stateDiagram-v2`, with the conditions on the transitions;
- the data tables with values copied from the files, and the edge cases (clamps, overflows, special states).

## 4. Confirm (the oracle)

Predict a number with the formula, then check it: for `offline-ok` games live (the study-runtime skill:
`gst trace scan`, `gst trace watch`), or by diffing two saves around the action (`gst trace snapshot`/`diff`).
Record the result; whatever was not confirmed goes to "Não verificado".

## 5. Several systems at once

One `code-navigator` agent per system, in parallel, each writing its own note; the workflow
`/gst:map-subsystems` runs the map, the agents and the consolidation in one go; `/gst:study-all` does it for
systems, mechanics and asset families together after `gst study`. For a note pre-filled from the analyses use
`gst notes new logic/<system> --template sistema --system <regex>` (or `mecanica` for a mechanic's numbers).

## Done when

`notes/logic/<system>.md` has evidence for every rule and formula, at least one prediction checked (or a
clear reason why not), and a "Não verificado" list. `gst notes check` reports no broken citation.
