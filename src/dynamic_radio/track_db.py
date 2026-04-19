"""SQLite track cache, play history, and feedback signals for the Dynamic Radio.

Stores track metadata (BPM, key, artist, etc.), play history with
duration/skip tracking, and like/dislike ratings for preference learning.
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".local" / "share" / "dynamic-radio" / "tracks.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    tidal_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    bpm REAL,
    key TEXT,
    key_scale TEXT,
    duration INTEGER,
    dj_ready INTEGER DEFAULT 0,
    stem_ready INTEGER DEFAULT 0,
    isrc TEXT,
    genres TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS play_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tidal_id INTEGER NOT NULL,
    played_at TEXT NOT NULL,
    play_duration INTEGER,
    skipped_at TEXT,
    FOREIGN KEY (tidal_id) REFERENCES tracks(tidal_id)
);

CREATE TABLE IF NOT EXISTS liked (
    tidal_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS disliked (
    tidal_id INTEGER PRIMARY KEY
);

CREATE INDEX IF NOT EXISTS idx_play_history_played_at ON play_history(played_at);
CREATE INDEX IF NOT EXISTS idx_play_history_tidal_id ON play_history(tidal_id);
CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm);
"""


_MIGRATIONS = [
    "ALTER TABLE play_history ADD COLUMN play_duration INTEGER",
    "ALTER TABLE play_history ADD COLUMN skipped_at TEXT",
    "CREATE TABLE IF NOT EXISTS liked (tidal_id INTEGER PRIMARY KEY)",
    "ALTER TABLE tracks ADD COLUMN isrc TEXT",
]


