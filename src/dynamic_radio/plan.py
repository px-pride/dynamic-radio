"""DJ plan loader and utilities.

Plans are JSON files at ~/.local/share/dynamic-radio/plans/YYYY-MM-DD.json,
generated externally by an Axi scheduled task. This module only reads them.
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLANS_DIR = Path.home() / ".local" / "share" / "dynamic-radio" / "plans"

# Default plan used when no Axi-generated plan exists for today.
# Covers the full day with safe defaults matching the user's profile.
DEFAULT_PLAN_BLOCKS: list[dict[str, Any]] = [
    {"start": "00:00", "end": "05:00", "mood": "sleep", "energy": 0.05, "genres": ["drone", "ambient"], "bpm_range": [55, 75], "description": "Sleep — near silence."},
    {"start": "05:00", "end": "07:00", "mood": "contemplative", "energy": 0.1, "genres": ["ambient", "drone"], "bpm_range": [60, 80], "description": "Pre-dawn meditation."},
    {"start": "07:00", "end": "09:00", "mood": "gentle", "energy": 0.25, "genres": ["ambient", "downtempo", "lo-fi"], "bpm_range": [75, 100], "description": "Gentle morning."},
    {"start": "09:00", "end": "12:00", "mood": "focused", "energy": 0.45, "genres": ["minimal", "IDM", "deep house"], "bpm_range": [110, 128], "description": "Deep work."},
    {"start": "12:00", "end": "13:00", "mood": "midday", "energy": 0.5, "genres": ["jazz fusion", "dub", "downtempo"], "bpm_range": [95, 120], "description": "Lunch break."},
    {"start": "13:00", "end": "16:00", "mood": "afternoon", "energy": 0.4, "genres": ["downtempo", "IDM", "minimal"], "bpm_range": [100, 125], "description": "Afternoon flow."},
    {"start": "16:00", "end": "18:00", "mood": "creative", "energy": 0.5, "genres": ["deep house", "minimal techno", "dub"], "bpm_range": [115, 130], "description": "Creative session."},
    {"start": "18:00", "end": "20:00", "mood": "evening", "energy": 0.3, "genres": ["downtempo", "lo-fi", "jazz fusion"], "bpm_range": [85, 110], "description": "Evening wind-down."},
    {"start": "20:00", "end": "22:00", "mood": "night", "energy": 0.15, "genres": ["ambient", "drone", "downtempo"], "bpm_range": [65, 90], "description": "Prepare for sleep."},
    {"start": "22:00", "end": "23:59", "mood": "sleep", "energy": 0.05, "genres": ["drone", "ambient"], "bpm_range": [55, 75], "description": "Sleep — near silence."},
]


def default_plan(target_date: date | None = None) -> dict[str, Any]:
    """Return a fallback plan when no Axi-generated plan exists."""
    if target_date is None:
        target_date = date.today()
    return {
        "date": target_date.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "blocks": DEFAULT_PLAN_BLOCKS,
    }


def save_plan(plan: dict[str, Any], target_date: date) -> Path:
    """Save a plan to disk."""
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    path = PLANS_DIR / f"{target_date.isoformat()}.json"
    path.write_text(json.dumps(plan, indent=2))
    logger.debug("Plan saved to %s", path)
    return path


def load_plan(target_date: date | None = None) -> dict[str, Any] | None:
    """Load a plan from disk. Returns None if not found."""
    if target_date is None:
        target_date = date.today()
    path = PLANS_DIR / f"{target_date.isoformat()}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def get_current_block(plan: dict[str, Any]) -> dict[str, Any] | None:
    """Get the active block for the current time.

    Returns the block whose start <= now < end, or None if no block matches
    (e.g. gaps in the schedule).
    """
    now = datetime.now().strftime("%H:%M")
    for block in plan.get("blocks", []):
        if block["start"] <= now < block["end"]:
            return block
    return None


def interpolate_blocks(
    current_block: dict[str, Any],
    next_block: dict[str, Any],
    progress: float,
) -> dict[str, Any]:
    """Interpolate between two blocks for smooth transitions.

    Args:
        current_block: The block being transitioned from.
        next_block: The block being transitioned to.
        progress: 0.0 = fully current, 1.0 = fully next.

    Returns:
        A blended parameter dict with interpolated energy and bpm_range.
    """
    energy = current_block["energy"] * (1 - progress) + next_block["energy"] * progress

    cur_bpm = current_block["bpm_range"]
    nxt_bpm = next_block["bpm_range"]
    bpm_low = int(cur_bpm[0] * (1 - progress) + nxt_bpm[0] * progress)
    bpm_high = int(cur_bpm[1] * (1 - progress) + nxt_bpm[1] * progress)

    # Blend genres: use current block's genres early, next block's genres late
    if progress < 0.5:
        genres = current_block["genres"]
    else:
        genres = next_block["genres"]

    return {
        "energy": round(energy, 2),
        "bpm_range": [bpm_low, bpm_high],
        "genres": genres,
        "mood": current_block["mood"] if progress < 0.5 else next_block["mood"],
    }
