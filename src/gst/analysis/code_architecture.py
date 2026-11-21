"""``gst analyze code_architecture``: how the game's code is organized and which patterns and techniques it uses ->
``analysis/code_architecture.json`` + ``.md`` (study material: structure, libraries, entry points, patterns,
performance techniques).

Sources: the code index (``index/code.db``: files per module = assembly, symbols with line ranges, bases, call
targets), the decompiled sources it points to (pattern lines and ``using`` directives) and, for Unity, the install's
``*_Data/Managed`` folder (every assembly the build ships, decompiled or not).

- ``modules``: every indexed module with its kind (game, engine, system, third-party, other), the library it belongs
  to and what that library is for (``LIBRARIES``, by assembly or namespace name), files, lines, types, namespaces.
- ``libraries``: the ``using`` directives of the game's own modules folded by library: which game files use it.
- ``entry``: lifecycle method counts, the longest per-frame methods, manager-like types ranked by how often other
  code calls them, the most common base types.
- ``patterns``: line regexes (``PATTERNS``) over the C# sources of the game's own modules (``Assembly-CSharp`` by
  default; option ``modules``), each hit tagged with the method it sits in: ``hot`` = Update, FixedUpdate,
  LateUpdate, OnGUI...; ``init`` = Awake, Start, OnEnable. Groups: events, architecture, data, async, network,
  entry, performance (techniques) and hot-cost (allocations and lookups inside per-frame methods).

Counts are signals, not verified facts: the note (template ``arquitetura-codigo``) confirms them from the examples.
"""

from __future__ import annotations

import collections
import re
import sqlite3
from pathlib import Path

from gst.analysis import ANY, Report, analysis, counted, md_table

HOT = frozenset({"Update", "FixedUpdate", "LateUpdate", "OnGUI", "OnAnimatorMove", "OnAnimatorIK"})
INIT = frozenset({"Awake", "Start", "OnEnable"})
LIFECYCLE = ("Awake", "OnEnable", "Start", "Update", "FixedUpdate", "LateUpdate", "OnGUI", "OnDisable", "OnDestroy",
             "OnValidate", "OnApplicationQuit", "OnTriggerEnter", "OnCollisionEnter")
MANAGER_RX = re.compile(r"(Manager|Handler|Service|Director|Bootstrap\w*|Loader|Spawner|Controller|System|Game)$")
TYPE_KINDS = ("class", "struct", "interface", "enum")

