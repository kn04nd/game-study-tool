"""Network/voice middleware in recon, note templates pre-filled from analyses, focus setting, SubagentStop hook."""

import io
import json
from pathlib import Path

import pytest

from gst import cli, config, hooks, notes, notetemplates, workspace
from gst.common import GsError
from gst.hooks import handlers
from gst.recon import middleware, scan


# --- recon: network and voice middleware ---------------------------------------------------------------

@pytest.mark.parametrize("files, expected", [
    (["Game_Data/Managed/PhotonUnityNetworking.dll", "Game_Data/Managed/PhotonRealtime.dll",
      "Game_Data/Managed/PhotonVoice.API.dll", "Game_Data/Managed/Photon.Voice.PUN.dll",
      "Game_Data/Plugins/x86_64/steam_api64.dll", "Game_Data/Managed/com.rlabrecque.steamworks.net.dll"],
     {"Photon PUN", "Photon Voice", "Steam API", "Steamworks.NET"}),
    (["Game_Data/Managed/Fusion.Runtime.dll", "Game_Data/Managed/DissonanceVoip.dll",
      "Game_Data/Plugins/x86_64/AudioPluginDissonance.dll"], {"Photon Fusion", "Dissonance"}),
    (["Game_Data/Managed/FishNet.Runtime.dll", "Game_Data/Managed/Unity.Services.Vivox.dll"], {"FishNet", "Vivox"}),
    (["Game_Data/Managed/Mirror.dll", "Game_Data/Managed/kcp2k.dll", "Game_Data/Managed/Unity.Netcode.Runtime.dll",
      "Game_Data/Managed/Unity.Networking.Transport.dll", "Game_Data/Managed/Facepunch.Steamworks.Win64.dll"],
     {"Mirror", "Mirror transports (kcp2k/Telepathy)", "Netcode for GameObjects", "Unity Transport", "Facepunch.Steamworks"}),
    (["game.exe", "addons/godotsteam/win64/libgodotsteam.windows.template_release.x86_64.dll", "EOSSDK-Win64-Shipping.dll"],
     {"GodotSteam", "Epic Online Services"}),
    (["Game/Binaries/Win64/Game-Win64-Shipping.exe", "Engine/Binaries/ThirdParty/Vivox/vivoxsdk.dll",
      "Engine/Binaries/ThirdParty/Steamworks/Steamv153/Win64/steam_api64.dll"], {"Vivox", "Steam API"}),
    (["Game_Data/Managed/Assembly-CSharp.dll", "Game_Data/Managed/UnityEngine.dll", "rousrDissonance.dll"], set()),
])
def test_middleware_from_file_names(files, expected):
    found = middleware.detect(files)
    assert {m["name"] for m in found} == expected
    for m in found:
        assert m["kind"] in {"network", "voice", "platform"} and 1 <= len(m["evidence"]) <= 3


def test_middleware_summary_and_order():
    found = middleware.detect(["a/Mirror.dll", "a/DissonanceVoip.dll", "a/steam_api64.dll"])
    assert [m["kind"] for m in found] == ["network", "voice", "platform"]
    assert middleware.summary(found) == "network: Mirror; voice: Dissonance; platform: Steam API"
    assert middleware.summary([]) == "none detected"
    assert middleware.detect([], dirs={"Engine/Plugins/Online/OnlineSubsystemSteam"})[0]["name"] == "Unreal OnlineSubsystem"


def unity_install(tmp_path: Path) -> Path:
    root = tmp_path / "Steam" / "Coop Game"
    for rel in ("CoopGame.exe", "UnityPlayer.dll", "CoopGame_Data/globalgamemanagers",
                "CoopGame_Data/Managed/Assembly-CSharp.dll", "CoopGame_Data/Managed/PhotonUnityNetworking.dll",
                "CoopGame_Data/Managed/PhotonVoice.dll"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"MZ" if rel.endswith((".exe", ".dll")) else b"2021.3.1f1")
    return root


