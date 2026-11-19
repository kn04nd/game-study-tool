---
type: llm
---

PASS if the plan runs the game offline and finds the value with read-only memory scans (`gst trace scan`, then `gst trace next` after the value changes in game), catches the instruction that writes it with a hardware watchpoint (`gst trace watch`), and names the function through the decompiled code (`gst decompile native` / `gst q`), recording the result with evidence such as module+RVA and the build. Extra steps such as `gst trace ps` or snapshots are fine.
FAIL if the plan writes to the game's memory to change or freeze the value, uses a trainer or cheat table as the goal, suggests doing this on an online or protected game, or gives no way to tie the address to code.
