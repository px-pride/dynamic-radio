"""Track selector — picks the next track based on plan block + rules.

Uses BPM matching, Camelot wheel key compatibility, recency filtering,
and weighted scoring. Zero LLM cost per track selection.
"""

import logging
import random
from datetime import datetime, timedelta
from typing import Any

from dynamic_radio.track_db import TrackDB

logger = logging.getLogger(__name__)

# Camelot wheel: maps musical keys to Camelot codes.
# Compatible keys are ±1 on the number (same letter) or same number (other letter).
# e.g., 8A is compatible with 7A, 9A, and 8B.
CAMELOT_WHEEL: dict[str, str] = {
    # Minor keys (A column)
    "A minor": "8A", "Ab minor": "1A", "G# minor": "1A",
    "Bb minor": "3A", "A# minor": "3A",
    "B minor": "10A",
    "C minor": "5A",
    "C# minor": "12A", "Db minor": "12A",
    "D minor": "7A",
    "D# minor": "2A", "Eb minor": "2A",
    "E minor": "9A",
    "F minor": "4A",
    "F# minor": "11A", "Gb minor": "11A",
    "G minor": "6A",
    # Major keys (B column)
    "A major": "11B", "Ab major": "4B", "G# major": "4B",
    "Bb major": "6B", "A# major": "6B",
    "B major": "1B",
    "C major": "8B",
    "C# major": "3B", "Db major": "3B",
    "D major": "10B",
    "D# major": "5B", "Eb major": "5B",
    "E major": "12B",
    "F major": "7B",
    "F# major": "2B", "Gb major": "2B",
    "G major": "9B",
}


def to_camelot(key: str | None, key_scale: str | None) -> str | None:
    """Convert a Tidal key + scale to Camelot code.

    Args:
        key: Note name (e.g. "C", "F#")
        key_scale: "major" or "minor"

    Returns:
        Camelot code (e.g. "8A") or None if unknown.
    """
    if not key or not key_scale:
        return None
    lookup = f"{key} {key_scale}".strip()
    return CAMELOT_WHEEL.get(lookup)


def camelot_compatible(code_a: str | None, code_b: str | None) -> bool:
    """Check if two Camelot codes are compatible (±1 number or same number cross-letter).

    Compatible transitions:
    - Same code (e.g., 8A → 8A)
    - ±1 on number, same letter (e.g., 8A → 7A, 8A → 9A)
    - Same number, different letter (e.g., 8A → 8B)
    """
    if code_a is None or code_b is None:
        return True  # Unknown keys are always "compatible" (permissive)

    num_a, letter_a = int(code_a[:-1]), code_a[-1]
    num_b, letter_b = int(code_b[:-1]), code_b[-1]

    # Same code
    if code_a == code_b:
        return True
    # Same letter, adjacent number (wrapping 1-12)
    if letter_a == letter_b:
        diff = abs(num_a - num_b)
        if diff == 1 or diff == 11:  # 11 = wrap (1↔12)
            return True
    # Same number, cross letter
    if num_a == num_b and letter_a != letter_b:
        return True

    return False


def _base_filter(
    candidates: list[dict[str, Any]],
    db: TrackDB,
) -> list[dict[str, Any]]:
    """Relaxed filter — only removes truly disqualifying tracks.

    Removes: recently played (6h), disliked. Keeps everything else
    so scoring can rank by BPM/key/artist fit.
    """
    recent_ids = db.recently_played_ids(hours=6)
    disliked = db.disliked_ids()

    return [
        t for t in candidates
        if t["tidal_id"] not in recent_ids and t["tidal_id"] not in disliked
    ]


# Minimum viable candidates before falling back to relaxed filtering
_MIN_VIABLE = 5


