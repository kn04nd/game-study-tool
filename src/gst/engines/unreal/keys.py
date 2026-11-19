"""AES keys for encrypted Unreal containers: only community-published keys with their source URL
(guardrails.md rule 4). Never brute force, never read from the exe or memory.

Sources, merged in this order:

- ``game.json`` ``keys``: ``[{"scheme": "unreal-aes", "key": "0x...", "guid": "<32 hex or omitted>", "source": "https://..."}]``
- ``notes/keys.json`` in the workspace (the user's file, same shape: a list or ``{"keys": [...]}``).

Every key must carry a ``source`` URL (where it was published); an entry without one is ignored and
reported. The decision is :func:`gst.policy.check` ``decrypt``. A container whose key GUID has no usable
key stays ``blocked-policy``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gst.common import GsError, write_text

KEYS_FILE = "notes/keys.json"
SCHEME = "unreal-aes"
ZERO_GUID = "0" * 32


def _normalize(key: str) -> str | None:
    k = key.strip()
    if re.fullmatch(r"(0x)?[0-9a-fA-F]{64}", k):
        return "0x" + k[-64:].upper()
    return None


def load(ws: Path, game: dict) -> tuple[list[dict], list[str]]:
    """(usable keys, problems). A usable key has a 64-hex value and an http(s) source."""
    entries = list(game.get("keys") or [])
    path = Path(ws) / KEYS_FILE
    problems: list[str] = []
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{KEYS_FILE}: unreadable ({exc})")
            data = []
        entries += data.get("keys", []) if isinstance(data, dict) else list(data or [])
    out = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or e.get("scheme", SCHEME) != SCHEME:
            continue
        value = _normalize(str(e.get("key", "")))
        source = str(e.get("source", ""))
        if value is None:
            problems.append(f"key #{i + 1}: not a 256-bit hex key")
            continue
        if not source.startswith(("http://", "https://")):
            problems.append(f"key #{i + 1}: no source URL (only community-published keys, guardrails.md rule 4)")
            continue
        guid = str(e.get("guid") or ZERO_GUID).lower().replace("-", "")
        out.append({"scheme": SCHEME, "key": value, "guid": guid, "source": source})
    return out, problems


def key_for(ws: Path, game: dict, guid: str | None) -> tuple[dict | None, str]:
    """The published key for a container's key GUID, or (None, reason for blocked-policy)."""
    from gst import policy

    keys, problems = load(ws, game)
    want = (guid or ZERO_GUID).lower()
    match = next((k for k in keys if k["guid"] == want), None) or next((k for k in keys if k["guid"] == ZERO_GUID), None)
    if match is None:
        why = f"no published AES key for GUID {want} (game.json keys or {KEYS_FILE}, each with its source URL)"
        if problems:
            why += "; ignored: " + "; ".join(problems)
        return None, why + " (guardrails.md rule 4; never brute force)"
    decision = policy.check({**game, "keys": [{"scheme": SCHEME, "source": match["source"]}]}, "decrypt")
    if not decision.allowed:
        return None, decision.reason
    return match, ""


def key_file(ws: Path, key: dict) -> Path:
    """The key in a workspace file (uas --aes-file), so it never shows on a command line or in a log."""
    path = Path(ws) / "dumps" / "unreal" / "keys" / f"{key['guid']}.key"
    write_text(path, key["key"] + "\n")
    return path


def require(ws: Path, game: dict, container: dict) -> dict | None:
    """Key for an encrypted container (raises GsError code 3 = blocked-policy); None when it is plain."""
    if not container.get("encrypted"):
        return None
    key, why = key_for(ws, game, container.get("key_guid"))
    if key is None:
        raise GsError(f"{container['name']} is encrypted: {why}", 3)
    return key
