"""AudioClip -> WAV (UnityPy + FMOD); fallback: raw FSB5 bytes + vgmstream-cli when installed."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _raw_audio(clip) -> tuple[bytes, str]:
    from UnityPy.helpers.ResourceReader import get_resource_data

    res = getattr(clip, "m_Resource", None)
    if res is not None and getattr(res, "m_Size", 0):
        data = get_resource_data(res.m_Source, clip.object_reader.assets_file, res.m_Offset, res.m_Size)
    else:
        data = bytes(getattr(clip, "m_AudioData", b"") or b"")
    ext = ".fsb" if data[:4] == b"FSB5" else ".bin"
    return bytes(data), ext


def _vgmstream(src: Path, dest: Path) -> bool:
    try:
        from gst import tools

        exe = tools.bin_path("vgmstream")
    except Exception:
        exe = None
    if not exe:
        return False
    proc = subprocess.run([str(exe), "-o", str(dest), str(src)], capture_output=True, stdin=subprocess.DEVNULL)
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 44


def export_audio(s, obj) -> list:
    clip = obj.read(check_read=False)
    name = clip.m_Name or f"AudioClip_{obj.path_id}"
    w = s.w
    try:
        samples = clip.samples
    except Exception as exc:  # FMOD missing or codec unsupported: keep the raw stream
        samples = None
        err = f"{exc.__class__.__name__}: {exc}"
    if samples:
        out = []
        for i, (_sname, wav) in enumerate(samples.items()):
            stem = name if len(samples) == 1 else f"{name}_{i}"
            path = w.target("AudioClip", stem, ".wav", f"{obj.path_id}_{i}")
            out.append(w.commit(path, bytes(wav), meta=s.meta(obj, clip.m_Name, tool_detail="UnityPy+fmod")))
        return out
    data, ext = _raw_audio(clip)
    if not data:
        if samples is None:
            raise RuntimeError(f"no samples and no raw data ({err})")
        return []
    raw = w.target("AudioClip", name, ext, obj.path_id, "_raw")
    w.commit(raw, data, meta=s.meta(obj, clip.m_Name, note="raw stream; UnityPy could not decode"))
    out = [raw]
    wav = w.target("AudioClip", name, ".wav", obj.path_id)

    def convert(tmp: Path) -> None:
        if not _vgmstream(raw, tmp):
            raise RuntimeError("vgmstream-cli not installed or failed")

    try:
        w.commit(wav, writer=convert, meta=s.meta(obj, clip.m_Name, tool="vgmstream"))
        out.append(wav)
    except Exception:
        s.notes.append(f"AudioClip {name}: raw {ext} kept (vgmstream not available or failed)")
    return out