# (regex on an assembly or namespace name, library, purpose, kind). First match wins.
LIBRARIES: list[tuple[str, str, str, str]] = [
    (r"^(UnityEngine|UnityEditor)\b", "Unity engine", "engine API", "engine"),
    (r"^Unity\.TextMeshPro|^TMPro\b", "TextMeshPro", "text rendering", "engine"),
    (r"^Unity\.(InputSystem|Input)|^UnityEngine\.InputSystem", "Unity Input System", "input", "engine"),
    (r"^Unity\.RenderPipelines|^UnityEngine\.Rendering", "Unity render pipeline", "rendering (URP/HDRP)", "engine"),
    (r"^Unity\.(Burst|Collections|Mathematics|Jobs)\b", "Unity DOTS packages", "Burst/Jobs/maths", "engine"),
    (r"^Unity\.Addressables|^UnityEngine\.AddressableAssets|^Unity\.ResourceManager", "Addressables",
     "asset loading", "engine"),
    (r"^Unity\.Localization", "Unity Localization", "localization", "engine"),
    (r"^Unity\.Cinemachine|^Cinemachine\b", "Cinemachine", "camera", "engine"),
    (r"^Unity\.Netcode", "Netcode for GameObjects", "networking", "engine"),
    (r"^Unity\.Services", "Unity Gaming Services", "online services", "engine"),
    (r"^Unity\.VisualScripting|^Bolt\b", "Visual Scripting", "visual scripting", "engine"),
    (r"^Unity\.", "Unity package", "engine package", "engine"),
    (r"^(System|mscorlib|netstandard|Mono\.|Microsoft\.)", ".NET", "base class library", "system"),
    (r"^(PhotonVoice|Photon\.Voice)", "Photon Voice", "voice chat", "third-party"),
    (r"^(PhotonUnityNetworking|Photon\.Pun|PhotonRealtime|Photon\.Realtime|Photon3Unity3D|ExitGames|PhotonChat|Photon\.Chat)",
     "Photon PUN/Realtime", "networking", "third-party"),
    (r"^Fusion\b|^Photon\.Fusion", "Photon Fusion", "networking", "third-party"),
    (r"^(com\.rlabrecque\.steamworks|Steamworks)\b", "Steamworks.NET", "Steam API", "third-party"),
    (r"^Facepunch", "Facepunch.Steamworks", "Steam API", "third-party"),
    (r"^(Epic\.OnlineServices|PlayEveryWare|EpicCommon|Epic\b)", "Epic Online Services", "online services",
     "third-party"),
    (r"^(Mirror|kcp2k|Telepathy)\b", "Mirror", "networking", "third-party"),
    (r"^FishNet", "FishNet", "networking", "third-party"),
    (r"^(LiteNetLib|Riptide|ENet)\b", "UDP networking library", "networking", "third-party"),
    (r"^Dissonance", "Dissonance", "voice chat", "third-party"),
    (r"^(VivoxUnity|Unity\.Services\.Vivox)", "Vivox", "voice chat", "third-party"),
    (r"^(DOTween|DG\.Tweening)", "DOTween", "tweening", "third-party"),
    (r"^(LeanTween|PrimeTween)", "Tween library", "tweening", "third-party"),
    (r"^Newtonsoft", "Json.NET", "JSON", "third-party"),
    (r"^(MessagePack|Google\.Protobuf|protobuf|Ceras)", "Binary serializer", "serialization", "third-party"),
    (r"^Sirenix", "Odin", "editor/serialization", "third-party"),
    (r"^Rewired", "Rewired", "input", "third-party"),
    (r"^FMOD|^FMODUnity", "FMOD", "audio engine", "third-party"),
    (r"^(AK\b|AkSoundEngine|Ak\w+)", "Wwise", "audio engine", "third-party"),
    (r"^(UniTask|Cysharp)", "UniTask", "async", "third-party"),
    (r"^(Zenject|VContainer|Extenject)", "DI container", "dependency injection", "third-party"),
    (r"^(UniRx|R3)\b", "Reactive extensions", "reactive events", "third-party"),
    (r"^RootMotion\.Dynamics", "PuppetMaster", "active ragdoll", "third-party"),
    (r"^RootMotion", "Final IK", "inverse kinematics", "third-party"),
    (r"^Animancer", "Animancer", "animation", "third-party"),
    (r"^(Pathfinding|AstarPathfinding)", "A* Pathfinding Project", "pathfinding", "third-party"),
    (r"^Obi\b", "Obi", "physics (ropes/cloth)", "third-party"),
    (r"^(NaughtyAttributes|MyBox)", "Inspector attributes", "editor tooling", "third-party"),
    (r"^(UnityUIExtensions|UnityEngine\.UI\.Extensions)", "Unity UI Extensions", "UI widgets", "third-party"),
    (r"websocket-sharp|^WebSocketSharp", "websocket-sharp", "WebSockets", "third-party"),
    (r"^Discord", "Discord SDK", "Discord integration", "third-party"),
    (r"^Sentry", "Sentry", "crash reporting", "third-party"),
    (r"^(0Harmony|HarmonyLib)", "Harmony", "runtime patching", "third-party"),
    (r"^MoreMountains", "Feel/TopDown Engine", "game feel / framework", "third-party"),
    (r"^(Lean\.|LeanPool)", "Lean", "pooling/touch", "third-party"),
    (r"^(DemiLib|DOTweenPro)", "DOTween", "tweening", "third-party"),
    (r"^Tayx\.Graphy", "Graphy", "FPS/memory/audio monitor", "third-party"),
    (r"UnityMeshSimplifier", "Unity Mesh Simplifier", "mesh simplification (LOD)", "third-party"),
    (r"^(JetBrains\.Annotations)", "JetBrains Annotations", "code annotations", "third-party"),
    (r"^(XInputDotNet|SharpDX)", "XInput/SharpDX", "controller input", "third-party"),
]
_LIB_RX = [(re.compile(rx), lib, purpose, kind) for rx, lib, purpose, kind in LIBRARIES]

