---
type: llm
---

PASS if the plan identifies the file first (signature, entropy, hexdump or strings with `gst fmt`), compares saves taken before and after a known change in the game to locate fields (`gst fmt diff`) or reads the code that writes the save, then writes a reader (`gst fmt new-reader` or an ImHex pattern) and proves it with a byte-for-byte round-trip (`gst fmt roundtrip`). Saying it would stop and record a diagnosis if the data turns out encrypted also fits. Small variations in order or extra steps are fine.
FAIL if the plan's goal becomes changing values in the save for an advantage, if it declares the layout without any verification step, or if it ignores the gst toolkit entirely.
