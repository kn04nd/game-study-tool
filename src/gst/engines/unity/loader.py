"""Load Unity player files one at a time, streaming, with the UnityPy 1.25 workarounds in one place.

Never ``UnityPy.load(<Game>_Data)``: that parses every file and keeps it in RAM. Here each serialized
file or bundle is opened alone (:func:`open_env`); bundles go through :mod:`gst.engines.unity.bundle`
(blocks decompressed on demand). Files referenced by a PPtr (externals, ``.resS``) are opened lazily
by UnityPy's own ``find_file`` relative to the file's folder, or through :mod:`catalog`.

Known UnityPy 1.25.4 pitfalls handled here:

- ``PPtr.read(check_read=False)`` raises TypeError: use :func:`deref` (``ObjectReader.read(check_read=False)``).
- Reading a component's typetree without the ``m_Enabled`` alignment shifts the next PPtr by 3 bytes:
  ``m_FileID == 16777216`` -> :func:`fix_pptr` (``>> 24`` on both fields).
- ``m_Skin`` is None on some meshes: :func:`mesh_handler` returns a processed ``MeshHandler``.
- ``m_MuscleClip`` is an OffsetPtr: :func:`unwrap_offsetptr` (``.data``).
- A PPtr with ``m_FileID != 0`` must be resolved in the external file, not in the current one.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Iterator

from gst.common import GsError

BUNDLE_SIGNATURES = (b"UnityFS", b"UnityWeb", b"UnityRaw", b"UnityArchive")
RESOURCE_SUFFIXES = (".resS", ".resource")
SKIP_SUFFIXES = (".dll", ".exe", ".pdb", ".json", ".txt", ".xml", ".png", ".jpg", ".config", ".info", ".mdb",
                 ".so", ".dylib", ".bin", ".hash", ".manifest", ".ini", ".cfg", ".csv", ".bank", ".wem", ".bnk",
                 ".pck", ".usm", ".mp4", ".webm", ".ogg", ".wav", ".dat")


def unitypy():
    try:
        import UnityPy
    except ImportError as exc:
        raise GsError("UnityPy is not installed (pip install 'game-study-tool[unity]')") from exc
    return UnityPy


def unitypy_version() -> str:
    try:
        return unitypy().__version__
    except GsError:
        return ""


def data_dirs(install: Path) -> list[Path]:
    """Every ``*_Data`` folder of a player build (some installs ship more than one)."""
    found = sorted(d for d in Path(install).glob("*_Data") if d.is_dir())
    if not found:
        raise GsError(f"no <Game>_Data folder in {install}: not a Unity player build?")
    return found


# --- file classification -------------------------------------------------------------------------

def classify(path: Path) -> str | None:
    """``bundle`` | ``serialized`` | ``resource`` | None, from the first bytes (never trusts the name)."""
    name = path.name
    if name.endswith(RESOURCE_SUFFIXES):
        return "resource"
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            head = fh.read(48)
    except OSError:
        return None
    if head.startswith(BUNDLE_SIGNATURES):
        return "bundle"
    if len(head) < 20 or name.lower().endswith(SKIP_SUFFIXES):
        return None
    _meta, fsize32, version, _off = struct.unpack(">IIII", head[:16])
    if not 5 <= version <= 64:
        return None
    if version >= 22:
        if len(head) < 40:
            return None
        fsize = struct.unpack(">q", head[24:32])[0]
        return "serialized" if fsize == size else None
    return "serialized" if fsize32 == size else None


def iter_unity_files(install: Path) -> Iterator[tuple[str, str, int]]:
    """``(relpath, kind, size)`` of every serialized file and bundle under all ``*_Data`` folders,
    StreamingAssets (incl. Addressables ``aa/``) included. Streams the walk; sorted per folder."""
    install = Path(install)
    for data in data_dirs(install):
        for dirpath, dirs, names in os.walk(data):
            dirs.sort()
            for name in sorted(names):
                p = Path(dirpath, name)
                kind = classify(p)
                if kind in ("bundle", "serialized"):
                    yield p.relative_to(install).as_posix(), kind, p.stat().st_size


def resource_size(install: Path, relpath: str) -> int:
    """Bytes of the ``.resS``/``.resource`` companions of a serialized file (they are read on demand)."""
    p = Path(install, relpath)
    total = 0
    cands = {Path(str(p) + suffix) for suffix in (".resS", ".resource")}
    cands |= {p.with_suffix(suffix) for suffix in (".resS", ".resource")}
    for cand in sorted(cands - {p}):
        if cand.is_file():
            total += cand.stat().st_size
    return total


# --- opening -------------------------------------------------------------------------------------

def open_env(path: Path, *, cache_bytes: int | None = None):
    """A UnityPy Environment holding only ``path`` (lazy bundle or streamed serialized file).
    Externals and resources load on demand from the same folder."""
    UnityPy = unitypy()
    path = Path(path)
    env = UnityPy.Environment(path=str(path.parent))
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"UnityFS"):
        from gst.engines.unity import bundle

        kw = {"cache_bytes": cache_bytes} if cache_bytes else {}
        bundle.load_bundle(env, path, name=path.name, **kw)
    else:
        env.load_file(str(path), name=path.name)
    return env


def close_env(env) -> None:
    """Release file handles and decompressed blocks (Windows keeps files locked otherwise)."""
    for f in list(getattr(env, "files", {}).values()):
        blocks = getattr(f, "_lazy_blocks", None)
        if blocks is not None:
            blocks.close()
        reader = getattr(f, "reader", None)
        stream = getattr(reader, "stream", None)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
    getattr(env, "files", {}).clear()
    getattr(env, "cabs", {}).clear()


def serialized_files(env, *, release: bool = False) -> Iterator:
    """Every SerializedFile in the env (inside bundles too), excluding dependencies loaded later.

    CABs of a lazy bundle are parsed one at a time; with ``release=True`` each one parsed here is
    forgotten after the caller moves on, so RAM holds one CAB's object table at a time."""
    from UnityPy.files import SerializedFile

    from gst.engines.unity import bundle as lazy

    def walk(item):
        for key, f in list(getattr(item, "files", {}).items()):
            if hasattr(f, "_lazy_nodes"):
                for node_name in list(f._lazy_nodes):
                    if lazy.is_resource_node(node_name):
                        continue
                    was_parsed = node_name in f.files
                    sf = lazy.parse_node(f, node_name)
                    if isinstance(sf, SerializedFile):
                        yield sf
                    if release and not was_parsed:
                        lazy.release_node(f, node_name)
            elif isinstance(f, SerializedFile):
                if not getattr(f, "is_dependency", False):
                    yield f
            elif getattr(f, "files", None):
                yield from walk(f)

    yield from walk(env)


