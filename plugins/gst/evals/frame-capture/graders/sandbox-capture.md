---
type: llm
---

PASS if the reply's plan has all four: (1) the sandbox copy, made with `gst sandbox create` or named as a requirement; (2) the capture with `gst gfx capture`; (3) reading the summary `frame.md` (or making it with `gst gfx export`) for the passes, render targets and shaders; (4) writing the findings into a file under `notes/`.
FAIL if any of the four is missing, if it runs RenderDoc or another capture tool on the original install instead of the sandbox copy, or if it proposes bypassing anti-cheat.
