"""Engine and scripting-runtime fingerprinting over a FileIndex.

detect() is adapted from universal-modder's um/scan.py (MIT): same idea (scored hits with
evidence), study-oriented labels/tools instead of mod routes, plus engines and probes it lacked
(UE3, Godot .NET, Java/JRE, PyInstaller, HashLink, Flash, Chrome Engine, No Man's Sky,
Stingray, Blizzard CASC, ForzaTech, RPG Maker 2000/2003, Wolf RPG) and SteamDB corroboration.
"""

from __future__ import annotations

import re

from gst.recon import sniff
from gst.recon.index import FileIndex

# key -> (label, reference doc under references/engines/, study tools, gst decompile backend or None)
ENGINES: dict[str, tuple[str, str, str, str | None]] = {
    "unity-mono": ("Unity (Mono)", "unity.md", "ilspycmd on <Game>_Data/Managed/*.dll; AssetRipper headless, UnityPy, UnityDataTool", "dotnet"),
    "unity-il2cpp": ("Unity (IL2CPP)", "unity.md", "Cpp2IL / Il2CppInspectorRedux -> C# stubs + Ghidra script; assets as for Mono", "il2cpp"),
    "unreal": ("Unreal Engine 4/5", "unreal.md", "repak/retoc + uas JSON export, UAssetGUI, kismet-analyzer; Ghidra for the shipping exe", "ue"),
    "unreal3": ("Unreal Engine 1-3", "ue3.md", "UELib/UE Explorer (UnrealScript), umodel; Ghidra for native code", "native"),
    "godot": ("Godot", "godot.md", "GDRE Tools (--headless --recover); ilspycmd for Godot .NET assemblies", "godot"),
    "gamemaker": ("GameMaker", "gamemaker.md", "UndertaleModCli dump (VM bytecode); Ghidra for YYC builds", "gamemaker"),
    "rpgmaker-mvmz": ("RPG Maker MV/MZ (NW.js)", "rpgmaker.md", "plain JS + data/*.json; wakaru for minified plugins", "js"),
    "rpgmaker-rgss": ("RPG Maker XP/VX/VX Ace (RGSS)", "rpgmaker.md", "rvpacker-txt-rs json generate (Ruby scripts, data)", None),
    "rpgmaker-2k": ("RPG Maker 2000/2003", "rpgmaker.md", "EasyRPG liblcf lcf2xml", None),
    "wolfrpg": ("Wolf RPG Editor", "misc-engines.md", "WolfDec (key policy applies)", None),
    "renpy": ("Ren'Py", "renpy.md", "unrpa + unrpyc", "python"),
    "pyinstaller": ("Python (PyInstaller)", "python.md", "pyinstxtractor-ng --info, then pycdc / local PyLingual", "python"),
    "xna-fna": ("XNA / FNA / MonoGame (.NET)", "dotnet-xna.md", "ilspycmd; xnbcli/xnb-js for Content/*.xnb", "dotnet"),
    "dotnet": (".NET application", "dotnet-xna.md", "ilspycmd", "dotnet"),
    "java": ("Java (LWJGL/libGDX/custom)", "java.md", "Vineflower (jadx as second opinion); unluac for embedded Lua", "java"),
    "source": ("Source (1)", "source.md", "VPKEdit, srctools, BSPSource; read Source SDK 2013 before decompiling", None),
    "source2": ("Source 2", "source.md", "Source2Viewer-CLI (VPK v2, _c resources)", None),
    "creation": ("Bethesda Creation Engine / Gamebryo", "bethesda.md", "Spriggit/Mutagen (records), Champollion (Papyrus), BSA/BA2 readers", None),
    "rage": ("Rockstar RAGE", "rage.md", "SparkIV/OpenIV/CodeWalker (GUI); public decompiled scripts; static only", None),
    "re-engine": ("Capcom RE Engine", "re-engine.md", "REE.PAK.Tool + community file lists; RszTool", None),
    "fromsoft": ("FromSoftware (Dantelion)", "fromsoft.md", "WitchyBND, Smithbox (static)", None),
    "frostbite": ("Frostbite", "native.md", "Frosty Toolsuite (GUI, supported titles only)", None),
    "idtech": ("id Tech", "misc-engines.md", "classics: read the GPL source; modern: EternalResourceExtractor", None),
    "chrome": ("Techland Chrome Engine", "chrome.md", "RP6_UnpackCLI; .scr scripts are text; official Developer Tools", None),
    "nms": ("No Man's Sky engine (Hello Games)", "nms.md", "HGPAKtool + MBINCompiler (copy paks to the workspace first)", None),
    "stingray": ("Autodesk Stingray / Bitsquid", "stingray.md", "filediver (Helldivers 2), LuaJIT decompilers", None),
    "blizzard-casc": ("Blizzard engine (CASC storage)", "blizzard.md", "rustydemon / CascView (static, never online mode)", None),
    "forzatech": ("ForzaTech", "native.md", "plaintext XML under media/ (zips partly encrypted)", None),
    "cryengine": ("CryEngine / Lumberyard", "cryengine.md", "pak = zip, Cryengine-Converter; O3DE source as reference", None),
    "clausewitz": ("Paradox Clausewitz / Jomini", "misc-engines.md", "plaintext scripts: rg / ast-grep", None),
    "genie": ("Genie (Age of Empires DE)", "genie.md", "genieutils-py read-only dumps; SLD reader", None),
    "redengine": ("CD Projekt REDengine", "misc-engines.md", "WolvenKit export", None),
    "electron": ("Electron / NW.js / HTML5", "html5.md", "@electron/asar extract; wakaru / webcrack", "js"),
    "love2d": ("LÖVE (Lua)", "misc-engines.md", "the .love/exe is a zip; LuaJIT decompiler", "lua"),
    "defold": ("Defold", "misc-engines.md", "arcdEx (Java)", None),
    "cocos": ("Cocos2d-x", "misc-engines.md", "plain Lua/JS if present; XXTEA-encrypted scripts -> diagnose only", None),
    "hashlink": ("Haxe / HashLink", "misc-engines.md", "crashlink / hlbc on hlboot.dat", "hashlink"),
    "haxe": ("Haxe / OpenFL / Lime (hxcpp)", "misc-engines.md", "assets; native code -> Ghidra", None),
    "flash": ("Adobe Flash / AIR", "misc-engines.md", "JPEXS FFDec CLI (-export script,image)", "flash"),
    "native": ("Custom native engine", "native.md", "Ghidra headless + RTTI recovery + strings/xrefs; DIE, capa", "native"),
}

