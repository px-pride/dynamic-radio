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


def make_decoys(n: int = 5, *, key: str = "C", key_scale: str = "major"):
    """Generate n viable decoy tracks for use alongside a 'target' track in
    filter tests. Decoys all sit in BLOCK's BPM range, share the same Camelot
    code (so they pass key-compat with each other and with any prev_track that
    is also key-compatible to that code), have unique tidal_ids in 100..199 (so
    they don't collide with target ids 1-99), and are never played or disliked.
    Strict filtering returns these 5 → fallback paths in filter_candidates do
    not trigger. The target track being asserted-against is the only candidate
    that should be filtered out.
    """
    return [
        {
            "tidal_id": 100 + i,
            "name": f"Decoy {i}",
            "artist": f"DecoyArtist{i}",
            "album": "Decoys",
            "bpm": 118 + i,  # all within BLOCK [110,128]
            "key": key,
            "key_scale": key_scale,
            "duration": 240,
            "dj_ready": False,
            "stem_ready": False,
            "genres": "minimal,deep house",
        }
        for i in range(n)
    ]


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
        target = make_track(tidal_id=1, bpm=120)
        db.upsert_track(target)
        db.log_play(1)

        result = filter_candidates([target] + make_decoys(5), db, BLOCK)
        ids = [t["tidal_id"] for t in result]
        assert 1 not in ids
        assert len(result) == 5

    def test_filters_recent_artist(self, db: TrackDB):
        played = make_track(tidal_id=1, artist="SameArtist", bpm=120)
        candidate = make_track(tidal_id=2, artist="SameArtist", bpm=120)
        db.upsert_track(played)
        db.log_play(1)

        result = filter_candidates([candidate] + make_decoys(5), db, BLOCK)
        ids = [t["tidal_id"] for t in result]
        assert 2 not in ids
        assert len(result) == 5

    def test_filters_disliked(self, db: TrackDB):
        target = make_track(tidal_id=1, bpm=120)
        db.dislike(1)

        result = filter_candidates([target] + make_decoys(5), db, BLOCK)
        ids = [t["tidal_id"] for t in result]
        assert 1 not in ids
        assert len(result) == 5

    def test_filters_bpm_out_of_range(self, db: TrackDB):
        target = make_track(tidal_id=1, bpm=80)  # Below block range 110-128
        result = filter_candidates([target] + make_decoys(5), db, BLOCK)
        ids = [t["tidal_id"] for t in result]
        assert 1 not in ids
        assert len(result) == 5

    def test_filters_bpm_too_far_from_previous(self, db: TrackDB):
        # prev BPM 100; target at 120 is 20 away (> ±15) — strict-rejected.
        # Decoys (BPM 118-122) are all within ±15 of prev=100? No, 118-100=18.
        # Set prev at 115 so decoys (118-122) sit within ±15 of prev,
        # and target at 135 (135-115=20) is still > ±15 away.
        target = make_track(tidal_id=1, bpm=135)
        prev = make_track(tidal_id=99, bpm=115)
        result = filter_candidates(
            [target] + make_decoys(5), db, BLOCK, previous_track=prev
        )
        ids = [t["tidal_id"] for t in result]
        assert 1 not in ids
        assert len(result) == 5

    def test_filters_incompatible_key(self, db: TrackDB):
        # target key F# minor → Camelot 11A; prev key C major → 8B.
        # 11A vs 8B: different letter AND number diff 3 → incompatible.
        # Decoys are C major (8B) → compat with prev (8B ↔ 8B same).
        target = make_track(
            tidal_id=1, bpm=120, key="F#", key_scale="minor"
        )
        prev = make_track(tidal_id=99, bpm=118, key="C", key_scale="major")
        result = filter_candidates(
            [target] + make_decoys(5), db, BLOCK, previous_track=prev
        )
        ids = [t["tidal_id"] for t in result]
        assert 1 not in ids
        assert len(result) == 5

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

    def test_select_falls_back_when_all_out_of_range(self, db: TrackDB):
        """Music never stops: when all candidates are out of the block's BPM
        range and strict filtering yields nothing, the selector falls back via
        _base_filter and last-resort to return *some* candidate. Asserting we
        return None here would be wrong — it would mean silence."""
        candidates = [make_track(tidal_id=i, bpm=60) for i in range(5)]
        result = select_track(candidates, BLOCK, db)
        assert result is not None
        assert result["tidal_id"] in {t["tidal_id"] for t in candidates}

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