def test_recon_reports_net_voice_and_game_json_keeps_it(tmp_path):
    root = unity_install(tmp_path)
    game = {"name": "Coop Game", "store": None, "appid": None, "path": str(root)}
    report = scan.run_recon(game)
    assert {m["name"] for m in report["net_voice"]} == {"Photon PUN", "Photon Voice"}
    assert "- **Network/voice:** network: Photon PUN; voice: Photon Voice" in scan.render_markdown(report)
    ws = workspace.create(game, report, "coop")
    meta = workspace.load(ws)
    assert meta["middleware"] == [{"name": "Photon PUN", "kind": "network"}, {"name": "Photon Voice", "kind": "voice"}]


# --- focus ---------------------------------------------------------------------------------------------

def test_focus_default_and_override(monkeypatch):
    assert config.focus() == list(config.DEFAULT_FOCUS) and "ragdoll" in config.focus()
    monkeypatch.setenv("GST_FOCUS", "inventário, , física")
    assert config.focus() == ["inventário", "física"]


# --- note templates --------------------------------------------------------------------------------------

def analysis(ws: Path, name: str, summary: dict, data) -> None:
    (ws / "analysis").mkdir(parents=True, exist_ok=True)
    (ws / "analysis" / f"{name}.json").write_text(json.dumps({"header": {"analysis": name}, "summary": summary, "data": data}),
                                                   encoding="utf-8")


def synthetic_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "decomp" / "cs" / "Game").mkdir(parents=True)
    (ws / "decomp" / "cs" / "Game" / "PlayerMovement.cs").write_text("class PlayerMovement\n{\n  float speed = 5f;\n}\n", encoding="utf-8")
    analysis(ws, "systems", {"systems": 2}, {"systems": [
        {"label": "Jogador e entrada", "types": 3, "uses": {"Física": 2},
         "top": [{"name": "PlayerMovement", "qname": "PlayerMovement", "path": "decomp/cs/Game/PlayerMovement.cs", "line": 1, "refs": 9}]},
        {"label": "Rede e online", "types": 1, "top": []}]})
    analysis(ws, "godot_scenes", {"files": 1}, {"tunables": {"res://PlayerMovement.cs": {
        "source": "decomp/cs/Game/PlayerMovement.cs", "class": "PlayerMovement", "base": "CharacterBody3D",
        "exports": {"Speed": {"type": "float", "default": "5f", "values": {"7.5": {"count": 2, "where": ["res://p.tscn::."]}}},
                    "Steps": {"type": "AudioStream", "values": {'ExtResource("res://s.wav")': {"count": 1}}},
                    "Body": {"type": "", "values": {'NodePath("../Body")': {"count": 1}}},
                    "Mode": {"type": "", "default": "2", "values": {"3": {"count": 1}}}}}},
        "instance_graph": {"top_level": ["res://levels/cave_level.tscn"], "most_instanced": {"res://p.tscn": 3}}})
    analysis(ws, "godot_animation", {"ragdoll_scenes": 1}, {"ragdolls": [
        {"file": "res://ragdoll.tscn", "physical_bones": 19, "joint_nodes": 18, "joint_node_types": {"Generic6DOFJoint3D": 18},
         "mass_total": 3.8}]})
    analysis(ws, "audio_catalog", {"events": 3, "kinds": {"sound": 3}}, {"categories": {"player": 2, "ui": 1}})
    return ws


def test_every_template_renders_on_an_empty_workspace(tmp_path):
    ws = tmp_path / "empty"
    ws.mkdir()
    assert len(notetemplates.template_names()) == 9
    for name in notetemplates.template_names():
        text = notetemplates.render(ws, {"name": "Game"}, name, title="T", note="notes/x/y.md", date="2026-01-01")
        assert text.startswith("# T\n") and "{{" not in text and "não pôde" not in text, name
        assert handlers.UNVERIFIED_RX.search(text), name
        assert notetemplates.unfilled(text) >= 3, name


