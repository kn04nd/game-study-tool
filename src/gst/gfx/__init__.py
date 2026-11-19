"""Graphics study (`gst gfx`): frame captures with RenderDoc and their summary (`frame.md`).

RenderDoc injects into the game: captures run only from the sandbox copy (guardrails rule 5) and only for
games whose verdict allows runtime work. Its Python API runs inside qrenderdoc (`--python`, before the UI
opens; gst's scripts exit by themselves). gst keeps RenderDoc's analytics and update checks off and never
enables callstack capture (it would query symbol servers).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from gst import policy, tools
from gst.common import GsError, human_size, read_json, write_text

SCRIPTS = Path(__file__).resolve().parent.parent / "data" / "renderdoc_scripts"
PRIVACY = {"Analytics_TotalOptOut": True, "CheckUpdate_AllowChecks": False, "Tips_HasSeenFirst": True}


def renderdoc() -> tuple[Path, Path]:
    cmd, ui = tools.bin_path("renderdoc", "renderdoccmd.exe"), tools.bin_path("renderdoc", "qrenderdoc.exe")
    if cmd is None or ui is None:
        raise GsError("RenderDoc is not installed (gst tools install renderdoc)")
    return cmd, ui


def prepare_config() -> Path:
    """Analytics and update checks off, first-run tips seen: no dialog blocks a scripted run."""
    path = Path(os.environ.get("APPDATA", str(Path.home()))) / "qrenderdoc" / "UI.config"
    config = read_json(path, {}) or {}
    if any(config.get(k) != v for k, v in PRIVACY.items()):
        config.update(PRIVACY)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=4), encoding="utf-8")
    return path


def run_script(script: str, env: dict[str, str], timeout: int) -> None:
    """qrenderdoc --python <script>; the script exits the process. Stopped by its pid after ``timeout``."""
    _cmd, ui = renderdoc()
    prepare_config()
    proc = subprocess.Popen([str(ui), "--python", str(SCRIPTS / script)], env={**os.environ, **env},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()  # this exact pid: the qrenderdoc we started
        proc.wait(timeout=30)
        raise GsError(f"RenderDoc did not finish {script} within {timeout} s") from None


def sandbox_exe(ws: Path, game: dict, exe: str | None = None) -> Path:
    sandbox = ws / "sandbox"
    if not sandbox.is_dir() or not any(sandbox.iterdir()):
        raise GsError("RenderDoc injects into the game, so it runs only from the sandbox copy: gst sandbox create (rule 5)")
    names = [exe] if exe else list(game.get("exes") or [])
    for name in names:
        hits = sorted(sandbox.rglob(Path(name).name))
        if hits:
            return hits[0]
    raise GsError(f"no {' / '.join(names) or 'game executable'} in {sandbox} (pass --exe <name.exe>)")


def _check(game: dict, exe: Path) -> None:
    for action, target in (("runtime", None), ("inject", str(exe))):
        decision = policy.check(game, action, target)
        if not decision.allowed:
            raise GsError(f"{decision.reason} (guardrails.md rule {decision.rule})", 3)


def _stop(pid: int | None, sandbox: Path) -> bool:
    """Stop the game RenderDoc started: that exact pid, and only if it runs from the sandbox."""
    from gst import runtime
    from gst.common import is_under

    if not pid:
        return False
    for proc in runtime.processes():
        if proc["pid"] == pid and proc["path"] and is_under(proc["path"], sandbox):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30)
            return True
    return False


def capture(ws: Path, game: dict, *, after: float = 5.0, exe: str | None = None, args: str = "",
            manual: bool = False, timeout: int = 300) -> dict:
    path = sandbox_exe(ws, game, exe)
    _check(game, path)
    out = ws / "traces" / "gfx"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    template = out / stamp
    before = set(out.glob("*.rdc"))
    if manual:
        cmd, _ui = renderdoc()
        prepare_config()
        subprocess.run([str(cmd), "capture", "-w", "-c", str(template), "-d", str(path.parent), str(path), *args.split()],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=24 * 3600)
        captures = sorted(set(out.glob("*.rdc")) - before)
        return {"exe": str(path), "captures": [str(c) for c in captures], "manual": True}
    status = out / f"{stamp}.capture.json"
    run_script("gs_capture.py", {"GS_RD_EXE": str(path), "GS_RD_WORKDIR": str(path.parent), "GS_RD_ARGS": args,
                                 "GS_RD_CAPTURE": str(template), "GS_RD_AFTER": str(after), "GS_RD_OUT": str(out)},
               timeout)
    result = read_json(out / "capture.json", {}) or {}
    if (out / "capture.json").exists():
        (out / "capture.json").replace(status)  # keep the script's report next to the capture
    stopped = _stop(result.get("pid"), ws / "sandbox")
    if result.get("error") or not result.get("capture"):
        raise GsError("capture failed: " + (result.get("error") or "no capture").strip().splitlines()[-1])
    return {"exe": str(path), "captures": [result["capture"]], "api": result.get("api"), "pid": result.get("pid"),
            "stopped": stopped, "frame": result.get("frame")}


def export(ws: Path, capture_file: Path, *, max_images: int = 16, timeout: int = 900) -> dict:
    from PIL import Image

    from gst.assets import thumbnail

    if not capture_file.is_file():
        raise GsError(f"no capture at {capture_file}")
    out = capture_file.parent / capture_file.stem
    out.mkdir(parents=True, exist_ok=True)
    for stale in ("frame.json", "export.log"):
        (out / stale).unlink(missing_ok=True)
    t0 = time.time()
    run_script("gs_frame_export.py", {"GS_RD_CAPTURE": str(capture_file), "GS_RD_OUT": str(out),
                                      "GS_RD_MAX_IMAGES": str(max_images)}, timeout)
    frame = read_json(out / "frame.json", None)
    if frame is None:
        log = (out / "export.log").read_text(encoding="utf-8", errors="replace") if (out / "export.log").exists() else ""
        raise GsError("export failed: " + (log.strip().splitlines()[-1] if log.strip() else "no output"))
    for png in sorted((out / "targets").glob("*.png")):
        with Image.open(png) as image:
            thumbnail(image.convert("RGB"), out / "thumbs" / png.name)
    text = frame_markdown(frame, capture_file, ws)
    write_text(out / "frame.md", text)
    return {"out": str(out), "frame_md": str(out / "frame.md"), "passes": len(frame["passes"]),
            "draws": frame["draws"], "shaders": len(frame["shaders"]), "api": frame["api"],
            "seconds": round(time.time() - t0, 1)}


def frame_markdown(frame: dict, capture_file: Path, ws: Path, limit: int = 10_000) -> str:
    """The frame in at most ~10 KB: passes with targets and shaders, then the shaders, then the targets."""
    tex = frame["textures"]

    def target(key: str | None) -> str:
        if not key or key not in tex:
            return "-"
        t = tex[key]
        name = f" `{t['name']}`" if t["name"] and not t["name"].startswith(("Texture2D", "Swap")) else ""
        return f"{key}{name} {t['width']}x{t['height']} {t['format']}"

    lines = [f"# Frame: {capture_file.name}", "",
             f"- API {frame['api']}{' (degraded)' if frame['degraded'] else ''}; {frame['events']} actions: "
             f"{frame['draws']} draws, {frame['dispatches']} dispatches, {frame['clears']} clears; "
             f"{len(frame['passes'])} passes; {len(frame['shaders'])} shaders ({frame['disassembly']}).",
             f"- Capture: `{capture_file.relative_to(ws).as_posix() if capture_file.is_relative_to(ws) else capture_file}`; "
             "images in `targets/` (thumbnails in `thumbs/`), shader listings in `shaders/`.",
             f"- Final image: `{frame['final']}`" if frame["final"] else "- Final image: not saved", "",
             "## Passes", "", "| # | Events | Draws | Writes to | Depth | Shaders |", "| --- | --- | --- | --- | --- | --- |"]
    for p in frame["passes"]:
        shaders = ", ".join(f"{stage} {', '.join(ids)}" for stage, ids in p["shaders"].items())
        markers = f" ({' > '.join(p['markers'][-2:])})" if p["markers"] else ""
        lines.append(f"| {p['index']}{markers} | {p['first_eid']}-{p['last_eid']} | {p['draws']}"
                     f"{' (compute)' if p['dispatch'] else ''} | {'; '.join(target(o) for o in p['outputs']) or '-'} | "
                     f"{target(p['depth'])} | {shaders or '-'} |")
    lines += ["", "## Shaders", "", "| Id | Stage | Entry | Constant buffers | Textures | Lines |", "| --- | --- | --- | --- | --- | --- |"]
    for key, s in frame["shaders"].items():
        refl = s.get("reflection") or {}
        cbs = "; ".join(f"{cb['name']} ({cb['bytes']} B: {', '.join(cb['variables'][:6])})" for cb in refl.get("constant_blocks", []))
        lines.append(f"| {key} | {s['stage']} | {refl.get('entry', '?')} | {cbs or '-'} | "
                     f"{', '.join(refl.get('textures', [])) or '-'} | {s.get('lines', '-')} |")
    lines += ["", "## Render targets", "", "| Id | Name | Size | Format |", "| --- | --- | --- | --- |"]
    written = {o for p in frame["passes"] for o in p["outputs"]} | {p["depth"] for p in frame["passes"] if p["depth"]}
    for key in sorted(written, key=lambda k: int(k) if k.isdigit() else 0):
        if key in tex:
            t = tex[key]
            lines.append(f"| {key} | {t['name'] or '-'} | {t['width']}x{t['height']}{f' x{t["array"]}' if t['array'] > 1 else ''} "
                         f"| {t['format']}{f' MSAA x{t["msaa"]}' if t['msaa'] > 1 else ''} |")
    text = "\n".join(lines) + "\n"
    if len(text.encode("utf-8")) > limit:
        cut = text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
        text = cut[:cut.rfind("\n")] + "\n\n(cut at 10 KB: the full data is in frame.json)\n"
    return text


def captures(ws: Path) -> list[dict]:
    out = ws / "traces" / "gfx"
    return [{"capture": str(c), "size": c.stat().st_size, "exported": (c.parent / c.stem / "frame.md").is_file(),
             "size_h": human_size(c.stat().st_size)} for c in sorted(out.glob("*.rdc"))] if out.is_dir() else []
