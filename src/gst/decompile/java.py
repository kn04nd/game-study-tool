"""Java games -> Java source with Vineflower, into ``decomp/java/``.

One unit of work per jar and per loose class tree (``code.java`` in ``gst study``; ``gst decompile java``
runs the same extractor in this process):

- a .jar outside bundled JREs (at most 3 folders deep: deeper ones are mods or workshop content) ->
  ``decomp/java/<jar stem>/``;
- loose .class files: a folder's classpath root comes from the package of its first class file (read from the
  constant pool); one unit = root + top-level package, e.g. ``zombie/`` -> ``decomp/java/classes/zombie/``.
  The classes go into a stored zip in the staging folder, so Vineflower never copies unrelated files.

Games often ship a fat jar (the game plus every library). By default only the game's own package
roots are decompiled: the root of the manifest's Main-Class plus top-level packages that are not
well-known library namespaces. Options (``gst study --opt`` and the ``gst decompile java`` flags):
``java.filter=zombie/core/,zombie/ai/`` (package prefixes; ``--only``), ``java.all=1`` (every package and the
library jars; ``--all``), ``java.units=<jar or package>,...`` (decompile these even when they look like
libraries; the names given to ``gst decompile java``), ``java.threads=N`` (Vineflower threads; default every CPU).
"""

from __future__ import annotations

import collections
import hashlib
import os
import re
import struct
import subprocess
import time
import zipfile
from pathlib import Path

from gst import tools
from gst.common import GsError

OUT_DIR = Path("decomp") / "java"
CLASS_OUT = "classes"  # decomp/java/classes/[<root>/]<top package>/ for loose class trees
EXPORT_VERSION = 1     # bump when the Vineflower command or the output layout changes: every Java unit reruns
JAR_DEPTH = 3          # jars in folders deeper than this are mods or workshop content, not the game
LIBRARY_ROOTS = {
    "org", "com", "net", "io", "javax", "java", "jdk", "sun", "jakarta", "kotlin", "kotlinx", "scala", "groovy",
    "gnu", "okhttp3", "okio", "oshi", "lombok", "it", "edu", "ch", "de", "pl", "fr", "uk", "us", "nu", "info",
    "imgui", "jassimp", "android", "androidx", "junit", "mozilla", "reactor", "rx", "dev", "me", "META-INF",
    "org_", "assets", "natives", "linux", "windows", "macos", "darwin", "x64", "x86",
}
QUALIFIED_ROOTS = {"com", "org", "net", "io", "de", "fr", "uk", "us", "me", "dev"}  # two-level game roots (com/studio)
SKIP_DIRS = re.compile(r"(?:^|/)(?:jre\w*|jdk\w*|runtime|java|steamapps|\.git|__MACOSX)(?:/|$)", re.I)


def find_jars(install: Path) -> list[Path]:
    jars = []
    for dirpath, dirs, files in os.walk(install):
        rel = Path(dirpath).relative_to(install).as_posix()
        if rel != "." and (SKIP_DIRS.search(rel) or rel.count("/") >= JAR_DEPTH):
            dirs[:] = []
            continue
        jars += [Path(dirpath) / f for f in files if f.lower().endswith(".jar")]
    return sorted(jars, key=lambda p: -p.stat().st_size)


def jar_roots(jar: Path) -> dict:
    """Class counts per top-level package and the Main-Class (if any)."""
    with zipfile.ZipFile(jar) as z:
        counts = collections.Counter(n.split("/", 1)[0] for n in z.namelist() if n.endswith(".class") and "/" in n)
        top_level = sum(1 for n in z.namelist() if n.endswith(".class") and "/" not in n)
        main = None
        try:
            manifest = z.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace")
            m = re.search(r"^Main-Class:\s*(\S+)", manifest, re.M)
            main = m.group(1).replace(".", "/") if m else None
        except KeyError:
            pass
    return {"counts": dict(counts), "default_package_classes": top_level, "main": main}


def game_roots(info: dict) -> list[str]:
    """Package prefixes (with a trailing /) that are most likely the game's own code."""
    roots = set()
    main = info.get("main")
    if main:
        parts = main.split("/")
        roots.add("/".join(parts[:2] if parts[0] in QUALIFIED_ROOTS and len(parts) > 2 else parts[:1]) + "/")
    roots |= {f"{r}/" for r in info["counts"] if r not in LIBRARY_ROOTS}
    return sorted(roots)


def command(java: Path, vineflower: Path, jar: Path, out: Path, only: list[str], threads: int) -> list[str]:
    # --skip-extra-files: the jar's resources (library properties, natives, assets) are not code; gst assets reads data
    cmd = [str(java), "-jar", str(vineflower), "--folder", "--skip-extra-files", f"--thread-count={threads}", "--log-level=warn"]
    cmd += [f"--only={prefix}" for prefix in only]
    return cmd + [str(jar), str(out)]


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _csv(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value)
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


# --- loose class files ----------------------------------------------------------------------------