def test_template_prefill_from_analyses(tmp_path):
    ws = synthetic_ws(tmp_path)
    text = notetemplates.render(ws, {"name": "Game", "engine": {"label": "Godot", "version": "4.3"}}, "mecanica",
                                title="Movimento", note="notes/logic/movimento.md", date="2026-01-01",
                                options={"system": "jogador", "match": "player"})
    assert "Godot 4.3" in text and "`analysis/systems.json`" in text
    assert "| `PlayerMovement` | decomp/cs/Game/PlayerMovement.cs:1 | 9 |" in text
    assert "| `PlayerMovement` | `Speed` | float | 5f | 7.5 ×2 | decomp/cs/Game/PlayerMovement.cs |" in text
    assert "`Mode`" in text and "`Steps`" not in text and "`Body`" not in text  # references stay in the JSON
    ok, broken = notes.check_citations(ws, text)
    assert ok and not broken
    rag = notetemplates.render(ws, {}, "ragdoll-fisica", title="R", note="n", date="d")
    assert "| res://ragdoll.tscn | 19 | 18 | Generic6DOFJoint3D 18 | 3.8 |" in rag
    audio = notetemplates.render(ws, {}, "audio-catalogo", title="A", note="n", date="d")
    assert "| player | 2 |" in audio and "kinds | sound 3" in audio
    maps = notetemplates.render(ws, {}, "mapa-niveis", title="M", note="n", date="d")
    assert "`res://levels/cave_level.tscn`" in maps
    net = notetemplates.render(ws, {}, "rede-voz", title="N", note="n", date="d")
    assert "gst recon" in net  # recon.json without net_voice: says how to get it
    (ws / "recon").mkdir()
    (ws / "recon" / "recon.json").write_text(json.dumps({"net_voice": middleware.detect(["x/Mirror.dll"])}), encoding="utf-8")
    assert "| Mirror | network | `x/mirror.dll` |" in notetemplates.render(ws, {}, "rede-voz", title="N", note="n", date="d")


def test_comparativo_lists_the_workspaces(tmp_path, isolated_env):
    root = unity_install(tmp_path)
    game = {"name": "Coop Game", "store": None, "appid": None, "path": str(root)}
    workspace.create(game, scan.run_recon(game), "coop")
    text = notetemplates.render(tmp_path, {}, "comparativo", title="C", note="n", date="d")
    assert "| Coop Game | `coop` | Unity" in text and "Photon PUN, Photon Voice" in text
    assert "nenhum workspace" in notetemplates.render(tmp_path, {}, "comparativo", title="C", note="n", date="d",
                                                       options={"games": "nope"})


def test_notes_new_with_template_and_cli(tmp_path, monkeypatch, capsys):
    ws = synthetic_ws(tmp_path)
    path = notes.new(ws, "movimento", template="mecanica", game={"name": "Game"}, options={"system": "jogador"})
    assert path == ws / "notes" / "logic" / "movimento.md"
    with pytest.raises(GsError, match="unknown note template"):
        notes.new(ws, "x", template="nope")
    (ws / "game.json").write_text(json.dumps({"gs_workspace": 1, "slug": "g", "name": "Game", "install_dir": str(tmp_path)}),
                                  encoding="utf-8")
    monkeypatch.chdir(ws)
    assert cli.main(["notes", "new", "assets/sons", "--template", "audio-catalogo", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["template"] == "audio-catalogo" and out["unfilled"] >= 3
    assert cli.main(["notes", "templates", "--json"]) == 0
    assert {r["name"] for r in json.loads(capsys.readouterr().out)} == set(notetemplates.TEMPLATES)
    assert cli.main(["notes", "check", "--json"]) == 0
    check = json.loads(capsys.readouterr().out)
    assert check["unfilled"]["notes/assets/sons.md"] >= 3 and not check["broken"]


# --- SubagentStop: deliver-first -----------------------------------------------------------------------------

def transcript(tmp_path: Path, prompt) -> str:
    path = tmp_path / "agent.jsonl"
    lines = [{"type": "system", "message": None},
             {"type": "user", "message": {"role": "user", "content": prompt}},
             {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "DELIVERABLE: other.md"}]}}]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return str(path)


