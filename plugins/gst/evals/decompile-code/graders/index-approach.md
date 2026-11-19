---
type: llm
---

PASS if the plan decompiles the game's code with `gst decompile` (when not done yet) and then finds the save logic through the code index with `gst q` queries (for example strings, def, callers, callees, show or grep), and says the findings will be backed by file and line evidence. A plan that adapts these steps to what is already in the workspace also passes.
FAIL if the plan proposes editing game files, uses decompilers outside this gst flow as the main path, or states where the save code is without a way to verify it.