# (key, group, label, regex, scope): scope "all" counts every line, "hot" only lines inside per-frame methods.
PATTERNS: list[tuple[str, str, str, str, str]] = [
    ("csharp_event", "events", "C# `event` fields", r"\bevent\s+[\w.<>, ]+\s+\w+\s*[;={]", "all"),
    ("action_delegate", "events", "Action/Func delegate fields",
     r"^\s*(public|private|protected|internal)(\s+(static|readonly))*\s+(System\.)?(Action|Func)(<[^;=()]*>)?\s+\w+\s*[;=]",
     "all"),
    ("event_subscribe", "events", "subscriptions (`+=` handler)", r"\w\s*\+=\s*(new\s+\w+\(\s*)?[A-Za-z_][\w.]*\s*\)?\s*;", "all"),
    ("unity_event", "events", "UnityEvent", r"\bUnityEvent(<[^>]*>)?\b", "all"),
    ("send_message", "events", "SendMessage/BroadcastMessage", r"\b(SendMessage|BroadcastMessage|SendMessageUpwards)\s*\(", "all"),
    ("event_bus", "events", "message/event bus",
     r"\b\w*(EventBus|MessageBus|EventManager|EventHub|Messenger|EventChannel)\b|\b(Publish|Subscribe|Unsubscribe|Broadcast)\s*<",
     "all"),
    ("delegate_type", "events", "delegate types", r"\bdelegate\s+[\w<>]+\s+\w+\s*\(", "all"),
    ("singleton", "architecture", "singleton declarations (static Instance, Singleton<T> base, instance = this)",
     r"\bstatic\s+[\w<>.]+\s+(Instance|instance|Singleton|I)\s*[{;=]|:\s*(Mono)?Singleton\w*<|\b[Ii]nstance\s*=\s*this\b",
     "all"),
    ("singleton_access", "architecture", "singleton accesses (X.Instance, Singleton<T>.Instance)",
     r"\b\w+(<\w+>)?\.(Instance|instance)\b", "all"),
    ("service_locator", "architecture", "service locator",
     r"\bGetService\s*<|\bRegisterService\b|\bServiceLocator\b|\bAddService\s*<|\bServices\.Get\b", "all"),
    ("dependency_injection", "architecture", "dependency injection", r"\[Inject\]|\bContainer\.(Resolve|Bind)\b", "all"),
    ("state_machine", "architecture", "state machines (enum State, State classes, switch on state)",
     r"\benum\s+\w*State\b|\bclass\s+\w*(StateMachine|State)\b|\bswitch\s*\(\s*(this\.)?\w*[sS]tate\w*\s*\)|\bI\w*State\b",
     "all"),
    ("interface_impl", "architecture", "interfaces declared", r"\binterface\s+I[A-Z]\w*", "all"),
    ("require_component", "architecture", "RequireComponent (composition)", r"\[RequireComponent\b", "all"),
    ("scriptable_object", "data", "ScriptableObject data", r":\s*(ScriptableObject|SingletonAsset<\w+>)\b|\[CreateAssetMenu", "all"),
    ("serialize_reference", "data", "[SerializeReference] (polymorphic data)", r"\[SerializeReference\]", "all"),
    ("serialize_field", "data", "[SerializeField]", r"\[SerializeField\]", "all"),
    ("json", "data", "JSON (JsonUtility/Json.NET)", r"\bJsonUtility\.|\bJsonConvert\.|\bJObject\b", "all"),
    ("asset_loading", "data", "runtime asset loading", r"\bAddressables\.|\bResources\.Load\w*\b|\bAssetBundle\.Load\w*", "all"),
    ("coroutine", "async", "coroutines", r"\bIEnumerator\s+\w+\s*\(|\bStartCoroutine\s*\(", "all"),
    ("async_await", "async", "async/await", r"\basync\s+(Task|void|UniTask|Awaitable|ValueTask)\b|\bawait\s", "all"),
    ("jobs_burst", "async", "Jobs/Burst/NativeArray", r"\bIJob\w*\b|\[BurstCompile|\bNativeArray\s*<|\bJobHandle\b", "all"),
    ("threads", "async", "threads/thread pool", r"\bnew\s+Thread\s*\(|\bThreadPool\.|\bTask\.Run\s*\(|\bParallel\.For", "all"),
    ("invoke", "async", "Invoke/InvokeRepeating", r"\bInvoke(Repeating)?\s*\(\s*(\"|nameof)", "all"),
    ("rpc", "network", "RPC attributes", r"\[(PunRPC|ClientRpc|ServerRpc|Command|TargetRpc|Rpc)\b", "all"),
    ("raise_event", "network", "network events (RaiseEvent)", r"\bRaiseEvent\s*\(", "all"),
    ("net_sync", "network", "state sync (serialize view, SyncVar, NetworkVariable)",
     r"\bOnPhotonSerializeView\b|\[SyncVar\b|\bNetworkVariable\s*<", "all"),
    ("runtime_initialize", "entry", "[RuntimeInitializeOnLoadMethod]", r"\[RuntimeInitializeOnLoadMethod\b", "all"),
    ("dont_destroy", "entry", "DontDestroyOnLoad", r"\bDontDestroyOnLoad\s*\(", "all"),
    ("execution_order", "entry", "[DefaultExecutionOrder]", r"\[DefaultExecutionOrder\s*\(", "all"),
    ("scene_load", "entry", "scene loading", r"\bSceneManager\.LoadScene(Async)?\s*\(|\bPhotonNetwork\.LoadLevel\s*\(", "all"),
    ("object_pool", "performance", "object pooling",
     r"\bObjectPool\s*<|\bIObjectPool\b|\b\w*Pool(Manager)?\s*<|\b\w*[Pp]ool\w*\.(Get|Release|Return|Spawn|Despawn|Rent)\s*\(", "all"),
    ("non_alloc", "performance", "*NonAlloc physics/queries", r"\b\w+NonAlloc\s*\(", "all"),
    ("cached_component", "performance", "GetComponent in Awake/Start/OnEnable (cached)",
     r"\bGetComponent(InChildren|InParent|s)?\s*[<(]", "init"),
    ("hash_ids", "performance", "precomputed ids (StringToHash/PropertyToID)",
     r"\b(Animator\.StringToHash|Shader\.PropertyToID)\s*\(", "all"),
    ("compare_tag", "performance", "CompareTag", r"\.CompareTag\s*\(", "all"),
    ("sqr_magnitude", "performance", "sqrMagnitude distance checks", r"\.sqrMagnitude\b", "all"),
    ("string_builder", "performance", "StringBuilder", r"\bStringBuilder\b", "all"),
    ("structs", "performance", "struct types", r"\bstruct\s+[A-Z]\w*", "all"),
    ("span_stackalloc", "performance", "Span/stackalloc", r"\bstackalloc\b|\b(ReadOnly)?Span\s*<", "all"),
    ("lod_culling", "performance", "LOD and culling",
     r"\bLODGroup\b|\bCullingGroup\b|\bOnBecame(Visible|Invisible)\b|\.isVisible\b|\bGeometryUtility\.TestPlanesAABB", "all"),
    ("update_manager", "performance", "custom update manager / tick lists",
     r"\bUpdateManager\b|\bI\w*(Updatable|Updateable|Tickable)\b|\bManagedUpdate\b|\bTick\s*\(\s*float\b", "all"),
    ("interval_update", "performance", "work spread over time inside Update (timers, frame modulo)",
     r"\bframeCount\s*%|\b\w*([tT]imer|[cC]ooldown|[cC]ounter)\w*\s*[-+]=\s*Time\.(deltaTime|fixedDeltaTime|unscaledDeltaTime)|\bTime\.time\s*[<>]=?|[<>]=?\s*Time\.time\b",
     "hot"),
    ("layer_mask", "performance", "LayerMask filtering", r"\bLayerMask\b", "all"),
    ("get_component_hot", "hot-cost", "GetComponent inside per-frame methods", r"\bGetComponent(InChildren|InParent|s)?\s*[<(]", "hot"),
    ("find_hot", "hot-cost", "Find*/FindObjectOfType inside per-frame methods",
     r"\b(GameObject\.Find\w*|FindObjects?OfType\w*|FindFirstObjectByType|FindAnyObjectByType|FindObjectsByType)\s*[<(]", "hot"),
    ("linq_hot", "hot-cost", "LINQ inside per-frame methods",
     r"\.(Where|Select|OrderBy\w*|Any|First(OrDefault)?|ToList|ToArray|Count|Sum|Max|Min)\s*\(", "hot"),
    ("string_hot", "hot-cost", "string building inside per-frame methods", r"\$\"|\bstring\.Format\s*\(|\"\s*\+|\+\s*\"", "hot"),
    ("alloc_hot", "hot-cost", "collection allocations inside per-frame methods",
     r"\bnew\s+(List|Dictionary|HashSet|Queue|Stack)\s*<|\bnew\s+\w+\[\s*\w+\s*\]", "hot"),
    ("camera_main_hot", "hot-cost", "Camera.main inside per-frame methods", r"\bCamera\.main\b", "hot"),
]
GROUPS = ("architecture", "events", "data", "async", "network", "entry", "performance", "hot-cost")
GROUP_TITLE = {"architecture": "Architecture", "events": "Events and messages", "data": "Data", "async": "Async work",
               "network": "Network", "entry": "Entry points", "performance": "Performance techniques",
               "hot-cost": "Costs inside per-frame methods"}
