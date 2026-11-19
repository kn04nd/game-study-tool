"""Protection detection (detect only, never bypass): anti-cheat, DRM/anti-tamper, launcher
accounts, online-only status, encrypted content. Feeds policy.verdict()."""

from __future__ import annotations

import json
import re
from pathlib import Path

from gst.common import run
from gst.recon import rules as rules_mod
from gst.recon import sniff
from gst.recon.index import FileIndex

# Known always-online / competitive titles (lower-case substrings of the game name).
ONLINE_ONLY = [
    "valorant", "league of legends", "fortnite", "apex legends", "pubg", "rainbow six siege", "call of duty",
    "destiny 2", "genshin impact", "escape from tarkov", "battlefield 2042", "overwatch", "counter-strike 2",
    "dota 2", "marvel rivals", "the finals", "dead by daylight", "naraka", "warframe", "deadlock", "diablo iv",
    "helldivers 2", "world of warcraft", "path of exile",
]

ACCOUNT_NOTICE_KIND = "launcher-account"
ONLINE_BACKEND_SDKS = {"Photon", "PlayFab", "AzurePlayFab", "Azure_Playfab_Party", "Nakama", "Mirror"}


def _item(kind: str, name: str, evidence: str, source: str) -> dict:
    return {"kind": kind, "name": name, "evidence": evidence, "source": source}


def from_rules(rule_hits: dict) -> list[dict]:
    out = []
    for name, paths in rule_hits.get("AntiCheat", {}).items():
        kind = "vac" if name.upper() == "VAC" else "anticheat"
        out.append(_item(kind, name.replace("_", " "), ", ".join(paths[:2]), "files"))
    for name, paths in rule_hits.get("Launcher", {}).items():
        out.append(_item(ACCOUNT_NOTICE_KIND, f"launcher: {name}", ", ".join(paths[:2]), "files"))
    return out


def from_awacy(appid: str | None) -> list[dict]:
    entry = rules_mod.awacy_lookup(appid)
    out = []
    for name in (entry or {}).get("anticheats", []):
        kind = "vac" if name == "VAC" else "anticheat"
        out.append(_item(kind, name, f"AreWeAntiCheatYet entry '{entry['name']}'", "awacy"))
    return out


def from_pe(ix: FileIndex, exes: list[str]) -> tuple[list[dict], dict, list[str]]:
    """PE section heuristics on the main executables -> (protections, pe details, protected modules)."""
    out, details, protected = [], {}, []
    for rel in exes:
        info = sniff.pe_detail(ix.path(rel))
        if not info:
            continue
        details[rel] = {k: info[k] for k in ("arch", "managed", "pdb", "version", "imports") if info.get(k)}
        details[rel]["sections"] = [s["name"] for s in info.get("sections", [])]
        for kind, name in info.get("protector_hints", []):
            out.append(_item(kind, name, rel, "pe"))
            protected.append(rel.rsplit("/", 1)[-1])
    return out, details, sorted(set(protected))


def from_die(diec: Path | None, ix: FileIndex, exes: list[str]) -> list[dict]:
    """Detect It Easy deep scan of the main exes (Protector/Protection lines only)."""
    if not diec:
        return []
    out = []
    for rel in exes[:3]:
        proc = run([str(diec), "-j", "-d", str(ix.path(rel))], check=False)
        text = proc.stdout or ""
        start = text.find("{")
        try:
            data = json.loads(text[start:]) if start >= 0 else {}
        except json.JSONDecodeError:
            continue
        for det in data.get("detects", []):
            for value in det.get("values", []):
                vtype = str(value.get("type", "")).lower()
                name = value.get("name", "")
                if vtype in {"protector", "protection"} or "denuvo" in name.lower():
                    kind = "drm-wrapper" if "steam" in name.lower() else "anti-tamper"
                    out.append(_item(kind, f"{name} {value.get('version', '')}".strip(), rel, "die"))
    return out


def from_store(store: dict | None) -> list[dict]:
    """Steam store appdetails signals (see recon/online.py)."""
    if not store:
        return []
    out = []
    cats = {c.get("id") for c in store.get("categories", [])}
    if 8 in cats:
        out.append(_item("vac", "Valve Anti-Cheat", "store category 8", "store"))
    for name in store.get("anticheat_names", []):
        out.append(_item("anticheat", name, "store page anti-cheat section", "store"))
    drm = store.get("drm_notice") or ""
    if "denuvo" in drm.lower():
        out.append(_item("anti-tamper", "Denuvo Anti-tamper", f"drm_notice: {drm[:120]}", "store"))
    account = store.get("ext_user_account_notice")
    if account:
        out.append(_item(ACCOUNT_NOTICE_KIND, f"third-party account: {account}", "ext_user_account_notice", "store"))
    single = 2 in cats
    online_multi = bool(cats & {36, 38, 49, 20})  # online PvP, online co-op, PvP, MMO
    if not single and online_multi:
        out.append(_item("online-backend", "online multiplayer only (no single-player category)", "store categories", "store"))
    return out


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split())


def from_name(name: str) -> list[dict]:
    low = _plain(name)
    hit = next((n for n in ONLINE_ONLY if _plain(n) in low), None)
    return [_item("online-only", f"always-online / competitive title ({hit})", "known title list", "list")] if hit else []


def online_backend_sdks(rule_hits: dict) -> list[str]:
    return sorted(n for n in rule_hits.get("SDK", {}) if n in ONLINE_BACKEND_SDKS)


def encrypted_content(ix: FileIndex, engine_hits: list[dict]) -> list[dict]:
    out = []
    for hit in engine_hits:
        det = hit.get("details", {})
        if hit["key"] == "unreal":
            for footer in det.get("pak_footers", []):
                if footer.get("encrypted_index"):
                    out.append(_item("encrypted-content", "Unreal pak index encrypted (AES)", footer["file"], "files"))
                    break
            for toc in det.get("utoc_headers", []):
                if toc.get("encrypted"):
                    out.append(_item("encrypted-content", "Unreal IoStore container encrypted (AES)", toc["file"], "files"))
                    break
        if hit["key"] == "godot" and det.get("dir_encrypted"):
            out.append(_item("encrypted-content", "Godot PCK directory encrypted (AES)", hit["evidence"][0], "files"))
        if hit["key"] == "pyinstaller":
            from gst.policy import PYINSTALLER_KEY_ARCHIVE

            for exe in [f for f in ix.files if f.endswith(".exe") and "/" not in f][:3]:
                if sniff.grep_file(ix.path(exe), [rb"pyimod00_crypto_key"]):
                    out.append(_item("encrypted-content", PYINSTALLER_KEY_ARCHIVE, exe, "files"))
                    break
    if ix.has("game.rgssad", "game.rgss2a", "game.rgss3a"):
        out.append({**_item("encrypted-content", "RGSS archive (public XOR scheme)", ix.find("game.rgss*")[0], "files"),
                    "scheme": "rgssad"})
    for system in ix.find("www/data/system.json", "data/system.json"):
        try:
            text = ix.path(system).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r'"hasEncrypted(?:Images|Audio)"\s*:\s*true', text):
            out.append({**_item("encrypted-content", "RPG Maker MV/MZ encrypted assets (key in System.json)", system, "files"),
                        "scheme": "rpgmaker-mv-systemjson"})
    return out


def obfuscation_hint(rule_hits: dict) -> list[dict]:
    names = [n for n in rule_hits.get("SDK", {}) if re.search(r"obfusc|confuser|beebyte|dotfuscator", n, re.I)]
    return [_item("obfuscation", n, ", ".join(rule_hits["SDK"][n][:1]), "files") for n in names]
