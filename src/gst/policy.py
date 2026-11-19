"""Protection policy: turn recon findings into a verdict and decide what gst may do.

Verdicts (most permissive first):

* ``offline-ok``  - no anti-cheat, DRM wrapper or protector found and an offline/solo mode exists.
* ``static-only`` - DRM wrapper (SteamStub), anti-tamper (Denuvo, Arxan, Themida, VMProtect,
  Enigma), launcher/account DRM, or an online backend without a solo mode. Files on disk only.
* ``no-runtime``  - any anti-cheat (kernel or user mode, VAC included) or an always-online game.

See plugins/gst/guardrails.md; rule numbers below refer to it. Stdlib only.
"""

from __future__ import annotations

import re

OFFLINE_OK = "offline-ok"
STATIC_ONLY = "static-only"
NO_RUNTIME = "no-runtime"
RANK = {OFFLINE_OK: 0, STATIC_ONLY: 1, NO_RUNTIME: 2}

# protection kind -> verdict it forces (None = informative only)
KIND_VERDICT: dict[str, str | None] = {
    "anticheat": NO_RUNTIME,
    "vac": NO_RUNTIME,
    "online-only": NO_RUNTIME,
    "drm-wrapper": STATIC_ONLY,
    "anti-tamper": STATIC_ONLY,
    "launcher-account": STATIC_ONLY,
    "online-backend": STATIC_ONLY,
    "encrypted-content": None,
    "obfuscation": None,
}

CAPABILITIES = {
    OFFLINE_OK: {"runtime": True, "inject": "sandbox-only", "decompile": "all", "data": True},
    STATIC_ONLY: {"runtime": False, "inject": False, "decompile": "unprotected-modules", "data": True},
    NO_RUNTIME: {"runtime": False, "inject": False, "decompile": False, "data": True},
}

# Schemes whose key ships as plain data or whose algorithm is public (guardrail 4).
PUBLIC_SCHEMES = {"rgssad", "rpgmaker-mv-systemjson", "vgmstream-hca-builtin"}
# recon's name for a PyInstaller --key build: its key sits inside the exe, so it never counts as published, and
# pyinstxtractor-ng would use it silently (no key flag on the command line)
PYINSTALLER_KEY_ARCHIVE = "PyInstaller --key archive (key inside the exe, not published)"


class Decision:
    """Outcome of a check. ``ask`` lets the user decide instead of denying outright.

    A plain class, not a dataclass: importing dataclasses costs ~70 ms in the PreToolUse hook.
    """

    __slots__ = ("allowed", "reason", "rule", "ask")

    def __init__(self, allowed: bool, reason: str = "", rule: int | None = None, ask: bool = False):
        self.allowed, self.reason, self.rule, self.ask = allowed, reason, rule, ask

    def __repr__(self) -> str:
        return f"Decision(allowed={self.allowed!r}, reason={self.reason!r}, rule={self.rule!r}, ask={self.ask!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Decision):
            return NotImplemented
        return (self.allowed, self.reason, self.rule, self.ask) == (other.allowed, other.reason, other.rule, other.ask)

    def as_hook(self) -> dict:
        """PreToolUse hookSpecificOutput payload (None-equivalent when allowed)."""
        decision = "ask" if self.ask else ("allow" if self.allowed else "deny")
        rule = f" (guardrails.md rule {self.rule})" if self.rule else ""
        return {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": f"{self.reason}{rule}".strip(),
        }


ALLOW = Decision(True)


def verdict(protections: list[dict]) -> str:
    """Most restrictive verdict implied by the detected protections."""
    result = OFFLINE_OK
    for item in protections or []:
        forced = KIND_VERDICT.get(str(item.get("kind", "")))
        if forced and RANK[forced] > RANK[result]:
            result = forced
    return result


def capabilities(game_verdict: str) -> dict:
    return dict(CAPABILITIES.get(game_verdict, CAPABILITIES[NO_RUNTIME]))


def describe(game_verdict: str) -> str:
    return {
        OFFLINE_OK: "good offline study target: every layer, runtime only offline/solo, injection only in a sandbox copy",
        STATIC_ONLY: "static only: files on disk and unprotected modules; no runtime, never unpack the protected exe",
        NO_RUNTIME: "no runtime: anti-cheat or always-online; data on disk only, no client-code decompilation",
    }.get(game_verdict, "unknown verdict: run gst recon")


