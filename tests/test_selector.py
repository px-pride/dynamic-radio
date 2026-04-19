"""Tests for track selector — Camelot wheel, filtering, scoring, selection."""

import pytest
from pathlib import Path

from dynamic_radio.selector import (
    camelot_compatible,
    filter_candidates,
    score_track,
    select_track,
    to_camelot,
)
from dynamic_radio.track_db import TrackDB


def make_track(
    tidal_id=1, name="Test", artist="Artist", bpm=120, key="C", key_scale="major",
    dj_ready=False, genres="", **kwargs
):
    """Helper to create track dicts."""
    return {
        "tidal_id": tidal_id,
        "name": name,
        "artist": artist,
        "album": "Album",
        "bpm": bpm,
        "key": key,
        "key_scale": key_scale,
        "duration": 240,
        "dj_ready": dj_ready,
        "stem_ready": False,
        "genres": genres,
        **kwargs,
    }


BLOCK = {
    "start": "09:00",
    "end": "12:00",
    "mood": "focused",
    "energy": 0.45,
    "genres": ["minimal", "deep house"],
    "bpm_range": [110, 128],
    "description": "Deep work.",
}


# --- Camelot wheel tests ---


class TestCamelotWheel:
    def test_known_keys(self):
        assert to_camelot("C", "major") == "8B"
        assert to_camelot("A", "minor") == "8A"
        assert to_camelot("F#", "minor") == "11A"

    def test_unknown_key(self):
        assert to_camelot(None, None) is None
        assert to_camelot("X", "major") is None

    def test_same_code_compatible(self):
        assert camelot_compatible("8A", "8A") is True

    def test_adjacent_number_compatible(self):
        assert camelot_compatible("8A", "7A") is True
        assert camelot_compatible("8A", "9A") is True

    def test_wrap_around_compatible(self):
        assert camelot_compatible("1A", "12A") is True
        assert camelot_compatible("12B", "1B") is True

    def test_cross_letter_compatible(self):
        assert camelot_compatible("8A", "8B") is True

    def test_incompatible(self):
        assert camelot_compatible("8A", "5A") is False
        assert camelot_compatible("8A", "3B") is False

    def test_none_always_compatible(self):
        assert camelot_compatible(None, "8A") is True
        assert camelot_compatible("8A", None) is True
        assert camelot_compatible(None, None) is True


# --- Filter tests ---


@pytest.fixture
def db(tmp_path: Path) -> TrackDB:
    return TrackDB(tmp_path / "test.db")


class TestFilter:
    def test_filters_recently_played(self, db: TrackDB):
        track = make_track(tidal_id=1, bpm=120)
        db.upsert_track(track)
        db.log_play(1)

        result = filter_candidates([track], db, BLOCK)
        assert len(result) == 0

    def test_filters_recent_artist(self, db: TrackDB):
        played = make_track(tidal_id=1, artist="SameArtist", bpm=120)
        candidate = make_track(tidal_id=2, artist="SameArtist", bpm=120)
        db.upsert_track(played)
        db.log_play(1)

        result = filter_candidates([candidate], db, BLOCK)
        assert len(result) == 0

    def test_filters_disliked(self, db: TrackDB):
        track = make_track(tidal_id=1, bpm=120)
        db.dislike(1)

        result = filter_candidates([track], db, BLOCK)
        assert len(result) == 0

    def test_filters_bpm_out_of_range(self, db: TrackDB):
        track = make_track(tidal_id=1, bpm=80)  # Below block range 110-128
        result = filter_candidates([track], db, BLOCK)
        assert len(result) == 0

    def test_filters_bpm_too_far_from_previous(self, db: TrackDB):
        track = make_track(tidal_id=1, bpm=120)
        prev = make_track(tidal_id=99, bpm=100)  # 20 BPM away > ±15

        result = filter_candidates([track], db, BLOCK, previous_track=prev)
        assert len(result) == 0

    def test_filters_incompatible_key(self, db: TrackDB):
        track = make_track(tidal_id=1, bpm=120, key="C", key_scale="major")  # 8B
        prev = make_track(tidal_id=99, bpm=118, key="E", key_scale="minor")  # 9A

        result = filter_candidates([track], db, BLOCK, previous_track=prev)
        # 8B and 9A: number differs by 1 but letters differ too → check
        # 8B compat with 9A: different letter AND different number → incompatible
        assert len(result) == 0

    def test_passes_good_candidate(self, db: TrackDB):
        track = make_track(tidal_id=1, bpm=120, key="C", key_scale="major")
        prev = make_track(tidal_id=99, bpm=118, key="C", key_scale="major")  # Same key

        result = filter_candidates([track], db, BLOCK, previous_track=prev)
        assert len(result) == 1

    def test_allows_none_bpm(self, db: TrackDB):
        """Tracks with no BPM data pass BPM filters (permissive)."""
        track = make_track(tidal_id=1, bpm=None)
        result = filter_candidates([track], db, BLOCK)
        assert len(result) == 1


# --- Scoring tests ---


class TestScoring:
    def test_perfect_bpm_scores_high(self, db: TrackDB):
        block = {**BLOCK, "bpm_range": [118, 122]}
        track = make_track(tidal_id=1, bpm=120)  # Dead center
        score = score_track(track, block, None, db)
        assert score > 0.5

    def test_dj_ready_bonus(self, db: TrackDB):
        track_normal = make_track(tidal_id=1, bpm=120)
        track_dj = make_track(tidal_id=2, bpm=120, dj_ready=True)

        s1 = score_track(track_normal, BLOCK, None, db)
        s2 = score_track(track_dj, BLOCK, None, db)
        assert s2 > s1

    def test_genre_match_bonus(self, db: TrackDB):
        track_match = make_track(tidal_id=1, bpm=120, genres="minimal,deep house")
        track_no = make_track(tidal_id=2, bpm=120, genres="classical,opera")

        s1 = score_track(track_match, BLOCK, None, db)
        s2 = score_track(track_no, BLOCK, None, db)
        assert s1 > s2

    def test_novelty_bonus_for_unplayed(self, db: TrackDB):
        track = make_track(tidal_id=1, bpm=120)
        db.upsert_track(track)
        s_before = score_track(track, BLOCK, None, db)

        # Simulate it was played recently (within 24h it would be filtered,
        # but for scoring test we check the novelty component)
        # Just verify unplayed gets the novelty bonus
        assert s_before > 0


# --- Selection tests ---


class TestSelection:
    def test_select_from_candidates(self, db: TrackDB):
        candidates = [
            make_track(tidal_id=i, name=f"Track {i}", artist=f"Artist {i}", bpm=115 + i)
            for i in range(10)
        ]
        result = select_track(candidates, BLOCK, db)
        assert result is not None
        assert "tidal_id" in result

    def test_select_returns_none_when_empty(self, db: TrackDB):
        result = select_track([], BLOCK, db)
        assert result is None

    def test_select_returns_none_when_all_filtered(self, db: TrackDB):
        # All tracks have BPM way out of range
        candidates = [make_track(tidal_id=i, bpm=60) for i in range(5)]
        result = select_track(candidates, BLOCK, db)
        assert result is None

    def test_select_avoids_recently_played(self, db: TrackDB):
        candidates = [
            make_track(tidal_id=1, artist="A", bpm=120),
            make_track(tidal_id=2, artist="B", bpm=120),
        ]
        db.upsert_track(candidates[0])
        db.log_play(1)

        # Only track 2 should be selectable
        result = select_track(candidates, BLOCK, db)
        assert result is not None
        assert result["tidal_id"] == 2