_PAT_RX = [(key, group, label, re.compile(rx), scope) for key, group, label, rx, scope in PATTERNS]
_USING = re.compile(r"^\s*using\s+(static\s+)?([A-Za-z_][\w.]*)\s*;")
_ORDER = re.compile(r"\[DefaultExecutionOrder\s*\(\s*(-?\d+)")
_CLASS = re.compile(r"\b(class|struct)\s+([A-Za-z_]\w*)")


def library_of(name: str) -> tuple[str, str, str] | None:
    """(library, purpose, kind) of an assembly or namespace name, None if unknown."""
    for rx, lib, purpose, kind in _LIB_RX:
        if rx.search(name):
            return lib, purpose, kind
    return None


def namespace_of(path: str, module: str) -> str:
    """Namespace from the decompiled layout ``decomp/<backend>/<module>/<ns parts>/<File>.cs``."""
    parts = path.replace("\\", "/").split("/")
    try:
        i = parts.index(module)
    except ValueError:
        return "(global)"
    ns = parts[i + 1:-1]
    return ".".join(ns) if ns else "(global)"


def method_ranges(symbols: list[tuple[str, int, int]]) -> list[tuple[int, int, str]]:
    """``[(line, end_line, "hot"|"init")]`` of the per-frame and init methods of one file."""
    out = []
    for name, line, end in symbols:
        if name in HOT:
            out.append((line, end or line, "hot"))
        elif name in INIT:
            out.append((line, end or line, "init"))
    return out


