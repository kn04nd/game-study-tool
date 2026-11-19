"""Native modules (C/C++ exe and dll) -> Ghidra headless -> decomp/ghidra/<module>/.

Each module is copied into the workspace and imported into its own Ghidra project
(`decomp/ghidra/_projects/<module>/`), analyzed with the PDB that ships next to it (never fetched from a
symbol server: GsPdbPrescript.java), then GsExport.java writes decompiled C grouped by namespace with
`.map.json` sidecars (functions, exact call sites) that `gst index` reads, plus functions/calls/imports/
strings as JSONL. Big binaries decompile the named functions first, up to --max-functions; the rest are
listed with their call edges and can be decompiled later with --functions <regex> (no re-analysis).
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from gst import policy, tools
from gst.common import GsError, read_json, write_json, write_text
from gst.decompile.dotnet import is_managed, sha256_of

OUT_DIR = Path("decomp") / "ghidra"
SCRIPT_VERSION = 4    # bump when GsExport.java output changes: re-export (the analysis is reused). 4: no caps
ANALYSIS_VERSION = 2  # bump when the analysis options change (GsPdbPrescript.java, JVM flags): re-analyze
JAVA_OPTIONS = "-Dghidra.pdb.developerMode=true"  # exposes the PDB "Class Hierarchy" layout to the prescript
DEFAULT_MAX_FUNCTIONS = 0  # 0 = every function
LARGE = 64 << 20  # flagged as large (hours, GBs of RAM) but analyzed like any other
MIN_SIZE = 32 << 10

# Stems of engine runtimes, system/middleware and anti-cheat modules: not the game's own code.
THIRD_PARTY = re.compile(
    r"^(?:steam_api\w*|steamclient\w*|steamworks\w*|galaxy\w*|eossdk[-\w]*|discord\w*|"
    r"d3dcompiler\w*|dxcompiler|dxil|d3d\d+\w*|dxgi|nvngx\w*|nvapi\w*|amd_\w+|amdxc\w*|ffx_\w+|sl\.\w+|vulkan-1|"
    r"openvr_api|openxr_loader|nvtoolsext\w*|gfsdk\w*|physx\w*|apex\w*|flex\w*|tbb\w*|embree\w*|"
    r"fmod\w*|aksoundengine\w*|wwise\w*|bink\w*|oo2core\w*|oo2net\w*|granny\w*|openal32|xaudio\w*|libogg\w*|libvorbis\w*|"
    r"msvcp\w*|vcruntime\w*|ucrtbase\w*|api-ms-\S+|concrt\w*|mfc\d+\w*|vccorlib\w*|vcomp\w*|libgcc\w*|libstdc\w*|"
    r"libwinpthread\w*|unityplayer|unitycrashhandler\w*|mono-2\.0-\w+|monobleedingedge|baselib|gameassembly|"
    r"libcef|chrome_elf|cefsharp\w*|libegl|libglesv2|icu\w*|crashpad_handler|crashreportclient|unrealcefsubprocess|"
    r"epicwebhelper|sentry\w*|bugsplat\w*|breakpad\w*|easyanticheat\w*|eac\w*|beclient\w*|battleye\w*|"
    r"sdl2|glfw3|zlib\w*|libcurl\w*|libssl\w*|libcrypto\w*|ssleay\w*|libeay\w*|lua5\w*|luajit\w*|python\d+\w*|"
    r"xinput\w*|dinput8|dsound|winmm|version|dbghelp|7za?|unins\d+|vc_?redist\w*|dxsetup|ue4prereqsetup\w*|"
    r"vk_swiftshader|vulkan_swiftshader|swiftshader\w*|ffmpeg\w*|av(?:codec|format|util)\w*|swresample\w*|"
    r"monoposixhelper|c?imgui\w*|resonanceaudio|sdkencryptedappticket\w*|gfsdk\w*|nvrtc\w*|cudart\w*)$",
    re.IGNORECASE,
)


# --- discovery ------------------------------------------------------------------------------------

def find_modules(install: Path, game: dict | None = None) -> list[dict]:
    """Native (unmanaged) PE modules of the install; `game` marks the ones analyzed by default."""
    from gst.recon.index import FileIndex

    ix = FileIndex(install)
    exes = {e.lower() for e in (game or {}).get("exes", [])}
    engine = str(((game or {}).get("engine") or {}).get("key") or "native")
    stub_exes = engine.startswith(("unity", "godot", "java")) or engine in {"xna", "rpgmaker-mvmz"}
    found = []
    for rel in ix.files:
        if not rel.endswith((".exe", ".dll")) or ix.sizes.get(rel, 0) < MIN_SIZE:
            continue
        stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        path = ix.path(rel)
        if is_managed(path):
            continue
        # .NET apphost: a generic native launcher next to the managed <name>.dll
        apphost = rel.endswith(".exe") and path.with_name(f"{path.stem}.runtimeconfig.json").is_file()
        third_party = bool(THIRD_PARTY.match(stem)) or apphost
        has_pdb = path.with_suffix(".pdb").is_file() and not apphost  # an apphost's .pdb belongs to the managed dll
        size = ix.sizes.get(rel, 0)
        own_exe = rel.endswith(".exe") and path.name.lower() in exes and not stub_exes
        game_module = not third_party and (has_pdb or own_exe)
        found.append({"name": path.name, "path": str(path), "rel": rel, "size": size, "pdb": has_pdb,
                      "game": game_module, "large": size > LARGE, "third_party": third_party})
    found.sort(key=lambda m: (not m["game"], m["third_party"], m["rel"]))
    return found


def select(found: list[dict], names: list[str] | None = None) -> list[dict]:
    if names:
        by_name = {m["name"].lower(): m for m in found} | {m["name"].rsplit(".", 1)[0].lower(): m for m in found}
        unknown = [n for n in names if n.lower() not in by_name]
        if unknown:
            raise GsError(f"not a native module of this game: {', '.join(unknown)} (gst decompile native --list)")
        return [by_name[n.lower()] for n in names]
    return [m for m in found if m["game"]]


def safe_name(name: str) -> str:
    """Module file name usable on a cmd.exe command line and as a folder name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "module"


