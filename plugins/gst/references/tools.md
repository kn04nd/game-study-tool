# Tools

Which third-party tool fits a study question, its pinned version, how `gst` drives it, and whether it is
allowed. Versions come from `src/gst/data/catalog.toml` (`gst tools info <id>`); "(not in catalog)" marks
the version researched when the toolkit was built (verified 2026-10-05). Limits: [guardrails.md](../guardrails.md).

## Install and locate

| Command | Does |
| --- | --- |
| `gst tools list [--profile <p>]` | catalog with state (`ok`, `missing`, `other-version`, `guided`), installed and wanted version |
| `gst tools install <id\|profile>... [--force]` | installs with dependencies (e.g. Ghidra pulls `temurin21`); guided tools only print their steps |
| `gst tools info <id>` · `gst tools path <id> [--bin <exe>]` | catalog entry (kind, URL, hash, guided steps) · absolute path of an executable, for manual commands |
| `gst doctor [--profile <p>\|all]` · `gst selftest --tools` | tools of a profile (default `core`), GS_HOME, LongPaths, anti-cheat services · wrapped pipelines run live on the sample game (minutes) |

Profiles: `core`, `unity`, `unreal`, `jvm-lua-py`, `indie` (GDRE only), `native`, `runtime`, `graphics`, `formats`, `aaa` (empty), `all`.

Where tools live, by catalog `kind`:

