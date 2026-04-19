"""MCP server for Axi to control the Dynamic Radio daemon.

Both this MCP server and the daemon run on 127.0.0.1.

Configure in ~/axi-user-data/mcp_servers.json:
    {"dynamic-radio": {"command": "python", "args": ["-m", "dynamic_radio.mcp_server"]}}
"""

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp import stdio_server, types
from mcp.server import Server

DAEMON_URL = "http://127.0.0.1:8420"

# Tidal searches can take 30-60s via MusicBrainz ISRC cross-referencing
REQUEST_TIMEOUT = 120


def _daemon_url() -> str:
    return DAEMON_URL


def _http_get(path: str) -> dict[str, Any]:
    url = f"{_daemon_url()}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())


def _http_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{_daemon_url()}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())


server = Server("dynamic-radio")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="dj_status",
            description=(
                "Get current Dynamic Radio status: playing track, plan block, "
                "volume, time remaining, daemon state, stream_url (Icecast MP3 mount)."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="dj_command",
            description=(
                "Send a command to the Dynamic Radio. Commands: "
                "play <query>, skip, pause, resume, volume <0-100>, "
                "status, mood <mood>."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Command text, e.g. 'play brostep', 'skip', 'volume 50'",
                    },
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="dj_health",
            description="Check if the Dynamic Radio daemon is reachable.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="dj_upload_plan",
            description=(
                "Upload a day plan to the daemon. The daemon saves it locally "
                "and uses it for track selection. Use after generating or "
                "adjusting a plan."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "object",
                        "description": "Full plan JSON with date, generated_at, and blocks array.",
                    },
                },
                "required": ["plan"],
            },
        ),
        types.Tool(
            name="dj_get_plan",
            description="Get today's plan from the daemon.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="dj_search",
            description=(
                "Search Tidal for tracks. Returns track metadata (tidal_id, name, "
                "artist, album, duration, bpm, key). Use for finding tracks to queue."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query — artist name, track name, genre, or mood.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20).",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="dj_feedback",
            description=(
                "Get feedback summary: total likes/dislikes, recent plays with "
                "skip info and play duration. Use for understanding listener "
                "preferences when selecting tracks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "Lookback window in hours (default 24).",
                        "default": 24,
                    },
                },
            },
        ),
        types.Tool(
            name="dj_queue_tracks",
            description=(
                "Push a batch of tracks to the daemon's play queue. Tracks are "
                "played in order. Use tidal_id from dj_search results."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tracks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tidal_id": {"type": "integer"},
                                "name": {"type": "string"},
                                "artist": {"type": "string"},
                                "album": {"type": "string"},
                            },
                            "required": ["tidal_id", "name", "artist"],
                        },
                        "description": "Array of track objects with at least tidal_id, name, artist.",
                    },
                },
                "required": ["tracks"],
            },
        ),
        types.Tool(
            name="dj_mood",
            description=(
                "Apply a mood change to the Dynamic Radio. Modifies remaining plan "
                "blocks, flushes queue, and does an immediate programmatic "
                "refill (~15 tracks). The daemon automatically triggers the "
                "refill agent when the queue runs low."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mood": {
                        "type": "string",
                        "description": "Mood description, e.g. 'more energy', 'something chill', 'focus mode'",
                    },
                },
                "required": ["mood"],
            },
        ),
        types.Tool(
            name="dj_clear_queue",
            description=(
                "Flush the track queue. Use after mood/plan changes to remove "
                "tracks selected for the old context. The daemon falls back to "
                "Tidal search using the new plan block until the agent refills."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        if name == "dj_status":
            result = _http_get("/status")
        elif name == "dj_command":
            text = arguments.get("text", "")
            result = _http_post("/command", {"text": text})
        elif name == "dj_health":
            result = _http_get("/health")
        elif name == "dj_upload_plan":
            plan = arguments.get("plan", {})
            result = _http_post("/plan", plan)
        elif name == "dj_get_plan":
            result = _http_get("/plan")
        elif name == "dj_search":
            query = arguments.get("query", "")
            limit = arguments.get("limit", 20)
            result = _http_get(f"/search?q={urllib.parse.quote(query)}&limit={limit}")
        elif name == "dj_feedback":
            hours = arguments.get("hours", 24)
            result = _http_get(f"/feedback?hours={hours}")
        elif name == "dj_queue_tracks":
            tracks = arguments.get("tracks", [])
            result = _http_post("/queue", {"tracks": tracks})
        elif name == "dj_mood":
            mood = arguments.get("mood", "")
            result = _http_post("/mood", {"mood": mood})
        elif name == "dj_clear_queue":
            result = _http_post("/queue/clear", {})
        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    except urllib.error.URLError as e:
        return [types.TextContent(
            type="text",
            text=f"Cannot reach Dynamic Radio daemon at {_daemon_url()}: {e}",
        )]
    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