class TrackDB:
    """SQLite-backed track cache and play history."""

    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Apply schema migrations to existing databases."""
        for sql in _MIGRATIONS:
            try:
                self.conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # Column/table already exists
        self.conn.commit()

    def close(self):
        self.conn.close()

    def upsert_track(self, track: dict[str, Any]) -> None:
        """Insert or update a track in the cache.

        If the track has an ISRC and no genres, attempts MusicBrainz lookup
        to enrich with real genre data before storing.
        """
        # Genre enrichment via MusicBrainz (if ISRC available, no genres yet)
        if not track.get("genres") and track.get("isrc"):
            from dynamic_radio.genre_lookup import enrich_track
            enrich_track(track)

        self.conn.execute(
            """INSERT INTO tracks (tidal_id, name, artist, album, bpm, key, key_scale,
               duration, dj_ready, stem_ready, isrc, genres, updated_at)
               VALUES (:tidal_id, :name, :artist, :album, :bpm, :key, :key_scale,
               :duration, :dj_ready, :stem_ready, :isrc, :genres, :updated_at)
               ON CONFLICT(tidal_id) DO UPDATE SET
               bpm=excluded.bpm, key=excluded.key, key_scale=excluded.key_scale,
               dj_ready=excluded.dj_ready, stem_ready=excluded.stem_ready,
               isrc=COALESCE(excluded.isrc, tracks.isrc),
               genres=COALESCE(excluded.genres, tracks.genres),
               updated_at=excluded.updated_at""",
            {
                "tidal_id": track["tidal_id"],
                "name": track["name"],
                "artist": track["artist"],
                "album": track.get("album"),
                "bpm": track.get("bpm"),
                "key": track.get("key"),
                "key_scale": track.get("key_scale"),
                "duration": track.get("duration"),
                "dj_ready": int(track.get("dj_ready", False)),
                "stem_ready": int(track.get("stem_ready", False)),
                "isrc": track.get("isrc"),
                "genres": track.get("genres"),
                "updated_at": datetime.now().isoformat(),
            },
        )
        self.conn.commit()

    def get_track(self, tidal_id: int) -> dict[str, Any] | None:
        """Get a cached track by Tidal ID."""
        row = self.conn.execute(
            "SELECT * FROM tracks WHERE tidal_id = ?", (tidal_id,)
        ).fetchone()
        return dict(row) if row else None

    def log_play(self, tidal_id: int) -> int:
        """Record that a track started playing now. Returns the play_history row ID."""
        cursor = self.conn.execute(
            "INSERT INTO play_history (tidal_id, played_at) VALUES (?, ?)",
            (tidal_id, datetime.now().isoformat()),
        )
        self.conn.commit()
        return cursor.lastrowid

    def log_play_end(self, play_id: int, duration: int, skipped: bool = False) -> None:
        """Record how a play ended — duration listened and whether it was skipped."""
        skipped_at = datetime.now().isoformat() if skipped else None
        self.conn.execute(
            "UPDATE play_history SET play_duration = ?, skipped_at = ? WHERE id = ?",
            (duration, skipped_at, play_id),
        )
        self.conn.commit()

    def recently_played_ids(self, hours: int = 24) -> set[int]:
        """Get IDs of tracks played in the last N hours."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT DISTINCT tidal_id FROM play_history WHERE played_at > ?",
            (cutoff,),
        ).fetchall()
        return {row["tidal_id"] for row in rows}

    def recently_played_artists(self, hours: int = 2) -> set[str]:
        """Get artist names played in the last N hours."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            """SELECT DISTINCT t.artist FROM play_history ph
               JOIN tracks t ON ph.tidal_id = t.tidal_id
               WHERE ph.played_at > ?""",
            (cutoff,),
        ).fetchall()
        return {row["artist"] for row in rows}

    def like(self, tidal_id: int) -> None:
        """Mark a track as liked (removes from disliked if present)."""
        self.conn.execute(
            "INSERT OR IGNORE INTO liked (tidal_id) VALUES (?)", (tidal_id,)
        )
        self.conn.execute("DELETE FROM disliked WHERE tidal_id = ?", (tidal_id,))
        self.conn.commit()

    def liked_ids(self) -> set[int]:
        """Get all liked track IDs."""
        rows = self.conn.execute("SELECT tidal_id FROM liked").fetchall()
        return {row["tidal_id"] for row in rows}

    def dislike(self, tidal_id: int) -> None:
        """Mark a track as disliked (removes from liked if present)."""
        self.conn.execute(
            "INSERT OR IGNORE INTO disliked (tidal_id) VALUES (?)", (tidal_id,)
        )
        self.conn.execute("DELETE FROM liked WHERE tidal_id = ?", (tidal_id,))
        self.conn.commit()

    def disliked_ids(self) -> set[int]:
        """Get all disliked track IDs."""
        rows = self.conn.execute("SELECT tidal_id FROM disliked").fetchall()
        return {row["tidal_id"] for row in rows}

    def skip_rate(self, tidal_id: int) -> float | None:
        """Get the fraction of plays that were skipped early (<30s). None if no plays."""
        rows = self.conn.execute(
            "SELECT play_duration, skipped_at FROM play_history WHERE tidal_id = ?",
            (tidal_id,),
        ).fetchall()
        plays_with_data = [r for r in rows if r["play_duration"] is not None]
        if not plays_with_data:
            return None
        early_skips = sum(1 for r in plays_with_data if r["skipped_at"] and r["play_duration"] < 30)
        return early_skips / len(plays_with_data)

    def feedback_summary(self, hours: int = 24) -> dict[str, Any]:
        """Get a summary of recent feedback signals for agent context."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        liked = self.conn.execute("SELECT COUNT(*) as cnt FROM liked").fetchone()["cnt"]
        disliked = self.conn.execute("SELECT COUNT(*) as cnt FROM disliked").fetchone()["cnt"]
        recent_plays = self.conn.execute(
            """SELECT t.name, t.artist, t.genres, ph.play_duration, ph.skipped_at, t.duration
               FROM play_history ph JOIN tracks t ON ph.tidal_id = t.tidal_id
               WHERE ph.played_at > ? ORDER BY ph.played_at DESC LIMIT 20""",
            (cutoff,),
        ).fetchall()
        return {
            "total_liked": liked,
            "total_disliked": disliked,
            "recent_plays": [dict(r) for r in recent_plays],
        }

    def play_count(self, tidal_id: int) -> int:
        """Get total play count for a track."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM play_history WHERE tidal_id = ?",
            (tidal_id,),
        ).fetchone()
        return row["cnt"] if row else 0

    def last_played_at(self, tidal_id: int) -> str | None:
        """Get the ISO timestamp when a track was last played."""
        row = self.conn.execute(
            "SELECT played_at FROM play_history WHERE tidal_id = ? ORDER BY played_at DESC LIMIT 1",
            (tidal_id,),
        ).fetchone()
        return row["played_at"] if row else None
