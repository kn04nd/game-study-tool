"""Public store metadata (opt-in, cached 7 days): Steam appdetails + the store page anti-cheat
section. Reads public data only; nothing about the local game is sent anywhere."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from gst import config
from gst.common import read_json, write_json

TTL = 7 * 24 * 3600
USER_AGENT = "game-study-toolkit/0.1 (local study tool; +recon cache 7d)"
_last_request = 0.0


def _get(url: str, cookie: str | None = None) -> str | None:
    global _last_request
    wait = 1.5 - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError):
        return None
    finally:
        _last_request = time.time()


def steam_store(appid: str | None, *, force: bool = False) -> dict | None:
    """Essential appdetails fields + store-page anti-cheat names; None when offline/not allowed."""
    if not appid or config.online_allowed() is not True:
        return None
    cache = config.cache_dir() / "steam" / f"app_{appid}.json"
    cached = read_json(cache)
    if cached and not force and cached.get("fetched_at", 0) > time.time() - TTL:
        return cached
    raw = _get(f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&l=english")
    if raw is None:
        return cached
    try:
        payload = json.loads(raw).get(str(appid), {})
    except json.JSONDecodeError:
        return cached
    if not payload.get("success"):
        return None
    d = payload.get("data", {})
    info = {
        "appid": str(appid),
        "name": d.get("name"),
        "type": d.get("type"),
        "developers": d.get("developers", []),
        "publishers": d.get("publishers", []),
        "release_date": (d.get("release_date") or {}).get("date"),
        "categories": [{"id": c.get("id"), "name": c.get("description")} for c in d.get("categories", [])],
        "genres": [g.get("description") for g in d.get("genres", [])],
        "drm_notice": d.get("drm_notice"),
        "ext_user_account_notice": d.get("ext_user_account_notice"),
        "anticheat_names": [],
        "kernel_anticheat": False,
        "fetched_at": int(time.time()),
    }
    page = _get(f"https://store.steampowered.com/app/{appid}/?l=english&cc=us",
                cookie="birthtime=0; lastagecheckage=1-0-1990; wants_mature_content=1")
    if page:
        info["anticheat_names"] = sorted(set(re.findall(r'class="anticheat_name">\s*([^<]+?)\s*<', page)))
        info["kernel_anticheat"] = "Kernel Level Anti-Cheat" in page
    write_json(cache, info)
    return info
