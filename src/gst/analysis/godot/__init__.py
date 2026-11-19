"""Shared helpers of the Godot analyses: where the recovered project is, which scenes/resources it holds
(text and binary), and on-demand parsing keyed by ``res://`` path. Parsers live in ``gst.engines.godot`` (stdlib);
import remaps and the scene -> model -> skeleton links in :mod:`gst.analysis.godot.links`."""

from __future__ import annotations

from pathlib import Path

TEXT_EXTS = (".tscn", ".tres", ".escn")
BINARY_EXTS = (".scn", ".res")
SCENE_EXTS = TEXT_EXTS + BINARY_EXTS
IMPORT_DIRS = (".godot/imported", ".import")  # Godot 4 / Godot 3 import caches
# Packed float/vector/byte arrays of this many bytes or more in binary resources are not decoded (mesh buffers,
# long keyframe arrays): their type and value count are kept (``tres.Bulk``), so compressed blocks holding only such
# data are never decompressed. Option ``bulk=all`` of the analyses decodes everything.
BULK = 4096
# Sub-resources a rig/scene-tree walk never needs (geometry, images, collision data): skipped unread by Docs(skip=).
GEOMETRY_TYPES = ("ArrayMesh", "ImporterMesh", "PlaceholderMesh", "MultiMesh", "ConcavePolygonShape3D",
                  "ConvexPolygonShape3D", "HeightMapShape3D", "Image", "ImageTexture", "PortableCompressedTexture2D",
                  "NavigationMesh", "ArrayOccluder3D", "OccluderPolygon2D", "Shape3D")


def project_root(ws: Path) -> Path | None:
    """The recovered Godot project (``assets/godot/**/project.godot``; else ``assets/godot/extract``)."""
    root = Path(ws) / "assets" / "godot"
    if not root.is_dir():
        return None
    found = sorted(root.rglob("project.godot"), key=lambda p: (len(p.parts), str(p)))
    if found:
        return found[0].parent
    extract = root / "extract"
    if extract.is_dir() and any(extract.rglob("*.tscn")):
        return extract
    return None


def need_project(ws: Path, game: dict):
    root = project_root(ws)
    if root is not None:
        return True, "", root
    return False, "no recovered Godot project under assets/godot (gst study runs godot.recover)", Path(ws) / "assets" / "godot"


def need_pack(ws: Path, game: dict):
    from gst.engines.godot import pck

    install = Path(game.get("install_dir") or "")
    packs = pck.find(install) if install.is_dir() else []
    if packs:
        return True, "", packs[0]
    return False, f"no Godot pack (.pck or exe with an embedded pack) in {install}", install


def iter_files(root: Path, exts=TEXT_EXTS):
    """(res path, file) of every file with ``exts`` in the project, sorted, skipping the import caches (``.godot/``,
    Godot 3 ``.import/``)."""
    out = []
    for p in root.rglob("*"):
        if p.suffix.lower() in exts and not {".godot", ".import"} & set(p.relative_to(root).parts) and p.is_file():
            out.append(("res://" + p.relative_to(root).as_posix(), p))
    return sorted(out)


def binary_files(root: Path) -> list[str]:
    """``.scn``/``.res`` outside ``.godot/`` that are binary resources (GDRE did not convert them to text)."""
    from gst.engines.godot.rsrc import is_binary

    out = []
    for res, p in iter_files(root, BINARY_EXTS):
        try:
            with open(p, "rb") as fh:
                if is_binary(fh.read(4)):
                    out.append(res)
        except OSError:
            pass
    return out


def binary_type(path: Path) -> str | None:
    """Resource class of a binary resource (header only; RSRC or compressed RSCC); None when unreadable."""
    from gst.engines.godot import rsrc

    try:
        return rsrc.header(path)["type"] or None
    except (OSError, rsrc.RsrcError):
        return None


def bulk_option(ctx) -> int | None:
    """The ``bulk`` analysis option: bytes (default :data:`BULK`), or ``all`` = decode every packed array."""
    value = str(ctx.opt("bulk", BULK))
    return None if value.lower() == "all" else int(value)


class Docs:
    """Parses scenes/resources on demand, text (``tres``) or binary (``rsrc``: RSRC/RSCC, also the imported files
    under ``.godot/imported``); no cache: one document in memory at a time. ``get(res)`` -> parsed doc or None
    (missing, not a scene/resource, or broken: the parse error goes in ``errors``). ``binary`` = res paths read
    from binary files."""

    def __init__(self, root: Path, bulk: int | None = BULK, skip: tuple = ()):
        self.root = Path(root)
        self.bulk = bulk
        self.skip = tuple(skip)
        self.errors: dict[str, str] = {}
        self.binary: set[str] = set()

    def file(self, res: str) -> Path:
        return self.root / (res[len("res://"):] if res.startswith("res://") else res)

    def get(self, res: str) -> dict | None:
        from gst.engines.godot import rsrc, tres

        p = self.file(res)
        if not p.is_file():
            return None
        try:
            with open(p, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            self.errors[res] = str(exc)
            return None
        if data[:4] == b"GDEC":  # FileAccessEncrypted: reported, never decrypted (guardrail 4)
            self.errors[res] = "encrypted (GDEC): not decrypted"
            return None
        if rsrc.is_binary(data):
            try:
                doc = rsrc.read_bytes(data, bulk=self.bulk, skip_types=self.skip)
            except rsrc.RsrcError as exc:
                self.errors[res] = f"binary: {exc}"
                return None
            self.binary.add(res)
            return doc
        if p.suffix.lower() not in TEXT_EXTS:
            return None
        try:
            return tres.parse(data.decode("utf-8", errors="replace"))
        except tres.TresError as exc:
            self.errors[res] = str(exc)
            return None
