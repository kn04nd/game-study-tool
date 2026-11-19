import pytest

from gst import policy


@pytest.mark.parametrize(
    "kinds, expected",
    [
        ([], policy.OFFLINE_OK),
        (["obfuscation"], policy.OFFLINE_OK),
        (["encrypted-content"], policy.OFFLINE_OK),
        (["drm-wrapper"], policy.STATIC_ONLY),
        (["anti-tamper", "launcher-account"], policy.STATIC_ONLY),
        (["online-backend"], policy.STATIC_ONLY),
        (["drm-wrapper", "anticheat"], policy.NO_RUNTIME),
        (["vac"], policy.NO_RUNTIME),
        (["online-only"], policy.NO_RUNTIME),
    ],
)
def test_verdict(kinds, expected):
    assert policy.verdict([{"kind": k} for k in kinds]) == expected


def test_runtime_only_for_offline_ok():
    assert policy.check({"verdict": policy.OFFLINE_OK}, "runtime").allowed
    for v in (policy.STATIC_ONLY, policy.NO_RUNTIME, None):
        d = policy.check({"verdict": v, "name": "X"}, "runtime")
        assert not d.allowed and d.rule == 2


def test_inject_requires_sandbox(tmp_path):
    sandbox = tmp_path / "ws" / "sandbox"
    game = {"verdict": policy.OFFLINE_OK, "sandbox_dir": str(sandbox)}
    assert policy.check(game, "inject", str(sandbox / "Game.exe")).allowed
    assert not policy.check(game, "inject", str(tmp_path / "Steam" / "Game.exe")).allowed
    assert not policy.check({**game, "verdict": policy.STATIC_ONLY}, "inject", str(sandbox / "Game.exe")).allowed


def test_decompile_rules():
    assert policy.check({"verdict": policy.OFFLINE_OK}, "decompile", "Game.exe").allowed
    static = {"verdict": policy.STATIC_ONLY, "protected_modules": ["SkyrimSE.exe"]}
    assert not policy.check(static, "decompile", r"D:\Steam\SkyrimSE.exe").allowed
    assert policy.check(static, "decompile", r"D:\Steam\binkw64.dll").allowed
    assert not policy.check({"verdict": policy.NO_RUNTIME}, "decompile", "cs2.exe").allowed


def test_decrypt_needs_public_key_or_scheme():
    assert not policy.check({"verdict": policy.OFFLINE_OK}, "decrypt").allowed
    assert policy.check({"keys": [{"source": "https://example.org/keys"}]}, "decrypt").allowed
    assert policy.check({"keys": [{"scheme": "rgssad"}]}, "decrypt").allowed
    assert not policy.check({"keys": [{"source": "extracted from exe"}]}, "decrypt").allowed


@pytest.mark.parametrize(
    "command",
    [
        "Steamless.CLI.exe --quiet SkyrimSE.exe",
        "AESDumpster.exe FSD-Win64-Shipping.exe",
        "cricodecs --recover-key a.hca",
        "sc stop BEService",
        "sc.exe delete EasyAntiCheat_EOS",
        "Stop-Service -Name vgc",
        "taskkill /IM BEService.exe /F",
        "TTD.exe -monitor game.exe",
        "UXM.exe patch",
        "quickbms script.bms process://game.exe out",
    ],
)
def test_deny_rules_match(command):
    assert policy.deny_rule_for(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "rg -n PlayerController decomp",
        "TTD.exe -accepteula -noUI -attach 1234 -out D:/game-study/x/traces",
        "sc query BEService",
        "Get-Service BEService",
        "quickbms -l unreal.bms game.pak",
    ],
)
def test_deny_rules_do_not_overmatch(command):
    assert policy.deny_rule_for(command) is None


def test_hook_payload_shape():
    payload = policy.Decision(False, "nope", 7).as_hook()
    assert payload["permissionDecision"] == "deny"
    assert "rule 7" in payload["permissionDecisionReason"]
    assert policy.Decision(False, "check", 2, ask=True).as_hook()["permissionDecision"] == "ask"