def where(ranges: list[tuple[int, int, str]], line: int) -> str:
    for start, end, tag in ranges:
        if start <= line <= end:
            return tag
    return ""


def scan_source(text: str, ranges: list[tuple[int, int, str]]):
    """Yield ``(key, line, tag, stripped text)`` for every pattern hit of one C# file, and ``("using", line, ns, "")``
    for its using directives. Comments are skipped (``//`` lines)."""
    for n, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("//"):
            continue
        u = _USING.match(raw)
        if u:
            yield "using", n, u.group(2), ""
            continue
        tag = where(ranges, n)
        for key, _group, _label, rx, scope in _PAT_RX:
            if scope == "hot" and tag != "hot":
                continue
            if scope == "init" and tag != "init":
                continue
            if rx.search(raw):
                yield key, n, tag, stripped[:160]


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _game_modules(modules: dict[str, dict], pattern: str | None) -> list[str]:
    names = sorted(modules)
    if pattern:
        rx = re.compile(pattern)
        return [m for m in names if rx.search(m)]
    exact = [m for m in names if m == "Assembly-CSharp"]
    if exact:
        return exact
    prefixed = [m for m in names if m.startswith("Assembly-CSharp") and "firstpass" not in m]
    if prefixed:
        return prefixed
    others = [m for m in names if modules[m]["kind"] in ("other", "game")]
    return [max(others, key=lambda m: modules[m]["files"])] if others else []


def _examples(hits: list[dict], n: int, prefer: str = "") -> list[dict]:
    """First hit per file (the preferred tag first), sorted by path then line, at most ``n``."""
    pool = sorted(hits, key=lambda h: (h["tag"] != prefer if prefer else False, h["path"], h["line"]))
    seen, out = set(), []
    for h in pool:
        if h["path"] in seen:
            continue
        seen.add(h["path"])
        out.append({"path": h["path"], "line": h["line"], "in": h["tag"] or "", "text": h["text"]})
    out.sort(key=lambda e: (e["path"], e["line"]))
    return out[:n]


@analysis("code_architecture", engines=(ANY,), needs=("code.index",),
          description="Code architecture: modules and namespaces, third-party libraries and where they are used, "
                      "entry points and lifecycle, design patterns and performance techniques with examples",
          version=1,
          options={"modules": "regex on module (assembly) names whose sources count as the game's own code "
                              "(default: Assembly-CSharp)",
                   "examples": "examples kept per pattern (default 6)"})