# SteamDB [Engine] rule names -> our keys (corroboration and fallback)
STEAMDB_ENGINE_MAP = {
    "Unity": "unity-mono", "Unreal": "unreal", "Godot": "godot", "GameMaker": "gamemaker", "Source": "source",
    "Source2": "source2", "Bitsquid": "stingray", "ChromeEngine": "chrome", "RenPy": "renpy", "CryEngine": "cryengine",
    "Frostbite": "frostbite", "RPGMaker": "rpgmaker-mvmz", "Defold": "defold", "Cocos": "cocos", "Love2D": "love2d",
    "Electron": "electron", "NWjs": "electron", "Construct": "electron", "Phaser": "electron", "HashLink": "hashlink",
    "Heaps": "hashlink", "OpenFL": "haxe", "Lime": "haxe", "Clausewitz": "clausewitz", "XNA": "xna-fna", "FNA": "xna-fna",
    "MonoGame": "xna-fna", "CreationEngine": "creation", "NoMansSkyEngine": "nms", "BlizzardCASC": "blizzard-casc",
    "ForzaTech": "forzatech", "RAGE": "rage", "Flash": "flash", "PyInstaller": "pyinstaller", "JavaGame": "java",
    "GodotNET": "godot", "RPGMaker2000": "rpgmaker-2k", "WolfRPG": "wolfrpg", "UnrealEngine3": "unreal3", "Stingray": "stingray",
}

PYI_COOKIE = b"MEI\x0c\x0b\x0a\x0b\x0e"
SKIP_EXES = re.compile(r"(?:unitycrashhandler|crashpad_handler|crashreportclient|crashreporter|uninstall|unins\d+|vc_redist|"
                       r"dxsetup|easyanticheat|beservice|eaanticheat|start_protected_game|gameguard|launcher|setup|updater|"
                       r"restarter|cefsharp|cef_|7za?\.exe|dotnet|ue4prereq|unrealcefsubprocess|bugsplat|sentry|"
                       r"createdump|workshopuploader|redist)", re.I)
