"""HTTP API for remote daemon control.

Exposes the DJController via a lightweight HTTP server so Axi can
send commands over the network (Tailscale).
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from aiohttp import web

from dynamic_radio.controller import DJController
from dynamic_radio.plan import get_current_block, load_plan, save_plan
from dynamic_radio.mood import apply_mood

logger = logging.getLogger(__name__)


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Allow cross-origin requests from the Icecast web UI."""
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def create_app(controller: DJController, daemon: Any = None) -> web.Application:
    """Create an aiohttp app wired to the given controller."""
    app = web.Application(middlewares=[cors_middleware])
    app["controller"] = controller
    app["daemon"] = daemon
    app.router.add_post("/command", handle_command)
    app.router.add_post("/queue", handle_queue)
    app.router.add_post("/queue/clear", handle_queue_clear)
    app.router.add_post("/mood", handle_mood)
    app.router.add_post("/plan", handle_plan_upload)
    app.router.add_get("/plan", handle_plan_get)
    app.router.add_post("/volume", handle_volume)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/search", handle_search)
    app.router.add_get("/feedback", handle_feedback)
    app.router.add_get("/ws", handle_websocket)
    app.router.add_get("/", handle_now_playing)
    return app


async def handle_command(request: web.Request) -> web.Response:
    """Pass-through to DJController.handle_command()."""
    controller: DJController = request.app["controller"]
    try:
        body = await request.json()
        text = body.get("text", "")
    except (json.JSONDecodeError, KeyError):
        return web.json_response({"error": "Expected JSON with 'text' field"}, status=400)

    response = await asyncio.to_thread(controller.handle_command, text)
    return web.json_response({"response": response})


async def handle_queue(request: web.Request) -> web.Response:
    """Add tracks to the agent-curated queue."""
    daemon = request.app.get("daemon")
    if daemon is None:
        return web.json_response({"error": "Daemon not available"}, status=503)
    try:
        body = await request.json()
        tracks = body.get("tracks", [])
    except (json.JSONDecodeError, KeyError):
        return web.json_response({"error": "Expected JSON with 'tracks' array"}, status=400)

    if not tracks:
        return web.json_response({"error": "No tracks provided"}, status=400)

    depth = daemon.add_to_queue(tracks)
    return web.json_response({"ok": True, "added": len(tracks), "queue_depth": depth})


async def handle_queue_clear(request: web.Request) -> web.Response:
    """Flush the track queue. Used on mood/plan changes to invalidate stale tracks."""
    daemon = request.app.get("daemon")
    if daemon is None:
        return web.json_response({"error": "Daemon not available"}, status=503)
    removed = daemon.clear_queue()
    return web.json_response({"ok": True, "removed": removed, "queue_depth": 0})


async def handle_volume(request: web.Request) -> web.Response:
    """Set stream volume preference (client-side attenuation level)."""
    daemon = request.app.get("daemon")
    if daemon is None:
        return web.json_response({"error": "Daemon not available"}, status=503)
    try:
        body = await request.json()
        vol = int(body.get("volume", 80))
    except (json.JSONDecodeError, KeyError, ValueError):
        return web.json_response({"error": "Expected JSON with 'volume' integer"}, status=400)
    result = daemon.set_stream_volume(vol)
    return web.json_response({"ok": True, **result})


async def handle_mood(request: web.Request) -> web.Response:
    """Apply a mood change: modify plan and flush queue. Tick loop handles refill."""
    daemon = request.app.get("daemon")
    if daemon is None:
        return web.json_response({"error": "Daemon not available"}, status=503)
    try:
        body = await request.json()
        mood = body.get("mood", "")
    except (json.JSONDecodeError, KeyError):
        return web.json_response({"error": "Expected JSON with 'mood' field"}, status=400)

    if not mood:
        return web.json_response({"error": "Empty mood description"}, status=400)

    # 1. Load and modify plan
    plan = load_plan()
    if plan is None:
        return web.json_response({"error": "No plan for today"}, status=404)

    modified_blocks = apply_mood(plan, mood)
    save_plan(plan, datetime.now().date())
    logger.info("Mood change applied: '%s' (%d blocks modified)", mood, modified_blocks)

    # 2. Flush queue — tick loop will auto-refill on next iteration
    flushed = daemon.clear_queue()

    return web.json_response({
        "ok": True,
        "mood": mood,
        "blocks_modified": modified_blocks,
        "queue_flushed": flushed,
    })


