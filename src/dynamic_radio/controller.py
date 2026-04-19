"""DJ controller — state machine and command dispatcher.

Manages the Dynamic Radio lifecycle (active/paused/override) and handles
user commands. Axi calls handle_command() with raw text from Discord;
the controller dispatches to the appropriate action and returns a
response string for Axi to post back.
"""

import enum
import logging
from datetime import date, datetime
from typing import Any

from dynamic_radio.plan import get_current_block, load_plan
from dynamic_radio.player import MpvPlayer
from dynamic_radio.track_db import TrackDB

logger = logging.getLogger(__name__)


class DJState(enum.Enum):
    ACTIVE = "active"      # Dynamic Radio is selecting and playing tracks
    PAUSED = "paused"      # User paused — mpv may still be playing but no new tracks queued
    OVERRIDE = "override"  # User played something specific, Dynamic Radio will resume after


class DJController:
    """Central controller for the Dynamic Radio service.

    Coordinates the player, plan, track database, and Tidal session.
    Exposes handle_command() for Axi to call from Discord.
    """

    def __init__(
        self,
        player: MpvPlayer,
        db: TrackDB,
        tidal_session: Any = None,
    ):
        self.player = player
        self.db = db
        self.tidal_session = tidal_session
        self.state = DJState.PAUSED
        self._current_track: dict[str, Any] | None = None
        self.on_skip: Any = None  # Callback set by daemon for skip tracking
        self.on_volume: Any = None  # Callback set by daemon for stream volume

    @property
    def status_emoji(self) -> str:
        """Emoji for Discord channel status."""
        if self.state == DJState.ACTIVE:
            return "🎵"
        if self.state == DJState.OVERRIDE:
            return "⏸️"
        return "🔇"

    def handle_command(self, text: str) -> str:
        """Parse and dispatch a /dj command. Returns response text.

        Args:
            text: The command text after "/dj", e.g. "play something chill"
                  or "" for status.

        Returns:
            A string response to post back to Discord.
        """
        text = text.strip()

        if not text or text == "status":
            return self._cmd_status()

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        handlers = {
            "play": self._cmd_play,
            "queue": self._cmd_queue,
            "skip": self._cmd_skip,
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "mood": self._cmd_mood,
            "history": self._cmd_history,
            "plan": self._cmd_plan,
            "like": self._cmd_like,
            "dislike": self._cmd_dislike,
            "volume": self._cmd_volume,
        }

        handler = handlers.get(cmd)
        if handler is None:
            return f"Unknown command: `{cmd}`. Try: play, queue, skip, pause, resume, mood, history, plan, like, dislike, volume"

        try:
            return handler(arg)
        except Exception as e:
            logger.error("Command '%s' failed: %s", text, e, exc_info=True)
            return f"Error: {e}"

    # --- Command handlers ---

    def _cmd_status(self) -> str:
        """Show current DJ state."""
        status = self.player.get_status()
        state_str = self.state.value

        if status["idle"]:
            track_str = "Nothing playing"
        else:
            track_str = f"Playing: {self._current_track_display()}"
            remaining = self.player.time_remaining()
            if remaining > 0:
                mins, secs = divmod(int(remaining), 60)
                track_str += f" ({mins}:{secs:02d} remaining)"

        plan = load_plan()
        block_str = ""
        if plan:
            block = get_current_block(plan)
            if block:
                block_str = f"\nCurrent block: {block['mood']} ({block['start']}-{block['end']}, energy={block['energy']})"

        volume = status.get("volume", 0)
        return f"**Dynamic Radio: {state_str}** {self.status_emoji}\n{track_str}\nVolume: {int(volume)}%{block_str}"

    def _cmd_play(self, query: str) -> str:
        """Search Tidal and play immediately (override mode)."""
        if not query:
            return "Usage: `/dj play <search query>`"
        if not self.tidal_session:
            return "Tidal session not connected"

        results = self.tidal_session.search(query, limit=10)
        tracks = results.get("tracks", []) if isinstance(results, dict) else []

        # tidalapi search returns a SearchResult with .tracks attribute
        if hasattr(results, "tracks"):
            tracks = results.tracks

        if not tracks:
            return f"No results for: {query}"

        track = tracks[0]
        url = track.get_url()

        self.player.play_url(url)
        self.state = DJState.OVERRIDE

        track_info = self._cache_tidal_track(track)
        self._current_track = track_info

        return f"▶️ Playing: **{track.name}** — {track.artist.name}\n(Dynamic Radio paused, `/dj resume` to return)"

    def _cmd_queue(self, query: str) -> str:
        """Search Tidal and append to playlist."""
        if not query:
            return "Usage: `/dj queue <search query>`"
        if not self.tidal_session:
            return "Tidal session not connected"

        results = self.tidal_session.search(query, limit=10)
        tracks = []
        if hasattr(results, "tracks"):
            tracks = results.tracks
        elif isinstance(results, dict):
            tracks = results.get("tracks", [])

        if not tracks:
            return f"No results for: {query}"

        track = tracks[0]
        url = track.get_url()

        self.player.append_url(url)
        self._cache_tidal_track(track)

        return f"➕ Queued: **{track.name}** — {track.artist.name}"

    def _cmd_skip(self, _arg: str) -> str:
        """Skip current track."""
        status = self.player.get_status()
        if status["idle"]:
            return "Nothing playing to skip"

        if self.on_skip:
            self.on_skip()
        self.player.skip()
        return "⏭️ Skipped"

    def _cmd_pause(self, _arg: str) -> str:
        """Pause Dynamic Radio (music keeps playing if on)."""
        self.state = DJState.PAUSED
        self.player.pause()
        return "⏸️ Dynamic Radio paused"

    def _cmd_resume(self, _arg: str) -> str:
        """Resume Dynamic Radio from current time context."""
        self.state = DJState.ACTIVE
        self.player.resume()
        return "▶️ Dynamic Radio resumed"

    def _cmd_mood(self, description: str) -> str:
        """Tell user to adjust mood via Axi in Discord."""
        if not description:
            return "Usage: `/dj mood <description>` (e.g. 'more energy', 'something chill')"

        return (
            f"🎭 Mood request noted: *{description}*\n"
            "Plan adjustments are handled by Axi — use `/dj mood <description>` "
            "in the dynamic-radio Discord channel and Axi will rewrite the remaining plan blocks."
        )

    def _cmd_history(self, _arg: str) -> str:
        """Show last 10 played tracks."""
        rows = self.db.conn.execute(
            """SELECT t.name, t.artist, ph.played_at
               FROM play_history ph
               JOIN tracks t ON ph.tidal_id = t.tidal_id
               ORDER BY ph.played_at DESC LIMIT 10"""
        ).fetchall()

        if not rows:
            return "No play history yet"

        lines = []
        for row in rows:
            time_str = datetime.fromisoformat(row["played_at"]).strftime("%H:%M")
            lines.append(f"• `{time_str}` {row['name']} — {row['artist']}")
        return "**Recent tracks:**\n" + "\n".join(lines)

    def _cmd_plan(self, _arg: str) -> str:
        """Show today's DJ plan."""
        plan = load_plan()
        if not plan:
            return "No plan for today. Generate one with the daily scheduler."

        lines = [f"**DJ Plan for {plan['date']}**"]
        now = datetime.now().strftime("%H:%M")
        for block in plan.get("blocks", []):
            marker = " ◀️" if block["start"] <= now < block["end"] else ""
            genres = ", ".join(block["genres"][:3])
            lines.append(
                f"• `{block['start']}-{block['end']}` {block['mood']} "
                f"(energy={block['energy']}, BPM={block['bpm_range'][0]}-{block['bpm_range'][1]}) "
                f"[{genres}]{marker}"
            )
        return "\n".join(lines)

    def _cmd_like(self, _arg: str) -> str:
        """Like current track."""
        if not self._current_track:
            return "Nothing playing to like"
        # Liking just means the track gets played more (higher play count → higher affinity score)
        return f"👍 Liked: {self._current_track_display()}"

    def _cmd_dislike(self, _arg: str) -> str:
        """Dislike current track — skips and marks for avoidance."""
        if not self._current_track:
            return "Nothing playing to dislike"

        tid = self._current_track["tidal_id"]
        self.db.dislike(tid)
        self.player.skip()
        return f"👎 Disliked and skipped: {self._current_track_display()}"

    def _cmd_volume(self, arg: str) -> str:
        """Set stream volume preference (0-100). Clients apply this as local attenuation."""
        if not arg:
            if self.on_volume:
                result = self.on_volume(None)
                return f"🔊 Volume: {result['volume']}%"
            status = self.player.get_status()
            return f"🔊 Volume: {int(status['volume'])}%"
        try:
            vol = int(arg)
        except ValueError:
            return "Usage: `/dj volume <0-100>`"
        if self.on_volume:
            self.on_volume(vol)
        return f"🔊 Volume set to {vol}%"

    # --- Helpers ---

    def _current_track_display(self) -> str:
        if not self._current_track:
            return "Unknown"
        return f"{self._current_track.get('name', '?')} — {self._current_track.get('artist', '?')}"

    def _cache_tidal_track(self, track: Any) -> dict[str, Any]:
        """Cache a tidalapi Track object into the DB and return a dict."""
        info = {
            "tidal_id": track.id,
            "name": track.name,
            "artist": track.artist.name if hasattr(track.artist, "name") else str(track.artist),
            "album": track.album.name if hasattr(track, "album") and track.album else None,
            "bpm": getattr(track, "bpm", None),
            "key": getattr(track, "key", None),
            "key_scale": getattr(track, "key_scale", None),
            "duration": track.duration if hasattr(track, "duration") else None,
            "dj_ready": getattr(track, "dj_ready", False),
            "stem_ready": getattr(track, "stem_ready", False),
        }
        self.db.upsert_track(info)
        self.db.log_play(info["tidal_id"])
        return info
