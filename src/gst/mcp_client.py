"""A minimal MCP client over stdio (stdlib only): start a server as `.mcp.json` describes it, initialize, list
its tools or call one, stop it. JSON-RPC 2.0, one message per line. Used by `gst mcp tools` / `gst mcp call` to
prove a configured server answers.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time

from gst.common import GsError

PROTOCOL = "2025-06-18"


class Session:
    def __init__(self, config: dict, *, startup: float = 120.0):
        if config.get("type", "stdio") != "stdio":
            raise GsError("gst mcp talks to stdio servers only")
        env = {**os.environ, **{k: str(v) for k, v in (config.get("env") or {}).items()}}
        self.proc = subprocess.Popen([config["command"], *config.get("args", [])], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True,
                                     encoding="utf-8", errors="replace", bufsize=1)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0
        self.startup = startup
        self.server = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                  "clientInfo": {"name": "gst", "version": "1"}}, timeout=startup)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None, *, timeout: float = 300.0) -> dict:
        self.next_id += 1
        wanted = self.next_id
        self._send({"jsonrpc": "2.0", "id": wanted, "method": method, **({"params": params} if params is not None else {})})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GsError(f"MCP server did not answer {method} within {timeout:.0f} s")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                raise GsError(f"MCP server exited during {method}")
            try:
                message = json.loads(line)
            except ValueError:
                continue  # not JSON-RPC (a stray print): ignore
            if "method" in message and "id" in message:  # a request from the server (roots, sampling): not offered
                self._send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "not supported"}})
                continue
            if message.get("id") != wanted:
                continue  # notifications and stale replies
            if "error" in message:
                raise GsError(f"{method}: {message['error'].get('message')}")
            return message.get("result", {})

    def tools(self) -> list[dict]:
        return self.request("tools/list").get("tools", [])

    def call(self, name: str, arguments: dict | None = None, *, timeout: float = 300.0) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()  # the server we started: its own pid
            self.proc.wait(timeout=10)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def text_of(result: dict) -> str:
    return "\n".join(c.get("text", json.dumps(c)) for c in result.get("content", []))