- `zip`, `file`: `%LOCALAPPDATA%\game-study\tools\<id>\<version>\` (+ the zip's root, e.g. `ghidra\12.1.4\ghidra_12.1.4_PUBLIC\`);
  `GS_TOOLS` or `gst config set tools_dir <dir>` moves it. SHA-256 (pinned, GitHub digest, or first use) in `tools.lock.json` there.
- `winget`: `%LOCALAPPDATA%\Microsoft\WinGet\Packages\<winget id>_*`, or Program Files (7-Zip, ImageMagick: UAC).
- `dotnet-tool`: `%USERPROFILE%\.dotnet\tools` · `uv-tool`: `%USERPROFILE%\.local\bin` · `python`: gst's own venv ·
  `builtin`: detected only (VS Build Tools, Windows SDK) · `guided`: never installed by gst.

Install column: **A** automatic (`gst tools install <id>`: silent, free, hash-checked) · **G** guided (GUI, bundled offers,
login, EULA, driver or admin: follow the step with the user) · **O** optional (by hand, when a study needs it) ·
**not in catalog**: gst neither installs nor wraps it; install only with the user's OK. "manual:" commands write under the
workspace, never into the install; log the exact command in STUDY.md. Tags: **(R)** live process: verdict `offline-ok`
only · **(S)** injects or drops files: `gst sandbox` copy only.

Proven on real games (what is still unproven: docs/KNOWN-LIMITATIONS.md): ilspycmd, UnityPy, AssetRipper, vgmstream (ONI); GDRE (Pratfall); Vineflower,
unluac (PZ; partial on Kahlua bytecode); repak, uas (DRG); Ghidra, pyghidra-mcp, ImHex, dissect.cstruct, RenderDoc,
`gst trace` (sample game, ONI, DRG). Never exercised: Sysinternals, Cheat Engine, every runtime tool not in the catalog.

## Base and recon

| Tool | For | Version | gst / manual | Install |
| --- | --- | --- | --- | --- |
| Detect It Easy (`diec`) | compiler, packer, protector (Denuvo, SteamStub, Themida, VMProtect) | 3.21 | `gst recon --deep` (`diec -j -d` on the main exes) | A `die` · core |
| YARA-X (`yr`) | own engine and protector signatures | 1.21.0 | manual: `yr scan --output-format json <rules> <target>` | A `yara-x` · core |
| 7-Zip | zip, 7z, rar, cab | 26.04 | manual: `7z l -slt <archive>`; `7z x <archive> -o<ws dir>` | A `7zip` · formats (UAC) |
| Temurin JDK 21 (portable) | Java for Ghidra, Vineflower, unluac | 21.0.12.1 | used by `gst decompile java\|lua\|native` | A `temurin21` · core |
| pefile | PE sections, imports, CodeView/PDB | 2024.8.26 (not in catalog) | `gst recon` | gst dependency |
| SteamDB FileDetectionRuleSets · AreWeAntiCheatYet | path rules for engines, SDKs, anti-cheat · AppID → anti-cheat | vendored commits 2026-04-01 · 2026-09-18 (not in catalog) | `gst recon` | ships with gst |
| Steam store API (appdetails) | VAC, DRM notice, third-party account | cached 7 days (not in catalog) | `gst recon`, after `gst config set online_metadata yes` | opt-in |
| PCGamingWiki Cargo | engine, middleware, DRM | login required since 2026-08 (not in catalog) | manual (table `Game`) | not in catalog · G: bot password |

## .NET and Unity

| Tool | For | Version | gst / manual | Install |
| --- | --- | --- | --- | --- |
| ilspycmd | .NET/Mono → one C# project per assembly (Unity Mono, Godot .NET, XNA/FNA) | 11.1.0.9782 | `gst decompile dotnet [names] [--list] [--all] [--jobs N]`; names from a shipped `.pdb` | A `ilspycmd` · core (.NET SDK) |
| UnityPy (+ TypeTreeGeneratorAPI) | Unity inventory; textures → PNG, audio → WAV, TextAsset, Font, Mesh → OBJ, MonoBehaviour → JSON | 1.25.4 | `gst assets list`; `gst assets extract --types <T,...> [--name <re>] [--all]` | A `unitypy` · unity |
| AssetRipper | whole Unity project, scenes and prefabs as YAML | 2.0.0 | `gst assets project [--mode primary]` (headless web API on 127.0.0.1) | A `assetripper` · unity |
| UnityDataTool | official inspector: player data → SQLite (types, sizes, references) | 2.4.0 | manual: `UnityDataTool analyze <Game>_Data -o <ws>\assets\unity.db` | A `unitydatatool` · unity |
| Cpp2IL | IL2CPP → dummy DLLs with `[Address]`/`[Token]`/`[FieldOffset]` (metadata 24-31, 35, 38, 39, 104-106; Unity 6) | 2022.1.0-pre-release.21 | `gst decompile il2cpp` / `gst study` (`code.il2cpp.dummydll`): `--game-path <G> --use-processor attributeinjector --output-as dummydll` | A `cpp2il` · unity |
| Il2CppInspectorRedux (legacy CLI) | IL2CPP → method address map JSON, C# with RVAs, C++ type headers, Ghidra script (metadata 24-39, 104) | 2026.2 | `gst decompile il2cpp --maps` / `gst study` (`code.il2cpp.maps`); the new `Redux.CLI` never exits (local web server) | A `il2cppinspector` · unity |
| Il2CppDumper | legacy IL2CPP (metadata ≤ 31): `script.json`, `dump.cs`, `il2cpp.h`, dummy DLLs; v39 rejected | 6.7.46 | `gst decompile il2cpp --maps` / `gst study` (`code.il2cpp.maps`) | A `il2cppdumper` · unity |
| REA (rea-agents) | static managed members + call graph per assembly; IL2CPP metadata + GameAssembly method RVAs and field/parameter types; Godot PCK inventory cross-check | 6.9.0 (optional local build with `patches/rea/` for raisable IL2CPP caps: `GST_REA_SPEC=<tarball>`, docs/tools/rea.md) | `gst study --only rea` (`dumps/rea/`, `rea_*` tables for `gst q callers/callees`); never its Frida/runtime tools | A `rea` · core (npm, Node.js) |
| de4dotEx | detect or clean obfuscation of the game's own assemblies | 3.10.0 (not in catalog) | manual: `de4dot -d` | not in catalog · O |
| ILSpy GUI · dnSpyEx · dotPeek | browsing by hand | 11.1 · 6.6.0 · 2026.2.3.1 (not in catalog) | GUI | not in catalog · G: winget |
| AssetStudioModCLI · UABEA | fallback export · inspection by hand | 0.19.0 · v8 (not in catalog) | manual: `AssetStudioModCLI … -m export -t tex2d,audio` | not in catalog · O · G |
| UnityExplorer + BepInEx · MelonLoader **(R)(S)** | live inspection inside the game | 4.13.6 + 5.4.23.5 / 6.0.0-be.788 · 0.7.3 (not in catalog) | GUI in game, started from `gst sandbox` | not in catalog · G: sandbox only |
| xnbcli · xnb-js | XNA `.xnb` content | 1.0.7 · 1.3.0-a (not in catalog) | manual: `xnbcli unpack` | not in catalog |

## Unreal

| Tool | For | Version | gst / manual | Install |
| --- | --- | --- | --- | --- |
| repak | `.pak` v1–v11: info, list, unpack | 0.2.3 | `gst assets list` (`repak list`); manual: `repak unpack <pak> -o <ws dir>` | A `repak` · unreal |
| UnrealAssetScout (`uas`) | CUE4Parse: list; export JSON (with Kismet bytecode), textures, audio, models, raw; paks and IoStore | 0.5.0 | `gst assets list`; `gst assets extract --types json\|textures\|audio\|models\|raw --name <re> [--bytecode] [--ue 4.27]` | A `uas` · unreal |
| retoc | IoStore `.utoc/.ucas`: info, list, to-legacy | 0.1.5 | manual: `retoc info\|list <utoc>`; `retoc to-legacy <Paks> <ws dir>` | A `retoc` · unreal |
| FModel · CUE4Parse | browse and preview (GUI) · the library under uas | Aug 2026 · 1.2.2.202610 (not in catalog) | GUI · through uas | not in catalog · G: FModel zip |
| UAssetGUI | `.uasset` → JSON (uas already covers it) | 1.1.0 (not in catalog) | manual: `UAssetGUI tojson in out VER_UE4_27` | not in catalog |
| kismet-analyzer · KismetKompiler | Blueprint control-flow graph · `.kms` | dev build · 0.4.0-alpha (not in catalog) | manual: `kismet-analyzer gen-cfg-tree … --render` | not in catalog · O |
| RE-UE4SS · Dumper-7 · jmap **(R)(S)** | reflection dumps, SDK headers, `.usmap` | 3.0.1 · 7.0.1 · 0.2.0 (not in catalog) | injection, `gst sandbox` only | not in catalog · G: sandbox only |

repak 0.2.3 stops at pak v11 (UE 5.3+ writes v12): gst lists those with uas. A narrower uas `--filter` deletes earlier
exports (gst passes the union). Encrypted containers are diagnosed, never opened (inventory from `Manifest_UFSFiles_*.txt`).

## Other engines

| Tool | For | Version | gst / manual | Install |
| --- | --- | --- | --- | --- |
| GDRE Tools (gdsdecomp) | Godot 2/3/4: GDScript decompiled, project recovered, resources to text/PNG/WAV | 2.7.0 | `gst decompile godot`; `gst assets list\|extract\|project` (encrypted pack: diagnosed only) | A `gdre` · indie |
| Vineflower | Java bytecode → source | 1.12.0 | `gst decompile java [--only <prefix>/] [--all]` (the game's own packages by default) | A `vineflower` · jvm-lua-py |
| unluac | Lua 5.0–5.4 bytecode → source | 2025-12-23 | `gst decompile lua` (plain Lua copied; failures kept as `.partial.lua`) | A `unluac` · jvm-lua-py |
| GodotPckTool | Godot `.pck` list and extract | 2.3 (not in catalog) | manual | not in catalog |
| jadx · luajit-decompiler-v2 | Java second opinion · LuaJIT (gst only diagnoses it) | 1.5.6 · Mar 2024 (not in catalog) | manual: `jadx -d out --no-res`; `ljdv2 in -o out -s -f` with a timeout (may open a modal dialog) | not in catalog |
| UndertaleModTool CLI · rvpacker-txt-rs | GameMaker `data.win` → GML (VM builds) · RPG Maker XP–VX Ace data → JSON | 0.9.2.0 · 16.2.0 (not in catalog) | manual: `UndertaleModCli dump data.win -c UMT_DUMP_ALL`; `rvpacker-txt-rs json generate -i <game>` | not in catalog |
| unrpa · unrpyc · pyinstxtractor-ng · pycdc · PyLingual (local) | Ren'Py archives and scripts · packed Python | 2.3.0 · 2.0.4 · 2026.07.03 · MSVC build · 0.0.1 (not in catalog) | manual: `unrpa -mp`; `unrpyc.py -c`; `pyinstxtractor-ng --info`; `pycdc f.pyc` | not in catalog |
| crashlink · hlbc · JPEXS FFDec | HashLink `hlboot.dat` · Flash/AIR (AS2/AS3) | 0.0.9 · 0.7.0 · 26.3.0 (not in catalog) | manual: `crashlink hlboot.dat -c funcs`; `ffdec-cli -export script,image out g.swf` | not in catalog |
| @electron/asar · wakaru · webcrack | HTML5/Electron, JS deobfuscation | 4.3.1 · 1.13.0 · 2.16.0 (not in catalog) | manual: `asar extract`; `wakaru --unpack` | not in catalog (webcrack: Node 22/24) |
| Source2Viewer-CLI · VPKEdit · BSPSource · srctools | Source 2 and Source 1 | 20.0 · 5.0.0.4 · 1.4.8 · 2.7.0 (not in catalog) | manual: `Source2Viewer-CLI -i pak01_dir.vpk --vpk_list` | not in catalog |
| Spriggit · Mutagen · Champollion | Creation: plugins → YAML, `.pex` → `.psc` | 0.41.0 · 0.54.4 · 1.3.2 (not in catalog) | manual: `Spriggit.CLI serialize -i Skyrim.esm -o … -g SkyrimSE` | not in catalog |
| HGPAKtool · MBINCompiler · RP6_UnpackCLI | No Man's Sky · Dying Light `.rpack` (both extract next to their input) | 1.1.3 · 7.04.1-pre3 · 1.5.1 (not in catalog) | manual: `hgpaktool -U` on a copy in the workspace; `RP6_UnpackCLI <rpack>` with the workspace as cwd | not in catalog |
| REE.PAK.Tool · filediver · rustydemon · RPKG-Tool · WitchyBND | RE Engine · Helldivers 2 · Diablo IV · Hitman · FromSoftware (static) | rolling · 0.7.58 · 0.4.4 · 2.34.0 · 3.0.1.0 (not in catalog) | manual CLIs | not in catalog · O |
| xEdit · CodeWalker · OpenIV · SparkIV · Smithbox · Creation Kit · Dying Light DevTools | community and official GUIs (xEdit read-only; headless: Spriggit/Mutagen) | (not in catalog) | GUI | not in catalog · G |

## Native code

| Tool | For | Version | gst / manual | Install |
| --- | --- | --- | --- | --- |
| Ghidra | headless disassembly and decompilation of exe/dll | 12.1.4 | `gst decompile native [modules] [--list] [--functions <re>] [--max-functions N] [--timeout s]` | A `ghidra` · core |
| pyghidra-mcp | MCP over the workspace's Ghidra project | 0.2.7 | `gst mcp add ghidra` (see MCP servers) | A `pyghidra-mcp` · native (uv tool) |
| FLOSS | static and obfuscated strings | 3.1.1 | manual: `floss -j --only static -L <exe>` (static only on big game exes) | A `floss` · native |
| dumpbin | PE headers, imports, exports as text | builtin (VS Build Tools) | manual: `dumpbin /headers /imports <pe>` | builtin `dumpbin`, detected · core |
| PyGhidra · ghidrecomp · ghidriff | Python 3 scripting · every function to `.c` + call graph · patch diff | 3.1.0 · 0.5.9 · 1.0.0 (not in catalog) | manual: `ghidrecomp --filter`; `ghidriff old new` | not in catalog |
| ReVa | Ghidra extension with MCP | 7.3.1 (not in catalog) | HTTP `/mcp/message` + API key; builds only up to Ghidra 12.1.3 | not in catalog · O |
| radare2 + r2ghidra + r2mcp | quick triage; second MCP | 6.2.2 · 6.2.2 · 1.8.8 (not in catalog) | manual: `r2 -qc "iIj;iij;izzj" <exe>` | not in catalog (rizin 0.9.1, Cutter 2.5.0: O) |
| capa | capabilities of a binary | 9.4.0 (not in catalog) | manual: `capa -j <exe>` | not in catalog |
| PE-bear · Capstone | PE viewer · disassembly library | 0.7.2 · 5.0.9 (not in catalog) | GUI · library | not in catalog · O |
| Binary Ninja Free · IDA Free | GUIs without a headless API | 6.0 · 9.4 (not in catalog) | GUI | not in catalog · O |

`gst decompile native` copies the module into the workspace and loads only the `.pdb` shipped next to it (no symbol
server; gst sets `-Dghidra.pdb.developerMode=true` for class layouts). Ghidra 12 has no Jython: `.py` scripts need
`pyghidraRun`. `static-only` games: unprotected modules only. pyghidra-mcp locks the project: not while gst decompiles it.

## Runtime (verdict `offline-ok` only)

| Tool | For | Version | gst / manual | Install |
| --- | --- | --- | --- | --- |
| `gst trace` **(R)** · `gst sandbox` | processes, read-only value scans, hardware write watchpoint named by function, folder snapshots · install copy for injectors | built into gst (ctypes: ReadProcessMemory, Debug API; no elevation, injection or writes) | `gst trace ps`; `scan <value> [--type]`; `next [<value>\|--changed…]`; `watch <0x…\|scan>`; `snapshot <name>`; `diff <a> <b>`; `gst sandbox create\|status\|remove` | with gst |
| Sysinternals Suite **(R)** | Procmon (file, registry), ProcDump, Handle, TCPView, ListDLLs, sigcheck | 2026-09-10 | manual: `Procmon64 /AcceptEula /Quiet /Minimized /BackingFile x.pml /Runtime 60` → `/SaveAs x.csv` (UAC) | A `sysinternals` · runtime |
| Cheat Engine **(R)** | value scans, find what writes, structure dissect | 7.7 | manual (GUI) | G: cheatengine.org, decline every bundled offer; then, in CE's settings, kernel-mode options off (DBK driver, DBVM, kernel debugger) and Lua scripts on "ask" (`gst tools install cheatengine` prints the steps) |
| x64dbg + x64dbg-automate **(R)** | debugger + Python automation | 2026.05.27 · 0.9.2 (not in catalog) | Python client (`gst trace watch` already answers "who writes") | not in catalog |
| Frida · frida-tools **(R)(S)** | function hooks and traces | 17.22.2 · 14.11.0 (not in catalog) | manual: `frida-trace -p <pid> -i "kernel32.dll!CreateFileW"` on the sandbox copy | not in catalog |
| WinDbg + TTD + mcp-windbg **(R)** | dumps; time travel ("who wrote X") | 1.2606.22001 · 1.11.611 · 1.4.0 (not in catalog) | manual: `TTD.exe -accepteula -noUI -attach <pid> -out <dir>`; `dx @$cursession.TTD.Memory(a,b,"w")` | not in catalog · G: recording needs admin |
| ReClass.NET **(R)** | struct reconstruction (dormant since v1.2, 2019) | 1.2 (not in catalog) | GUI | not in catalog · G: `.rar` without checksum (7-Zip) |
| Wireshark/tshark + Npcap · pktmon | the game's own LAN traffic only | 4.6.9 + 1.89 · built into Windows (not in catalog) | manual: `tshark -r cap -T json`; `pktmon start -c` → `etl2pcap` (UAC) | not in catalog · G: Npcap (silent only in OEM build) |
| PE-sieve **(R)** | dump modules from memory | 0.4.1.1 (not in catalog) | manual: `pe-sieve /pid <pid> /json` | not in catalog · O |
| API Monitor **(R)(S)** | abandoned (v2 alpha r13, 2013, HTTP-only download); prefer frida-trace | r13 (not in catalog) | GUI | not in catalog · G: exploration by hand only |

## Graphics

| Tool | For | Version | gst / manual | Install |
| --- | --- | --- | --- | --- |
| RenderDoc **(R)(S)** | frame capture: D3D11/12, Vulkan, OpenGL | 1.46 | `gst gfx capture [--after s] [--manual] [--exe x] [--args "…"]` (sandbox copy); `gst gfx export [x.rdc]` → `frame.md`; `gst gfx list` | A `renderdoc` · graphics |
| fxc (Windows SDK) | DXBC shader disassembly | builtin (Windows SDK) | manual: `fxc /dumpbin <shader>` | builtin `fxc`, detected · graphics |
| PresentMon **(R)** | frame timing via ETW, no injection | 2.6.0 (not in catalog) | manual: `--process_name X --output_file f.csv --timed 30` | not in catalog |
| PIX **(R)** | D3D12 captures | 2603.25 (not in catalog) | manual: `pixtool` | not in catalog · O (EULA) |
| Nsight Graphics **(R)(S)** | NVIDIA frame debugger | 2026.3.1 (not in catalog) | `ngfx.exe` | not in catalog · G: NVIDIA driver ≥ 615 |
| apitrace · GFXReconstruct **(R)(S)** | GL, D3D9–11 traces · Vulkan traces | 14.0 · Vulkan SDK 1.4.363.0 (not in catalog) | manual: `apitrace dump`; `gfxrecon-convert` (JSONL) | not in catalog |
| DXC · 3Dmigoto cmd_Decompiler · SPIRV-Cross | DXIL, DXBC, SPIR-V → asm, HLSL, GLSL | DXC 1.9.2609 (not in catalog) | manual: `dxc -dumpbin`; `cmd_Decompiler -D`; `spirv-cross --hlsl` | not in catalog |
| DXVK · ReShade · 3Dmigoto proxy **(S)** | capture tricks | 3.1.1 · 6.8.0 · 1.4.9 (not in catalog) | `gst sandbox` only | not in catalog · G: sandbox only |

gst keeps RenderDoc's analytics, update checks and callstack capture off. Vulkan: ask before `renderdoccmd vulkanlayer --register --user`.

## Formats and assets

| Tool | For | Version | gst / manual | Install |
| --- | --- | --- | --- | --- |
| `gst fmt` | identify, bounded hexdump, entropy, strings, folder survey, byte diff | built into gst | `gst fmt id\|hex\|entropy\|strings\|scan\|diff` | with gst |
| ImHex | hex editor + pattern language, decoded tree as JSON | 1.38.1 | `gst fmt pattern <x.hexpat> <files>` (`imhex --pl format … -f json`); GUI: `gst tools path imhex` | A `imhex` · core |
| dissect.cstruct | C-like struct readers with byte-exact round-trip (the default) | 4.7 | `gst fmt new-reader <name> [--sample <f>]`; `gst fmt roundtrip <reader.py> <samples>` | A `dissect-cstruct` · core, formats |
| vgmstream-cli | game audio (Wwise, FMOD, CRI, XACT…) → WAV + JSON | r2117 | `gst assets audio [patterns] [--list] [--name <re>]` | A `vgmstream` · core |
| FFmpeg (ffprobe) | audio/video probe and conversion (Bink 1; Bink 2 needs RAD Video Tools, G) | 9.0.2 | `gst audio index` (durations of non-WAV audio), `gst audio pcm` (ADPCM → PCM copies); manual: `ffprobe -of json -show_streams <file>` | A `ffmpeg` · core |
| texconv | DDS/BCn → PNG | 2026.5.7 | manual: `texconv -ft png -o <ws dir> <in.dds>` | A `texconv` · core |
| texdiag | DDS metadata (format, mips, arrays) | 2026.5.7 | manual: `texdiag info <in.dds>` | A `texdiag` · core |
| ImageMagick | thumbnails, contact sheets | 7.1.2.31 | manual: `magick` | A `imagemagick` · formats (UAC) |
| ImHex-Patterns | community `.hexpat` library | git (not in catalog) | with `gst fmt pattern` | not in catalog |
| Kaitai Struct · construct | `.ksy` specs → Python (read-write) · reader library | 0.11 · 2.10.70 (not in catalog) | manual: `kaitai-struct-compiler --read-write -t python` | not in catalog |
| QuickBMS | community BMS scripts | 0.12.0 (not in catalog) | manual: `quickbms -l script.bms <file>`, never the process or network modes | not in catalog (site down; mirror with the official zip's SHA-256) |
| TrID · binwalk | identification | 2.48 · 3.1.0 (not in catalog) | manual: `trid <file>`; binwalk has no Windows binary: `gst fmt id\|scan` | not in catalog |
| wwiser · cricodecs · astcenc · texture2ddecoder | Wwise banks · CRI audio · ASTC and other GPU textures | 20260808 · 1.2.0 · 5.7.0 · 1.0.6 (not in catalog) | manual | not in catalog |
| Blender · assimp · f3d · glTF-Transform · Noesis | models → glTF, thumbnail, markdown summary | 5.2.2 · 6.0.5 · 3.5.0 · 4.5.1 · 4474 (not in catalog) | manual: `f3d m.glb --output=t.png`; `gltf-transform inspect --format md` | not in catalog · G: Noesis |
| Oodle (the game's own DLL) · zstd, lz4, brotli | decompression | (not in catalog) | no gst command yet: load the game's `oo2core_*.dll` in place (ctypes), never copy it | not in catalog · O |

## Code navigation

| Tool | For | Version | gst / manual | Install |
| --- | --- | --- | --- | --- |
| tree-sitter + grammars | parsers behind the code index; decompiled code that does not compile still parses | 0.26.0 | `gst index [--rebuild]`; `gst q def\|sym\|refs\|callers\|callees\|show\|outline\|hier\|strings\|stats` | A `tree-sitter` · core |
| ripgrep | text search over `decomp/` | 15.2.0 | `gst q grep <regex> [--glob '*.cs'] [-i]`; manual: `rg --json --no-ignore-vcs` | A `ripgrep` · core |
| Universal Ctags | symbol fallback for languages without a grammar | 6.1.0 | manual: `ctags -R --output-format=json` | A `ctags` · core |
| ast-grep | structural search | 0.45.3 (not in catalog) | manual: `ast-grep run -p '<pat>' -l cs --json=stream` | not in catalog |
| csharp-ls · clangd · LuaLS | LSP navigation | 0.28.0 · 23.1.0 · 3.19.1 (not in catalog) | plugin `gst-lsp` (off by default, no server config yet) | not in catalog · O |
| Serena · Joern · ck | semantic navigation · code property graph · local semantic search | 1.7.0 · 4.0.648 · 0.7.11 (not in catalog) | none | not in catalog · O |

## MCP servers

`gst mcp add <name>` writes the workspace's `.mcp.json`, enables the server and pre-approves its read-only tools; also
`gst mcp list`, `remove <name>`, `check` (audit), `tools <name>`, `call <name> <tool> '<json>'`.

Templates shipped (`TEMPLATES` in `src/gst/mcp.py`): only `ghidra`, pyghidra-mcp over the project that
`gst decompile native` built in `decomp/ghidra/_projects/<module>/` (`--module` when several; refused when the verdict
forbids decompiling). It writes stdio, `--project-path <gpr> --no-symbols --transport stdio`, env `GHIDRA_INSTALL_DIR`,
`JAVA_HOME`, `ANONYMIZED_TELEMETRY=False`. Pre-approved: `decompile_function`, `search_symbols_by_name`, `search_code`,
`list_project_binaries`, `list_project_binary_metadata`, `list_exports`, `list_imports`, `list_xrefs`, `search_strings`,
`read_bytes`, `disassemble`, `gen_callgraph`. Denied: `delete_project_binary`. Ask: renames, comments, types, `save`, `import_binary`.

Rule: stdio or 127.0.0.1 only; pyghidra-mcp always with `--no-symbols` (otherwise PDB names and GUIDs reach public
symbol servers). `gst mcp check`, and the guard on a hand-written `.mcp.json`, refuse other hosts, `0.0.0.0`, a non-local
`--host`/`--bind`/`--listen`, pyghidra-mcp without `--no-symbols`, and the rejected servers. Pin versions, review a
server before enabling it, raise `MAX_MCP_OUTPUT_TOKENS` only when needed.

Optional servers from the plan (no template, so `gst mcp add` refuses them; configure by hand only with the user's OK):

| Server | For | Transport and caveats |
| --- | --- | --- |
| r2mcp 1.8.8 | light second native MCP | stdio, `-R` read-only, `-s <dir>` sandbox |
| mcp-windbg 1.4.0 | dumps, TTD | stdio only: its HTTP mode (127.0.0.1:8000) has no auth |
| x64dbg-automate-mcp 0.9.2 | debugger (preferred) | stdio + x64dbg plugin |
| x64dbg-mcp-server (duty1g) | debugger | HTTP binds 0.0.0.0:9094 by default (+ Bearer): rebind to 127.0.0.1 or skip |
| ya-frida-mcp 0.6.1 | hooks, memory (sandbox) | stdio, never `serve -H 0.0.0.0`; inject/spawn need confirmation; writes denied |
| CheatEngine.Mcp 2.0.0-beta.2 | scans, reads, disassembly, structures | authenticated stdio ↔ HTTP gateway on 127.0.0.1; `memory_write*` denied; CE without DBK/DBVM/kernel; offline copy only |
| ReClass.NET-MCP (NateWeav) | structs | stdio + TCP 127.0.0.1:27015, no auth |
| RenderDocMCP (halby24) | capture drill-down after `gst gfx export` | stdio, file IPC |
| ImHex `--mcp` | patterns, hex | nightly builds only (catalog pins 1.38.1); stdio → 127.0.0.1:19743 |
| CUE4Parse-MCP · jadx-ai-mcp · Binary Ninja Free · ast-grep-mcp | UE · Java · GUI · structural search | stdio (immature) · 127.0.0.1:8650 · 127.0.0.1:24642 + token · stdio |
| ida-mcp (HexRaysSA) | IDA | stdio or 127.0.0.1:8737; needs IDA Pro/Home 9.4+ with idalib: not applicable (free tools only) |
| also in plan §4 | FModel-mcp · ue4ss-bridge · ProcmonMCP · pix-mcp · Blender MCP · `crashlink mcp` · ReVa | HTTP 127.0.0.1:44551 (immature) · 127.0.0.1:8930 (sandbox) · XML · immature · opt-in · stdio · HTTP + API key |

Rejected (`REJECTED` in `mcp.py`: `gst mcp check` and the guard refuse them), then avoided (plan only, no gst pattern):

- LaurieWired GhidraMCP (`bridge_mcp_ghidra`): HTTP on every interface, no auth, unmaintained · D.I.E-MCP: SSE on 0.0.0.0:8080.
- cervonwong ILSpy-MCP: HTTP on 0.0.0.0:3001 · xrkk dnSpy MCP: LAN address by default · WireMCP: external lookups.
- il2cpp-dump-analyzer: remote database · cheatengine-mcp-bridge: no safety switches, kernel evasion tools.
- MCP-UAsset-Toolkit, the UndertaleModTool MCP fork: write tools over game assets (modding).
- Avoid: Unity-Runtime-MCP (writes), dnakov/frida-mcp (unmaintained), frida-game-hacking-mcp (APIs removed in Frida 16), CE TCP bridges on 0.0.0.0.

## Excluded on purpose

| Tool | Why |
| --- | --- |
| Steamless · ScyllaHide | strips Steam DRM · hides debuggers from protections (anti-anti-debug) |
| RenderDoc Pro | advertises anti-cheat compatibility |
| AES key extractors · Forza-Crypto-Tool | pull keys out of executables or memory · decrypts through a remote backend |
| UXM "Patch" | patches the game executable (UXM unpack is fine) |
| CodeQL | its license forbids analyzing closed-source code |
| claude-context, code-graph-rag | send code to cloud services |
| 010 Editor 16.0.4 · IDA Pro/Home (+ ida-mcp) | paid (010: US$ 59.95+; ImHex, Kaitai and dissect.cstruct cover it); free tools only |
| Intel GPA · RetDec, JD-GUI, umodel | end of life · unmaintained, legacy only |
| Il2CppDumper on Unity 6 | legacy (Unity ≤ 2022.2): Cpp2IL or Il2CppInspectorRedux instead |
| SeaGOAT | needs Python < 3.14, untested on Windows |
| binwalk | no Windows binary: `gst fmt` has its own entropy and magic scanner |

## Commands the guard denies

The PreToolUse hook and `gst guard cmd "<command>"` apply `DENY_RULES` and `KEY_FLAGS` from `src/gst/policy.py`,
plus a few checks in `guard.py`. The rules behind each number: [guardrails.md](../guardrails.md).

| Command matches (case-insensitive) | Rule |
| --- | --- |
| `steamless`; `scyllahide`; `renderdoc.net`, `renderdoc pro`; `dbk32`, `dbk64` or `dbvm` with `load`, `enable` or `install` | 2 |
| `aesdumpster`, `aeskeyfinder`, `aesfinder`, `ue4aeskey` (spaces, `_`, `-` allowed); `forza crypto`; `cricodecs … --recover-key\|--build` | 4 |
| `010editor … -process`; `quickbms … process://` | 3 |
| `ttd … -monitor`; `sc stop\|delete\|config`, `Stop-/Set-/Remove-Service`, `taskkill`, `Stop-Process` on BEService, BEDaisy, EasyAntiCheat, vgc, vgk, npggsvc, GameMon, PnkBstr, EAAntiCheat, FACEIT, ACE-, AntiCheatExpert, xhunter, mhyprot | 8 |
| `gdre_tools --pck-create\|--pck-patch\|--compile\|--txt-to-bin\|--patch-translations`; `repak pack`; `retoc pack-raw\|to-zen`; `uassetgui … fromjson` | 1 |
| `pyghidra-mcp` without `--no-symbols`, or with `--host`/`-o` other than 127.0.0.1, localhost, ::1 | 9 |
| `KEY_FLAGS` (any long option ending in `key`/`keys`/`key-file`, e.g. `--key`, `--aes-key`, `--tact-keys`; `--aes`, `--aes-file`, `-k`, `aes=`; `-a` of repak, retoc and uas, also attached as `-a<key>`; uas `-A`) with repak, retoc, gdre, uas, ue4export, cricodecs, unrpa, rpatool, pyinstxtractor, rustydemon, unless `game.json` records a published key with its source URL or a public scheme | 4 |
| `uxm … patch`; guard.py: `repak unpack` without `-o`, HGPAKtool on a path inside an install, a redirect into an install; RP6_UnpackCLI (and filediver without `-o`) run from inside an install, `cd` in the command included; unrpyc, REE.Unpacker, rvpacker, UndertaleModCli on an install path without an output folder under GS_HOME | 5 |
| guard.py: pyinstxtractor (except `--info`) on a game whose recon found a PyInstaller `--key` build (the key sits in the exe) | 4 |
| guard.py: `git add\|commit\|init\|stage` inside GS_HOME, or staging derived files | 7 |
| guard.py: runtime tools (x64dbg, frida, ttd, procdump, cheatengine, windbg, cdb, renderdoccmd, qrenderdoc, pixtool, ngfx, apitrace, pe-sieve, presentmon…) on a game whose verdict forbids runtime; asks when the game has no recon | 2 |