def check(game: dict, action: str, target: str | None = None) -> Decision:
    """Can ``action`` run against ``game`` (a game.json / registry entry)?

    Actions: runtime, inject, decompile, extract, decrypt.
    """
    v = game.get("verdict") or NO_RUNTIME
    caps = capabilities(v)
    name = game.get("name") or game.get("slug") or "this game"
    if action == "runtime":
        if caps["runtime"]:
            return ALLOW
        return Decision(False, f"{name} is '{v}': runtime tools are not allowed. {describe(v)}", 2)
    if action == "inject":
        if caps["inject"] != "sandbox-only":
            return Decision(False, f"{name} is '{v}': no injection-based tools", 2)
        sandbox = game.get("sandbox_dir")
        from gst.common import is_under  # local import keeps hook start-up light

        if target and sandbox and is_under(target, sandbox):
            return ALLOW
        return Decision(False, "injection-based tools run only inside a sandbox copy of the install, never the original", 5)
    if action == "decompile":
        mode = caps["decompile"]
        if mode == "all":
            return ALLOW
        if mode == "unprotected-modules":
            protected = {m.lower() for m in game.get("protected_modules", [])}
            if target and target.replace("\\", "/").rsplit("/", 1)[-1].lower() not in protected:
                return ALLOW
            return Decision(False, f"{target or 'this module'} is protected (DRM/anti-tamper); never unpack it", 2)
        return Decision(False, f"{name} is '{v}': client code is not decompiled", 2)
    if action == "extract":
        return ALLOW
    if action == "decrypt":
        keys = game.get("keys")
        for key in keys if isinstance(keys, list) else []:  # a malformed game.json must not crash the guard
            if not isinstance(key, dict):
                continue
            if key.get("scheme") in PUBLIC_SCHEMES or str(key.get("source", "")).startswith(("http://", "https://")):
                return ALLOW
        return Decision(False, "encrypted content: only community-published keys (with source URL) or public schemes", 4)
    return Decision(False, f"unknown action '{action}'")


# --- command deny-list (applies to any shell command, anywhere) -------------------------------

class DenyRule:
    __slots__ = ("pattern", "rule", "reason")

    def __init__(self, pattern: re.Pattern, rule: int, reason: str):
        self.pattern, self.rule, self.reason = pattern, rule, reason

    def __repr__(self) -> str:
        return f"DenyRule({self.pattern.pattern!r}, rule={self.rule})"


def _rx(expr: str) -> re.Pattern:
    return re.compile(expr, re.IGNORECASE)


_AC_SERVICES = r"(?:BEService\w*|BEDaisy|EasyAntiCheat\w*|vgc|vgk|npggsvc|GameMon\w*|PnkBstr\w*|EAAntiCheat\w*|FACEIT\w*|ACE-\w+|AntiCheatExpert\w*|xhunter\d*|mhyprot\d*)"

