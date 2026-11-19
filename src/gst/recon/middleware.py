"""Network and voice middleware from file names (assemblies, native DLLs, plugin folders).

Cheap and offline: matches the install's relative paths only (no file is opened). Unity Mono/IL2CPP ship one
managed assembly per package (``Managed/PhotonUnityNetworking.dll``, IL2CPP keeps their names in
``global-metadata.dat`` but the native plugins and ``ScriptingAssemblies.json`` still list them), Godot ships
GDExtension libraries (``addons/godotsteam/...``) or .NET assemblies, Unreal ships third-party DLLs under
``Engine/Binaries/ThirdParty/<SDK>/``. Every hit keeps up to three evidence paths.
"""

from __future__ import annotations

import re

# name -> (kind, regexes on the lower-case '/'-separated relative path). kind: network | voice | platform.
SIGNATURES: dict[str, tuple[str, tuple[str, ...]]] = {
    "Photon PUN": ("network", (r"(?:^|/)photonunitynetworking(?:\.utilities)?\.dll$", r"(?:^|/)photonrealtime\.dll$",
                               r"(?:^|/)photon3unity3d\.dll$", r"(?:^|/)photon\.pun\.dll$")),
    "Photon Fusion": ("network", (r"(?:^|/)fusion\.(?:runtime|realtime|sockets|common)\.dll$",)),
    "Photon Quantum": ("network", (r"(?:^|/)quantum\.(?:deterministic|engine|simulation)\.dll$",
                                   r"(?:^|/)photon\.deterministic\.dll$")),
    "Photon Voice": ("voice", (r"(?:^|/)photon\.?voice[\w.]*\.dll$", r"(?:^|/)opus_egpv\.dll$")),
    "Dissonance": ("voice", (r"(?:^|/)dissonance[\w.]*\.dll$", r"(?:^|/)audioplugindissonance\.dll$")),
    "Vivox": ("voice", (r"(?:^|/)vivoxunity\.dll$", r"(?:^|/)unity\.services\.vivox[\w.]*\.dll$",
                        r"(?:^|/)vivoxsdk\.dll$", r"(?:^|/)thirdparty/vivox/")),
    "Steamworks.NET": ("platform", (r"(?:^|/)steamworks\.net\.dll$", r"(?:^|/)com\.rlabrecque\.steamworks\.net\.dll$")),
    "Facepunch.Steamworks": ("platform", (r"(?:^|/)facepunch\.steamworks(?:\.win64|\.posix)?\.dll$",)),
    "GodotSteam": ("platform", (r"(?:^|/)addons/godotsteam/", r"(?:^|/)(?:lib)?godotsteam[\w.]*\.(?:dll|so|dylib)$")),
    "Steam API": ("platform", (r"(?:^|/)steam_api(?:64)?\.dll$", r"(?:^|/)thirdparty/steamworks/")),
    "Epic Online Services": ("platform", (r"(?:^|/)eossdk-win(?:64|32)-shipping\.dll$", r"(?:^|/)addons/(?:eosg|epic-online-services-godot)/",
                                          r"(?:^|/)pureplayer\.eos[\w.]*\.dll$", r"(?:^|/)epiconlineservices[\w.]*\.dll$")),
    "FishNet": ("network", (r"(?:^|/)fishnet\.runtime\.dll$", r"(?:^|/)fishnet[\w.]*\.dll$")),
    "Mirror": ("network", (r"(?:^|/)mirror\.dll$", r"(?:^|/)mirror\.(?:components|transports|authenticators)\.dll$")),
    "Mirror transports (kcp2k/Telepathy)": ("network", (r"(?:^|/)kcp2k\.dll$", r"(?:^|/)telepathy\.dll$")),
    "Netcode for GameObjects": ("network", (r"(?:^|/)unity\.netcode\.runtime\.dll$",)),
    "Netcode for Entities": ("network", (r"(?:^|/)unity\.netcode\.dll$",)),
    "Unity Transport": ("network", (r"(?:^|/)unity\.networking\.transport\.dll$",)),
    "Unity Gaming Services (Relay/Lobby)": ("network", (r"(?:^|/)unity\.services\.(?:relay|lobbies|lobby|multiplayer)[\w.]*\.dll$",)),
    "LiteNetLib": ("network", (r"(?:^|/)litenetlib\.dll$",)),
    "Riptide": ("network", (r"(?:^|/)riptidenetworking\.dll$",)),
    "Netick": ("network", (r"(?:^|/)netick(?:\.unity)?\.dll$",)),
    "Coherence": ("network", (r"(?:^|/)coherence\.(?:core|toolkit)[\w.]*\.dll$",)),
    "Discord Game SDK": ("platform", (r"(?:^|/)discord_game_sdk\.dll$",)),
    "Opus codec": ("voice", (r"(?:^|/)(?:lib)?opus\.dll$", r"(?:^|/)concentus\.dll$")),
    "Unreal OnlineSubsystem": ("network", (r"(?:^|/)plugins/online/onlinesubsystem\w*/",)),
}

_COMPILED = {name: (kind, tuple(re.compile(rx) for rx in rxs)) for name, (kind, rxs) in SIGNATURES.items()}


def detect(paths, dirs=None) -> list[dict]:
    """``[{name, kind, evidence}]`` sorted by kind then name; ``paths`` are relative to the install."""
    pool = [p.replace("\\", "/").lower() for p in paths]
    pool += [d.replace("\\", "/").lower().rstrip("/") + "/" for d in (dirs or ())]
    out = []
    for name, (kind, rxs) in _COMPILED.items():
        evidence: list[str] = []
        for path in pool:
            if any(rx.search(path) for rx in rxs):
                if path not in evidence:
                    evidence.append(path)
                if len(evidence) >= 3:
                    break
        if evidence:
            out.append({"name": name, "kind": kind, "evidence": evidence})
    order = {"network": 0, "voice": 1, "platform": 2}
    return sorted(out, key=lambda m: (order.get(m["kind"], 9), m["name"]))


def summary(found: list[dict]) -> str:
    """One line for RECON.md / `gst recon`: ``network: A, B; voice: C; platform: D``."""
    parts = []
    for kind in ("network", "voice", "platform"):
        names = [m["name"] for m in found if m["kind"] == kind]
        if names:
            parts.append(f"{kind}: {', '.join(names)}")
    return "; ".join(parts) or "none detected"