def iter_objects(env, *, release: bool = False) -> Iterator:
    for sf in serialized_files(env, release=release):
        yield from list(sf.objects.values())


def unity_version(env) -> str:
    for f in getattr(env, "files", {}).values():
        v = getattr(f, "unity_version", "")
        if v and not str(v).startswith("0.0"):
            return str(v)
    for f in getattr(env, "files", {}).values():
        v = getattr(f, "version_engine", "")
        if v:
            return str(v)
    return ""


# --- object helpers ------------------------------------------------------------------------------

def type_name(obj) -> str:
    try:
        return obj.type.name
    except Exception:
        return f"ClassID{getattr(obj, 'class_id', '?')}"


def peek_name(obj) -> str:
    try:
        return obj.peek_name() or ""
    except Exception:
        return ""


def deref(ptr):
    """Read the object a PPtr points to, or None (no ``check_read`` argument: UnityPy 1.25 TypeError)."""
    try:
        if ptr is None or not getattr(ptr, "m_PathID", 0):
            return None
        reader = ptr.deref()
        return reader.read(check_read=False)
    except Exception:
        return None


def deref_reader(ptr):
    try:
        if ptr is None or not getattr(ptr, "m_PathID", 0):
            return None
        return ptr.deref()
    except Exception:
        return None


def fix_pptr(file_id: int, path_id: int) -> tuple[int, int]:
    """Undo the 3-byte shift of a PPtr read right after an unaligned ``m_Enabled``."""
    if file_id > 0 and (file_id & 0xFFFFFF) == 0:
        return file_id >> 24, path_id >> 24
    return file_id, path_id


def raw_script_pptr(obj) -> tuple[int, int] | None:
    """``(m_FileID, m_PathID)`` of a MonoBehaviour's ``m_Script`` from the raw bytes, without a typetree.

    Layout (format >= 14, 64-bit path ids): m_GameObject PPtr (4+8), m_Enabled (1, aligned to 4),
    m_Script at offset 16 (fileID int32) / 20 (pathID int64)."""
    try:
        reader = obj.reader
        endian = getattr(reader, "endian", "<")
        pos = obj.Position
        obj.reset()
        head = reader.read_bytes(28)
        obj.Position = pos
    except Exception:
        return None
    if len(head) < 28:
        return None
    fid = struct.unpack_from(endian + "i", head, 16)[0]
    pid = struct.unpack_from(endian + "q", head, 20)[0]
    return fix_pptr(fid, pid)


def raw_gameobject_pid(obj) -> int | None:
    """``m_GameObject.m_PathID`` of a component from the raw bytes (offset 4)."""
    try:
        reader = obj.reader
        endian = getattr(reader, "endian", "<")
        pos = obj.Position
        obj.reset()
        head = reader.read_bytes(12)
        obj.Position = pos
        return struct.unpack_from(endian + "q", head, 4)[0]
    except Exception:
        return None


def mesh_handler(mesh):
    """A processed ``MeshHandler`` (works when ``m_Skin`` is None, unlike ``Mesh.export`` on some builds)."""
    from UnityPy.helpers.MeshHelper import MeshHandler

    h = MeshHandler(mesh)
    h.process()
    return h


def unwrap_offsetptr(value):
    """``m_MuscleClip`` and friends are OffsetPtr wrappers: return the wrapped ``data``."""
    if isinstance(value, dict) and set(value) == {"data"}:
        return value["data"]
    return getattr(value, "data", value)


def external_name(sf, file_id: int) -> str | None:
    """File name of external ``file_id`` (1-based) of a SerializedFile, or None for the file itself."""
    if file_id == 0:
        return None
    try:
        ext = sf.externals[file_id - 1]
    except (IndexError, AttributeError):
        return None
    return os.path.basename(str(ext.path).replace("\\", "/"))