DENY_RULES: list[DenyRule] = [
    DenyRule(_rx(r"\bsteamless\b"), 2, "Steamless removes Steam DRM"),
    DenyRule(_rx(r"\bscyllahide\b"), 2, "ScyllaHide hides debuggers from protections"),
    DenyRule(_rx(r"renderdoc\.net|renderdoc[\s_-]*pro\b"), 2, "RenderDoc Pro advertises anti-cheat compatibility"),
    DenyRule(_rx(r"aes[\s_-]*dumpster|aes[\s_-]*key[\s_-]*finder|\baesfinder\b|ue4[\s_-]*aes[\s_-]*key"), 4,
             "extracting encryption keys from executables or memory is not allowed"),
    DenyRule(_rx(r"forza[\s_-]*crypto"), 4, "Forza-Crypto-Tool sends decryption to a remote backend"),
    DenyRule(_rx(r"\bcricodecs\b.*--(?:recover-key|build)\b"), 4, "key recovery / rebuilding is out of scope"),
    DenyRule(_rx(r"010\s*editor.*\s-process\b|\b010editor(?:\.exe)?\b.*\s-process\b"), 3, "010 Editor -process opens live process memory"),
    DenyRule(_rx(r"\bquickbms(?:\.exe)?\b.*process://"), 3, "QuickBMS process memory mode is out of scope"),
    DenyRule(_rx(r"\buxm\b.*\bpatch\b"), 5, "UXM Patch modifies the game executable"),
    DenyRule(_rx(r"\bttd(?:\.exe)?\b.*\s[-/]monitor\b"), 8, "TTD -monitor installs a kernel driver"),
    DenyRule(_rx(rf"\bsc(?:\.exe)?\s+(?:stop|delete|config)\s+{_AC_SERVICES}\b"), 8, "never stop or remove anti-cheat services"),
    DenyRule(_rx(rf"\b(?:stop|set|remove)-service\b.*\b{_AC_SERVICES}\b"), 8, "never stop or remove anti-cheat services"),
    DenyRule(_rx(rf"\b(?:taskkill|stop-process)\b.*\b{_AC_SERVICES}\b"), 8, "never kill anti-cheat processes"),
    DenyRule(_rx(r"\b(?:dbk32|dbk64|dbvm)\b.*\b(?:load|enable|install)\b|\b(?:load|enable|install)\b.*\b(?:dbk32|dbk64|dbvm)\b"), 2,
             "Cheat Engine kernel driver / DBVM is never enabled"),
    DenyRule(_rx(r"\bgdre(?:_tools)?(?:\.exe)?\b.*--(?:pck-create|pck-patch|compile|txt-to-bin|patch-translations)\b"), 1,
             "building or patching Godot packs is modding, out of scope (study only)"),
    DenyRule(_rx(r"\brepak(?:\.exe)?\s+pack\b|\bretoc(?:\.exe)?\s+(?:pack-raw|to-zen)\b|\buassetgui(?:\.exe)?\b.*\bfromjson\b"), 1,
             "building Unreal paks, IoStore containers or assets is modding, out of scope (study only)"),
    DenyRule(_rx(r"\bpyghidra-mcp(?:\.exe)?\b.*(?:--host|\s-o)(?:\s+|=)['\"]?(?!127\.0\.0\.1\b|localhost\b|::1\b)[^\s'\"]"), 9,
             "MCP servers bind 127.0.0.1 only"),
    DenyRule(_rx(r"\bpyghidra-mcp(?:\.exe)?\b(?!.*(?:--no-symbols|--help|--version|\s-[hv]\b|--list-project-binaries))"), 9,
             "run pyghidra-mcp with --no-symbols: PDB names and GUIDs never go to symbol servers"),
]

# Tools that attach to, inject into, launch-and-capture, or dump a live process.
RUNTIME_TOOLS = re.compile(
    r"\b(?:x64dbg|x32dbg|x96dbg|frida(?:-trace|-ps)?|ttd|procdump64|procdump|cheatengine(?:-x86_64)?|windbg|windbgx|cdb|"
    r"renderdoccmd|qrenderdoc|pixtool|ngfx(?:-capture)?|apitrace|drrun|drltrace|drstrace|pe-sieve|hollows_hunter|"
    r"winapioverride\w*|tinytracer|presentmon\w*)(?:\.exe)?\b",
    re.IGNORECASE,
)

# Flags that pass an encryption key to an extractor; only allowed with a recorded public key.
# Any long option whose last word is key/keys (--key, --aes-key, --tact-keys, --key-file), --aes and
# --aes-file, -k; repak and retoc take -a <key> (clap: also -a<key>, -a=<key>); uas takes -a <key> and
# -A <key file>.
KEY_FLAGS = re.compile(r"(?:--(?:\w+-)*keys?(?:-?file)?|--aes(?:-file)?|-k)\b|\baes="
                       r"|\b(?:uas|repak|retoc)(?:\.exe)?\b.*(?:\s|[\"'])-a", re.IGNORECASE)
KEY_TOOLS = re.compile(r"\b(?:repak|retoc|gdre_tools|gdre|uas|ue4export|cricodecs|unrpa|rpatool|pyinstxtractor\w*|"
                       r"rustydemon)(?:\.exe)?\b", re.IGNORECASE)


def deny_rule_for(command: str) -> DenyRule | None:
    for rule in DENY_RULES:
        if rule.pattern.search(command):
            return rule
    return None