# --- running Ghidra -------------------------------------------------------------------------------

def _memory_gb() -> int:
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_avail", ctypes.c_ulonglong),
                    ("virtual_total", ctypes.c_ulonglong), ("virtual_avail", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]

    try:
        status = Status()
        status.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return max(1, status.total >> 30)
    except (AttributeError, OSError):  # pragma: no cover - non-Windows
        return 8


def environment() -> dict[str, str]:
    java = tools.bin_path("temurin21")
    if java is None:
        raise GsError("the JDK for Ghidra is not installed (gst tools install temurin21)")
    env = dict(os.environ)
    env["JAVA_HOME"] = str(Path(java).parent.parent)
    env.setdefault("GHIDRA_HEADLESS_MAXMEM", f"{min(16, max(2, _memory_gb() // 2))}G")
    env["GHIDRA_HEADLESS_JAVA_OPTIONS"] = f"{env.get('GHIDRA_HEADLESS_JAVA_OPTIONS', '')} {JAVA_OPTIONS}".strip()
    return env


def scripts_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "ghidra_scripts"


def command(headless: Path, project: Path, module: Path, out: Path, *, pdb: Path | None, max_functions: int,
            seconds: int, only_file: Path | None, analysis_timeout: int | None, reuse: bool, log: Path) -> list[str]:
    """analyzeHeadless arguments. Every path is gs-made (no cmd.exe metacharacters); the regex goes in a file."""
    cmd = [str(headless), str(project.parent), project.name]
    if reuse:
        cmd += ["-process", module.name, "-noanalysis", "-readOnly"]
    else:
        cmd += ["-import", str(module), "-overwrite", "-preScript", "GsPdbPrescript.java", str(pdb) if pdb else "-"]
        if analysis_timeout:  # only when asked: Ghidra analyzes to completion by default
            cmd += ["-analysisTimeoutPerFile", str(analysis_timeout)]
    cmd += ["-scriptPath", str(scripts_dir()),
            "-postScript", "GsExport.java", str(out), str(max_functions), str(seconds), f"@{only_file}" if only_file else "-",
            "-max-cpu", str(max(1, (os.cpu_count() or 4) - 2)), "-log", str(log), "-scriptlog", str(log.with_suffix(".script.log"))]
    return cmd


def _run(cmd: list[str], env: dict, console: Path, timeout: float | None) -> int:
    with open(console, "w", encoding="utf-8", errors="replace") as fh:
        try:
            return subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                  timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            fh.write(f"\ngs: timed out after {timeout} s\n")
            return -1


