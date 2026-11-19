---
type: llm
---

PASS if the plan has all three: (1) starting from what the study already extracted or analyzed (for example a template or note pre-filled from `analysis/*.json`, glTF or DCC summaries, exported files); (2) the origin tool presented as an inference with its evidence (names, primitives, transforms); (3) rigs and animations described from the data, plus a list of what was not verified.
FAIL if any of the three is missing, if the origin is stated as certain without evidence, or if it proposes re-extracting the whole game.
