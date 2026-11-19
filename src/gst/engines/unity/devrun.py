"""Dev harness: run Unity extractors sequentially without the pipeline.

    python -X utf8 -m gst.engines.unity.devrun <install> <workspace> [--extractor X ...] [--file REGEX]
        [--name REGEX] [--limit N] [--subprocess] [--log run.jsonl]

Discovers the units of each extractor (inventory first), runs them one by one (in-process, or one
subprocess per unit with ``--subprocess`` so a native crash costs one unit) and logs one JSON line per
unit: extractor, unit, status, outputs, bytes, seconds, peak RSS of the process that ran it. Writes
only under ``<workspace>``; the install is opened read-only.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from gst.engines.unity import mem
from gst.extractors.base import Context, Unit

ORDER = ["unity.inventory", "unity.textures", "unity.sprites", "unity.audio", "unity.meshes", "unity.gltf",
         "unity.animations", "unity.materials", "unity.shaders", "unity.monobehaviours", "unity.localization",
         "unity.prefabs_scenes", "unity.text", "unity.fonts", "unity.video", "unity.terrain", "unity.navmesh",
         "unity.addressables", "unity.assetripper_project"]


def make_ctx(install: Path, workspace: Path, options: dict) -> Context:
    game = {}
    gj = Path(workspace) / "game.json"
    if gj.is_file():
        try:
            game = json.loads(gj.read_text(encoding="utf-8"))
        except ValueError:
            game = {}
    game.setdefault("name", Path(install).name)
    return Context(workspace=Path(workspace), game=game, install_dir=Path(install),
                   out_root=Path(workspace) / "assets" / "unity", policy="static-only",
                   log=logging.getLogger("gst.devrun"), options=options)


def _extractor(name: str):
    from gst.engines.unity import extractors  # noqa: F401  (registers)
    from gst.extractors.registry import get

    return get(name)()


def run_unit_inline(name: str, unit: Unit, ctx: Context) -> dict:
    ext = _extractor(name)
    t0 = time.time()
    try:
        res = asdict(ext.run(unit, ctx))
    except Exception as exc:  # a bug: report it like the worker would
        import traceback

        res = {"status": "failed", "error": "".join(traceback.format_exception(exc)), "outputs": [], "out_bytes": 0,
               "notes": []}
    res["seconds"] = round(time.time() - t0, 2)
    res["peak_rss"] = mem.peak_rss()
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gst.engines.unity.devrun")
    ap.add_argument("install", type=Path)
    ap.add_argument("workspace", type=Path)
    ap.add_argument("--extractor", action="append", help="repeatable; default: all except assetripper_project")
    ap.add_argument("--file", help="regex on unit relpaths")
    ap.add_argument("--name", help="regex on object names")
    ap.add_argument("--limit", type=int, help="objects per unit (explicit only)")
    ap.add_argument("--subprocess", action="store_true", help="one python -X utf8 process per unit")
    ap.add_argument("--log", type=Path, help="JSONL log (default <workspace>/logs/devrun-<ts>.jsonl)")
    ap.add_argument("--unit-json", help=argparse.SUPPRESS)  # internal: run one unit and print the result
    args = ap.parse_args(argv)
    options = {k: v for k, v in (("file", args.file), ("name", args.name), ("limit", args.limit)) if v is not None}
    ctx = make_ctx(args.install, args.workspace, options)
    if args.unit_json:
        spec = json.loads(Path(args.unit_json).read_text(encoding="utf-8"))
        res = run_unit_inline(spec["extractor"], Unit(**spec["unit"]), ctx)
        print("@@RESULT@@" + json.dumps(res, ensure_ascii=False))
        return 0
    names = args.extractor or [n for n in ORDER if n != "unity.assetripper_project"]
    log_path = args.log or Path(args.workspace) / "logs" / f"devrun-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    totals: dict[str, dict] = {}
    with open(log_path, "a", encoding="utf-8", newline="\n") as log:
        for name in names:
            ext = _extractor(name)
            t_ext = time.time()
            agg = totals.setdefault(name, {"units": 0, "done": 0, "failed": 0, "unsupported": 0, "outputs": 0,
                                           "bytes": 0, "seconds": 0.0, "peak_rss": 0})
            for unit in ext.discover(ctx):
                if args.subprocess:
                    res = _run_subprocess(name, unit, args)
                else:
                    res = run_unit_inline(name, unit, ctx)
                agg["units"] += 1
                agg[res["status"] if res["status"] in ("done", "failed", "unsupported") else "failed"] += 1
                agg["outputs"] += len(res.get("outputs", []))
                agg["bytes"] += res.get("out_bytes", 0)
                agg["peak_rss"] = max(agg["peak_rss"], res.get("peak_rss", 0))
                row = {"extractor": name, "unit": unit.id, "size": unit.size, "status": res["status"],
                       "outputs": len(res.get("outputs", [])), "out_bytes": res.get("out_bytes", 0),
                       "seconds": res.get("seconds"), "peak_rss": res.get("peak_rss"), "notes": res.get("notes"),
                       "error": res.get("error")}
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
                log.flush()
                print(f"{name} {unit.id}: {res['status']} {row['outputs']} files {row['out_bytes'] / 1e6:.1f} MB "
                      f"{row['seconds']} s rss {(row['peak_rss'] or 0) / 1e6:.0f} MB", flush=True)
            if type(ext).has_finalize():  # like the pipeline's <finalize> job, after every unit
                t_fin = time.time()
                fin = ext.finalize(ctx)
                row = {"extractor": name, "unit": "<finalize>", "status": fin.status if fin else "skipped",
                       "outputs": len(fin.outputs) if fin else 0, "seconds": round(time.time() - t_fin, 2),
                       "notes": fin.notes if fin else []}
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{name} <finalize>: {row['status']} {row['notes']}", flush=True)
            agg["seconds"] = round(time.time() - t_ext, 1)
            log.write(json.dumps({"extractor": name, "total": agg}) + "\n")
            log.flush()
            print(f"== {name}: {agg}", flush=True)
    return 0


def _run_subprocess(name: str, unit: Unit, args) -> dict:
    spec_dir = Path(args.workspace) / "logs" / "devrun-units"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec = spec_dir / f"{abs(hash((name, unit.id)))}.json"
    spec.write_text(json.dumps({"extractor": name, "unit": asdict(unit)}), encoding="utf-8")
    cmd = [sys.executable, "-X", "utf8", "-m", "gst.engines.unity.devrun", str(args.install), str(args.workspace),
           "--unit-json", str(spec)]
    for flag in ("file", "name", "limit"):
        if getattr(args, flag) is not None:
            cmd += [f"--{flag}", str(getattr(args, flag))]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL)
    spec.unlink(missing_ok=True)
    for line in proc.stdout.splitlines():
        if line.startswith("@@RESULT@@"):
            return json.loads(line[len("@@RESULT@@"):])
    return {"status": "failed", "error": f"worker exit {proc.returncode}\n{proc.stderr}", "outputs": [], "out_bytes": 0,
            "seconds": round(time.time() - t0, 2), "peak_rss": 0, "notes": []}


if __name__ == "__main__":
    sys.exit(main())
