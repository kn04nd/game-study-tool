# Study: {name}

This folder is a **Game Study Toolkit workspace** for {name} (slug `{slug}`). Goal: understand how
the game is made, through reverse engineering, and write it down in `ARCHITECTURE.md`.

- Install (read-only): `{install_dir}`
- Engine: {engine_label}{engine_version}. Verdict: `{verdict}` ({verdict_text})
- Write notes, STUDY.md, FACTS.md and ARCHITECTURE.md in **Brazilian Portuguese (pt-BR)**; keep code
  identifiers, paths and commands as they are.

## Flow

1. `gst status` (where am I) and `recon/RECON.md` (what the game is made of).
2. Pick the layer(s) that answer the user's question: code, assets, data formats, runtime (offline
   only), rendering, logic. The game-study skill maps each layer to its `gst` command or tool, and
   `gst --help` lists the commands of this toolkit version. A tool without a `gst` command yet: write
   its output under this folder and note the exact command in STUDY.md.
   Every study also covers **audio** (study-audio skill: sound events -> files -> who triggers them,
   the sound engine, music; `gst audio`) and **maps/levels** (study-maps skill: format, content, load
   flow; `gst maps`), even when the user did not ask: `gst report --check` requires both.
3. Record as you go: findings in `notes/<layer>/<topic>.md`, critical facts in `FACTS.md`,
   attempts and dead ends in `STUDY.md`.
4. Consolidate into `ARCHITECTURE.md`: every claim with evidence, plus what was not verified.

## Where things live

| Folder | Content |
|---|---|
| `recon/` | recon.json, RECON.md, protections.md |
| `decomp/` | decompiled code (one subfolder per tool/module) |
| `assets/` | extracted and converted assets + INDEX.md |
| `dumps/` | data tables, memory dumps, snapshots |
| `traces/` | Procmon/Frida/TTD/RenderDoc captures and summaries |
| `notes/` | topic notes; `notes/renames.json` = names you gave to symbols |
| `index/` | code index (search database over `decomp/`) |
| `sandbox/` | copy of the install for injection-based tools (verdict `offline-ok` only) |

## Conventions

- Addresses: `module+0xRVA @ build {buildid}` (never raw absolute addresses).
- Symbols: `Namespace.Type::Method(params)` for C#, `pkg.Class#method` for Java, Ghidra names as-is.
- Every fact cites its evidence: `path:line`, an index id, or a capture/trace file.
- Small excerpts of decompiled code are fine in notes; explain in your own words, prefer tables,
  formulas and state machines over long code dumps.

## Golden rules

- **Read the real thing, don't guess.** Decompiled code, the actual data, a live capture is the
  spec. Mark anything unverified as such.
- **Attempt limit:** after 3 identical failures, change approach and log it in STUDY.md.
- Search and read slices (the code index, `rg -n`, Read with offset/limit) instead of opening huge
  decompiled files; never paste more than ~200 lines.
- Prefer `gst` commands where they exist: they write artifacts here, keep the journal and respect the policy.

## Guardrails (summary; full text: {guardrails})

Personal study only; nothing derived leaves `{gs_home}`. Never bypass anti-cheat, DRM or protectors.
Nothing against players or servers; runtime only offline/solo and only with verdict `offline-ok`.
Encrypted content only with a community-published key (source recorded) or a public scheme.
The install is read-only (injection tools only in a sandbox copy). Observe first; memory writes only
when the user asks. MCP servers local only. Kill processes by PID; never touch anti-cheat services.
