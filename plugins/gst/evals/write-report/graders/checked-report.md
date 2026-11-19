---
type: llm
---

PASS if the plan builds ARCHITECTURE.md from the study's own material (notes, FACTS.md, the system map, the recon), keeps path:line evidence for the claims and a section of what was not verified, includes at least one diagram (for example Mermaid), and validates the result with `gst report --check`, fixing what it reports. Also asking for the workspace path or for permissions is fine.
FAIL if the plan writes the document with no validation step, fills it with claims that have no evidence, or the reply gives no process at all.