def decompile(ws: Path, game: dict, *, names: list[str] | None = None, max_functions: int = DEFAULT_MAX_FUNCTIONS,
              only: str | None = None, seconds: int = 0, analysis_timeout: int | None = None, force: bool = False) -> dict:
    headless = tools.bin_path("ghidra")
    if headless is None:
        raise GsError("Ghidra is not installed (gst tools install ghidra)")
    env = environment()
    install = Path(game["install_dir"])
    found = find_modules(install, game)
    chosen = select(found, names)
    if not chosen:
        raise GsError("no native game module recognized (none ships a PDB): name one or more (gst decompile native --list)")
    for module in chosen:
        decision = policy.check(game, "decompile", module["path"])
        if not decision.allowed:
            raise GsError(f"{decision.reason} (guardrails.md rule {decision.rule})", 3)
    if only:
        re.compile(only)

    root = ws / OUT_DIR
    root.mkdir(parents=True, exist_ok=True)
    (ws / "logs").mkdir(exist_ok=True)
    manifest_path = root / "MANIFEST.json"
    manifest = read_json(manifest_path, {}) or {}
    previous = manifest.get("modules", {})
    results: dict[str, dict] = {}
    for module in chosen:
        name = safe_name(module["name"])
        source = Path(module["path"])
        sha = sha256_of(source)
        prev = previous.get(name) or {}
        out = root / name
        analyzed = prev.get("sha256") == sha and prev.get("analysis_version") == ANALYSIS_VERSION
        same_request = (prev.get("script_version") == SCRIPT_VERSION and prev.get("max_functions") == max_functions
                        and prev.get("only") == only and prev.get("function_timeout", 30) == seconds)
        if not force and analyzed and same_request and (out / "meta.json").is_file():
            results[name] = {**prev, "reused": True}
            continue
        project = root / "_projects" / name / name
        reuse = not force and analyzed and (project.parent / f"{name}.gpr").is_file()
        work = root / "_input"
        work.mkdir(exist_ok=True)
        local = work / name
        pdb = None
        if not reuse:
            shutil.copy2(source, local)  # the install stays untouched; gs-made path for the command line
            if module["pdb"]:
                pdb = work / (Path(name).stem + ".pdb")
                shutil.copy2(source.with_suffix(".pdb"), pdb)
            if project.parent.exists():
                shutil.rmtree(project.parent)
        project.parent.mkdir(parents=True, exist_ok=True)
        only_file = None
        if only:
            only_file = work / f"{name}.only.txt"
            write_text(only_file, only + "\n")
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        log = ws / "logs" / f"ghidra-{name}.log"
        cmd = command(Path(headless), project, local, out, pdb=pdb, max_functions=max_functions, seconds=seconds,
                      only_file=only_file, analysis_timeout=analysis_timeout, reuse=reuse, log=log)
        what = "re-exporting" if reuse else "analyzing"
        sys.stderr.write(f"gst: Ghidra {what} {module['name']} ({module['size'] >> 10} KB"
                         f"{', with its PDB' if pdb else ''}); minutes for small modules, hours for big ones...\n")
        t0 = time.time()
        code = _run(cmd, env, ws / "logs" / f"ghidra-{name}.console.txt", analysis_timeout + 7200 if analysis_timeout else None)
        for leftover in (local, pdb, only_file):
            if leftover is not None and leftover.exists():
                leftover.unlink()
        meta = read_json(out / "meta.json", None)
        if meta is None:
            results[name] = {"source": str(source), "sha256": sha, "exit_code": code, "failed": True,
                             "seconds": round(time.time() - t0, 1), "log": str(log)}
            continue
        results[name] = {"source": str(source), "sha256": sha, "pdb": bool(meta.get("pdb_loaded")),
                         "functions": meta.get("functions"), "decompiled": meta.get("decompiled"),
                         "listed": meta.get("listed"), "failed_functions": meta.get("failed"), "imports": meta.get("imports"),
                         "strings": meta.get("strings"), "max_functions": max_functions, "only": only,
                         "function_timeout": seconds,
                         "script_version": SCRIPT_VERSION, "analysis_version": ANALYSIS_VERSION,
                         "ghidra": meta.get("ghidra"), "exit_code": code,
                         "seconds": round(time.time() - t0, 1), "reused_analysis": reuse, "reused": False}
    work = root / "_input"
    if work.is_dir() and not any(work.iterdir()):
        work.rmdir()
    manifest = {"backend": "native", "tool": "ghidra", "tool_version": tools.installed_version(tools.get("ghidra")),
                "game_build": game.get("buildid"), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "modules": {**previous, **{k: {kk: vv for kk, vv in v.items() if kk != "reused"} for k, v in results.items()}}}
    write_json(manifest_path, manifest)
    skipped = [m["name"] for m in found if m not in chosen]
    return {"out": str(root), "modules": results, "skipped": skipped,
            "failed": [k for k, v in results.items() if v.get("failed")]}
