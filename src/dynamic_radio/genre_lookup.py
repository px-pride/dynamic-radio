"""MusicBrainz genre lookup — resolves ISRC to genre tags.

Uses the MusicBrainz API to look up recordings by ISRC and extract
tag information. Two-step: ISRC → recording ID → tags.
Rate-limited to 1 req/sec per MB policy.
"""

import logging
import os
import time
from typing import Any

import musicbrainzngs

logger = logging.getLogger(__name__)

# Required by MusicBrainz API policy
_MB_CONTACT = os.environ.get("MB_CONTACT", "dynamic-radio@example.com")
musicbrainzngs.set_useragent("Dynamic Radio", "0.1.0", _MB_CONTACT)

# Minimum interval between API calls (MB policy: 1 req/sec)
_MIN_INTERVAL = 1.1
_last_call: float = 0.0


def _rate_limit() -> None:
    """Sleep if needed to respect MusicBrainz rate limit."""
    global _last_call
    now = time.monotonic()
    elapsed = now - _last_call
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_call = time.monotonic()


def lookup_genres(isrc: str) -> str | None:
    """Look up genres for a track by ISRC via MusicBrainz.

    Two-step lookup: ISRC → recording ID, then recording → tags.
    Returns a comma-separated genre string, or None if not found.
    Tags are sorted by vote count (most popular first).
    """
    if not isrc:
        return None

    try:
        # Step 1: ISRC → recording ID
        _rate_limit()
        result = musicbrainzngs.get_recordings_by_isrc(isrc)
        recordings = result.get("isrc", {}).get("recording-list", [])
        if not recordings:
            return None

        # Use the first matching recording
        recording_id = recordings[0]["id"]

        # Step 2: recording ID → tags
        _rate_limit()
        detail = musicbrainzngs.get_recording_by_id(recording_id, includes=["tags"])
        tags = detail.get("recording", {}).get("tag-list", [])

    except musicbrainzngs.WebServiceError as e:
        logger.debug("MusicBrainz lookup failed for ISRC %s: %s", isrc, e)
        return None
    except Exception as e:
        logger.debug("MusicBrainz unexpected error for ISRC %s: %s", isrc, e)
        return None

    if not tags:
        return None

    # Filter to positive-count tags, sort by vote count descending
    tag_counts = [
        (t["name"].lower(), int(t.get("count", 0)))
        for t in tags
        if int(t.get("count", 0)) > 0
    ]
    tag_counts.sort(key=lambda x: x[1], reverse=True)

    # Take top 5 tags
    top = [name for name, _ in tag_counts[:5]]
    return ",".join(top) if top else None


def enrich_track(track: dict[str, Any]) -> dict[str, Any]:
    """Add genre info to a track dict if it has an ISRC and no genres yet.

    Mutates and returns the track dict.
    """
    if track.get("genres"):
        return track

    isrc = track.get("isrc")
    if not isrc:
        return track

    genres = lookup_genres(isrc)
    if genres:
        track["genres"] = genres
        logger.info(
            "Genre enriched: %s — %s → %s",
            track.get("artist"), track.get("name"), genres,
        )

    return track