SKIP_EXE_DIRS = re.compile(r"(?:^|/)(?:jre\w*|jdk\w*|java)/bin/|(?:^|/)(?:_commonredist|redist|redistributables|directx|"
                           r"engine/extras|engine/binaries/thirdparty)/", re.I)
UE_PAK_VERSIONS = {8: "UE 4.22-4.24", 9: "UE 4.25", 10: "UE 4.25+", 11: "UE 4.26-5.3", 12: "UE 5.4+"}
KEY_MODULES = re.compile(r"(?:^|/)(?:unityplayer|gameassembly|engine_x64_rwdi|gamedll_x64_rwdi|engine2|godotsharp|"
                         r"(?!eossdk)[^/]*-win64-shipping)\.(?:dll|exe)$", re.I)


def key_modules(ix: FileIndex, limit: int = 3) -> list[str]:
    """Engine/game DLLs worth a PE look (graphics API imports, protectors) besides the exes."""
    return [f for f in ix.files if KEY_MODULES.search(f) and f.endswith(".dll")][:limit]


def _tail_has(path, needle: bytes, size: int = 4096) -> bool:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - size))
            return needle in f.read()
    except OSError:
        return False


def main_executables(ix: FileIndex, limit: int = 6) -> list[str]:
    """Likely game executables: shipping exes first, then top-level/biggest exes, minus helpers."""
    shipping = ix.find("*/binaries/win64/*-win64-shipping.exe", "*/binaries/win64/*-wingdk-shipping.exe")
    exes = [f for f in ix.files if f.endswith(".exe") and not SKIP_EXES.search(f.rsplit("/", 1)[-1])
            and not SKIP_EXE_DIRS.search(f)]
    shallow = sorted((f for f in exes if f.count("/") <= 2), key=lambda f: (f.count("/"), -ix.sizes.get(f, 0)))
    ordered = []
    for f in shipping + shallow + sorted(exes, key=lambda f: -ix.sizes.get(f, 0)):
        if f not in ordered:
            ordered.append(f)
    return ordered[:limit]