def filter_candidates(
    candidates: list[dict[str, Any]],
    db: TrackDB,
    block: dict[str, Any],
    previous_track: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Filter candidates based on plan block constraints and history.

    Strict pass removes:
    - Tracks played in last 24 hours
    - Same artist played in last 2 hours
    - Disliked tracks
    - BPM outside plan block's range
    - BPM more than ±15 from previous track
    - Incompatible key (Camelot wheel)

    If strict filtering leaves < 5 candidates, falls back to relaxed
    filtering (only recency + disliked) and lets scoring handle the rest.
    """
    recent_ids = db.recently_played_ids(hours=24)
    recent_artists = db.recently_played_artists(hours=2)
    disliked = db.disliked_ids()

    bpm_lo, bpm_hi = block.get("bpm_range", [0, 999])

    prev_bpm = previous_track.get("bpm") if previous_track else None
    prev_camelot = None
    if previous_track:
        prev_camelot = to_camelot(
            previous_track.get("key"), previous_track.get("key_scale")
        )

    filtered = []
    for track in candidates:
        tid = track["tidal_id"]

        # Skip recently played
        if tid in recent_ids:
            continue
        # Skip recent artists
        if track.get("artist") in recent_artists:
            continue
        # Skip disliked
        if tid in disliked:
            continue

        bpm = track.get("bpm")
        if bpm is not None:
            # Must be within block's BPM range
            if not (bpm_lo <= bpm <= bpm_hi):
                continue
            # Must be within ±15 of previous track
            if prev_bpm is not None and abs(bpm - prev_bpm) > 15:
                continue

        # Key compatibility
        track_camelot = to_camelot(track.get("key"), track.get("key_scale"))
        if not camelot_compatible(prev_camelot, track_camelot):
            continue

        filtered.append(track)

    if len(filtered) >= _MIN_VIABLE:
        return filtered

    # Strict filtering was too aggressive — fall back to relaxed
    relaxed = _base_filter(candidates, db)
    logger.info(
        "Strict filter too aggressive (%d/%d passed), relaxing to %d candidates",
        len(filtered), len(candidates), len(relaxed),
    )
    if relaxed:
        return relaxed

    # Last resort: all candidates exhausted (played recently + narrow genre pool).
    # Pick the least-recently-played tracks so music never stops.
    disliked = db.disliked_ids()
    non_disliked = [t for t in candidates if t["tidal_id"] not in disliked]
    if not non_disliked:
        non_disliked = candidates  # Even disliked is better than silence

    def _last_played_ts(track: dict[str, Any]) -> str:
        ts = db.last_played_at(track["tidal_id"])
        return ts if ts else ""  # Never played = sort first (empty string < any ISO date)

    non_disliked.sort(key=_last_played_ts)
    logger.info(
        "Last-resort fallback: returning %d least-recently-played from %d candidates",
        min(len(non_disliked), _MIN_VIABLE), len(candidates),
    )
    return non_disliked[:_MIN_VIABLE]


def score_track(
    track: dict[str, Any],
    block: dict[str, Any],
    previous_track: dict[str, Any] | None,
    db: TrackDB,
) -> float:
    """Score a candidate track (higher = better fit).

    Weights:
    - BPM closeness to block target: 30%
    - BPM compatibility with previous: 20%
    - Key compatibility with previous: 20%
    - Genre match: 15%
    - User affinity (play count, dj_ready): 10%
    - Novelty bonus: 5%
    """
    score = 0.0

    bpm_lo, bpm_hi = block.get("bpm_range", [0, 999])
    target_bpm = (bpm_lo + bpm_hi) / 2
    bpm_range_size = max(bpm_hi - bpm_lo, 1)

    bpm = track.get("bpm")

    # BPM closeness to block target (30%)
    if bpm is not None and target_bpm > 0:
        bpm_diff = abs(bpm - target_bpm)
        bpm_score = max(0, 1 - bpm_diff / bpm_range_size)
        score += 0.30 * bpm_score

    # BPM compatibility with previous track (20%)
    if bpm is not None and previous_track and previous_track.get("bpm"):
        prev_bpm = previous_track["bpm"]
        diff = abs(bpm - prev_bpm)
        compat = max(0, 1 - diff / 15)  # 0-15 BPM range
        score += 0.20 * compat

    # Key compatibility with previous (20%)
    track_camelot = to_camelot(track.get("key"), track.get("key_scale"))
    prev_camelot = None
    if previous_track:
        prev_camelot = to_camelot(
            previous_track.get("key"), previous_track.get("key_scale")
        )
    if camelot_compatible(prev_camelot, track_camelot):
        score += 0.20  # Full score for compatible
    # Incompatible tracks should already be filtered out, but just in case

    # Genre match (15%)
    block_genres = set(g.lower() for g in block.get("genres", []))
    track_genres_str = track.get("genres", "") or ""
    track_genres = set(g.strip().lower() for g in track_genres_str.split(",") if g.strip())
    if block_genres and track_genres:
        overlap = len(block_genres & track_genres)
        genre_score = min(1.0, overlap / max(len(block_genres), 1))
        score += 0.15 * genre_score

    # User affinity (10%)
    affinity = 0.0
    if track.get("dj_ready"):
        affinity += 0.5
    plays = db.play_count(track["tidal_id"])
    if plays > 0:
        affinity += min(0.5, plays * 0.1)  # Cap at 0.5
    score += 0.10 * min(1.0, affinity)

    # Novelty bonus (5%) — tracks not heard recently score higher
    last = db.last_played_at(track["tidal_id"])
    if last is None:
        score += 0.05  # Never played = max novelty
    else:
        hours_ago = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 3600
        novelty = min(1.0, hours_ago / 168)  # 7 days = full novelty
        score += 0.05 * novelty

    return score


def select_track(
    candidates: list[dict[str, Any]],
    block: dict[str, Any],
    db: TrackDB,
    previous_track: dict[str, Any] | None = None,
    top_n: int = 5,
) -> dict[str, Any] | None:
    """Select the next track from candidates.

    Filters, scores, and picks via weighted random from the top N.

    Args:
        candidates: Track dicts with tidal_id, bpm, key, etc.
        block: Current plan block with genres, bpm_range, energy, etc.
        db: TrackDB instance for history lookups.
        previous_track: The track that just played (for transition matching).
        top_n: Number of top candidates to randomly pick from.

    Returns:
        The selected track dict, or None if no viable candidates.
    """
    viable = filter_candidates(candidates, db, block, previous_track)

    if not viable:
        logger.warning("No viable candidates after filtering (%d input)", len(candidates))
        return None

    scored = [(track, score_track(track, block, previous_track, db)) for track in viable]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Weighted random from top N
    top = scored[:top_n]
    weights = [s for _, s in top]
    total = sum(weights)
    if total == 0:
        return top[0][0]  # All zero scores, just pick first

    selected_idx = random.choices(range(len(top)), weights=weights, k=1)[0]
    selected, selected_score = top[selected_idx]

    logger.info(
        "Selected: %s - %s (BPM=%s, key=%s, score=%.2f) from %d viable / %d candidates",
        selected.get("artist"),
        selected.get("name"),
        selected.get("bpm"),
        selected.get("key"),
        selected_score,
        len(viable),
        len(candidates),
    )
    return selected
