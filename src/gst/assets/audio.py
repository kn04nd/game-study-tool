"""Game audio containers (FMOD .bank/.fsb, Wwise .wem/.bnk, CRI .acb/.awb/.hca, XACT, ...) -> WAV, with
vgmstream-cli. Any engine. `streams` lists the subsongs of a container (names, durations) in one call;
`convert` writes `assets/audio/<container>/<stream>.wav` plus a `_folder.gs.json` sidecar."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from gst import tools
from gst.assets import FOLDER_SIDECAR, assets_dir, safe_name, sha256_file
from gst.common import GsError, write_json

# Wwise .pck packages are left out of the default scan (Godot games use .pck for everything): pass them by pattern.
AUDIO_EXT = {".bank", ".fsb", ".wem", ".bnk", ".acb", ".awb", ".hca", ".adx", ".xwb", ".xwm", ".at9", ".sab", ".sob"}


def vgmstream() -> Path:
    exe = tools.bin_path("vgmstream")
    if exe is None:
        raise GsError("vgmstream-cli is not installed (gst tools install vgmstream)")
    return exe


def find(install: Path, patterns: list[str] | None = None) -> list[Path]:
    """Audio containers: glob patterns relative to the install or absolute (e.g. files extracted into the
    workspace), or every known extension in the install."""
    import glob

    if patterns:
        found = []
        for pattern in patterns:
            if Path(pattern).is_absolute():  # Path.glob refuses absolute patterns
                found += sorted(Path(hit) for hit in glob.glob(pattern, recursive=True))
            else:
                found += sorted(install.glob(pattern))
        return [f for f in found if f.is_file()]
    return sorted(f for f in install.rglob("*") if f.suffix.lower() in AUDIO_EXT and f.is_file()
                  and not f.name.lower().endswith(".strings.bank"))


def streams(path: Path, timeout: float | None = None) -> list[dict]:
    """Every subsong of a container: index, name, seconds, channels, encoding (one vgmstream call)."""
    proc = subprocess.run([str(vgmstream()), "-I", "-m", "-S", "0", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    out = []
    for line in proc.stdout.splitlines():
        if not line.startswith("{"):
            continue
        info = json.loads(line)
        stream = info.get("streamInfo") or {}
        rate = info.get("sampleRate") or 1
        out.append({"index": stream.get("index", 1) or 1, "name": stream.get("name") or path.stem,
                    "seconds": round((info.get("numberOfSamples") or 0) / rate, 2),
                    "channels": info.get("channels"), "encoding": info.get("encoding")})
    return out


def convert(ws: Path, files: list[Path], *, name: str | None = None, limit: int | None = None,
            out_root: Path | None = None, build: str | None = None) -> dict:
    t0 = time.time()
    exe = vgmstream()
    label = f"vgmstream {tools.installed_version(tools.get('vgmstream')) or ''}".strip()
    rx = re.compile(name, re.I) if name else None
    total, done, failed = 0, {}, []
    budget = limit
    for container in files:
        listed = streams(container)
        chosen = [s for s in listed if not rx or rx.search(s["name"])]
        total += len(chosen)
        if budget is not None:
            chosen = chosen[:max(0, budget)]
            budget -= len(chosen)
        if not chosen:
            continue
        folder = (out_root or assets_dir(ws) / "audio") / safe_name(container.stem, 60)
        folder.mkdir(parents=True, exist_ok=True)
        side = folder / FOLDER_SIDECAR
        previous = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
        by_index: dict[str, str] = previous.get("streams", {})  # subsong index -> file: re-runs skip what exists
        taken = set(by_index.values())
        written, size = 0, 0
        for s in chosen:
            key = str(s["index"])
            if key in by_index and (folder / by_index[key]).exists():
                continue
            target = folder / f"{safe_name(s['name'], 70)}.wav"
            if target.name in taken:  # another stream with the same name
                target = folder / f"{safe_name(s['name'], 60)}__{s['index']}.wav"
            proc = subprocess.run([str(exe), "-i", "-s", key, "-o", str(target), str(container)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  stdin=subprocess.DEVNULL)
            if proc.returncode != 0 or not target.exists():
                failed.append(f"{container.name}#{s['index']} {s['name']}: {(proc.stderr or proc.stdout).strip()}")
                continue
            by_index[key] = target.name
            taken.add(target.name)
            written += 1
            size += target.stat().st_size
        names = sorted(by_index.values())
        from gst.assets import write_folder_sidecar

        tool_name, _, tool_version = label.partition(" ")
        write_folder_sidecar(folder, {"source": str(container), "res_path": container.name, "source_sha256": sha256_file(container),
                                      "tool": tool_name, "tool_version": tool_version, "build": build,
                                      "streams_in_source": len(listed), "count": len(names),
                                      "bytes": previous.get("bytes", 0) + size, "streams": by_index, "examples": names[:6]})
        done[container.name] = written
    return {"matched": total, "exported": done, "failed": failed, "failures": len(failed),
            "limited": limit is not None and total > sum(done.values()) + len(failed), "seconds": round(time.time() - t0, 1)}