def class_name(path: Path) -> str | None:
    """Internal name (``a/b/C``) of a .class file, read from its constant pool; None when it is not one."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data[:4] != b"\xca\xfe\xba\xbe" or len(data) < 10:
        return None
    try:
        count = struct.unpack_from(">H", data, 8)[0]
        pos, index = 10, 1
        utf8: dict[int, bytes] = {}
        classes: dict[int, int] = {}
        while index < count:
            tag = data[pos]
            pos += 1
            if tag == 1:  # Utf8
                size = struct.unpack_from(">H", data, pos)[0]
                utf8[index] = data[pos + 2:pos + 2 + size]
                pos += 2 + size
            elif tag == 7:  # Class
                classes[index] = struct.unpack_from(">H", data, pos)[0]
                pos += 2
            elif tag in (8, 16, 19, 20):  # String, MethodType, Module, Package
                pos += 2
            elif tag == 15:  # MethodHandle
                pos += 3
            elif tag in (3, 4, 9, 10, 11, 12, 17, 18):  # Integer, Float, member refs, NameAndType, (Invoke)Dynamic
                pos += 4
            elif tag in (5, 6):  # Long, Double: two slots
                pos += 8
                index += 1
            else:
                return None
            index += 1
        this = struct.unpack_from(">H", data, pos + 2)[0]  # after access_flags
        return utf8[classes[this]].decode("utf-8", errors="replace")
    except (struct.error, IndexError, KeyError):
        return None


def scan(install: Path) -> tuple[list[Path], dict[str, list[str]]]:
    """(jars largest first, class file names per folder relative to the install) outside bundled JREs."""
    jars: list[Path] = []
    class_dirs: dict[str, list[str]] = {}
    for dirpath, dirs, files in os.walk(install):
        rel = Path(dirpath).relative_to(install).as_posix()
        if rel != "." and SKIP_DIRS.search(rel):
            dirs[:] = []
            continue
        jar_ok = rel == "." or rel.count("/") < JAR_DEPTH
        for name in files:
            low = name.lower()
            if low.endswith(".jar") and jar_ok:
                jars.append(Path(dirpath) / name)
            elif low.endswith(".class"):
                class_dirs.setdefault(rel, []).append(name)
    return sorted(jars, key=lambda p: -p.stat().st_size), class_dirs


def class_groups(install: Path, class_dirs: dict[str, list[str]]) -> dict[tuple[str, str], list[str]]:
    """(classpath root, top-level package) -> class files relative to the install. A folder's root comes from
    the package of its first readable class file; a folder whose path does not end with that package (or that
    holds default-package classes) is its own root, with package ``""``."""
    groups: dict[tuple[str, str], list[str]] = {}
    for folder, names in sorted(class_dirs.items()):
        names = sorted(names)
        internal = None
        for name in names:
            internal = class_name(install / folder / name)
            if internal:
                break
        package = internal.rsplit("/", 1)[0].split("/") if internal and "/" in internal else []
        parts = [] if folder == "." else folder.split("/")
        tail = [p.lower() for p in parts[len(parts) - len(package):]] if package else []
        if package and len(parts) >= len(package) and tail == [p.lower() for p in package]:
            root, top = "/".join(parts[:len(parts) - len(package)]) or ".", parts[len(parts) - len(package)]
        else:
            root, top = folder, ""
        groups.setdefault((root, top), []).extend(name if folder == "." else f"{folder}/{name}" for name in names)
    return groups


def units(install: Path, engine_key: str | None = None) -> list[dict]:
    """Every unit of work: jars (largest first) then class trees. ``game`` marks the ones decompiled by default:
    a jar with game package roots (or the largest jar of a Java game), a class tree outside library namespaces."""
    install = Path(install)
    jars, class_dirs = scan(install)
    out: list[dict] = []
    used = {CLASS_OUT}
    for i, jar in enumerate(jars):
        rel = jar.relative_to(install).as_posix()
        try:
            info, error = jar_roots(jar), None
        except (zipfile.BadZipFile, OSError, KeyError) as exc:
            info, error = {"counts": {}, "default_package_classes": 0, "main": None}, f"{exc.__class__.__name__}: {exc}"
        roots = game_roots(info)
        name = jar.stem
        if name.lower() in used:
            name = f"{jar.stem}-{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:8]}"
        used.add(name.lower())
        out.append({"id": rel, "kind": "jar", "name": jar.stem, "out": name, "relpaths": [rel], "size": jar.stat().st_size,
                    "game": bool(roots) or (i == 0 and engine_key == "java" and error is None), "roots": roots,
                    "main": info.get("main"), "classes": sum(info["counts"].values()) + info["default_package_classes"],
                    "library_roots": sorted(r for r in info["counts"] if r in LIBRARY_ROOTS), "error": error})
    for (root, top), files in sorted(class_groups(install, class_dirs).items()):
        base = CLASS_OUT if root == "." else f"{CLASS_OUT}/{root}"
        label = top or "(default)"
        out.append({"id": (f"{root}/" if root != "." else "") + f"{label}/", "kind": "classes", "name": label,
                    "root": root, "package": top, "out": f"{base}/{top or '_default'}", "relpaths": files,
                    "size": sum((install / f).stat().st_size for f in files), "game": top not in LIBRARY_ROOTS,
                    "roots": [], "classes": len(files)})
    return out


def wanted(unit: dict, options: dict) -> bool:
    """Is this unit decompiled with these options (game code, ``java.all``, or named in ``java.units``)?"""
    if unit.get("game") or _truthy(options.get("java.all")):
        return True
    names = {n.lower() for n in _csv(options.get("java.units"))}
    keys = {str(unit.get(k) or "").lower() for k in ("id", "name", "out", "package")}
    keys.add(Path(str(unit.get("id") or "")).stem.lower())
    return bool(names & (keys - {""}))


def prefixes_for(unit: dict, options: dict) -> list[str]:
    """Vineflower ``--only`` prefixes ([] = every class): ``java.all``, else ``java.filter``, else the jar's game roots."""
    if _truthy(options.get("java.all")):
        return []
    chosen = [p.replace("\\", "/").replace(".", "/") for p in _csv(options.get("java.filter"))]
    if chosen:
        return chosen
    return list(unit.get("roots") or []) if unit.get("kind") == "jar" else []


