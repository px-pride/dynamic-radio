"""Mood-to-plan mapping — translates mood descriptions into plan block adjustments.

Simple keyword-based heuristic for v1. Modifies remaining plan blocks
(from current time onward) based on mood keywords.
"""

import re
from datetime import datetime
from typing import Any

# Mood keyword mappings: each keyword maps to parameter adjustments
MOOD_PRESETS: dict[str, dict[str, Any]] = {
    "energy": {"energy_delta": 0.25, "bpm_delta": 20, "genres": ["techno", "drum and bass", "breakbeat", "house"]},
    "energetic": {"energy_delta": 0.25, "bpm_delta": 20, "genres": ["techno", "drum and bass", "breakbeat", "house"]},
    "hype": {"energy_delta": 0.35, "bpm_delta": 30, "genres": ["drum and bass", "brostep", "dubstep", "techno"]},
    "intense": {"energy_delta": 0.30, "bpm_delta": 25, "genres": ["techno", "industrial", "drum and bass", "darkstep"]},
    "chill": {"energy_delta": -0.25, "bpm_delta": -20, "genres": ["ambient", "downtempo", "lo-fi", "chillout"]},
    "relax": {"energy_delta": -0.25, "bpm_delta": -20, "genres": ["ambient", "downtempo", "lo-fi", "chillout"]},
    "calm": {"energy_delta": -0.30, "bpm_delta": -25, "genres": ["ambient", "drone", "new age", "classical"]},
    "focus": {"energy_delta": 0.0, "bpm_delta": 0, "genres": ["minimal", "IDM", "deep house", "ambient techno"]},
    "work": {"energy_delta": 0.0, "bpm_delta": 0, "genres": ["minimal", "IDM", "deep house", "ambient techno"]},
    "study": {"energy_delta": -0.10, "bpm_delta": -10, "genres": ["ambient", "lo-fi", "minimal", "post-rock"]},
    "dark": {"energy_delta": 0.10, "bpm_delta": 10, "genres": ["dark ambient", "industrial", "darkstep", "witch house"]},
    "upbeat": {"energy_delta": 0.20, "bpm_delta": 15, "genres": ["house", "disco", "funk", "breakbeat"]},
    "happy": {"energy_delta": 0.15, "bpm_delta": 10, "genres": ["house", "disco", "funk", "soul"]},
    "sad": {"energy_delta": -0.15, "bpm_delta": -15, "genres": ["ambient", "post-rock", "shoegaze", "slowcore"]},
    "mellow": {"energy_delta": -0.20, "bpm_delta": -15, "genres": ["downtempo", "trip-hop", "jazz", "neo-soul"]},
    "jazz": {"energy_delta": -0.05, "bpm_delta": -5, "genres": ["jazz fusion", "jazz", "nu-jazz", "bossa nova"]},
    "dub": {"energy_delta": 0.05, "bpm_delta": 0, "genres": ["dub", "dub techno", "reggae", "dubstep"]},
    "bass": {"energy_delta": 0.20, "bpm_delta": 15, "genres": ["dubstep", "drum and bass", "bass house", "future bass"]},
    "glitch": {"energy_delta": 0.10, "bpm_delta": 5, "genres": ["glitch", "IDM", "glitch hop", "breakcore"]},
    "sleep": {"energy_delta": -0.40, "bpm_delta": -40, "genres": ["drone", "ambient", "dark ambient", "sleep"]},
}


def _match_mood(mood: str) -> dict[str, Any] | None:
    """Find the best matching mood preset from the description."""
    mood_lower = mood.lower()
    # Check for "more X" or "less X" patterns
    more_match = re.search(r"\bmore\s+(\w+)", mood_lower)
    less_match = re.search(r"\bless\s+(\w+)", mood_lower)

    if more_match:
        keyword = more_match.group(1)
        if keyword in MOOD_PRESETS:
            return MOOD_PRESETS[keyword]
    if less_match:
        keyword = less_match.group(1)
        if keyword in MOOD_PRESETS:
            # Invert the deltas
            preset = MOOD_PRESETS[keyword]
            return {
                "energy_delta": -preset["energy_delta"],
                "bpm_delta": -preset["bpm_delta"],
                "genres": preset.get("genres", []),
            }

    # Direct keyword match (first match wins)
    for keyword, preset in MOOD_PRESETS.items():
        if keyword in mood_lower:
            return preset

    # Check for "something X" pattern
    something_match = re.search(r"\bsomething\s+(\w+)", mood_lower)
    if something_match:
        keyword = something_match.group(1)
        if keyword in MOOD_PRESETS:
            return MOOD_PRESETS[keyword]

    return None


def apply_mood(plan: dict[str, Any], mood: str) -> int:
    """Modify remaining plan blocks based on mood description. Returns count of modified blocks.

    Only modifies blocks from current time onward. Past/current blocks are unchanged.
    Mutates the plan dict in place.
    """
    preset = _match_mood(mood)
    if preset is None:
        return 0

    now = datetime.now().strftime("%H:%M")
    blocks = plan.get("blocks", [])
    modified = 0

    for block in blocks:
        # Only modify future blocks
        if block.get("end", "00:00") <= now:
            continue

        # Apply energy delta (clamped to 0-1)
        energy = block.get("energy", 0.5)
        energy = max(0.0, min(1.0, energy + preset["energy_delta"]))
        block["energy"] = round(energy, 2)

        # Apply BPM delta
        bpm_range = block.get("bpm_range", [80, 130])
        bpm_range = [
            max(40, bpm_range[0] + preset["bpm_delta"]),
            max(60, bpm_range[1] + preset["bpm_delta"]),
        ]
        block["bpm_range"] = bpm_range

        # Replace genres with mood-appropriate ones
        if preset.get("genres"):
            block["genres"] = preset["genres"]

        # Update mood description
        block["mood"] = mood

        modified += 1

    return modified
