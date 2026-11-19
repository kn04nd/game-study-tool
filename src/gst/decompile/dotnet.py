"""Managed (.NET / Mono) assemblies -> one C# project per assembly, with ilspycmd.

Covers Unity Mono (`<Game>_Data/Managed`), Godot .NET (`data_<game>_*`), XNA/FNA/MonoGame and other
.NET games. Game assemblies are picked by default; framework, engine and well-known third-party
libraries are listed but skipped unless named or `--all`.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gst import policy, tools
from gst.common import GsError, read_json, write_json, write_text

OUT_DIR = Path("decomp") / "cs"

# File stems of framework, engine and widespread third-party assemblies: not the game's own code.
NOT_GAME = re.compile(
    r"^(?:mscorlib|netstandard|System(?:\..+)?|Microsoft\..+|Mono(?:\..+)?|WindowsBase|Presentation\w+|Accessibility|"
    r"SMDiagnostics|I18N(?:\..+)?|UnityEngine(?:\..+)?|Unity\..+|UnityEditor.*|UnityScript.*|Boo\.Lang.*|"
    r"Assembly-UnityScript.*|GodotSharp\w*|Godot\..+|MonoGame\..+|FNA|Microsoft\.Xna\..+|"
    r"0Harmony|Harmony\w*|Newtonsoft\..+|Mono\.Cecil.*|BepInEx.*|MelonLoader.*|Steamworks.*|com\.rlabrecque\..+|"
    r"Facepunch\..+|FMOD\w*|ImGui\w*(?:\.NET)?|Ionic\..+|DOTween\w*|DemiLib|Rewired\w*|Sirenix\..+|BouncyCastle.*|"
    r"Photon\w*|Mirror\w*|Cinemachine\w*|protobuf-net.*|Google\..+|ICSharpCode\..+|YamlDotNet|VYaml.*|LibNoise\w*|"
    r"ArabicSupport|Discord\w*|EOS\w*|EpicOnlineServices?|NAudio\w*|Sentry\w*|SharpZipLib|ZString|UniTask\w*|"
    r"nunit\..+|Ookii\..+|Febucci\w*|AstarPathfindingProject|Purchasing\.Common|websocket-sharp|LiteNetLib|"
    r"KinematicCharacterController|Zenject|UniRx\w*|Spine\..+|MessagePack.*|Cysharp\..+)$",
    re.IGNORECASE,
)


# --- discovery ------------------------------------------------------------------------------------

def is_managed(path: Path) -> bool:
    """A PE file with a CLR runtime header (data directory 14): a .NET assembly."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    if head[:2] != b"MZ" or len(head) < 0x40:
        return False
    pe = int.from_bytes(head[0x3C:0x40], "little")
    if pe + 0x100 > len(head) or head[pe:pe + 4] != b"PE\0\0":
        return False
    opt = pe + 24
    magic = int.from_bytes(head[opt:opt + 2], "little")
    if magic not in (0x10B, 0x20B):
        return False
    count_at, dirs_at = (opt + 92, opt + 96) if magic == 0x10B else (opt + 108, opt + 112)
    if int.from_bytes(head[count_at:count_at + 4], "little") < 15:
        return False
    clr = dirs_at + 14 * 8
    return int.from_bytes(head[clr:clr + 4], "little") != 0 and int.from_bytes(head[clr + 4:clr + 8], "little") != 0


def assembly_dirs(install: Path) -> list[Path]:
    """Folders that hold the game's managed assemblies, most specific first."""
    dirs = sorted(install.glob("*_Data/Managed"))                                          # Unity Mono
    dirs += sorted(d for d in install.glob("data_*") if d.is_dir() and any(d.glob("GodotSharp*.dll")))  # Godot .NET
    if not dirs:
        dirs = [install] + sorted(d for d in install.iterdir() if d.is_dir() and d.name.lower() in {"bin", "x64", "managed"})
        apps = {p.parent for pattern in ("*/*.runtimeconfig.json", "*/*/*.runtimeconfig.json") for p in install.glob(pattern)}
        dirs += sorted(apps - set(dirs))  # .NET (Core) apps in subfolders: tools/, launcher/...
    return dirs


