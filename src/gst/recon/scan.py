"""gst recon pipeline: index -> rules -> engine -> PE/protections -> store -> verdict -> report."""

from __future__ import annotations

import re
import time
from pathlib import Path

from gst import __version__, policy, tools
from gst.common import human_size, write_json, write_text
from gst.recon import datadirs, fingerprint, middleware, online, protections, rules
from gst.recon.index import FileIndex

ARCHIVE_EXT = {".pak", ".utoc", ".ucas", ".vpk", ".bsa", ".ba2", ".rpf", ".pck", ".assets", ".bundle", ".arc",
               ".forge", ".rpack", ".wad", ".pk3", ".bnk", ".bank", ".fsb", ".wem", ".rgss3a", ".rpa", ".nxa"}
INTERESTING_IMPORTS = {
    "d3d9.dll": "Direct3D 9", "d3d11.dll": "Direct3D 11", "d3d12.dll": "Direct3D 12", "vulkan-1.dll": "Vulkan",
    "opengl32.dll": "OpenGL", "dxgi.dll": "DXGI", "steam_api64.dll": "Steamworks", "eossdk-win64-shipping.dll": "Epic Online Services",
    "fmod.dll": "FMOD", "fmodstudio.dll": "FMOD Studio", "lua51.dll": "Lua 5.1/LuaJIT", "lua54.dll": "Lua 5.4",
    "mono-2.0-bdwgc.dll": "Mono", "python3.dll": "Python", "physx3_x64.dll": "PhysX 3", "bink2w64.dll": "Bink 2",
    "xinput1_3.dll": "XInput", "xinput1_4.dll": "XInput", "dbghelp.dll": "dbghelp", "ws2_32.dll": "Winsock",
}


JRE_DIRS = re.compile(r"(?:^|/)(?:jre\w*|jdk\w*|runtime|java|steamapps)/")
LUA_BYTECODE_EXT = (".luac", ".lbc", ".lub", ".lc", ".ljbc")


def script_code(ix: FileIndex) -> dict:
    """Java (jars, loose classes) and Lua (scripts, compiled chunks) in the install, whatever the engine: gst study
    plans code.java / code.lua for them (Lua ships next to many engines)."""
    files = [f for f in ix.files if not JRE_DIRS.search(f)]
    jars = [f for f in files if f.endswith(".jar")]
    classes = sum(1 for f in files if f.endswith(".class"))
    lua = sum(1 for f in files if f.endswith(".lua"))
    compiled = sum(1 for f in files if f.endswith(LUA_BYTECODE_EXT))
    out = {}
    if jars or classes:
        out["java"] = {"jars": len(jars), "class_files": classes, "jar_files": jars}
    if lua or compiled:
        out["lua"] = {"scripts": lua, "compiled": compiled}
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for item in items:
        key = (item["kind"], item["name"].lower())
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def engine_reference(name: str) -> str | None:
    """Absolute path of the plugin's engine notes (references/engines/<name>), when they exist."""
    from gst.cmd_setup import toolkit_root
    from gst.common import GsError

    try:
        path = toolkit_root() / "plugins" / "gst" / "references" / "engines" / name
    except GsError:
        return None
    return path.as_posix() if name and path.is_file() else None


def recommended_steps(engine_key: str, verdict: str, details: dict, available: set[str] | None = None,
                      scripts: dict | None = None) -> list[str]:
    """Next steps, naming a gst command only when this toolkit version has it (else the tool route)."""
    if available is None:
        from gst.cli import command_names

        available = command_names()
    from gst.assets import backend_for

    steps = []
    label, _ref, study_tools, backend = fingerprint.ENGINES.get(engine_key, ("", "", "", None))
    tools_hint = f" ({study_tools})" if study_tools else ""
    if "assets" not in available:
        assets_step = "assets: list the containers, convert samples into assets/ (texconv, vgmstream, ffmpeg)"
    elif backend_for(engine_key):
        assets_step = "gst assets list, then gst assets extract"
    else:  # gst assets list/extract cover Unity, Godot and Unreal 4/5 only
        assets_step = f"assets: the engine's tools{tools_hint} into assets/, gst fmt id|scan on unknown files; audio: gst assets audio"
    if verdict == policy.NO_RUNTIME:
        steps.append("data on disk only, unencrypted containers: " + (assets_step if "assets" in available else
                                                                       "read or convert files into assets/ (texts, tables, media)"))
    else:
        from gst.cmd_decompile import BACKENDS

        if backend in BACKENDS and "decompile" in available:
            steps.append(f"gst decompile {backend}")
        elif backend == "ue" and "decompile" in available:
            steps.append("Blueprints: gst assets extract --bytecode; C++: gst decompile native <Game>-Win64-Shipping.exe")
        elif backend:
            steps.append(f"decompile the {backend} code into decomp/{tools_hint}")
        if "decompile" in available:  # Java/Lua next to the engine (gst study plans code.java / code.lua for them)
            steps += [f"gst decompile {kind}" for kind in ("java", "lua") if kind in (scripts or {}) and kind != backend]
        steps.append(assets_step)
        steps.append("gst index && gst map" if {"index", "map"} <= available
                     else "map the code: search decomp/ with rg -n and ctags, write notes/<layer>/<topic>.md")
        if verdict == policy.OFFLINE_OK:
            steps.append("runtime (offline/solo only): gst trace scan <value> then gst trace watch (who writes it); "
                         "gst trace snapshot / gst trace diff around a save" if "trace" in available
                         else "runtime (offline/solo only): observe first with the runtime profile tools (gst tools install runtime)")
    if details.get("encrypted"):
        steps.append("encrypted containers: diagnose only unless a community-published key is recorded in game.json")
    return steps