def stop(ws: Path, tmp_path: Path, monkeypatch, capsys, prompt, agent="gst:mechanics-analyst", agent_id="a1"):
    payload = {"hook_event_name": "SubagentStop", "cwd": str(ws), "agent_type": agent, "agent_id": agent_id,
               "agent_transcript_path": transcript(tmp_path, prompt), "stop_hook_active": False}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert hooks.main("subagent-stop") == 0
    out = capsys.readouterr().out
    return json.loads(out) if out.strip() else None


@pytest.fixture
def study(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "game.json").write_text(json.dumps({"gs_workspace": 1, "slug": "g", "name": "G"}), encoding="utf-8")
    return ws


def test_deliverables_are_parsed_from_the_prompt():
    text = "Study X.\nDELIVERABLE: notes/logic/x.md\n- DELIVERABLE: `notes/assets/y.md`.\nDELIVERABLE: notes/logic/x.md\n"
    assert handlers.deliverables(text) == ["notes/logic/x.md", "notes/assets/y.md"]
    assert handlers.deliverables("no declaration, deliverable: later") == []


def test_subagent_stop_blocks_until_the_deliverable_is_written(study, tmp_path, monkeypatch, capsys):
    prompt = "Estude o movimento.\nDELIVERABLE: notes/logic/movimento.md\n"
    out = stop(study, tmp_path, monkeypatch, capsys, prompt)
    assert out["decision"] == "block" and "notes/logic/movimento.md does not exist yet" in out["reason"]
    note = study / "notes" / "logic" / "movimento.md"
    note.parent.mkdir(parents=True)
    note.write_text("# M\n\n## Não verificado\n\n<!-- preencher -->\n-\n", encoding="utf-8")
    out = stop(study, tmp_path, monkeypatch, capsys, prompt)
    assert out["decision"] == "block" and "still empty" in out["reason"]
    note.write_text("# M\n\n## 9. Não verificado\n\n- o valor em runtime\n\n## Fontes\n", encoding="utf-8")
    assert stop(study, tmp_path, monkeypatch, capsys, prompt) is None
    arch = "# A\n\n## 10. Evidências e o que NÃO foi verificado\n\n- o save\n\n## 11. Fontes\n"
    (study / "ARCHITECTURE.md").write_text(arch, encoding="utf-8")
    assert stop(study, tmp_path, monkeypatch, capsys, "DELIVERABLE: ARCHITECTURE.md", agent_id="s") is None


def test_subagent_stop_gives_up_after_max_blocks_and_journals(study, tmp_path, monkeypatch, capsys):
    prompt = [{"type": "text", "text": "DELIVERABLE: notes/logic/z.md"}]
    for _ in range(handlers.MAX_STOP_BLOCKS):
        assert stop(study, tmp_path, monkeypatch, capsys, prompt, agent_id="stuck")["decision"] == "block"
    assert stop(study, tmp_path, monkeypatch, capsys, prompt, agent_id="stuck") is None
    assert "stopped without its deliverable" in (study / "STUDY.md").read_text(encoding="utf-8")
    assert stop(study, tmp_path, monkeypatch, capsys, prompt, agent_id="other")["decision"] == "block"


def test_subagent_stop_is_quiet_without_declaration_or_outside_gst(study, tmp_path, monkeypatch, capsys):
    assert stop(study, tmp_path, monkeypatch, capsys, "just answer") is None
    assert stop(study, tmp_path, monkeypatch, capsys, "DELIVERABLE: notes/a.md", agent="general-purpose") is None
    outside = tmp_path / "plain"
    outside.mkdir()
    assert stop(outside, tmp_path, monkeypatch, capsys, "DELIVERABLE: notes/a.md") is None
