"""Format readers written during a study (dissect.cstruct) and their byte-exact round-trip check.

A reader is a Python file in the workspace (`notes/formats/<name>.py`) that defines
`parse(data: bytes) -> object` and `dump(obj) -> bytes`, and optionally `describe(obj) -> dict`.
`roundtrip` proves the layout: every sample must come back with the same sha256. Unknown regions stay
raw bytes until mapped, so the check holds while the work progresses (`describe` reports what is left).
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

from gst.common import GsError, write_text

READER_TEMPLATE = '''r"""Reader for the {name} format (sample: {sample}).

Layout and evidence: notes/formats/{name}.md. Check with:
    gst fmt roundtrip notes/formats/{name}.py <files or globs>
Every file must come back byte for byte (same sha256). Keep unknown regions as raw bytes (the
`rest` field) until they are mapped, so the round-trip holds while the work goes on.
"""

from __future__ import annotations

import io

from dissect.cstruct import cstruct

DEFINITION = """
struct Header {{
    char    magic[4];      // {magic}
    uint32  version;
}};
"""

cs = cstruct()  # little-endian; cstruct(endian=">") for big-endian data
cs.load(DEFINITION)


def parse(data: bytes) -> dict:
    stream = io.BytesIO(data)
    header = cs.Header(stream)
    return {{"header": header, "rest": stream.read()}}


def dump(parsed: dict) -> bytes:
    return parsed["header"].dumps() + parsed["rest"]


def describe(parsed: dict) -> dict:
    """Fields worth showing next to the round-trip result; unmapped_bytes = what is left to map."""
    return {{"version": parsed["header"].version, "unmapped_bytes": len(parsed["rest"])}}
'''

NOTE_TEMPLATE = """# Formato {name}

- Amostras: `{sample}` ({size} bytes; {kind}: {detail}; entropia {entropy})
- Leitor: `notes/formats/{name}.py` (`gst fmt roundtrip notes/formats/{name}.py <arquivos>`)
- Quem lê e escreve no código: `<caminho:linha>` (`gst q strings`, `gst q callers`)

## Primeiros bytes

```text
{hexdump}
```

## Layout

| Offset | Tamanho | Tipo | Campo | Evidência |
| --- | --- | --- | --- | --- |
| 0x00 | 4 | char[4] | magic | hexdump acima |

## Hipóteses e verificações

-

## Não verificado

-
"""


def new_reader(ws: Path, name: str, sample: Path | None = None) -> dict:
    from gst import formats

    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise GsError("reader names use letters, digits, _ and - (it becomes notes/formats/<name>.py)")
    folder = ws / "notes" / "formats"
    reader, note = folder / f"{name}.py", folder / f"{name}.md"
    if reader.exists():
        raise GsError(f"{reader} already exists: edit it, then gst fmt roundtrip {reader.relative_to(ws).as_posix()} <files>")
    info = formats.identify(sample) if sample else {"size": "?", "kind": "?", "detail": "?", "entropy": "?"}
    head = formats.read_range(sample, 0, 64) if sample else b""
    magic = head[:4].decode("latin-1").encode("unicode_escape").decode("ascii") if head else "?"
    shown = sample.as_posix() if sample else "-"
    write_text(reader, READER_TEMPLATE.format(name=name, sample=shown, magic=magic))
    if not note.exists():
        write_text(note, NOTE_TEMPLATE.format(name=name, sample=shown, size=info["size"], kind=info["kind"],
                                              detail=info["detail"], entropy=info["entropy"],
                                              hexdump=formats.hexdump(head) if head else "(no sample)"))
    return {"reader": str(reader), "note": str(note)}


def load_reader(path: Path):
    if not path.is_file():
        raise GsError(f"no reader at {path} (gst fmt new-reader <name> --sample <file>)")
    module_name = f"gs_reader_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise GsError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise GsError(f"{path.name}: {exc} (gst tools install dissect-cstruct)") from exc
    for needed in ("parse", "dump"):
        if not callable(getattr(module, needed, None)):
            raise GsError(f"{path.name} must define {needed}()")
    return module


def roundtrip(reader_path: Path, files: list[Path]) -> dict:
    module = load_reader(reader_path)
    rows = []
    for path in files:
        data = path.read_bytes()
        row = {"file": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        try:
            parsed = module.parse(data)
            again = bytes(module.dump(parsed))
        except Exception as exc:  # the reader is work in progress: report, keep going
            rows.append({**row, "ok": False, "error": f"{exc.__class__.__name__}: {exc}"[:300]})
            continue
        ok = again == data
        row.update(ok=ok, rewritten_sha256=hashlib.sha256(again).hexdigest())
        if not ok:
            first = next((i for i, (p, q) in enumerate(zip(data, again)) if p != q), min(len(data), len(again)))
            row.update(first_difference=first, rewritten_size=len(again))
        describe = getattr(module, "describe", None)
        if callable(describe):
            try:
                row["describe"] = describe(parsed)
            except Exception as exc:
                row["describe"] = {"error": f"{exc.__class__.__name__}: {exc}"[:200]}
        rows.append(row)
    return {"reader": str(reader_path), "files": len(rows), "identical": sum(1 for r in rows if r.get("ok")), "results": rows}
