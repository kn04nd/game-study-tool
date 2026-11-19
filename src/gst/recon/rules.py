"""SteamDB FileDetectionRuleSets matcher (+ gs_rules.ini) and the AreWeAntiCheatYet lookup.

Format: ``[Section]`` then ``Name = regex`` or ``Name[] = regex`` (``; comment``). Regexes are
case-insensitive and run on '/'-separated relative paths.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


@dataclass(frozen=True)
class Rule:
    section: str
    name: str
    regex: re.Pattern
    source: str
    literal: str = ""  # a substring every match must contain (cheap pre-filter); "" = none


_BREAK = "\x00"   # stands for "anything" (removed construct)
_QUANT = "\x01"   # a counted quantifier {m,n}
_DOT = "\x02"     # an escaped, literal dot


def required_literal(expr: str) -> str:
    """Longest literal run that every match must contain; "" when no safe literal exists.

    Conservative by design: optional groups, alternations, classes and quantified characters
    are dropped, so the result can only be shorter than ideal, never wrong.
    """
    s = expr.replace("\\.", _DOT)
    s = re.sub(r"\\[dDwWsSbB]", _BREAK, s)
    s = re.sub(r"\\(.)", r"\1", s)  # remaining escapes are literal characters
    s = re.sub(r"\[(?:[^\]])*\]", _BREAK, s)
    s = re.sub(r"\{\d*,?\d*\}", _QUANT, s)
    prev = None
    while prev != s:  # optional groups -> break; innermost first
        prev = s
        s = re.sub(r"\([^()]*\)[?*" + _QUANT + r"]\+?", _BREAK, s)
    prev = None
    while prev != s:  # groups with alternation -> break
        prev = s
        s = re.sub(r"\([^()]*\|[^()]*\)", _BREAK, s)
    if "|" in s:
        return ""
    best = ""
    run = ""
    i = 0
    while i <= len(s):
        ch = s[i] if i < len(s) else _BREAK
        if ch in "^$()[]{}|.+*?" + _BREAK + _QUANT or ch == ":" and run == "?":
            if ch in "?*" + _QUANT and run:
                run = run[:-1]  # the char before an optional quantifier may be absent
            if len(run) > len(best):
                best = run
            run = ""
        else:
            run += ch
        i += 1
    best = best.replace(_DOT, ".")
    return best.lower() if len(best) >= 3 else ""


def parse_rules(text: str, source: str) -> list[Rule]:
    rules: list[Rule] = []
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line:
            continue
        name, expr = line.split("=", 1)
        expr = re.split(r"\s+;\s", expr, maxsplit=1)[0].strip()
        try:
            rules.append(Rule(section, name.strip().removesuffix("[]"), re.compile(expr, re.IGNORECASE), source,
                              required_literal(expr)))
        except re.error:
            continue
    return rules


@lru_cache(maxsize=1)
def all_rules() -> tuple[Rule, ...]:
    base = resources.files("gst").joinpath("data/rules")
    out: list[Rule] = []
    for fname, source in (("steamdb_rules.ini", "steamdb"), ("gs_rules.ini", "gst")):
        out += parse_rules(base.joinpath(fname).read_text(encoding="utf-8"), source)
    return tuple(out)


def match(paths: list[str], dirs: set[str] | None = None) -> dict[str, dict[str, list[str]]]:
    """{section: {name: [evidence paths]}} for every rule hitting any path (or dir + '/')."""
    candidates = [p.lower() for p in paths] + [d.lower() + "/" for d in (dirs or set())]
    hits: dict[str, dict[str, list[str]]] = {}
    for rule in all_rules():
        pool = [p for p in candidates if rule.literal in p] if rule.literal else candidates
        for p in pool:
            if rule.regex.search(p):
                bucket = hits.setdefault(rule.section, {}).setdefault(rule.name, [])
                if len(bucket) < 3 and p not in bucket:
                    bucket.append(p)
                if len(bucket) >= 3:
                    break
    return hits


@lru_cache(maxsize=1)
def _awacy() -> dict[str, dict]:
    text = resources.files("gst").joinpath("data/rules/areweanticheatyet_games.json").read_text(encoding="utf-8")
    data = json.loads(text)
    return {str(g["steam"]): g for g in data.get("games", []) if g.get("steam")}


ANTICHEAT_ALIASES = {
    "valve anti-cheat": "VAC", "vac": "VAC",
    "netease anti-cheat expert": "Anti-Cheat Expert", "anti-cheat expert": "Anti-Cheat Expert",
    "easy anti-cheat": "Easy Anti-Cheat", "eac": "Easy Anti-Cheat",
    "ea anticheat": "EA Javelin", "ea javelin": "EA Javelin",
    "custom (unknown)": "Custom", "unknown (custom)": "Custom",
}


def awacy_lookup(appid: str | None) -> dict | None:
    """AreWeAntiCheatYet entry for a Steam AppID, with anti-cheat names normalised."""
    if not appid:
        return None
    entry = _awacy().get(str(appid))
    if not entry:
        return None
    names = sorted({ANTICHEAT_ALIASES.get(a.lower(), a) for a in entry.get("anticheats", [])})
    return {"name": entry.get("name"), "anticheats": names, "linux_status": entry.get("status")}
