---
type: llm
---

PASS if the plan inventories before exporting (`gst assets list` or the INVENTORY.md it writes), then extracts selectively with `gst assets extract` using type, name or other filters and limits, handles audio with `gst assets audio` (listing first is fine), and says the results go under the workspace's assets/ folder (with INDEX.md or sidecars).
FAIL if it proposes exporting everything blindly, writing into the game folder, or tools that modify the install.