def code_architecture(ctx) -> Report:
    db = ctx.workspace / "index" / "code.db"
    ctx.use("code index", db)
    n_examples = int(ctx.opt("examples", 6))
    con = _connect(db)
    try:
        files = [dict(r) for r in con.execute("SELECT id, path, lang, module, lines FROM files ORDER BY path")]
        kinds = collections.defaultdict(collections.Counter)
        types_by_file = collections.defaultdict(list)
        methods_by_file = collections.defaultdict(list)
        lifecycle = collections.defaultdict(collections.Counter)
        file_module = {f["id"]: f["module"] or "(none)" for f in files}
        for r in con.execute("SELECT file_id, kind, name, qname, line, end_line FROM symbols"):
            module = file_module.get(r["file_id"], "(none)")
            kinds[module][r["kind"]] += 1
            if r["kind"] in TYPE_KINDS:
                types_by_file[r["file_id"]].append((r["kind"], r["name"], r["qname"], r["line"]))
            elif r["kind"] == "method":
                methods_by_file[r["file_id"]].append((r["name"], r["line"], r["end_line"] or r["line"]))
                if r["name"] in LIFECYCLE:
                    lifecycle[module][r["name"]] += 1
        bases = collections.defaultdict(collections.Counter)
        for r in con.execute("SELECT file_id, base_name FROM bases"):
            if r["base_name"]:
                bases[file_module.get(r["file_id"], "(none)")][r["base_name"]] += 1
        inbound = collections.Counter()
        for (target,) in con.execute("SELECT target FROM refs WHERE target IS NOT NULL"):
            head = re.sub(r"<.*", "", str(target).split(".", 1)[0])
            if head:
                inbound[head] += 1
    finally:
        con.close()

    # --- modules --------------------------------------------------------------------------------------
    modules: dict[str, dict] = {}
    ns_files = collections.defaultdict(collections.Counter)
    ns_types = collections.defaultdict(collections.Counter)
    for f in files:
        m = f["module"] or "(none)"
        e = modules.setdefault(m, {"name": m, "files": 0, "lines": 0, "langs": collections.Counter()})
        e["files"] += 1
        e["lines"] += f["lines"] or 0
        e["langs"][f["lang"] or "?"] += 1
        ns = namespace_of(f["path"], m)
        ns_files[m][ns] += 1
        ns_types[m][ns] += len(types_by_file.get(f["id"], ()))
    for m, e in modules.items():
        lib = library_of(m) or _library_by_namespace(ns_files[m])
        e["kind"] = lib[2] if lib else ("game" if m.startswith("Assembly-CSharp") and "firstpass" not in m else "other")
        e["library"], e["purpose"] = (lib[0], lib[1]) if lib else (None, None)
        e["types"] = {k: kinds[m][k] for k in TYPE_KINDS if kinds[m][k]}
        e["methods"] = kinds[m]["method"]
        e["langs"] = counted(e["langs"])
        e["namespaces"] = [{"name": ns, "files": n, "types": ns_types[m][ns]}
                           for ns, n in sorted(ns_files[m].items(), key=lambda kv: (-kv[1], kv[0]))]
    game_mods = _game_modules(modules, ctx.opt("modules"))
    for m in game_mods:
        modules[m]["kind"] = "game"
    namespace_module = {}
    for m, e in modules.items():
        for ns in e["namespaces"]:
            if ns["name"] != "(global)":
                namespace_module.setdefault(ns["name"], m)

    # --- shipped assemblies (Unity Managed folder) ----------------------------------------------------
    shipped = []
    install = ctx.install_dir
    if install and Path(install).is_dir():
        for managed in sorted(Path(install).glob("*_Data/Managed")):
            ctx.use("managed assemblies", managed)
            for dll in sorted(managed.glob("*.dll")):
                name = dll.stem
                lib = library_of(name)
                kind = lib[2] if lib else ("game" if name in game_mods else "other")
                shipped.append({"assembly": name, "kind": kind, "library": lib[0] if lib else None,
                                "purpose": lib[1] if lib else None, "decompiled": name in modules,
                                "bytes": dll.stat().st_size})

    # --- sources of the game's own modules -------------------------------------------------------------
    hits = collections.defaultdict(list)
    usings = collections.defaultdict(list)
    orders = []
    game_ids = [f for f in files if (f["module"] or "(none)") in game_mods and (f["lang"] or "") == "csharp"]
    for f in game_ids:
        path = ctx.workspace / f["path"]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ranges = method_ranges(methods_by_file.get(f["id"], []))
        lines = text.splitlines()
        for key, line, tag, snippet in scan_source(text, ranges):
            if key == "using":
                usings[tag].append({"path": f["path"], "line": line})
                continue
            hits[key].append({"path": f["path"], "line": line, "tag": tag, "text": snippet})
            if key == "execution_order":
                m = _ORDER.search(lines[line - 1])
                cls = next((c.group(2) for ln in lines[line - 1:line + 4] for c in [_CLASS.search(ln)] if c), None)
                if m:
                    orders.append({"type": cls, "order": int(m.group(1)), "path": f["path"], "line": line})

    patterns = []
    for key, group, label, _rx, scope in _PAT_RX:
        h = hits.get(key, [])
        patterns.append({"key": key, "group": group, "label": label, "scope": scope, "count": len(h),
                         "files": len({x["path"] for x in h}), "hot": sum(1 for x in h if x["tag"] == "hot"),
                         "init": sum(1 for x in h if x["tag"] == "init"),
                         "examples": _examples(h, n_examples, prefer="hot" if group == "hot-cost" else "")})

    # --- libraries used by the game's code -----------------------------------------------------------
    libs: dict[str, dict] = {}
    unmapped = []
    for ns, uses in sorted(usings.items()):
        lib = library_of(ns)
        if lib is None and ns in namespace_module:
            owner = namespace_module[ns]
            if owner in game_mods:
                continue  # the game's own namespaces
            info = modules[owner]
            lib = (info["library"] or owner, info["purpose"], info["kind"])
        elif lib is None:
            owner = next((namespace_module[p] for p in _prefixes(ns) if p in namespace_module), None)
            if owner in game_mods:
                continue
            if owner:
                info = modules[owner]
                lib = (info["library"] or owner, info["purpose"], info["kind"])
        if lib is None:
            unmapped.append({"namespace": ns, "files": len({u["path"] for u in uses})})
            continue
        e = libs.setdefault(lib[0], {"library": lib[0], "purpose": lib[1], "kind": lib[2], "namespaces": {},
                                     "files": set(), "uses": []})
        e["namespaces"][ns] = len({u["path"] for u in uses})
        e["files"] |= {u["path"] for u in uses}
        e["uses"] += uses
    libraries = []
    for e in libs.values():
        file_count = collections.Counter(u["path"] for u in e["uses"])
        libraries.append({"library": e["library"], "purpose": e["purpose"], "kind": e["kind"],
                          "game_files": len(e["files"]), "namespaces": counted(e["namespaces"]),
                          "examples": [{"path": p, "line": min(u["line"] for u in e["uses"] if u["path"] == p)}
                                       for p in sorted(file_count)[:n_examples]]})
    libraries.sort(key=lambda r: (r["kind"] != "third-party", -r["game_files"], r["library"]))
    unmapped.sort(key=lambda r: (-r["files"], r["namespace"]))

    # --- entry points and lifecycle ------------------------------------------------------------------
    life = collections.Counter()
    for m in game_mods:
        life.update(lifecycle[m])
    hot_methods = []
    managers = []
    path_of = {f["id"]: f["path"] for f in files}
    for f in game_ids:
        for name, line, end in methods_by_file.get(f["id"], []):
            if name in HOT:
                hot_methods.append({"method": name, "path": f["path"], "line": line, "lines": end - line + 1})
        names = {n for n, _l, _e in methods_by_file.get(f["id"], [])}
        for kind, name, qname, line in types_by_file.get(f["id"], []):
            if kind == "class" and MANAGER_RX.search(name or ""):
                managers.append({"type": name, "path": path_of[f["id"]], "line": line, "calls_in": inbound.get(name, 0),
                                 "lifecycle": sorted(names & set(LIFECYCLE))})
    hot_methods.sort(key=lambda r: (-r["lines"], r["path"], r["line"]))
    managers.sort(key=lambda r: (-r["calls_in"], r["type"], r["path"]))
    base_counts = collections.Counter()
    for m in game_mods:
        base_counts.update(bases[m])
    orders.sort(key=lambda r: (r["order"], str(r["type"]), r["path"]))
    entry = {"lifecycle": counted(life), "per_frame_methods": len(hot_methods),
             "longest_per_frame": hot_methods[:15], "managers": managers[:25], "execution_order": orders,
             "bases": [{"base": b, "types": n} for b, n in counted(base_counts).items()][:20]}

    mods = sorted(modules.values(), key=lambda e: (["game", "other", "third-party", "engine", "system"].index(e["kind"])
                                                     if e["kind"] in ("game", "other", "third-party", "engine", "system")
                                                     else 9, -e["files"], e["name"]))
    data = {"game_modules": game_mods, "modules": mods, "shipped_assemblies": shipped, "libraries": libraries,
            "unmapped_namespaces": unmapped, "entry": entry, "patterns": patterns}
    summary = {"modules": len(mods), "game_modules": game_mods, "game_files": len(game_ids),
               "third_party_libraries": sum(1 for r in libraries if r["kind"] == "third-party"),
               "patterns_found": sum(1 for p in patterns if p["count"]),
               "per_frame_methods": len(hot_methods)}
    notes = [] if game_ids else ["no C# sources in the game's modules: patterns and libraries are empty "
                                 "(other languages: structure only)"]
    return Report(data=data, markdown=_markdown(data), summary=summary, notes=notes)


