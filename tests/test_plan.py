"""Tests for DJ plan loader and utilities."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from dynamic_radio.plan import (
    DEFAULT_PLAN_BLOCKS,
    default_plan,
    get_current_block,
    interpolate_blocks,
    load_plan,
    save_plan,
)

SAMPLE_PLAN = {
    "date": "2026-04-07",
    "generated_at": "2026-04-07T05:00:00-08:00",
    "blocks": [
        {
            "start": "05:00",
            "end": "07:00",
            "mood": "contemplative",
            "energy": 0.10,
            "genres": ["drone", "ambient"],
            "bpm_range": [55, 75],
            "description": "Pre-dawn meditation.",
        },
        {
            "start": "07:00",
            "end": "09:00",
            "mood": "gentle",
            "energy": 0.25,
            "genres": ["ambient", "downtempo"],
            "bpm_range": [70, 95],
            "description": "Morning warmth.",
        },
        {
            "start": "09:00",
            "end": "12:00",
            "mood": "focused",
            "energy": 0.45,
            "genres": ["minimal", "IDM", "deep house"],
            "bpm_range": [110, 128],
            "description": "Deep work flow.",
        },
        {
            "start": "22:00",
            "end": "23:59",
            "mood": "silent",
            "energy": 0.02,
            "genres": ["silence"],
            "bpm_range": [0, 0],
            "description": "Sleep.",
        },
    ],
}


def test_save_and_load_plan(tmp_path: Path):
    """Plans round-trip through save/load."""
    with patch("dynamic_radio.plan.PLANS_DIR", tmp_path):
        target = date(2026, 4, 7)
        save_plan(SAMPLE_PLAN, target)
        loaded = load_plan(target)

    assert loaded == SAMPLE_PLAN


def test_load_plan_missing(tmp_path: Path):
    """load_plan returns None for missing dates."""
    with patch("dynamic_radio.plan.PLANS_DIR", tmp_path):
        assert load_plan(date(2099, 1, 1)) is None


def test_get_current_block_match():
    """get_current_block finds the right block for a given time."""
    with patch("dynamic_radio.plan.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "10:30"
        block = get_current_block(SAMPLE_PLAN)

    assert block is not None
    assert block["mood"] == "focused"
    assert block["start"] == "09:00"


def test_get_current_block_no_match():
    """get_current_block returns None for gaps in schedule."""
    with patch("dynamic_radio.plan.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "15:00"
        block = get_current_block(SAMPLE_PLAN)

    assert block is None


def test_interpolate_blocks():
    """interpolate_blocks blends energy and BPM between blocks."""
    block_a = SAMPLE_PLAN["blocks"][0]  # energy=0.10, bpm=[55,75]
    block_b = SAMPLE_PLAN["blocks"][1]  # energy=0.25, bpm=[70,95]

    # Midpoint
    result = interpolate_blocks(block_a, block_b, 0.5)
    assert result["energy"] == pytest.approx(0.175, abs=0.01)
    assert result["bpm_range"] == [62, 85]
    # At 0.5, genres switch to next block
    assert result["genres"] == block_b["genres"]

    # Start (fully current)
    result = interpolate_blocks(block_a, block_b, 0.0)
    assert result["energy"] == pytest.approx(0.10, abs=0.01)
    assert result["genres"] == block_a["genres"]

    # End (fully next)
    result = interpolate_blocks(block_a, block_b, 1.0)
    assert result["energy"] == pytest.approx(0.25, abs=0.01)
    assert result["genres"] == block_b["genres"]


def test_default_plan_has_blocks():
    """default_plan returns a valid plan with all-day coverage."""
    plan = default_plan(date(2026, 4, 7))
    assert plan["date"] == "2026-04-07"
    assert "generated_at" in plan
    assert plan["blocks"] == DEFAULT_PLAN_BLOCKS
    assert len(plan["blocks"]) > 0
    # Verify blocks have required fields
    for block in plan["blocks"]:
        assert "start" in block
        assert "end" in block
        assert "mood" in block
        assert "energy" in block
        assert "genres" in block
        assert "bpm_range" in block