def detect(ix: FileIndex, rule_hits: dict | None = None) -> tuple[list[dict], dict]:
    """-> ([{key, score, evidence, details}] best first, facts)."""
    hits: list[dict] = []
    facts: dict = {}

    def add(key: str, score: int, evidence: list[str], **details) -> None:
        hits.append({"key": key, "score": score, "evidence": evidence,
                     "details": {k: v for k, v in details.items() if v not in (None, "", [], {})}})

    # Unity
    data_dirs = sorted({f.rsplit("/", 1)[0] for f in ix.find("*_data/globalgamemanagers", "*_data/data.unity3d", "*_data/maindata")})
    if ix.has("*unityplayer.dll") or data_dirs:
        dd = data_dirs[0] if data_dirs else ""
        il2cpp = ix.has("gameassembly.dll", "*/gameassembly.dll", "*il2cpp_data/metadata/global-metadata.dat")
        version = None
        for name in ("globalgamemanagers", "data.unity3d", "maindata"):
            rel = f"{dd}/{name}"
            if rel in ix:
                version = sniff.unity_version_from(ix.path(rel))
                if version:
                    break
        det = {"data_dir": dd, "version": version, "managed_dir": f"{dd}/managed" if not il2cpp else None,
               "addressables": ix.has(f"{dd}/streamingassets/aa/*") if dd else False,
               "render_pipeline": "HDRP" if ix.has("*unity.renderpipelines.highdefinition*") else
               "URP" if ix.has("*unity.renderpipelines.universal*") else None,
               "burst": ix.has("*lib_burst_generated.dll")}
        info = f"{dd}/app.info"
        if dd and info in ix:
            try:
                lines = ix.path(info).read_text(encoding="utf-8", errors="replace").splitlines() + ["", ""]
                det.update(company=lines[0], product=lines[1])
            except OSError:
                pass
        if il2cpp:
            add("unity-il2cpp", 100, ["UnityPlayer + GameAssembly.dll / global-metadata.dat"], **det)
        else:
            add("unity-mono", 100, ["UnityPlayer + Managed/Assembly-CSharp.dll" if ix.has("*_data/managed/assembly-csharp.dll") else "UnityPlayer"], **det)

    # Unreal 4/5
    shipping = ix.find("*/binaries/win64/*-win64-shipping.exe", "*/binaries/win64/*-wingdk-shipping.exe")
    paks = ix.find("*/content/paks/*.pak")
    utocs = ix.find("*/content/paks/*.utoc")
    if shipping or paks or utocs or ix.has_dir("engine/binaries/thirdparty"):
        det: dict = {"paks": len(paks), "iostore": bool(utocs), "sig_files": len(ix.find("*/content/paks/*.sig"))}
        if shipping:
            det["exe"] = shipping[0]
            det["project"] = shipping[0].split("/")[0]
            u16 = lambda t: re.escape(t.encode("utf-16-le"))  # noqa: E731
            pats = [rb"\+\+UE[45]\+Release-\d\.\d+", u16("++UE") + rb"[45]\x00" + u16("+Release-") + rb"\d\x00\.\x00\d\x00(?:\d\x00)?"]
            found = sniff.grep_file(ix.path(shipping[0]), pats, budget_s=8)
            for v in found.values():
                det["engine_version"] = v.replace(b"\x00", b"").decode(errors="replace").lstrip("+")
                break
        if "engine_version" not in det:
            # engine programs (CrashReportClient) and, by default, the game exe carry the engine version
            for exe in ix.find("engine/binaries/win64/crashreportclient.exe") + shipping[:1]:
                v = sniff.pe_fixed_version(ix.path(exe))
                if v and v.split(".")[0] in ("4", "5") and v != "4.0.0.0":
                    det["engine_version"] = ".".join(v.split(".")[:3])
                    det["engine_version_source"] = f"file version of {exe}"
                    break
        footers = []
        for p in sorted(paks, key=lambda f: -ix.sizes.get(f, 0))[:3]:
            footer = sniff.unreal_pak_footer(ix.path(p))
            if footer:
                footers.append({"file": p, **footer})
        if footers:
            det["pak_footers"] = footers
            det["pak_version"] = footers[0]["version"]
            if "engine_version" not in det and footers[0]["version"] in UE_PAK_VERSIONS:
                det["engine_version_hint"] = f"{UE_PAK_VERSIONS[footers[0]['version']]} (from pak v{footers[0]['version']})"
        tocs = []
        for t in sorted(utocs, key=lambda f: -ix.sizes.get(f, 0))[:3]:
            header = sniff.unreal_utoc(ix.path(t))
            if header:
                tocs.append({"file": t, **header})
        if tocs:
            det["utoc_headers"] = tocs
        add("unreal", 100 if shipping else 70, ["*-Win64-Shipping.exe" if shipping else "Content/Paks"], **det)

    # Unreal 1-3
    if ix.has_dir("*cookedpc", "*cookedpcconsole") or len(ix.find("*.upk")) >= 3:
        add("unreal3", 90, (ix.find("*.upk")[:1] or ["CookedPC/"]), packages=len(ix.find("*.upk", "*.u", "*.xxx")))

    # Godot (pck file or embedded) + Godot .NET
    godot_net = ix.find("data_*_windows_x86_64/godotsharp.dll")
    for p in ix.find("*.pck")[:3] + [e for e in main_executables(ix, 4)]:
        info = sniff.godot_pck(ix.path(p))
        if info:
            add("godot", 100, [p], version=info["version"], pack_format=info["pack_format"],
                dir_encrypted=info["dir_encrypted"], pck_where=info["where"],
                dotnet=bool(godot_net), dotnet_dir=godot_net[0].rsplit("/", 1)[0] if godot_net else None)
            break
    else:
        if godot_net:
            add("godot", 90, godot_net[:1], dotnet=True, dotnet_dir=godot_net[0].rsplit("/", 1)[0])

    # GameMaker
    for name in ("data.win", "game.unx", "assets/game.unx"):
        if name in ix:
            gm = sniff.gamemaker_data(ix.path(name)) or {}
            add("gamemaker", 100 if gm.get("form") else 70, [name], **gm)
            break

    # RPG Maker family
    if ix.has("www/js/rpg_core.js", "js/rpg_core.js"):
        add("rpgmaker-mvmz", 100, ["rpg_core.js (MV)"], version="MV")
    elif ix.has("js/rmmz_core.js", "www/js/rmmz_core.js"):
        add("rpgmaker-mvmz", 100, ["rmmz_core.js (MZ)"], version="MZ")
    if ix.has("game.rgssad", "game.rgss2a", "game.rgss3a", "data/scripts.rxdata", "data/scripts.rvdata", "data/scripts.rvdata2"):
        add("rpgmaker-rgss", 100, ix.find("game.rgss*", "data/scripts.r*")[:2])
    if ix.has("rpg_rt.exe", "rpg_rt.ldb"):
        add("rpgmaker-2k", 100, ix.find("rpg_rt.*")[:2])
    if ix.has("data.wolf", "data/basicdata.wolf", "*.wolf"):
        add("wolfrpg", 95, ix.find("data.wolf", "*.wolf")[:1])

    # Ren'Py, PyInstaller
    if ix.has_dir("renpy") and ix.has_dir("game"):
        add("renpy", 100, ["renpy/ + game/"], archives=len(ix.find("game/*.rpa")))
    pyi = ix.find("_internal/python3*.dll", "*/_internal/python3*.dll")
    if pyi or any(_tail_has(ix.path(e), PYI_COOKIE) for e in main_executables(ix, 3)):
        add("pyinstaller", 90, pyi[:1] or ["PyInstaller cookie in exe"])

    # .NET family
    xna = ix.find("fna.dll", "monogame.framework.dll", "microsoft.xna.framework*.dll", "*/fna.dll")
    managed = []
    for e in [f for f in ix.files if f.endswith((".exe", ".dll")) and f.count("/") <= 1][:40]:
        info = sniff.pe_quick(ix.path(e))
        if info:
            if e.endswith(".exe"):
                facts.setdefault("executables", {})[e] = info
            if info["managed"] and e.endswith(".exe"):
                managed.append(e)
    if xna or ix.has("content/*.xnb"):
        add("xna-fna", 95, (xna or ["Content/*.xnb"])[:2] + managed[:1])
    elif managed and not any(h["key"].startswith(("unity", "godot")) for h in hits):
        add("dotnet", 70, managed[:2])

    # Java (+ bundled JRE)
    jre = ix.find("jre*/release", "*/jre*/release", "jdk*/release")
    jars = ix.find("*.jar")
    if jre or (jars and len(jars) <= 12):
        det = {"jars": len(jars), "main_jar": max(jars, key=lambda f: ix.sizes.get(f, 0)) if jars else None}
        if jre:
            det["jre"] = sniff.java_release(ix.path(jre[0]))
        add("java", 80 if jre else 60, (jre[:1] + jars[:1]) or jars[:2], **det)

    # Source / Source 2
    if ix.has("*/gameinfo.gi") or ix.has("game/bin/win64/engine2.dll"):
        add("source2", 100, ix.find("*/gameinfo.gi")[:2] or ["game/bin/win64/engine2.dll"])
    elif ix.has("*/gameinfo.txt") and (ix.has("*_dir.vpk") or ix.has("bin/engine.dll", "bin/x64/engine.dll")):
        add("source", 100, ix.find("*/gameinfo.txt")[:2])

    # Bethesda Creation / Gamebryo
    if ix.has("data/*.esm") and ix.has("data/*.bsa", "data/*.ba2"):
        add("creation", 100, ix.find("data/*.esm")[:3], archives=len(ix.find("data/*.bsa", "data/*.ba2")))

    # Genie, Paradox, RE Engine, FromSoft, RAGE, REDengine
    if ix.has("resources/_common/dat/*.dat") and ix.has("aoe*_s.exe"):
        add("genie", 100, ix.find("resources/_common/dat/*.dat")[:1])
    if ix.has_dir("common") and ix.has_dir("events") and ix.has("launcher-settings.json", "*/launcher-settings.json"):
        add("clausewitz", 90, ["common/ + events/ + launcher-settings.json"])
    if ix.has("re_chunk_000.pak", "re_chunk_000.pak.*"):
        add("re-engine", 100, ["re_chunk_000.pak"])
    if ix.has("*eldenring.exe", "*darksoulsiii.exe", "*sekiro.exe", "*armoredcore6.exe", "*nightreign.exe") or ix.has("*regulation.bin"):
        add("fromsoft", 95, ix.find("*eldenring.exe", "*darksoulsiii.exe", "*sekiro.exe", "*armoredcore6.exe", "*nightreign.exe", "*regulation.bin")[:2])
    if ix.has("gta5.exe", "gta5_enhanced.exe", "playgtav.exe", "rdr2.exe", "gtaiv/gtaiv.exe", "gtaiv.exe", "title.rgl") or (ix.has("*.rpf") and ix.has("*.exe")):
        add("rage", 95, ix.find("gta5*.exe", "playgtav.exe", "rdr2.exe", "*gtaiv.exe", "title.rgl", "*.rpf")[:2],
            rpf_archives=len(ix.find("*.rpf")))
    if ix.has("archive/pc/content/*.archive") or ix.has("bin/x64/witcher3.exe", "content/content0/*.bundle"):
        add("redengine", 100, ix.find("archive/pc/content/*.archive", "bin/x64/witcher3.exe")[:1])

    # id Tech, CryEngine, Frostbite
    if ix.has("*.wad", "*.pk3", "base/*.pk4", "id1/pak0.pak", "base/*.resources"):
        add("idtech", 70, ix.find("*.wad", "*.pk3", "base/*.pk4", "id1/pak0.pak", "base/*.resources")[:2])
    if ix.has("*crysystem.dll"):
        add("cryengine", 95, ix.find("*crysystem.dll")[:1])
    if ix.has("data/cas.cat", "*layout.toc"):
        add("frostbite", 90, ix.find("data/cas.cat", "*layout.toc")[:1])

    # Techland, Hello Games, Stingray, Blizzard, ForzaTech
    if ix.has("*_x64_rwdi.dll", "*engine_pc.rpack", "*chromed.exe"):
        add("chrome", 95, ix.find("*engine_x64_rwdi.dll", "*_x64_rwdi.dll", "*engine_pc.rpack")[:2],
            devtools=ix.has_dir("devtools"))
    if ix.has("gamedata/pcbanks/nmsarc.*.pak"):
        add("nms", 100, ix.find("gamedata/pcbanks/nmsarc.*.pak")[:1], banks=len(ix.find("gamedata/pcbanks/*.pak")))
    if ix.has("data/bundle_database.data", "data/bundles.*.nxa") or (ix.has("*lua51.dll") and len(ix.find("data/????????????????", "data/????????????????.stream")) > 20):
        add("stingray", 90, ix.find("data/bundle_database.data", "data/bundles.*.nxa", "*lua51.dll")[:2])
    if ix.has("data/.build.config", ".build.info"):
        add("blizzard-casc", 95, ix.find("data/.build.config", ".build.info")[:1])
    if ix.has("media/zipmanifest.xml"):
        add("forzatech", 95, ["media/zipmanifest.xml"])

    # Web, LÖVE, Defold, Cocos, Haxe/HashLink, Flash
    if ix.has("resources/app.asar", "resources/app/package.json", "package.nw", "nw.dll", "*/nw.dll"):
        add("electron", 90, ix.find("resources/app.asar", "resources/app/package.json", "package.nw", "nw.dll")[:2],
            construct=ix.has("*c3runtime.js", "*c2runtime.js"), phaser=ix.has("*phaser*.js"))
    if ix.has("love.dll", "*.love", "lovec.exe"):
        add("love2d", 95, ix.find("love.dll", "*.love")[:1])
    if ix.has("game.dmanifest", "game.arcd"):
        add("defold", 100, ["game.arcd"])
    if ix.has("*libcocos2d*.dll", "*cocos2d*.dll"):
        add("cocos", 90, ix.find("*libcocos2d*.dll", "*cocos2d*.dll")[:1])
    if ix.has("hlboot.dat", "*/hlboot.dat"):
        add("hashlink", 100, ix.find("hlboot.dat", "*/hlboot.dat")[:1])
    elif ix.has("lime.ndll", "*lime.hdll"):
        add("haxe", 80, ix.find("lime.ndll", "*lime.hdll")[:1])
    swfs = ix.find("*.swf")
    if swfs and len(swfs) <= 30:
        add("flash", 80, swfs[:2])

    # SteamDB corroboration / fallback
    steamdb = sorted((rule_hits or {}).get("Engine", {}))
    facts["rule_engines"] = steamdb
    known = {h["key"] for h in hits}
    for name in steamdb:
        key = STEAMDB_ENGINE_MAP.get(name)
        if key and key not in known and not (key == "unity-mono" and "unity-il2cpp" in known):
            add(key, 60, [f"rule {name}: " + ", ".join(rule_hits["Engine"][name][:2])])
            known.add(key)

    if not hits:
        add("native", 10, ["no known engine signature"])
    hits.sort(key=lambda h: -h["score"])
    return hits, facts