def _library_by_namespace(namespaces: collections.Counter) -> tuple[str, str, str] | None:
    """The library of a module named after nothing known, from its main namespace (``Assembly-CSharp-firstpass``
    holding ``DG.Tweening`` is DOTween)."""
    for ns, _n in sorted(namespaces.items(), key=lambda kv: (-kv[1], kv[0])):
        if ns in ("(global)", "Properties"):
            continue
        return library_of(ns)
    return None


def _prefixes(ns: str) -> list[str]:
    parts = ns.split(".")
    return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def _ex(examples: list[dict], n: int = 3) -> str:
    return "; ".join(f"`{e['path']}:{e['line']}`" for e in examples[:n]) or "-"


def _markdown(d: dict) -> str:
    out = ["## Modules", "", f"Game modules: {', '.join(d['game_modules']) or '-'}. Kind `other` = no known library "
           "matched the assembly or its main namespace: the studio's own framework or an unlisted third party.", ""]
    out.append(md_table(["module", "kind", "library", "purpose", "files", "lines", "types", "top namespaces"],
                        [(m["name"], m["kind"], m["library"], m["purpose"], m["files"], m["lines"],
                          sum(m["types"].values()), ", ".join(n["name"] for n in m["namespaces"][:4]))
                         for m in d["modules"]]))
    if d["shipped_assemblies"]:
        not_dec = [a for a in d["shipped_assemblies"] if not a["decompiled"] and a["kind"] not in ("engine", "system")]
        out += ["", f"Shipped assemblies: {len(d['shipped_assemblies'])}; not decompiled and not engine/system: "
                    + (", ".join(a["assembly"] for a in not_dec) or "-")]
    out += ["", "## Libraries used by the game's code", ""]
    out.append(md_table(["library", "kind", "purpose", "game files", "namespaces", "examples"],
                        [(r["library"], r["kind"], r["purpose"], r["game_files"], ", ".join(list(r["namespaces"])[:4]),
                          _ex(r["examples"])) for r in d["libraries"]]))
    if d["unmapped_namespaces"]:
        out += ["", "Namespaces without a known library: "
                + ", ".join(f"{u['namespace']} ({u['files']})" for u in d["unmapped_namespaces"][:20])]
    e = d["entry"]
    out += ["", "## Entry points and lifecycle", "",
            "Lifecycle methods: " + (", ".join(f"{k} {v}" for k, v in e["lifecycle"].items()) or "-"), ""]
    out.append(md_table(["manager-like type", "calls in", "lifecycle", "where"],
                        [(m["type"], m["calls_in"], ", ".join(m["lifecycle"]), f"`{m['path']}:{m['line']}`")
                         for m in e["managers"][:15]]))
    if e["execution_order"]:
        out += ["", md_table(["execution order", "type", "where"],
                             [(o["order"], o["type"], f"`{o['path']}:{o['line']}`") for o in e["execution_order"]])]
    out += ["", md_table(["longest per-frame method", "lines", "where"],
                         [(h["method"], h["lines"], f"`{h['path']}:{h['line']}`") for h in e["longest_per_frame"][:10]])]
    out += ["", md_table(["base type", "game types"], [(b["base"], b["types"]) for b in e["bases"][:12]])]
    for group in GROUPS:
        rows = [p for p in d["patterns"] if p["group"] == group]
        out += ["", f"## {GROUP_TITLE[group]}", ""]
        out.append(md_table(["pattern", "hits", "files", "in per-frame", "in init", "examples"],
                            [(p["label"], p["count"], p["files"], p["hot"], p["init"], _ex(p["examples"])) for p in rows]))
    return "\n".join(out) + "\n"