def run_unit(install: Path, unit: dict, staging: Path, prefixes: list[str], threads: int | None = None) -> dict:
    """Vineflower on one unit into ``staging/out``. Returns the manifest facts; ``matched`` = classes under the
    prefixes (0 = nothing to decompile, Vineflower was not started)."""
    java = tools.bin_path("temurin21")
    vf = tools.bin_path("vineflower")
    if java is None or vf is None:
        raise GsError("Vineflower and a JDK are needed (gst tools install vineflower)")
    install = Path(install)
    out = staging / "out"
    out.mkdir(parents=True, exist_ok=True)
    if unit["kind"] == "jar":
        source = install / unit["relpaths"][0]
        with zipfile.ZipFile(source) as z:
            names = [n for n in z.namelist() if n.endswith(".class")]
        origin = str(source)
    else:
        root = install if unit["root"] == "." else install / unit["root"]
        source = staging / "classes.jar"
        names = []
        with zipfile.ZipFile(source, "w", zipfile.ZIP_STORED) as z:
            for rel in unit["relpaths"]:
                arcname = (install / rel).relative_to(root).as_posix()
                z.write(install / rel, arcname)
                names.append(arcname)
        origin = str(root / unit["package"]) if unit["package"] else str(root)
    matched = sum(1 for n in names if not prefixes or n.startswith(tuple(prefixes)))
    facts = {"source": origin, "kind": unit["kind"], "only": prefixes, "classes": len(names), "matched": matched,
             "files": 0, "exit_code": None, "seconds": 0.0, "command": None, "log": "",
             "skipped_roots": sorted({n.split("/", 1)[0] for n in names
                                      if "/" in n and prefixes and not n.startswith(tuple(prefixes))})}
    if matched:
        cmd = command(java, vf, source, out, prefixes, threads or os.cpu_count() or 4)
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL)
        facts.update({"exit_code": proc.returncode, "seconds": round(time.time() - t0, 1), "command": cmd,
                      "files": sum(1 for _ in out.rglob("*.java")),
                      "log": f"$ {subprocess.list2cmdline(cmd)}\n\n{proc.stdout or ''}{proc.stderr or ''}"})
    if unit["kind"] != "jar":
        source.unlink(missing_ok=True)
    return facts


def decompile(ws: Path, game: dict, *, jars: list[str] | None = None, only: list[str] | None = None,
              everything: bool = False, threads: int | None = None, force: bool = False) -> dict:
    """``gst decompile java``: the ``code.java`` extractor in this process (same units, outputs and manifest as
    ``gst study``); a unit whose recorded fingerprint did not change is reused unless ``force``."""
    from gst.extractors import javalua

    options: dict = {}
    if only:
        options["java.filter"] = ",".join(only)
    if everything:
        options["java.all"] = "1"
    if jars:
        options["java.units"] = ",".join(jars)
    if threads:
        options["java.threads"] = str(threads)
    names = {n.lower() for n in jars or []}

    def chosen(unit) -> bool:
        keys = {str(unit.meta.get(k) or "").lower() for k in ("name", "out", "package")}
        return not names or bool(names & (keys | {unit.id.lower(), Path(unit.id).stem.lower()}))

    rows = javalua.run_inprocess(ws, game, javalua.JavaCode, options, select=chosen, force=force)
    if not rows:
        raise GsError(f"no jar or class package named {', '.join(sorted(names))} under {game['install_dir']}" if names
                      else f"no .jar or .class files found under {game['install_dir']}")
    blocked = [r for r in rows if r["status"] == "blocked-policy"]
    if blocked and not any(r["status"] in ("done", "failed") for r in rows):
        raise GsError(blocked[0]["error"], 3)
    results = {r["out"]: {**r["facts"], "reused": r["reused"], "status": r["status"]}
               for r in rows if r["status"] in ("done", "failed")}
    return {"out": str(ws / OUT_DIR), "jars": results,
            "skipped": [{"unit": r["unit"], "why": "; ".join(r["notes"])} for r in rows if r["status"] == "skipped"],
            "failed": [r["out"] for r in rows if r["status"] in ("failed", "blocked-policy")]}
