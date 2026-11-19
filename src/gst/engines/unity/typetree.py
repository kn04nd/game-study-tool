"""Where MonoBehaviour typetrees come from, and reading them safely.

Source chain (first that works wins):

1. ``managed``: ``<Game>_Data/Managed`` with the game's assemblies (Mono builds).
2. ``backup``: ``<Game>_BackUpThisFolder_ButDontShipItWithYourGame/Managed`` (IL2CPP builds that shipped
   it). Snapshotted into ``decomp/il2cpp/managed-backup@<build>/`` the first time, because an update
   can delete the folder (Safari-TV); later runs of the same build use the snapshot.
3. ``il2cpp``: ``GameAssembly.dll`` + ``global-metadata.dat`` through UnityPy's TypeTreeGenerator
   (fails on metadata versions it does not know, e.g. v39; then the next source). Skipped when
   ``code.il2cpp.metadata`` recorded that it fails (``decomp/il2cpp/metadata.json`` ``typetree_il2cpp.ok: false``).
4. ``dummydll``: dummy assemblies made by Cpp2IL (or Il2CppDumper/Il2CppInspector) by ``code.il2cpp.dummydll``
   under ``decomp/il2cpp/dummydll/`` (or ``GST_DUMMYDLL_DIR``).
5. ``none``: MonoBehaviours keep only their header; the binary PPtr scan is the fallback.

Reading: never ``read_typetree`` on every object blindly (a wrong node can crash the parser natively).
:func:`read_mono` first resolves the script from the raw bytes and checks the generator knows the
type, then reads, catching every Python error.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from gst.engines.unity import loader

_GEN_CACHE: dict[tuple, tuple] = {}


def _game_dlls(folder: Path) -> bool:
    return folder.is_dir() and any(p.suffix == ".dll" for p in folder.iterdir())


def backup_dirs(install: Path) -> list[Path]:
    return sorted(p / "Managed" for p in Path(install).glob("*_BackUpThisFolder*") if (p / "Managed").is_dir())


def snapshot_backup(src: Path, workspace: Path, build: str) -> Path:
    """Copy the BackUp ``Managed`` DLLs into the workspace once per build (read-only on the install)."""
    from gst.recon.stores import build_id

    dest = Path(workspace) / "decomp" / "il2cpp" / f"managed-backup@{build_id(build) or 'unknown'}"
    if (dest / "MANIFEST.json").is_file():
        return dest
    partial = dest.with_name(dest.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    files = []
    for p in sorted(src.iterdir()):
        if p.is_file() and p.suffix.lower() == ".dll":
            shutil.copy2(p, partial / p.name)
            files.append({"file": p.name, "size": p.stat().st_size})
    (partial / "MANIFEST.json").write_text(json.dumps({"source": str(src), "build": build, "files": files,
                                                       "copied_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1),
                                           encoding="utf-8")
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    os.replace(partial, dest)
    return dest


def _il2cpp_probe_failed(workspace: Path | None) -> bool:
    """``code.il2cpp.metadata`` already tried TypeTreeGenerator on this GameAssembly + metadata and it failed
    (metadata newer than its LibCpp2IL, e.g. v39): skip the ``il2cpp`` source instead of loading ~200 MB per worker
    to fail again."""
    if workspace is None:
        return False
    path = Path(workspace) / "decomp" / "il2cpp" / "metadata.json"
    if not path.is_file():
        return False
    try:
        probe = json.loads(path.read_text(encoding="utf-8")).get("typetree_il2cpp")
    except (OSError, ValueError):
        return False
    return isinstance(probe, dict) and probe.get("ok") is False


def sources(install: Path, workspace: Path | None = None, build: str = "?") -> list[dict]:
    """The candidate sources in chain order: ``{"kind", "path"}`` (no loading)."""
    install = Path(install)
    out = []
    for data in loader.data_dirs(install):
        if _game_dlls(data / "Managed") and (data / "Managed" / "Assembly-CSharp.dll").exists():
            out.append({"kind": "managed", "path": str(data / "Managed")})
    if workspace is not None:
        snap = Path(workspace) / "decomp" / "il2cpp" / f"managed-backup@{build or 'unknown'}"
        if (snap / "MANIFEST.json").is_file():
            out.append({"kind": "backup", "path": str(snap)})
    for b in backup_dirs(install):
        out.append({"kind": "backup", "path": str(b), "snapshot": workspace is not None})
    ga = install / "GameAssembly.dll"
    if ga.is_file() and not _il2cpp_probe_failed(workspace):
        for data in loader.data_dirs(install):
            meta = data / "il2cpp_data" / "Metadata" / "global-metadata.dat"
            if meta.is_file():
                out.append({"kind": "il2cpp", "path": str(ga), "metadata": str(meta)})
    dummy = [Path(os.environ["GST_DUMMYDLL_DIR"])] if os.environ.get("GST_DUMMYDLL_DIR") else []
    if workspace is not None:
        dummy += [Path(workspace) / "decomp" / "il2cpp" / "dummydll"]
    out += [{"kind": "dummydll", "path": str(d)} for d in dummy if _game_dlls(d)]
    return out


def generator(install: Path, version: str, workspace: Path | None = None, build: str = "?"):
    """``(TypeTreeGenerator | None, source_kind)`` for this game, cached per process."""
    key = (str(install), version, str(workspace), build)
    if key in _GEN_CACHE:
        return _GEN_CACHE[key]
    loader.unitypy()
    from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

    result = (None, "none")
    for src in sources(install, workspace, build):
        try:
            gen = TypeTreeGenerator(version)
            if src["kind"] == "il2cpp":
                gen.load_il2cpp(Path(src["path"]).read_bytes(), Path(src["metadata"]).read_bytes())
            else:
                folder = Path(src["path"])
                if src["kind"] == "backup" and src.get("snapshot") and workspace is not None:
                    folder = snapshot_backup(folder, workspace, build)
                gen.load_local_dll_folder(str(folder))
            result = (gen, src["kind"])
            break
        except Exception:
            continue
    _GEN_CACHE[key] = result
    return result


def attach(env, gen) -> None:
    if gen is not None:
        env.typetree_generator = gen


def knows(gen, info) -> bool:
    """Does the generator have nodes for this script's type? (cheap, cached by UnityPy)."""
    if gen is None or info is None or not info.class_name:
        return False
    try:
        gen.get_nodes_up(info.assembly or "Assembly-CSharp", info.fullname)
        return True
    except Exception:
        return False


