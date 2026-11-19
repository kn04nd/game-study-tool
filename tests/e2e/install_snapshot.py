"""Read-only snapshot of an install: (relpath, size, mtime_ns) of every file, to prove a study never wrote
into it. Used by the e2e runs (``-m games``) and by hand:

    python tests/e2e/install_snapshot.py take <install> <out.json>
    python tests/e2e/install_snapshot.py compare <before.json> <after.json>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def take(root: Path) -> list[list]:
    base = str(root.resolve())
    if os.name == "nt" and not base.startswith("\\\\?\\"):
        base = "\\\\?\\" + base
    rows = []
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            full = os.path.join(dirpath, name)
            st = os.stat(full)
            rows.append([os.path.relpath(full, base).replace("\\", "/"), st.st_size, st.st_mtime_ns])
    return sorted(rows)


def compare(a: list[list], b: list[list]) -> dict:
    da, db = {r[0]: r[1:] for r in a}, {r[0]: r[1:] for r in b}
    return {"added": sorted(set(db) - set(da)), "removed": sorted(set(da) - set(db)),
            "changed": sorted(k for k in set(da) & set(db) if da[k] != db[k]), "files": len(da)}


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "take":
        rows = take(Path(argv[1]))
        Path(argv[2]).write_text(json.dumps(rows), encoding="utf-8")
        print(f"{len(rows)} files")
        return 0
    if len(argv) == 3 and argv[0] == "compare":
        a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in argv[1:])
        diff = compare(a, b)
        print(json.dumps(diff, indent=2))
        return 0 if not (diff["added"] or diff["removed"] or diff["changed"]) else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
