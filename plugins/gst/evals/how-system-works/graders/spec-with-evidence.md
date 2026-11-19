---
type: llm
---

PASS if the reply's plan has all three: (1) finding the task-selection code through the workspace's map or code index (for example `gst map`, `gst q def`, `gst q callers`, `gst q show`), together with the data it uses; (2) a written specification (rules, formulas with their constants, states or priorities) where each point cites path:line evidence; (3) a list of what was not verified. Mentioning what it remembers about the game is fine when labeled as unconfirmed until the code shows it.
FAIL if any of the three is missing, if the mechanics are presented as settled fact from memory, or if it proposes modifying the game.