async def handle_status(request: web.Request) -> web.Response:
    """Structured status for machine consumption."""
    controller: DJController = request.app["controller"]
    daemon = request.app.get("daemon")
    status = controller.player.get_status()
    remaining = controller.player.time_remaining()

    plan = load_plan()
    block: dict[str, Any] | None = None
    if plan:
        block = get_current_block(plan)

    # Upcoming queue (track name + artist only)
    upcoming: list[dict[str, Any]] = []
    if daemon:
        for t in daemon._track_queue[:5]:
            upcoming.append({"name": t.get("name", "?"), "artist": t.get("artist", "?")})

    # Recent history (last 5 played)
    history_rows = controller.db.conn.execute(
        """SELECT t.name, t.artist, ph.played_at
           FROM play_history ph
           JOIN tracks t ON ph.tidal_id = t.tidal_id
           ORDER BY ph.played_at DESC LIMIT 6""",
    ).fetchall()
    # Skip the first row (it's the currently playing track)
    recent_history = [
        {"name": row["name"], "artist": row["artist"]}
        for row in history_rows[1:]
    ]

    # Icecast stream URL for external consumers (e.g. Discord voice bot)
    stream_url = None
    if daemon and daemon.streamer:
        s = daemon.streamer
        stream_url = f"http://{s.icecast_host}:{s.icecast_port}/dynamicradio.mp3"

    data = {
        "state": controller.state.value,
        "idle": status["idle"],
        "volume": daemon._stream_volume if daemon else status.get("volume", 0),
        "volume_ts": daemon._stream_volume_ts if daemon else None,
        "time_remaining": remaining,
        "current_track": controller._current_track,
        "current_block": block,
        "queue_depth": daemon.queue_depth if daemon else 0,
        "queue_duration_minutes": round(daemon.queue_duration_minutes, 1) if daemon else 0,
        "needs_tracks": daemon.needs_tracks if daemon else True,
        "stream_url": stream_url,
        "upcoming": upcoming,
        "recent_history": recent_history,
        "timestamp": datetime.now().isoformat(),
    }
    return web.json_response(data)


async def handle_plan_upload(request: web.Request) -> web.Response:
    """Upload a plan JSON to the daemon's local filesystem."""
    try:
        plan = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    plan_date = plan.get("date")
    if not plan_date:
        return web.json_response({"error": "Plan must have a 'date' field"}, status=400)

    try:
        from datetime import date as date_cls
        target = date_cls.fromisoformat(plan_date)
    except (ValueError, TypeError):
        return web.json_response({"error": f"Invalid date: {plan_date}"}, status=400)

    path = save_plan(plan, target)
    logger.info("Plan uploaded for %s (%d blocks)", plan_date, len(plan.get("blocks", [])))

    # Flush queue — tracks were selected for the old plan context
    daemon = request.app.get("daemon")
    flushed = 0
    if daemon:
        flushed = daemon.clear_queue()

    return web.json_response({
        "ok": True, "date": plan_date, "path": str(path), "queue_flushed": flushed,
    })


async def handle_plan_get(request: web.Request) -> web.Response:
    """Get the current plan."""
    plan = load_plan()
    if plan is None:
        return web.json_response({"error": "No plan for today"}, status=404)
    return web.json_response(plan)


async def handle_search(request: web.Request) -> web.Response:
    """Search Tidal for tracks. Agent uses this for track discovery."""
    controller: DJController = request.app["controller"]
    query = request.query.get("q", "")
    if not query:
        return web.json_response({"error": "Missing 'q' parameter"}, status=400)

    limit = int(request.query.get("limit", "20"))
    session = controller.tidal_session
    if session is None:
        return web.json_response({"error": "Tidal session not connected"}, status=503)

    def _blocking_search():
        results = session.search(query, limit=limit)
        tracks = []
        if hasattr(results, "tracks"):
            tracks = results.tracks or []
        elif isinstance(results, dict):
            tracks = results.get("tracks", [])

        track_list = []
        for t in tracks:
            info = {
                "tidal_id": t.id,
                "name": t.name,
                "artist": t.artist.name if hasattr(t.artist, "name") else str(t.artist),
                "album": t.album.name if hasattr(t, "album") and t.album else None,
                "duration": t.duration if hasattr(t, "duration") else None,
                "bpm": getattr(t, "bpm", None),
                "key": getattr(t, "key", None),
                "key_scale": getattr(t, "key_scale", None),
                "dj_ready": getattr(t, "dj_ready", False),
                "stem_ready": getattr(t, "stem_ready", False),
                "isrc": getattr(t, "isrc", None),
            }
            controller.db.upsert_track(info)
            track_list.append(info)
        return track_list

    try:
        track_list = await asyncio.to_thread(_blocking_search)
        return web.json_response({"query": query, "count": len(track_list), "tracks": track_list})
    except Exception as e:
        logger.error("Tidal search failed for '%s': %s", query, e, exc_info=True)
        return web.json_response({"error": f"Search failed: {e}"}, status=500)


async def handle_feedback(request: web.Request) -> web.Response:
    """Get feedback summary for agent context."""
    controller: DJController = request.app["controller"]
    hours = int(request.query.get("hours", "24"))
    summary = controller.db.feedback_summary(hours=hours)
    return web.json_response(summary)


async def handle_health(request: web.Request) -> web.Response:
    """Liveness check."""
    return web.json_response({"ok": True})


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    """WebSocket endpoint for push-based UI updates."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    daemon = request.app.get("daemon")
    if daemon:
        daemon._ws_clients.add(ws)
        logger.info("WebSocket client connected (%d total)", len(daemon._ws_clients))

    try:
        async for msg in ws:
            pass  # Client→server messages not used; connection kept alive by ping/pong
    finally:
        if daemon:
            daemon._ws_clients.discard(ws)
            logger.info("WebSocket client disconnected (%d remaining)", len(daemon._ws_clients))

    return ws


async def handle_now_playing(request: web.Request) -> web.Response:
    """Serve now-playing HTML page."""
    from pathlib import Path
    html_path = Path(__file__).parent.parent.parent / "deploy" / "linux" / "now-playing.html"
    if not html_path.exists():
        return web.Response(text="now-playing.html not found", status=404)
    return web.Response(text=html_path.read_text(), content_type="text/html")