def read_mono(obj, gen, info) -> dict | None:
    """Full typetree dict of a MonoBehaviour, or None when its type is unknown (raw prefilter first)."""
    st = getattr(obj, "serialized_type", None)
    node = getattr(st, "node", None) if st is not None else None  # bundles often carry their own typetrees
    if node is None:
        if not knows(gen, info):
            return None
        try:
            node = gen.get_nodes_up(info.assembly or "Assembly-CSharp", info.fullname)
        except Exception:
            return None
    try:
        tree, complete = read_bounded(obj, node)
    except Exception:
        return None
    if not complete and isinstance(tree, dict):  # DLLs and data disagree on the size: keep it, flagged
        tree["_gst_check_read"] = False
    return tree


def read_bounded(obj, node) -> tuple[dict, bool]:
    """Read a typetree from the object's own bytes with UnityPy's pure-Python reader.

    The C reader (UnityPyBoost) is given garbage array lengths when the node does not match the data
    (wrong/old assembly) and can allocate gigabytes before failing (REPO resources.assets peaked at
    17 GB). Here the reader only sees ``byte_size`` bytes and lists grow element by element, so a
    mismatch ends at the end of the buffer. Returns ``(tree, read_exactly_byte_size)``."""
    from UnityPy.helpers import TypeTreeHelper
    from UnityPy.streams import EndianBinaryReader

    raw = obj.get_raw_data()
    reader = EndianBinaryReader(raw, endian=getattr(obj.reader, "endian", "<"))
    config = TypeTreeHelper.TypeTreeConfig(True, obj.assets_file, False)
    tree = TypeTreeHelper.read_value(node, reader, config)
    return tree, reader.Position == len(raw)


def read_builtin(obj) -> dict | None:
    """Typetree dict of a built-in class (UnityPy's bundled type info), errors -> None."""
    try:
        return obj.read_typetree(check_read=False)
    except Exception:
        return None
