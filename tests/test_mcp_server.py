"""Tests for the MCP server."""

import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch

import pytest

from dynamic_radio.mcp_server import call_tool, list_tools, _daemon_url

MCP_SERVER_SCRIPT = str(
    __import__("pathlib").Path(__file__).resolve().parent.parent
    / "src"
    / "dynamic_radio"
    / "mcp_server.py"
)


class FakeDaemonHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler simulating the Dynamic Radio daemon."""

    def do_GET(self):
        if self.path == "/health":
            self._respond({"ok": True})
        elif self.path == "/status":
            self._respond({
                "state": "active",
                "idle": False,
                "volume": 80,
                "time_remaining": 120.0,
                "current_track": {"name": "Test Track", "artist": "Test Artist"},
                "current_block": {"mood": "focused"},
                "timestamp": "2026-04-07T12:00:00",
            })
        else:
            self._respond({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path == "/command":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            self._respond({"response": f"Executed: {body.get('text', '')}"})
        else:
            self._respond({"error": "not found"}, status=404)

    def _respond(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, *args):
        pass  # suppress request logs


@pytest.fixture()
def fake_daemon():
    """Start a fake daemon HTTP server and patch the MCP server to use it."""
    srv = HTTPServer(("127.0.0.1", 0), FakeDaemonHandler)
    port = srv.server_address[1]
    t = Thread(target=srv.serve_forever, daemon=True)
    t.start()
    with patch.dict("os.environ", {"DYNAMIC_RADIO_HOST": "127.0.0.1", "DYNAMIC_RADIO_PORT": str(port)}):
        yield srv
    srv.shutdown()


EXPECTED_TOOL_NAMES = {
    "dj_status",
    "dj_command",
    "dj_health",
    "dj_upload_plan",
    "dj_get_plan",
    "dj_search",
    "dj_feedback",
    "dj_queue_tracks",
    "dj_mood",
    "dj_clear_queue",
}


@pytest.mark.asyncio
async def test_list_tools_returns_full_set():
    tools = await list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOL_NAMES


@pytest.mark.asyncio
async def test_dj_health(fake_daemon):
    result = await call_tool("dj_health", {})
    data = json.loads(result[0].text)
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_dj_status(fake_daemon):
    result = await call_tool("dj_status", {})
    data = json.loads(result[0].text)
    assert data["state"] == "active"
    assert data["volume"] == 80
    assert data["current_track"]["name"] == "Test Track"


@pytest.mark.asyncio
async def test_dj_command(fake_daemon):
    result = await call_tool("dj_command", {"text": "play brostep"})
    data = json.loads(result[0].text)
    assert data["response"] == "Executed: play brostep"


@pytest.mark.asyncio
async def test_dj_command_skip(fake_daemon):
    result = await call_tool("dj_command", {"text": "skip"})
    data = json.loads(result[0].text)
    assert data["response"] == "Executed: skip"


@pytest.mark.asyncio
async def test_unknown_tool(fake_daemon):
    result = await call_tool("nonexistent", {})
    assert "Unknown tool" in result[0].text


@pytest.mark.asyncio
async def test_daemon_unreachable():
    with patch.dict("os.environ", {"DYNAMIC_RADIO_HOST": "127.0.0.1", "DYNAMIC_RADIO_PORT": "1"}):
        result = await call_tool("dj_health", {})
        assert "Cannot reach" in result[0].text


def test_daemon_url_defaults():
    assert _daemon_url() == "http://127.0.0.1:8420"


def test_daemon_url_env_override():
    with patch.dict("os.environ", {"DYNAMIC_RADIO_HOST": "10.0.0.1", "DYNAMIC_RADIO_PORT": "9999"}):
        assert _daemon_url() == "http://10.0.0.1:9999"


# ---------------------------------------------------------------------------
# MCP stdio integration tests
# ---------------------------------------------------------------------------

def _jsonrpc(method: str, params: dict | None = None, req_id: int = 1) -> str:
    """Build a JSON-RPC 2.0 request line."""
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg) + "\n"


def _jsonrpc_notification(method: str, params: dict | None = None) -> str:
    """Build a JSON-RPC 2.0 notification (no id)."""
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg) + "\n"


def _run_mcp_session(messages: list[str], env_override: dict | None = None) -> list[dict]:
    """Spawn MCP server subprocess, send messages, collect responses."""
    import os

    env = os.environ.copy()
    if env_override:
        env.update(env_override)

    proc = subprocess.Popen(
        [sys.executable, MCP_SERVER_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    stdin_data = "".join(messages).encode()
    stdout, stderr = proc.communicate(input=stdin_data, timeout=15)

    responses = []
    for line in stdout.decode().strip().splitlines():
        line = line.strip()
        if line:
            responses.append(json.loads(line))
    return responses


def test_mcp_stdio_initialize():
    """Server responds to initialize with capabilities including tools."""
    init_msg = _jsonrpc("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0.1"},
    })
    initialized_notif = _jsonrpc_notification("notifications/initialized")

    responses = _run_mcp_session([init_msg, initialized_notif])

    assert len(responses) >= 1
    init_resp = responses[0]
    assert "result" in init_resp
    assert "capabilities" in init_resp["result"]
    assert "tools" in init_resp["result"]["capabilities"]


def test_mcp_stdio_list_tools():
    """Server lists the full DJ tool set over stdio."""
    init_msg = _jsonrpc("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0.1"},
    }, req_id=1)
    initialized_notif = _jsonrpc_notification("notifications/initialized")
    list_msg = _jsonrpc("tools/list", {}, req_id=2)

    responses = _run_mcp_session([init_msg, initialized_notif, list_msg])

    # Find the tools/list response (id=2)
    tools_resp = next((r for r in responses if r.get("id") == 2), None)
    assert tools_resp is not None, f"No tools/list response found in {responses}"
    tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
    assert tool_names == EXPECTED_TOOL_NAMES


def test_mcp_stdio_call_tool(fake_daemon):
    """Server executes dj_health tool call over stdio against fake daemon."""
    port = fake_daemon.server_address[1]

    init_msg = _jsonrpc("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0.1"},
    }, req_id=1)
    initialized_notif = _jsonrpc_notification("notifications/initialized")
    call_msg = _jsonrpc("tools/call", {
        "name": "dj_health",
        "arguments": {},
    }, req_id=2)

    responses = _run_mcp_session(
        [init_msg, initialized_notif, call_msg],
        env_override={"DYNAMIC_RADIO_HOST": "127.0.0.1", "DYNAMIC_RADIO_PORT": str(port)},
    )

    call_resp = next((r for r in responses if r.get("id") == 2), None)
    assert call_resp is not None, f"No tools/call response in {responses}"
    content = call_resp["result"]["content"]
    assert len(content) == 1
    data = json.loads(content[0]["text"])
    assert data["ok"] is True


def test_mcp_stdio_call_dj_command(fake_daemon):
    """Server executes dj_command over stdio."""
    port = fake_daemon.server_address[1]

    init_msg = _jsonrpc("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0.1"},
    }, req_id=1)
    initialized_notif = _jsonrpc_notification("notifications/initialized")
    call_msg = _jsonrpc("tools/call", {
        "name": "dj_command",
        "arguments": {"text": "volume 42"},
    }, req_id=2)

    responses = _run_mcp_session(
        [init_msg, initialized_notif, call_msg],
        env_override={"DYNAMIC_RADIO_HOST": "127.0.0.1", "DYNAMIC_RADIO_PORT": str(port)},
    )

    call_resp = next((r for r in responses if r.get("id") == 2), None)
    assert call_resp is not None
    data = json.loads(call_resp["result"]["content"][0]["text"])
    assert data["response"] == "Executed: volume 42"
