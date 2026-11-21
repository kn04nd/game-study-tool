---
name: study-report
description: Consolidate a game study into ARCHITECTURE.md - identity and technical stack (filled from recon), code architecture, one subsection per system from the notes, data and assets, rendering, runtime observations, Mermaid diagrams, glossary, evidence and what was NOT verified, sources - and prove it holds with gst report --check (sections written, systems with evidence, citations that resolve).
when_to_use: The user wants the study written up. Examples - "write the architecture document", "consolidate the study", "generate the report", "summarize what we learned about the game", "escreve o documento de arquitetura", "consolida o estudo", "gera o relatório do jogo", "resume o que aprendemos sobre o jogo".
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Write ARCHITECTURE.md

Where you are: the session context names the study workspace, if any (`gst status` shows it).

Goal: `ARCHITECTURE.md` that a reader can trust: every statement traceable to evidence, the gaps named.
Write it in pt-BR, identifiers as they are.

## 1. Gather

```
gst report                       # fills sections 1-2 from recon; shows what is empty and what fails
gst notes index                  # notes/INDEX.md (and the study list in GS_HOME/INDEX.md)
gst map                          # notes/MAP.md if it is older than the code index
```

Read `notes/INDEX.md`, `notes/MAP.md`, `FACTS.md` and the last sessions of `STUDY.md`; open notes only when
writing their part.

## 2. Write, section by section

- Section 3 ("Arquitetura do código"): from `notes/code/arquitetura-codigo.md` (template `arquitetura-codigo`,
  pre-filled from `analysis/code_architecture.json`): modules and libraries, entry points and managers, a table of
  the design patterns and performance techniques with `path:line`.
- Section 4 ("Sistemas"): one `### <System>` per system: purpose, key types with `path:line`, the data it
  reads, formulas, state machines, a link to its note. At least five systems for a full study, and always
  `### Áudio` (events -> files -> who triggers them, the sound engine, music; from the study-audio notes)
  and `### Mapas e níveis` (format, contents, load flow; from the study-maps notes). A game without one
  of them keeps the heading with a line saying why it does not apply.
- Cite evidence everywhere: `decomp/...:line`, `assets/...`, `notes/...:line`, `module+0xRVA`, `traces/...`.
- Prefer tables for lists of types, formats and constants; Mermaid for architecture (section 8: a
  `graph`/`flowchart` of systems, `stateDiagram-v2` for state machines, `sequenceDiagram` for a frame or a
  save).
- Section 10 is a bullet list of what was NOT verified (inferences, untested formulas, unread code).
- Section 11 lists the sources: tools and versions (from the MANIFEST files), public documentation used.

## 3. Check

```
gst notes check                  # citations in the notes that point nowhere
gst report --check               # every section, >= 5 systems with evidence (Áudio and Mapas included), citations, diagram, section 10
```

Fix what it reports and run it again. For an adversarial pass over the claims themselves, the workflow
`/game-study:verify-claims` re-reads the code for each claim and moves the doubtful ones to section 10.

## Done when

`gst report --check` passes.
