"""Dynamic Radio daemon — main polling loop.

Ties together plan, player, and Tidal to run 24/7.
Each cycle: check if a new track is needed, pop from the agent-curated
track queue if available, or fall back to algorithmic selection via Tidal.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import random
import signal
import time
from datetime import date, datetime
from typing import Any

import aiohttp
from aiohttp import web

from dynamic_radio.api import create_app
from dynamic_radio.controller import DJController, DJState
from dynamic_radio.plan import default_plan, get_current_block, load_plan, save_plan
from dynamic_radio.player import MpvPlayer
from dynamic_radio.selector import select_track
from dynamic_radio.tidal_auth import get_session, refresh_session
from dynamic_radio.track_db import TrackDB

logger = logging.getLogger(__name__)

# How far ahead (seconds) to queue the next track for gapless playback
PREFETCH_THRESHOLD = 30.0

# How often to poll (seconds) when actively playing
POLL_INTERVAL = 5.0

# How many Tidal search results to fetch per query
SEARCH_LIMIT = 50

# Queries per block — we search multiple genre terms to get variety
MAX_SEARCHES_PER_CYCLE = 3

# Agent queue: request refill when less than this many minutes of music queued
QUEUE_LOW_MINUTES = 60

# Trigger the refill agent when queue drops below this many minutes
REFILL_TRIGGER_MINUTES = 10

# Minimum seconds between refill agent triggers (avoid spamming)
REFILL_TRIGGER_COOLDOWN = 600  # 10 minutes

# Tidal access tokens live ~4h; refresh every 3h as a safe margin
TIDAL_REFRESH_INTERVAL = 10800


class DJDaemon:
    """Main daemon that runs the Dynamic Radio loop."""

    def __init__(self, controller: DJController, api_port: int = 8420, streamer=None,
                 initial_volume: int = 80):
        self.controller = controller
        self.api_port = api_port
        self.streamer = streamer
        self.controller.on_skip = lambda: self._end_current_play(skipped=True)
        self.controller.on_volume = lambda vol: self.set_stream_volume(vol) if vol is not None else {"volume": self._stream_volume, "ts": self._stream_volume_ts}
        self._running = False
        self._next_queued = False
        self._queued_track: dict[str, Any] | None = None
        self._plan_date: date | None = None
        self._current_play_id: int | None = None
        self._play_start_time: float | None = None
        self._track_queue: list[dict[str, Any]] = []  # Visible queue (agentic + 1 selector)
        self._selector_buffer: list[dict[str, Any]] = []  # Internal selector buffer
        self._last_refill_trigger: float = 0.0
        self._refill_in_progress: bool = False
        # Stream volume: stored preference for client-side attenuation.
        # mpv runs at 100; clients apply this as player.volume.
        self._stream_volume: int = initial_volume
        self._stream_volume_ts: str = datetime.now().isoformat()
        # Queue persistence
        self._queue_path = Path.home() / ".local" / "share" / "dynamic-radio" / "queue.json"
        # WebSocket clients
        self._ws_clients: set = set()

    def set_stream_volume(self, vol: int) -> dict[str, Any]:
        """Set the stream volume preference (client-side attenuation)."""
        self._stream_volume = max(0, min(100, vol))
        self._stream_volume_ts = datetime.now().isoformat()
        self.broadcast({"type": "volume", "volume": self._stream_volume, "volume_ts": self._stream_volume_ts})
        return {"volume": self._stream_volume, "ts": self._stream_volume_ts}

    # --- Queue persistence ---

    def _save_queue(self) -> None:
        """Persist track queue to disk."""
        try:
            self._queue_path.parent.mkdir(parents=True, exist_ok=True)
            self._queue_path.write_text(json.dumps(self._track_queue))
        except Exception:
            logger.error("Failed to save queue", exc_info=True)

    def _load_queue(self) -> int:
        """Load track queue from disk. Returns number of tracks loaded."""
        try:
            if self._queue_path.exists():
                tracks = json.loads(self._queue_path.read_text())
                if isinstance(tracks, list) and tracks:
                    self._track_queue = tracks
                    logger.info("Loaded %d tracks from persistent queue", len(tracks))
                    return len(tracks)
        except Exception:
            logger.error("Failed to load queue, starting fresh", exc_info=True)
        return 0

    # --- WebSocket broadcast ---

    def broadcast(self, data: dict[str, Any]) -> None:
        """Send JSON message to all connected WebSocket clients."""
        if not self._ws_clients:
            return
        msg = json.dumps(data)
        stale = set()
        for ws in self._ws_clients:
            try:
                asyncio.ensure_future(ws.send_str(msg))
            except Exception:
                stale.add(ws)
        self._ws_clients -= stale

    def broadcast_status(self) -> None:
        """Broadcast full status to all WebSocket clients."""
        if not self._ws_clients:
            return
        status = self.controller.player.get_status()
        block = None
        plan = load_plan()
        if plan:
            block = get_current_block(plan)
        upcoming = [{"name": t.get("name", "?"), "artist": t.get("artist", "?")}
                    for t in self._track_queue[:5]]
        self.broadcast({
            "type": "status",
            "state": self.controller.state.value,
            "current_track": self.controller._current_track,
            "current_block": block,
            "volume": self._stream_volume,
            "volume_ts": self._stream_volume_ts,
            "time_remaining": self.controller.player.time_remaining(),
            "queue_depth": self.queue_depth,
            "upcoming": upcoming,
        })

    async def run(self) -> None:
        """Run the DJ loop and HTTP API until stopped."""
        self._running = True
        logger.info("Dynamic Radio daemon starting")

        # Force mpv to max — volume control is client-side via stream_volume
        self.controller.player.set_volume(100)

        # Refresh Tidal token at startup — handles case where daemon was down
        # long enough for the cached access token to expire.
        await asyncio.to_thread(refresh_session, self.controller.tidal_session)

        # Ensure we have a plan for today
        await self._ensure_plan()

        # Start HTTP API server
        app = create_app(self.controller, daemon=self)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.api_port)
        await site.start()
        logger.info("HTTP API listening on port %d", self.api_port)

        # Restore queue from disk, or pre-fill if empty
        restored = self._load_queue()
        if restored == 0:
            prefilled = await self.batch_refill(5)
            logger.info("Startup pre-fill: queued %d tracks", prefilled)
        else:
            logger.info("Restored %d tracks from persistent queue, skipping pre-fill", restored)

        # Start in active mode
        self.controller.state = DJState.ACTIVE
        self.controller.player.resume()

        logger.info("Dynamic Radio daemon running")

        refresh_task = asyncio.create_task(self._tidal_refresh_loop())

        try:
            while self._running:
                try:
                    await self._tick()
                except Exception:
                    logger.error("Tick failed", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            refresh_task.cancel()
            try:
                await refresh_task
            except (asyncio.CancelledError, Exception):
                pass
            await runner.cleanup()

        logger.info("Dynamic Radio daemon stopped")

    async def _tidal_refresh_loop(self) -> None:
        """Background task: periodically refresh the Tidal access token.

        Access tokens expire ~4h after issue. Refreshing every 3h keeps them
        valid and avoids reliance on tidalapi's reactive refresh, which only
        fires on v1 error shapes (not the v2 openapi shape used by ISRC lookup).
        """
        while self._running:
            try:
                await asyncio.sleep(TIDAL_REFRESH_INTERVAL)
            except asyncio.CancelledError:
                return
            if not self._running:
                return
            try:
                await asyncio.to_thread(refresh_session, self.controller.tidal_session)
            except Exception:
                logger.error("Tidal refresh loop iteration failed", exc_info=True)

    def stop(self) -> None:
        """Signal the daemon to stop."""
        self._running = False

    async def _tick(self) -> None:
        """One iteration of the DJ loop."""
        # Refresh plan at midnight
        if self._plan_date != date.today():
            await self._ensure_plan()

        # Agentic refill — trigger when queue is running low
        if self.queue_depth < 3 and self.queue_duration_minutes < REFILL_TRIGGER_MINUTES:
            asyncio.create_task(self._trigger_refill_agent())

        # Auto-populate with selector tracks as fallback (covers agent startup delay)
        total_buffered = len(self._track_queue) + len(self._selector_buffer)
        if total_buffered < 3:
            await self.batch_refill(count=5 - total_buffered)

        # Feed all selector buffer tracks into visible queue
        if self._selector_buffer:
            self._track_queue.extend(self._selector_buffer)
            self._selector_buffer.clear()

        state = self.controller.state

        if state == DJState.PAUSED:
            return

        if state == DJState.OVERRIDE:
            status = self.controller.player.get_status()
            if status["idle"]:
                # Override finished, no queued track — play immediately
                logger.info("Override track finished, resuming auto-DJ")
                self.controller.state = DJState.ACTIVE
                self._next_queued = False
                await self._play_next()
                return

            remaining = self.controller.player.time_remaining()
            if remaining < PREFETCH_THRESHOLD and not self._next_queued:
                # Override ending soon — queue next auto-DJ track for gapless
                await self._queue_next()
                self._next_queued = True
            elif self._next_queued and remaining >= PREFETCH_THRESHOLD:
                # Queued track started playing (remaining jumped up)
                logger.info("Override→active transition (queued track started)")
                self.controller.state = DJState.ACTIVE
                self._next_queued = False
                if self._queued_track:
                    self._end_current_play()
                    self.controller._current_track = self._queued_track
                    self._current_play_id = self.controller.db.log_play(self._queued_track["tidal_id"])
                    self._play_start_time = time.time()
                    if self.streamer:
                        self.streamer.update_metadata(
                            self._queued_track.get("name", "Unknown"),
                            self._queued_track.get("artist", ""),
                        )
                    self._queued_track = None
                    self.broadcast_status()
            return

        # ACTIVE state — manage playback
        status = self.controller.player.get_status()
        remaining = self.controller.player.time_remaining()

        if status["idle"]:
            # Nothing playing — select and play immediately
            await self._play_next()
            self._next_queued = False
        elif remaining < PREFETCH_THRESHOLD and not self._next_queued:
            # Current track ending soon — queue next for gapless
            await self._queue_next()
            self._next_queued = True
        elif remaining >= PREFETCH_THRESHOLD:
            # Reset flag when we're far from the end (new track started)
            if self._next_queued and self._queued_track:
                self._end_current_play()
                self.controller._current_track = self._queued_track
                self._current_play_id = self.controller.db.log_play(self._queued_track["tidal_id"])
                self._play_start_time = time.time()
                logger.info(
                    "Now playing: %s — %s",
                    self._queued_track.get("name"),
                    self._queued_track.get("artist"),
                )
                if self.streamer:
                    self.streamer.update_metadata(
                        self._queued_track.get("name", "Unknown"),
                        self._queued_track.get("artist", ""),
                    )
                self._queued_track = None
                self.broadcast_status()
            self._next_queued = False

    async def _ensure_plan(self) -> None:
        """Load today's plan, falling back to defaults if none exists."""
        today = date.today()
        plan = load_plan(today)
        if plan is None:
            logger.info("No Axi-generated plan for %s, using defaults", today)
            plan = default_plan(today)
            save_plan(plan, today)
        self._plan_date = today
        logger.info("Plan ready for %s (%d blocks)", today, len(plan.get("blocks", [])))

    def _end_current_play(self, skipped: bool = False) -> None:
        """Record how the current play ended."""
        if self._current_play_id is not None and self._play_start_time is not None:
            duration = int(time.time() - self._play_start_time)
            self.controller.db.log_play_end(self._current_play_id, duration, skipped=skipped)
        self._current_play_id = None
        self._play_start_time = None

    @property
    def queue_depth(self) -> int:
        """Number of tracks in the agent-curated queue."""
        return len(self._track_queue)

    @property
    def queue_duration_minutes(self) -> float:
        """Total duration of queued tracks in minutes."""
        total_seconds = sum(t.get("duration", 0) or 0 for t in self._track_queue)
        return total_seconds / 60.0

    @property
    def needs_tracks(self) -> bool:
        """Whether the agent should refill the queue (< 60 min of music)."""
        return self.queue_duration_minutes < QUEUE_LOW_MINUTES

    def clear_queue(self) -> int:
        """Flush the track queue and selector buffer. Returns number of tracks removed."""
        removed = len(self._track_queue) + len(self._selector_buffer)
        self._track_queue.clear()
        self._selector_buffer.clear()
        if removed:
            logger.info("Queue flushed: removed %d tracks (queue + selector buffer)", removed)
        self._save_queue()
        return removed

    async def batch_refill(self, count: int = 15) -> int:
        """Programmatic batch refill via selector.py into the selector buffer.

        Tracks go into the internal buffer and are fed one at a time into
        the visible queue. Returns number of tracks added.
        """
        added = 0
        for _ in range(count):
            track = await self._select_from_tidal()
            if track is None:
                break
            track["_selector"] = True
            self._selector_buffer.append(track)
            self.controller.db.upsert_track(track)
            added += 1
        if added:
            logger.info("Batch refill: added %d tracks to selector buffer (buffer=%d)", added, len(self._selector_buffer))
        return added

    def add_to_queue(self, tracks: list[dict[str, Any]]) -> int:
        """Add agentic tracks to the queue, replacing any selector-chosen tracks.

        Flushes the selector buffer and removes selector tracks from the
        visible queue (the currently playing track is not affected).
        Returns new queue depth.
        """
        # Flush selector buffer — agentic tracks are higher quality
        if self._selector_buffer:
            logger.info("Agentic refill: clearing %d selector buffer tracks", len(self._selector_buffer))
            self._selector_buffer.clear()
        # Remove any selector tracks still in the visible queue
        old_depth = len(self._track_queue)
        self._track_queue = [t for t in self._track_queue if not t.get("_selector")]
        removed = old_depth - len(self._track_queue)
        if removed:
            logger.info("Agentic refill: removed %d selector tracks from queue", removed)
        for track in tracks:
            # Enrich missing duration from track DB (cached from dj_search)
            if not track.get("duration"):
                cached = self.controller.db.get_track(track["tidal_id"])
                if cached and cached.get("duration"):
                    track["duration"] = cached["duration"]
            self.controller.db.upsert_track(track)
        self._track_queue.extend(tracks)
        self._refill_in_progress = False
        logger.info("Queue: added %d agentic tracks (depth now %d)", len(tracks), len(self._track_queue))
        self._save_queue()
        return len(self._track_queue)

    def _pop_from_queue(self) -> dict[str, Any] | None:
        """Pop the next track from the agent queue. Returns None if empty."""
        if self._track_queue:
            track = self._track_queue.pop(0)
            logger.info(
                "Queue pop: %s — %s (depth now %d)",
                track.get("artist"), track.get("name"), len(self._track_queue),
            )
            self._save_queue()
            return track
        return None

    async def _get_next_track(self) -> dict[str, Any] | None:
        """Get next track: queue first, then selector buffer, then live Tidal search."""
        track = self._pop_from_queue()
        if track is not None:
            return track
        # Try selector buffer
        if self._selector_buffer:
            track = self._selector_buffer.pop(0)
            logger.info("Queue empty, using selector buffer track: %s — %s", track.get("artist"), track.get("name"))
            return track
        logger.debug("Queue and selector buffer empty, falling back to Tidal search")
        track = await self._select_from_tidal()
        if track is not None:
            track["_selector"] = True
        return track

    async def _trigger_refill_agent(self) -> None:
        """Fire-and-forget POST to Axi to spawn the refill agent."""
        if self._refill_in_progress:
            return
        now = time.time()
        if now - self._last_refill_trigger < REFILL_TRIGGER_COOLDOWN:
            return
        self._last_refill_trigger = now
        self._refill_in_progress = True

        trigger_url = os.environ.get("AXI_TRIGGER_URL")
        if not trigger_url:
            return

        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    trigger_url,
                    json={
                        "session": "dynamic-radio-refill",
                        "prompt": (
                            "Queue ran dry — do an agentic refill. "
                            "Read prompts/select-tracks.md for instructions."
                        ),
                        "cwd": str(Path(__file__).resolve().parent.parent.parent),
                        "extensions": ["dynamic-radio"],
                        "mcp_servers": ["dynamic-radio"],
                    },
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            logger.info("Triggered refill agent via %s", trigger_url)
        except Exception:
            logger.warning("Failed to trigger refill agent", exc_info=True)

    async def _play_next(self) -> None:
        """Select and play a track immediately."""
        self._end_current_play()
        track = await self._get_next_track()
        if track is None:
            logger.warning("No track selected, will retry next tick")
            return

        url = await asyncio.to_thread(self._get_stream_url, track)
        if url is None:
            logger.warning("No stream URL for track %s", track.get("tidal_id"))
            return

        self.controller.player.play_url(url)
        self.controller._current_track = track
        self._current_play_id = self.controller.db.log_play(track["tidal_id"])
        self._play_start_time = time.time()
        logger.info("Playing: %s — %s", track.get("name"), track.get("artist"))

        if self.streamer:
            self.streamer.update_metadata(
                track.get("name", "Unknown"),
                track.get("artist", ""),
            )
        self.broadcast_status()

    async def _queue_next(self) -> None:
        """Select and append next track for gapless playback."""
        track = await self._get_next_track()
        if track is None:
            logger.warning("No track to queue, will retry")
            return

        url = await asyncio.to_thread(self._get_stream_url, track)
        if url is None:
            return

        self.controller.player.append_url(url)
        self._queued_track = track
        logger.info("Queued next: %s — %s", track.get("name"), track.get("artist"))

    async def _select_from_tidal(self) -> dict[str, Any] | None:
        """Search MusicBrainz for genre-verified candidates and select the best one.

        Uses MB tag search with ISRC cross-reference to Tidal. If the first
        pass yields too few candidates, retries with a larger search limit.
        """
        session = self.controller.tidal_session
        if session is None:
            logger.error("No Tidal session")
            return None

        plan = load_plan()
        if plan is None:
            logger.warning("No plan loaded")
            return None

        block = get_current_block(plan)
        if block is None:
            logger.warning("No active plan block for current time")
            return None

        # Build search queries from block genres and mood
        queries = block.get("genres", [])[:MAX_SEARCHES_PER_CYCLE]
        if not queries:
            queries = [block.get("mood", "ambient")]

        # Run blocking MB search in a thread to avoid starving the event loop
        candidates = await asyncio.to_thread(self._search_via_musicbrainz, session, queries)

        # If MB yielded too few, retry with higher limit and different offset
        if len(candidates) < 5:
            logger.info("MB search yielded %d candidates, retrying with larger limit", len(candidates))
            seen_ids = {c["tidal_id"] for c in candidates}
            more = await asyncio.to_thread(
                self._search_via_musicbrainz, session, queries,
                limit=100, seen_ids=seen_ids,
            )
            candidates.extend(more)

        if not candidates:
            logger.warning("No candidates from any search")
            return None

        # Select the best track
        previous = self.controller._current_track
        selected = select_track(candidates, block, self.controller.db, previous_track=previous)
        return selected

    def _search_via_musicbrainz(
        self, session: Any, genre_tags: list[str],
        limit: int = SEARCH_LIMIT, seen_ids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Search MusicBrainz by genre tag, cross-reference to Tidal via ISRC.

        Returns candidates with real genre tags from MB. Only includes
        tracks that have ISRCs, verified genre tags, and are available on Tidal.
        """
        import musicbrainzngs
        from dynamic_radio.genre_lookup import _rate_limit

        candidates = []
        if seen_ids is None:
            seen_ids = set()

        for tag in genre_tags:
            try:
                _rate_limit()
                offset = random.randint(0, max(1, 200 - limit))
                result = musicbrainzngs.search_recordings(tag=tag, limit=limit, offset=offset)
                recordings = result.get("recording-list", [])

                for rec in recordings:
                    isrc_list = rec.get("isrc-list", [])
                    if not isrc_list:
                        continue

                    # Extract MB tags as genre string (top 5 by vote count)
                    mb_tags = rec.get("tag-list", [])
                    if not mb_tags:
                        continue  # No tags = can't verify genre

                    # Verify the searched tag actually appears in this recording's tags
                    tag_names = {t["name"].lower() for t in mb_tags if int(t.get("count", 0)) > 0}
                    if tag.lower() not in tag_names:
                        continue  # Search result doesn't actually have this genre tag

                    sorted_tags = sorted(mb_tags, key=lambda x: int(x.get("count", 0)), reverse=True)
                    genre_str = ",".join(
                        t["name"].lower() for t in sorted_tags[:5]
                        if int(t.get("count", 0)) > 0
                    )

                    # Try to find on Tidal via ISRC
                    for isrc in isrc_list[:1]:  # Just try first ISRC
                        try:
                            tidal_tracks = session.get_tracks_by_isrc(isrc)
                            for t in tidal_tracks[:1]:  # Take first match
                                if t.id in seen_ids:
                                    continue
                                seen_ids.add(t.id)

                                info = {
                                    "tidal_id": t.id,
                                    "name": t.name,
                                    "artist": t.artist.name if hasattr(t.artist, "name") else str(t.artist),
                                    "album": t.album.name if hasattr(t, "album") and t.album else None,
                                    "bpm": getattr(t, "bpm", None),
                                    "key": getattr(t, "key", None),
                                    "key_scale": getattr(t, "key_scale", None),
                                    "duration": t.duration if hasattr(t, "duration") else None,
                                    "dj_ready": getattr(t, "dj_ready", False),
                                    "stem_ready": getattr(t, "stem_ready", False),
                                    "isrc": getattr(t, "isrc", None),
                                    "genres": genre_str,
                                }
                                self.controller.db.upsert_track(info)
                                candidates.append(info)
                        except Exception:
                            continue  # Track not on Tidal, skip

            except Exception:
                logger.warning("MusicBrainz search failed for tag '%s'", tag, exc_info=True)

        if candidates:
            logger.info(
                "MB-first search: %d candidates from %d genre tags",
                len(candidates), len(genre_tags),
            )
        return candidates

    def _get_stream_url(self, track: dict[str, Any]) -> str | None:
        """Get a stream URL from Tidal for a track."""
        session = self.controller.tidal_session
        if session is None:
            return None
        try:
            tidal_track = session.track(track["tidal_id"])
            return tidal_track.get_url()
        except Exception:
            logger.error("Failed to get URL for track %s", track["tidal_id"], exc_info=True)
            return None


def create_daemon(
    volume: int = 80,
    audio_output: str | None = None,
    api_port: int = 8420,
    stream: bool = False,
) -> DJDaemon:
    """Create a fully wired DJDaemon instance."""
    from dynamic_radio.streamer import IcecastStreamer

    streamer = None

    if stream:
        streamer = IcecastStreamer()
        streamer.start()

    session = get_session()
    db = TrackDB()
    player = MpvPlayer(volume=volume, audio_output=audio_output)
    player.start()

    controller = DJController(player=player, db=db, tidal_session=session)
    return DJDaemon(controller, api_port=api_port, streamer=streamer,
                    initial_volume=volume)