def run_recon(game: dict, *, deep: bool = False) -> dict:
    root = Path(game["path"])
    t0 = time.time()
    ix = FileIndex(root)
    rule_hits = rules.match(ix.files, ix.dirs)
    hits, facts = fingerprint.detect(ix, rule_hits)
    exes = fingerprint.main_executables(ix)
    pe_prots, pe_details, protected = protections.from_pe(ix, exes + fingerprint.key_modules(ix))
    store = online.steam_store(game.get("appid")) if game.get("store") == "steam" else None
    prots = (protections.from_rules(rule_hits) + protections.from_awacy(game.get("appid")) + pe_prots
             + protections.from_store(store) + protections.from_name(game.get("name") or root.name)
             + protections.encrypted_content(ix, hits) + protections.obfuscation_hint(rule_hits))
    if deep:
        prots += protections.from_die(tools.bin_path("die"), ix, exes)
    prots = _dedupe(prots)
    verdict = policy.verdict(prots)
    scripts = script_code(ix)
    top = hits[0]
    label, ref_doc, study_tools, backend = fingerprint.ENGINES.get(top["key"], fingerprint.ENGINES["native"])
    imports = sorted({INTERESTING_IMPORTS[i] for d in pe_details.values() for i in d.get("imports", []) if i in INTERESTING_IMPORTS})
    pdbs = ix.find("*.pdb")
    archives = sorted({Path(f).suffix for f in ix.files if Path(f).suffix in ARCHIVE_EXT})
    warnings = []
    if len(ix.files) < 5:
        warnings.append("the install folder is (nearly) empty: not fully installed, or the game lives elsewhere")
    if ix.truncated:
        warnings.append("file index truncated (very large install); pass a subfolder for detail")
    if rule_hits.get("Loader"):
        warnings.append("third-party loaders already present (install already modified): " + ", ".join(rule_hits["Loader"]))
    if store is None and game.get("store") == "steam":
        warnings.append("store metadata not used (gst config set online_metadata yes to enable cached Steam lookups)")
    return {
        "gs_version": __version__,
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds": round(time.time() - t0, 1),
        "game": {k: game.get(k) for k in ("name", "store", "appid", "buildid", "path", "size_on_disk", "workshop")},
        "engine": {"key": top["key"], "label": label, "confidence": top["score"], "evidence": top["evidence"],
                   "details": top.get("details", {}), "reference": engine_reference(ref_doc),
                   "study_tools": study_tools, "decompile_backend": backend},
        "other_engine_signals": [{"key": h["key"], "evidence": h["evidence"][:1]} for h in hits[1:5]],
        "rule_engines": facts.get("rule_engines", []),
        "middleware": sorted(rule_hits.get("SDK", {})),
        "net_voice": middleware.detect(ix.files, ix.dirs),
        "imports": imports,
        "executables": {e: pe_details.get(e, {}) for e in exes},
        "key_modules": {m: pe_details.get(m, {}) for m in fingerprint.key_modules(ix)},
        "symbols": {"pdb_files": pdbs[:20], "codeview": {e: d.get("pdb") for e, d in pe_details.items() if d.get("pdb")}},
        "protections": prots,
        "protected_modules": protected,
        "verdict": verdict,
        "capabilities": policy.capabilities(verdict),
        "verdict_text": policy.describe(verdict),
        "store": store,
        "files": {"count": len(ix.files), "dirs": len(ix.dirs), "bytes": ix.total_size(), "truncated": ix.truncated,
                  "archives": archives, "by_extension": ix.ext_histogram(20), "largest": ix.largest(12)},
        "user_data": datadirs.hints(game.get("name") or root.name, hits),
        "loaders_present": sorted(rule_hits.get("Loader", {})),
        "script_code": scripts,
        "next_steps": recommended_steps(top["key"], verdict, {"encrypted": any(p["kind"] == "encrypted-content" for p in prots)},
                                        scripts=scripts),
        "warnings": warnings,
    }


