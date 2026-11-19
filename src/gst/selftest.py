"""`gst selftest`: is this installation healthy?

- environment: gst doctor's findings (errors fail, warnings are listed);
- --tools: every catalog tool's state, then live runs on the sample game (samples/sample-game) in the
  `_selftest-sample` workspace: save round-trip, native decompile + index, a hardware write watchpoint,
  a RenderDoc capture, the Ghidra MCP server;
- --games: read-only checks on the real studies (recon expectations, the code index, the report status).
  Nothing is written into a user's study workspace.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from gst.common import GsError

# appid -> what recon must find (engine key, verdict, version prefix) and symbols the index must hold
EXPECT = {
    "457140": {"engine": "unity-mono", "verdict": "offline-ok", "symbols": ["Game", "SimBase"]},
    "108600": {"engine": "java", "verdict": "offline-ok", "symbols": ["IsoPlayer"]},
    "548430": {"engine": "unreal", "verdict": "offline-ok", "version": "4.27"},
    "559650": {"engine": "unreal", "verdict": "no-runtime", "protection": "encrypted-content"},
    "4244510": {"engine": "godot", "verdict": "offline-ok", "version": "4."},
}


class Report:
    def __init__(self):
        self.rows: list[dict] = []

    def run(self, group: str, name: str, fn) -> None:
        t0 = time.time()
        try:
            status, detail = fn()
        except GsError as exc:
            status, detail = "fail", str(exc)
        except Exception as exc:  # a broken check must not hide the others
            status, detail = "fail", f"{exc.__class__.__name__}: {exc}"
        self.rows.append({"group": group, "name": name, "status": status, "detail": detail,
                          "seconds": round(time.time() - t0, 1)})

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r["status"] == "fail")


def environment(report: Report) -> None:
    from gst import doctor

    def check():
        found = doctor.findings(doctor.collect("core"))
        errors = [m for level, m in found if level == "error"]
        warnings = [m for level, m in found if level == "warn"]
        if errors:
            return "fail", "; ".join(errors)
        return ("warn", "; ".join(warnings)) if warnings else ("ok", "doctor: no findings")

    report.run("env", "doctor", check)


def tool_states(report: Report) -> None:
    from gst import tools

    def check():
        rows = [tools.status(t) for t in tools.load_catalog().values() if t.kind != "guided"]
        missing = [r["id"] for r in rows if r["state"] == "missing"]
        other = [f"{r['id']} {r['version']}" for r in rows if r["state"] == "other-version"]
        ok = sum(1 for r in rows if r["state"] == "ok")
        detail = f"{ok}/{len(rows)} ok" + (f"; not installed: {', '.join(missing)}" if missing else "") \
            + (f"; other versions: {', '.join(other)}" if other else "")
        return ("warn" if missing or other else "ok"), detail

    report.run("tools", "catalog", check)


def _sample_workspace():
    from gst import config, workspace
    from gst.cmd_setup import toolkit_root
    from gst.recon import scan, stores

    exe = toolkit_root() / "samples" / "sample-game" / "build" / "SampleGame.exe"
    if not exe.exists():
        raise GsError("the sample game is not built (samples/sample-game/build.ps1)")
    ws = config.gs_home() / "_selftest-sample"
    if not (ws / "game.json").exists():
        game = stores.resolve_game(str(exe.parent), games=[])
        workspace.create(game, scan.run_recon(game), "_selftest-sample")
    return ws, workspace.load(ws), exe


def sample(report: Report) -> None:
    from gst import tools

    state: dict = {}

    def setup():
        state["ws"], state["game"], state["exe"] = _sample_workspace()
        return "ok", str(state["ws"])

    report.run("sample", "workspace", setup)
    if "ws" not in state:
        return
    ws, game, exe = state["ws"], state["game"], state["exe"]

    def formats_check():
        from gst.formats import readers

        out = ws / "dumps" / "selftest.sgs"
        subprocess.run([str(exe), "--headless", "--ticks", "600", "--save", str(out), "--timestamp", "1700000000"],
                       check=True, capture_output=True, timeout=120)
        reader = Path(__file__).resolve().parent / "data" / "readers" / "sgsv.py"
        result = readers.roundtrip(reader, [out])["results"][0]
        if not result.get("ok"):
            return "fail", f"round-trip: {result.get('error') or result.get('first_difference')}"
        described = ", ".join(f"{k}={v}" for k, v in (result.get("describe") or {}).items())
        return "ok", f"save round-trips byte for byte ({described})"

    report.run("sample", "formats: save round-trip", formats_check)

    def native_check():
        from gst import index
        from gst.decompile import native
        from gst.index import query

        if tools.bin_path("ghidra") is None:
            return "skip", "Ghidra not installed"
        result = native.decompile(ws, game)["modules"]["SampleGame.exe"]
        index.build(ws)
        con = index.connect(ws)
        try:
            callers = {c["caller"] for c in query.callers(con, "sg::Crc32")}
        finally:
            con.close()
        if callers != {"sg.SaveWorld", "sg.LoadWorld"}:
            return "fail", f"callers of sg::Crc32: {sorted(callers)}"
        return "ok", f"{result.get('decompiled')} functions ({'reused' if result.get('reused') else 'analyzed'}); Crc32 callers found"

    report.run("sample", "native: Ghidra + index", native_check)

    def runtime_check():
        from gst import runtime
        from gst.runtime import memory, watch

        proc = subprocess.Popen([str(exe), "--headless", "--realtime", "--ticks", "6000"], stdout=subprocess.DEVNULL)
        try:
            time.sleep(1.0)
            runtime.target(game, proc.pid)  # the same policy and ownership gate as gst trace
            handle = memory.open_process(proc.pid)
            try:
                hits, _ = memory.first_scan(handle, "100", "int32")
                before = {a: memory.read(handle, a, 4) for a in hits}
                kept = []
                for _ in range(40):
                    time.sleep(1.0)
                    kept, _now = memory.next_scan(handle, hits, "int32", value=None, mode="decreased", previous=before)
                    if kept:
                        break
                if len(kept) != 1:
                    return "fail", f"{len(kept)} candidates for the HP"
                result = watch.watch(proc.pid, kept[0], 4, max_hits=2, seconds=40)
                sym = runtime.Symbolizer(ws, runtime.modules(handle))
                found = (sym.describe(h["rip"], inside_previous=True) for h in result["hits"])
                writers = sorted({d.get("function") or d["where"] for d in found})
            finally:
                runtime.win.CloseHandle(handle)
            alive = proc.poll() is None
        finally:
            proc.kill()  # the sample we started: its own pid
            proc.wait(timeout=10)
        if result["stopped_by"] != "hits":
            return "fail", f"no write caught ({result['stopped_by']})"
        if not alive:
            return "fail", "the game died after the watchpoint"
        named = {"sg.Enemy.Update", "sg.Player.Update"} & set(writers)
        if not named and (ws / "decomp" / "ghidra" / "SampleGame.exe" / "functions.jsonl").exists():
            return "fail", f"HP written at {', '.join(writers)}, not by the expected functions"
        return "ok", f"HP written by {', '.join(writers)}"

    report.run("sample", "runtime: who writes the HP", runtime_check)

    def gfx_check():
        from gst import gfx
        from gst.decompile.dotnet import sha256_of
        from gst.runtime import sandbox

        if tools.bin_path("renderdoc", "qrenderdoc.exe") is None:
            return "skip", "RenderDoc not installed"
        copy = ws / "sandbox" / "SampleGame.exe"
        if not copy.exists() or sha256_of(copy) != sha256_of(exe):
            sandbox.remove(ws)
            sandbox.create(ws, game)
        result = gfx.capture(ws, game, after=2, exe="SampleGame.exe", args="--frames 900")
        out = gfx.export(ws, Path(result["captures"][0]))
        return ("ok" if out["passes"] == 2 else "fail"), f"{out['api']}: {out['passes']} passes, {out['shaders']} shaders"

    report.run("sample", "graphics: RenderDoc capture", gfx_check)

    def mcp_check():
        import json

        from gst import mcp
        from gst.mcp_client import Session, text_of

        if tools.bin_path("pyghidra-mcp") is None or not mcp.projects(ws):
            return "skip", "pyghidra-mcp or the sample's Ghidra project is missing"
        mcp.add(ws, game, "ghidra")
        with Session(mcp.configured(ws)["ghidra"]) as session:
            binary = json.loads(text_of(session.call("list_project_binaries")))["programs"][0]["name"]
            code = json.loads(text_of(session.call("decompile_function", {"binary_name": binary, "name_or_address": "sg::Crc32"})))
        return ("ok" if "0xedb88320" in code.get("code", "") else "fail"), "pyghidra-mcp decompiled sg::Crc32"

    report.run("sample", "mcp: Ghidra server", mcp_check)


def _find_game(name: str) -> dict:
    """A registry entry by slug, workspace folder, AppID or part of the name; a study workspace wins over the
    bare library entry of the same game."""
    from gst import guard

    key = name.lower()
    exact, partial = [], []
    for slug, entry in guard.registry_games().items():
        folder = Path(entry.get("workspace") or "").name.lower()
        row = {**entry, "slug": slug}
        if key in {slug.lower(), folder, str(entry.get("appid"))}:
            exact.append(row)
        elif key in str(entry.get("name", "")).lower():
            partial.append(row)
    for rows in (exact, partial):
        if rows:
            return max(rows, key=lambda r: bool(r.get("workspace")))
    raise GsError(f"no game '{name}' in the registry (gst recon --library, or gst new)")


def games(report: Report, names: list[str]) -> None:
    from gst.recon import scan

    for name in names:
        try:
            entry = _find_game(name)
        except GsError as exc:
            report.run("games", name, lambda exc=exc: ("fail", str(exc)))
            continue
        expect = EXPECT.get(str(entry.get("appid")), {})

        def recon_check(entry=entry, expect=expect):
            game = {"name": entry.get("name"), "path": entry["install_dir"], "appid": entry.get("appid")}
            result = scan.run_recon(game)
            engine, verdict = result["engine"]["key"], result["verdict"]
            details = result["engine"].get("details") or {}
            version = str(details.get("version") or details.get("engine_version") or "")
            wrong = [f"{k} {got} (expected {expect[k]})" for k, got in (("engine", engine), ("verdict", verdict)) if k in expect and got != expect[k]]
            if expect.get("version") and not version.startswith(expect["version"]):
                wrong.append(f"version {version} (expected {expect['version']}*)")
            if expect.get("protection") and expect["protection"] not in {p["kind"] for p in result["protections"]}:
                wrong.append(f"no {expect['protection']} protection found")
            return ("fail" if wrong else "ok"), "; ".join(wrong) or f"{' '.join(filter(None, (engine, version)))}, {verdict}"

        report.run("games", f"{name}: recon", recon_check)
        ws = Path(entry["workspace"]) if entry.get("workspace") else None
        if ws is None or not (ws / "index" / "code.db").exists():
            continue

        def index_check(ws=ws, expect=expect):
            from gst import index
            from gst.index import query

            con = index.connect(ws)
            try:
                stats = index.stats(con)
                missing = [s for s in expect.get("symbols", []) if not query.defs(con, s)]
            finally:
                con.close()
            return ("fail" if missing else "ok"), (f"missing {missing}" if missing else
                                                   f"{stats['files']} files, {stats['symbols']} symbols")

        report.run("games", f"{name}: code index", index_check)
        if (ws / "ARCHITECTURE.md").exists():
            def report_check(ws=ws):
                from gst import report as rep
                from gst import workspace

                status = rep.check(ws, workspace.load(ws), min_systems=1)
                return ("ok" if status["ok"] else "warn"), (f"ARCHITECTURE.md: {len(status['systems'])} systems, "
                                                            f"{status['citations']} citations"
                                                            + ("" if status["ok"] else f", {len(status['problems'])} open points"))

            report.run("games", f"{name}: report", report_check)
