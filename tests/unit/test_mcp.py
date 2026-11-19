"""gst mcp without a real server: the rule 9 audit, add/remove, the guard on .mcp.json, and the stdio client
against a fake server (a tiny Python script speaking JSON-RPC)."""

import json
import sys
from pathlib import Path

import pytest

from gst import guard, mcp, policy
from gst.common import GsError
from gst.mcp_client import Session, text_of


@pytest.mark.parametrize("text, problem", [
    ('{"mcpServers": {"x": {"type": "http", "url": "http://192.168.0.5:8000/mcp"}}}', "points at 192.168.0.5"),
    ('{"mcpServers": {"x": {"type": "sse", "url": "http://0.0.0.0:8080/sse"}}}', "0.0.0.0"),
    ('{"mcpServers": {"x": {"command": "srv", "args": ["--host", "10.0.0.2"]}}}', "binds 10.0.0.2"),
    ('{"mcpServers": {"g": {"command": "python", "args": ["bridge_mcp_ghidra.py"]}}}', "LaurieWired"),
    ('{"mcpServers": {"g": {"command": "pyghidra-mcp", "args": ["--project-path", "p.gpr"]}}}', "--no-symbols"),
])
def test_audit_flags_non_local_and_rejected_servers(text, problem):
    assert any(problem in p for p in mcp.audit_text(text)), mcp.audit_text(text)


def test_audit_accepts_local_servers():
    ok = {"mcpServers": {"g": {"type": "stdio", "command": "pyghidra-mcp", "args": ["--project-path", "p.gpr", "--no-symbols"]},
                         "h": {"type": "http", "url": "http://127.0.0.1:8000/mcp"},
                         "i": {"command": "srv", "args": ["--host", "localhost"]}}}
    assert mcp.audit_text(json.dumps(ok)) == []


def test_add_and_remove_write_mcp_json_and_settings(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(gst *)"]},
                                                              "enabledMcpjsonServers": []}), encoding="utf-8")
    config = {"type": "stdio", "command": "pyghidra-mcp", "args": ["--project-path", "p.gpr", "--no-symbols"], "env": {}}
    monkeypatch.setattr(mcp, "server_config", lambda ws, name, module=None: config)
    game = {"name": "G", "verdict": policy.OFFLINE_OK}
    mcp.add(ws, game, "ghidra")
    assert mcp.configured(ws)["ghidra"] == config
    settings = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledMcpjsonServers"] == ["ghidra"]
    assert "mcp__ghidra__decompile_function" in settings["permissions"]["allow"] and "Bash(gst *)" in settings["permissions"]["allow"]
    assert settings["permissions"]["deny"] == ["mcp__ghidra__delete_project_binary"]
    assert mcp.remove(ws, "ghidra") and mcp.configured(ws) == {}
    settings = json.loads((ws / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledMcpjsonServers"] == [] and settings["permissions"]["allow"] == ["Bash(gst *)"]
    with pytest.raises(GsError, match="client code is not decompiled"):
        mcp.add(ws, dict(game, verdict=policy.NO_RUNTIME), "ghidra")
    monkeypatch.undo()  # the real server_config: only verified templates exist
    with pytest.raises(GsError, match="no verified template"):
        mcp.server_config(ws, "frida")


def test_guard_refuses_a_non_local_mcp_json(tmp_path):
    bad = '{"mcpServers": {"x": {"type": "http", "url": "http://0.0.0.0:9094/mcp"}}}'
    decision = guard.check_write(tmp_path / ".mcp.json", bad)
    assert not decision.allowed and decision.rule == 9
    assert guard.check_write(tmp_path / ".mcp.json", '{"mcpServers": {}}').allowed
    assert guard.check_write(tmp_path / "notes.md", bad).allowed  # only .mcp.json is audited


FAKE_SERVER = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg or "method" not in msg:
        continue  # notifications, and the client's answers to our own requests
    if msg["method"] == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info"}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "roots/list"}), flush=True)
        result = {"protocolVersion": msg["params"]["protocolVersion"], "serverInfo": {"name": "fake"}, "capabilities": {}}
    elif msg["method"] == "tools/list":
        result = {"tools": [{"name": "echo", "description": "echo back"}]}
    elif msg["method"] == "tools/call":
        result = {"content": [{"type": "text", "text": "echo:" + json.dumps(msg["params"]["arguments"])}]}
    else:
        result = None
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
'''


def test_stdio_client_against_a_fake_server(tmp_path):
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    config = {"type": "stdio", "command": sys.executable, "args": [str(script)]}
    with Session(config, startup=30) as session:
        assert session.server["serverInfo"]["name"] == "fake"
        assert [t["name"] for t in session.tools()] == ["echo"]
        assert text_of(session.call("echo", {"x": 1})) == 'echo:{"x": 1}'
    with pytest.raises(GsError, match="stdio servers only"):
        Session({"type": "http", "url": "http://127.0.0.1:1"})