def _scripts_line(scripts: dict) -> str:
    parts = []
    if "java" in scripts:
        parts.append(f"Java: {scripts['java']['jars']} jars, {scripts['java']['class_files']} class files (code.java)")
    if "lua" in scripts:
        parts.append(f"Lua: {scripts['lua']['scripts']} .lua, {scripts['lua']['compiled']} compiled chunks (code.lua)")
    return "; ".join(parts) or "none outside the engine's own"


def render_markdown(r: dict) -> str:
    g, e = r["game"], r["engine"]
    parts = []
    for k, v in e["details"].items():
        if isinstance(v, dict):
            parts.append(f"{k}=" + " ".join(str(x) for x in v.values()))
        elif not isinstance(v, list):
            parts.append(f"{k}={v}")
    det = ", ".join(parts)
    lines = [
        f"# Recon: {g['name']}",
        "",
        f"- **Install:** `{g['path']}`" + (f" ({g['store']} {g['appid']}, build {g.get('buildid')})" if g.get("store") else ""),
        f"- **Engine:** {e['label']} [{e['confidence']}%]; evidence: {', '.join(map(str, e['evidence']))}",
    ]
    if det:
        lines.append(f"- **Engine details:** {det}")
    lines += [
        f"- **Verdict:** `{r['verdict']}`: {r['verdict_text']}",
        f"- **Study tools:** {e['study_tools']}" + (f" (engine notes: `{e['reference']}`)" if e.get("reference") else ""),
        f"- **Middleware:** {', '.join(r['middleware']) or 'none detected'}; APIs: {', '.join(r['imports']) or '-'}",
        f"- **Network/voice:** {middleware.summary(r.get('net_voice') or [])}",
        f"- **Script code:** {_scripts_line(r.get('script_code') or {})}",
        f"- **Files:** {r['files']['count']} files, {human_size(r['files']['bytes'])}; archives: {', '.join(r['files']['archives']) or '-'}",
    ]
    if r["symbols"]["pdb_files"]:
        lines.append(f"- **Symbols shipped:** {', '.join(r['symbols']['pdb_files'][:6])}")
    if r["user_data"]:
        lines.append(f"- **Saves/config/logs:** {'; '.join(r['user_data'])}")
    lines += ["", "## Protections", ""]
    if r["protections"]:
        lines += [f"- `{p['kind']}` {p['name']} ({p['source']}: {p['evidence']})" for p in r["protections"]]
    else:
        lines.append("- none detected (still treat online modes as out of scope)")
    if r["protected_modules"]:
        lines.append(f"- protected modules (never unpack): {', '.join(r['protected_modules'])}")
    lines += ["", "## Executables and key modules", ""]
    for exe, info in {**r["executables"], **r.get("key_modules", {})}.items():
        ver = info.get("version", {})
        product = " ".join(v for v in (ver.get("ProductName"), ver.get("ProductVersion")) if v)
        lines.append(f"- `{exe}` {info.get('arch', '?')}{' .NET' if info.get('managed') else ''}"
                     + (f", {product}" if product else "")
                     + (f", PDB {info['pdb']}" if info.get("pdb") else ""))
    if r["other_engine_signals"]:
        lines += ["", "## Other engine signals", ""]
        lines += [f"- {o['key']}: {', '.join(map(str, o['evidence']))}" for o in r["other_engine_signals"]]
    lines += ["", "## Largest files", ""]
    lines += [f"- `{f}` {human_size(s)}" for f, s in r["files"]["largest"][:8]]
    if r["store"]:
        s = r["store"]
        lines += ["", "## Store (public metadata)", "",
                  f"- {', '.join(c['name'] for c in s['categories'])}",
                  f"- developers: {', '.join(s['developers'])}; publishers: {', '.join(s['publishers'])}; released: {s['release_date']}"]
    lines += ["", "## Next steps", ""] + [f"{i}. {s}" for i, s in enumerate(r["next_steps"], 1)]
    if r["warnings"]:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in r["warnings"]]
    return "\n".join(lines) + "\n"


def render_protections(r: dict) -> str:
    lines = [f"# Protections: {r['game']['name']}", "", f"Verdict: `{r['verdict']}`: {r['verdict_text']}", ""]
    if not r["protections"]:
        lines.append("No protection detected. Online modes stay out of scope (guardrails rule 3).")
    for p in r["protections"]:
        lines.append(f"- **{p['name']}** (`{p['kind']}`, via {p['source']}): {p['evidence']}")
    lines += ["", "Legitimate alternatives when something is blocked: offline/solo mode, data on disk, public",
              "documentation or source code, official tools. See guardrails.md rule 2 and rule 10."]
    return "\n".join(lines) + "\n"


def write_outputs(report: dict, recon_dir: Path) -> None:
    write_json(recon_dir / "recon.json", report)
    write_text(recon_dir / "RECON.md", render_markdown(report))
    write_text(recon_dir / "protections.md", render_protections(report))