def project_assemblies(folder: Path) -> set[str] | None:
    """Names of the app's own projects from `<App>.deps.json` (.NET Core/5+: type "project" vs NuGet
    "package"), lower-cased; None when there is no deps file."""
    names: set[str] = set()
    found = False
    for deps in folder.glob("*.deps.json"):
        try:
            data = json.loads(deps.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        found = True
        for key, lib in (data.get("libraries") or {}).items():
            if isinstance(lib, dict) and lib.get("type") == "project":
                names.add(key.split("/", 1)[0].lower())
    return names if found else None


def find_assemblies(install: Path) -> list[dict]:
    found, seen = [], set()
    for folder in assembly_dirs(install):
        projects = project_assemblies(folder)
        for path in sorted(list(folder.glob("*.dll")) + list(folder.glob("*.exe"))):
            key = path.name.lower()
            if key in seen or not is_managed(path):
                continue
            seen.add(key)
            pdb = path.with_suffix(".pdb")
            game = path.stem.lower() in projects if projects else not NOT_GAME.match(path.stem)
            found.append({"name": path.stem, "path": str(path), "size": path.stat().st_size, "game": game, "pdb": pdb.is_file()})
    return found


def select(found: list[dict], names: list[str] | None = None, everything: bool = False) -> list[dict]:
    if names:
        by_name = {a["name"].lower(): a for a in found} | {Path(a["path"]).name.lower(): a for a in found}
        unknown = [n for n in names if n.lower() not in by_name]
        if unknown:
            raise GsError(f"not a managed assembly of this game: {', '.join(unknown)} (gst decompile dotnet --list)")
        return [by_name[n.lower()] for n in names]
    return list(found) if everything else [a for a in found if a["game"]]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- running ilspycmd -----------------------------------------------------------------------------

def command(exe: Path, dll: Path, out: Path, *, pdb: bool) -> list[str]:
    cmd = [str(exe), "-p", "--nested-directories", "--disable-updatecheck", "-lv", "Latest",
           "-r", str(dll.parent), "--ignore-decompilation-errors", "-o", str(out)]
    if pdb:
        cmd.append("--use-varnames-from-pdb")
    return cmd + [str(dll)]


def _run_one(exe: Path, assembly: dict, out_root: Path, timeout: float | None = None) -> dict:
    dll, out = Path(assembly["path"]), out_root / assembly["name"]
    if out.exists():
        shutil.rmtree(out)
    cmd = command(exe, dll, out, pdb=assembly["pdb"])
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
        code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, stdout, stderr = -1, exc.stdout or "", f"timed out after {timeout}s (--timeout)"
    write_text(out_root / f"{assembly['name']}.log", f"$ {' '.join(cmd)}\n\n{stdout}\n{stderr}")
    files = sum(1 for _ in out.rglob("*.cs")) if out.exists() else 0
    errors = len(re.findall(r"(?im)^\s*(?:error|exception|failed)\b", stderr or ""))
    return {"source": str(dll), "size": assembly["size"], "pdb": assembly["pdb"], "files": files,
            "errors": errors, "exit_code": code, "seconds": round(time.time() - t0, 1), "command": cmd}


def decompile(ws: Path, game: dict, *, names: list[str] | None = None, everything: bool = False,
              jobs: int = 2, force: bool = False) -> dict:
    tool = tools.get("ilspycmd")
    exe = tools.bin_path(tool)
    if exe is None:
        raise GsError("ilspycmd is not installed (gst tools install ilspycmd)")
    install = Path(game["install_dir"])
    found = find_assemblies(install)
    if not found:
        raise GsError(f"no .NET assemblies found under {install} (this engine may need another backend)")
    chosen = select(found, names, everything)
    if not chosen:
        raise GsError("no game assemblies recognized; name them (gst decompile dotnet <Name> ...) or use --all")
    for assembly in chosen:
        decision = policy.check(game, "decompile", assembly["path"])
        if not decision.allowed:
            raise GsError(f"{decision.reason} (guardrails.md rule {decision.rule})", 3)

    out_root = ws / OUT_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / "MANIFEST.json"
    manifest = read_json(manifest_path, {}) or {}
    previous = manifest.get("assemblies", {})
    results, todo = {}, []
    for assembly in chosen:
        assembly["sha256"] = sha256_of(Path(assembly["path"]))
        prev = previous.get(assembly["name"])
        if not force and prev and prev.get("sha256") == assembly["sha256"] and (out_root / assembly["name"]).is_dir():
            results[assembly["name"]] = {**prev, "reused": True}
        else:
            todo.append(assembly)
    for assembly in todo:
        sys.stderr.write(f"gst: decompiling {assembly['name']} ({assembly['size'] // 1024} KB)...\n")
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for assembly, result in zip(todo, pool.map(lambda a: _run_one(exe, a, out_root), todo)):
            results[assembly["name"]] = {**result, "sha256": assembly["sha256"], "reused": False}
    manifest = {
        "backend": "dotnet", "tool": "ilspycmd", "tool_version": tools.installed_version(tool),
        "game_build": game.get("buildid"), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "assemblies": {**previous, **{k: {kk: vv for kk, vv in v.items() if kk != "reused"} for k, v in results.items()}},
    }
    write_json(manifest_path, manifest)
    skipped = [a["name"] for a in found if a not in chosen]
    return {"out": str(out_root), "assemblies": results, "skipped": skipped,
            "failed": [k for k, v in results.items() if v.get("exit_code") not in (0, None) and not v.get("files")]}
