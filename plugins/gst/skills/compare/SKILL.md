---
name: compare
description: Compare how several studied games solve the same thing (ragdoll and physics bodies, proximity voice, co-op networking, procedural maps, extraction loop, inventory, rigs, audio) from each study workspace's notes, analysis/*.json and recon middleware, into one comparativo note with a table per dimension (each cell sourced), common patterns, differences and which approach fits the user's game.
when_to_use: The user asks to compare games they studied, or wants the best approach across studies. Examples - "compare the ragdolls of PEAK and Pratfall", "which game does proximity voice best", "how do these games generate maps", "compara o ragdoll do PEAK e do Pratfall", "qual jogo faz melhor a voz por proximidade", "compara a geração de mapas dos jogos que estudei", "o que esses jogos têm em comum no co-op".
argument-hint: "[dimension] [game slugs]"
allowed-tools: Bash(gst *) PowerShell(gst *)
---

# Compare studied games

Each studied game has a workspace under GS_HOME (`gst config show` names it; GS_HOME/INDEX.md lists them after
`gst notes index`). Reply and write in pt-BR. One dimension per run; for several, one `comparer` agent each.

## 1. Refresh the library, pick the games and the dimension

The cross-game library (`GS_HOME/_library/library.db`) holds every workspace's analyses as tables and an index
of all notes. Rebuild it first (seconds, read-only on the workspaces): `gst lib build` (`gst lib status` lists the
games and their gaps).

- The dimension: one of the user's priorities (`gst config show` -> `focus`) or what they asked.
- The games: those with notes or analyses on it. Find the notes of every study at once:
  `gst lib search "ragdoll"` (FTS; `--game <slug>`, `--kind notes|analysis|facts`; prints `path:line`), then read
  the hits. Per workspace also `ARCHITECTURE.md` section 4 and `recon/recon.json` `net_voice`.
- When the dimension is a library facet (rigs, joints/ragdoll, animation, audio, middleware, systems, shaders,
  dcc), get the measured table: `gst lib compare <facet> <slug-a> <slug-b>` writes
  `GS_HOME/_library/compare/<facet>-<games>.md` (table, sources per game, "Não verificado / lacunas").
- Read other workspaces only; never run study commands in them.

## 2. Create the note first

From inside one of the compared workspaces:

```
gst notes new compare/<dimension> --template comparativo --games <slug-a>,<slug-b>,<slug-c> --facet <facet>
```

The note starts with the table of workspaces (engine, network/voice middleware, analyses present) and, when the
library exists and `--facet` (or the topic) names a facet, the `gst lib compare` table.

## 3. Fill the table

Keep the library rows (they cite `<slug>:analysis/<x>.json`) and add one row per aspect the analyses do not
measure (technique, key parameters with values, who simulates it, cost), one column per game. Every cell cites its source: `<slug>:notes/logic/x.md` (section) or
`<slug>:analysis/<x>.json` key. A game with nothing on it gets "sem nota": never fill from memory.
If an important cell needs a study that does not exist, list it under "Não verificado" with the agent that
would answer it (`mechanics-analyst`, `code-navigator`, `asset-analyst`); copy the library's gaps there too
(a missing analysis says which `gst analyze` would fill it).

## 4. Conclude

Common patterns, the differences and why (engine, team size, genre), and "Relevância para o meu jogo": which
approach to follow, what to combine, the cost.

## Done when

The note has the sourced table, the per-game summary with links, the verdict, and "Não verificado".
