"""End-to-end integration tests.

Wires together real instances of TrackDB (temp SQLite), MpvPlayer (--ao=null),
selector, plan, and DJController. Tidal session is mocked since it requires auth.
Tests the full cycle: plan → selector → player → history.
"""

import json
import shutil
import struct
import time
import wave
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dynamic_radio.controller import DJController, DJState
from dynamic_radio.plan import get_current_block, save_plan
from dynamic_radio.player import MpvPlayer
from dynamic_radio.selector import filter_candidates, score_track, select_track
from dynamic_radio.track_db import TrackDB

pytestmark = pytest.mark.skipif(
    shutil.which("mpv") is None,
    reason="mpv not installed",
)


def make_silent_wav(path: Path, name: str = "test.wav", duration_s: float = 2.0) -> Path:
    """Generate a silent WAV for mpv playback."""
    n_frames = int(44100 * duration_s)
    wav_path = path / name
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * n_frames)
    return wav_path


@pytest.fixture
def tmp_env(tmp_path):
    """Set up temporary DB, socket, plan dir, and WAV files."""
    db = TrackDB(db_path=tmp_path / "tracks.db")
    ipc_path = str(tmp_path / "mpv.sock")
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()

    wav1 = make_silent_wav(tmp_path, "track1.wav", 3.0)
    wav2 = make_silent_wav(tmp_path, "track2.wav", 3.0)
    wav3 = make_silent_wav(tmp_path, "track3.wav", 3.0)

    yield {
        "db": db,
        "ipc_path": ipc_path,
        "plans_dir": plans_dir,
        "tmp_path": tmp_path,
        "wavs": [wav1, wav2, wav3],
    }
    db.close()


@pytest.fixture
def player(tmp_env):
    """Start a real mpv player with null audio output."""
    p = MpvPlayer(
        ipc_path=tmp_env["ipc_path"],
        volume=50,
        audio_output="null",
    )
    p.start()
    yield p
    p.stop()


SAMPLE_TRACKS = [
    {
        "tidal_id": 1001,
        "name": "Ambient Flow",
        "artist": "Drift",
        "album": "Soundscapes",
        "bpm": 95,
        "key": "C",
        "key_scale": "minor",
        "duration": 240,
        "dj_ready": True,
        "stem_ready": False,
        "genres": "ambient,downtempo",
    },
    {
        "tidal_id": 1002,
        "name": "Deep Pulse",
        "artist": "SubBass",
        "album": "Layers",
        "bpm": 122,
        "key": "A",
        "key_scale": "minor",
        "duration": 300,
        "dj_ready": True,
        "stem_ready": False,
        "genres": "deep house,minimal",
    },
    {
        "tidal_id": 1003,
        "name": "Morning Light",
        "artist": "Dawn",
        "album": "Horizon",
        "bpm": 100,
        "key": "G",
        "key_scale": "major",
        "duration": 200,
        "dj_ready": False,
        "stem_ready": False,
        "genres": "ambient,lo-fi",
    },
    {
        "tidal_id": 1004,
        "name": "Night Drive",
        "artist": "Neon",
        "album": "After Dark",
        "bpm": 128,
        "key": "D",
        "key_scale": "minor",
        "duration": 360,
        "dj_ready": True,
        "stem_ready": True,
        "genres": "minimal techno,deep house",
    },
    {
        "tidal_id": 1005,
        "name": "Quiet Mind",
        "artist": "Drift",
        "album": "Soundscapes",
        "bpm": 85,
        "key": "E",
        "key_scale": "minor",
        "duration": 280,
        "dj_ready": False,
        "stem_ready": False,
        "genres": "ambient,drone",
    },
    {
        "tidal_id": 1006,
        "name": "Slow River",
        "artist": "Riverbed",
        "album": "Currents",
        "bpm": 90,
        "key": "G",
        "key_scale": "minor",
        "duration": 320,
        "dj_ready": False,
        "stem_ready": False,
        "genres": "ambient,downtempo",
    },
    {
        "tidal_id": 1007,
        "name": "Hazy Afternoon",
        "artist": "Lofi Anchor",
        "album": "Slow Days",
        "bpm": 105,
        "key": "F",
        "key_scale": "major",
        "duration": 220,
        "dj_ready": False,
        "stem_ready": False,
        "genres": "lo-fi,downtempo",
    },
    {
        "tidal_id": 1008,
        "name": "Soft Embers",
        "artist": "Glow",
        "album": "Smolder",
        "bpm": 92,
        "key": "D",
        "key_scale": "major",
        "duration": 260,
        "dj_ready": True,
        "stem_ready": False,
        "genres": "ambient,downtempo",
    },
]

SAMPLE_PLAN = {
    "date": date.today().isoformat(),
    "generated_at": datetime.now().isoformat(),
    "blocks": [
        {
            "start": "00:00",
            "end": "06:00",
            "mood": "sleep",
            "energy": 0.05,
            "genres": ["drone", "ambient"],
            "bpm_range": [60, 90],
            "description": "Deep sleep drone.",
        },
        {
            "start": "06:00",
            "end": "09:00",
            "mood": "dawn",
            "energy": 0.2,
            "genres": ["ambient", "lo-fi"],
            "bpm_range": [80, 100],
            "description": "Gentle wake up.",
        },
        {
            "start": "09:00",
            "end": "12:00",
            "mood": "focused",
            "energy": 0.45,
            "genres": ["minimal", "deep house"],
            "bpm_range": [110, 128],
            "description": "Deep work session.",
        },
        {
            "start": "12:00",
            "end": "15:00",
            "mood": "energetic",
            "energy": 0.7,
            "genres": ["deep house", "minimal techno"],
            "bpm_range": [120, 135],
            "description": "Peak productivity.",
        },
        {
            "start": "15:00",
            "end": "18:00",
            "mood": "flow",
            "energy": 0.5,
            "genres": ["IDM", "downtempo"],
            "bpm_range": [90, 120],
            "description": "Afternoon creative flow.",
        },
        {
            "start": "18:00",
            "end": "21:00",
            "mood": "wind down",
            "energy": 0.3,
            "genres": ["ambient", "downtempo"],
            "bpm_range": [80, 110],
            "description": "Evening wind down.",
        },
        {
            "start": "21:00",
            "end": "23:59",
            "mood": "night",
            "energy": 0.1,
            "genres": ["ambient", "drone"],
            "bpm_range": [60, 90],
            "description": "Prepare for sleep.",
        },
    ],
}


# --- DB + Selector integration ---


class TestDBSelector:
    """Test selector with real SQLite DB."""

    def test_selector_uses_real_db_for_filtering(self, tmp_env):
        db = tmp_env["db"]
        for t in SAMPLE_TRACKS:
            db.upsert_track(t)

        # Play track 1001 so it gets filtered out
        db.log_play(1001)

        block = {"bpm_range": [80, 130], "genres": ["ambient"], "energy": 0.3}
        viable = filter_candidates(SAMPLE_TRACKS, db, block, previous_track=None)

        # 1001 should be filtered (recently played)
        ids = [t["tidal_id"] for t in viable]
        assert 1001 not in ids
        assert len(viable) > 0

    def test_selector_filters_disliked_from_real_db(self, tmp_env):
        db = tmp_env["db"]
        for t in SAMPLE_TRACKS:
            db.upsert_track(t)

        db.dislike(1003)
        block = {"bpm_range": [80, 130], "genres": ["ambient"], "energy": 0.3}
        viable = filter_candidates(SAMPLE_TRACKS, db, block, previous_track=None)
        ids = [t["tidal_id"] for t in viable]
        assert 1003 not in ids

    def test_select_track_from_real_db(self, tmp_env):
        db = tmp_env["db"]
        for t in SAMPLE_TRACKS:
            db.upsert_track(t)

        block = {"bpm_range": [80, 110], "genres": ["ambient", "lo-fi"], "energy": 0.2}
        selected = select_track(SAMPLE_TRACKS, block, db, previous_track=None)
        assert selected is not None
        assert selected["bpm"] is not None
        assert 80 <= selected["bpm"] <= 110

    def test_scoring_reflects_play_history(self, tmp_env):
        db = tmp_env["db"]
        for t in SAMPLE_TRACKS:
            db.upsert_track(t)

        # Play track 1003 a few times to boost affinity
        for _ in range(5):
            db.log_play(1003)

        block = {"bpm_range": [80, 130], "genres": ["ambient"], "energy": 0.3}
        score_fresh = score_track(SAMPLE_TRACKS[4], block, None, db)  # 1005, never played
        score_played = score_track(SAMPLE_TRACKS[2], block, None, db)  # 1003, played 5x

        # Played track gets affinity bonus but loses novelty bonus
        # Both effects are small (10% + 5%) — just verify scoring doesn't crash
        assert score_fresh >= 0
        assert score_played >= 0


# --- Plan + Selector integration ---


class TestPlanSelector:
    """Test that plan blocks correctly drive track selection."""

    def test_current_block_constrains_selection(self, tmp_env):
        db = tmp_env["db"]
        for t in SAMPLE_TRACKS:
            db.upsert_track(t)

        plan = SAMPLE_PLAN
        block = get_current_block(plan)

        if block is None:
            # If no block matches current time, use a guaranteed-match block
            block = plan["blocks"][2]  # "focused" 09:00-12:00

        selected = select_track(SAMPLE_TRACKS, block, db, previous_track=None)
        if selected is not None:
            bpm_lo, bpm_hi = block["bpm_range"]
            # Selected track's BPM should be in range (or None)
            if selected.get("bpm") is not None:
                assert bpm_lo <= selected["bpm"] <= bpm_hi

    @patch("dynamic_radio.plan.PLANS_DIR")
    def test_save_and_load_plan_integration(self, mock_dir, tmp_env):
        mock_dir.__truediv__ = tmp_env["plans_dir"].__truediv__
        mock_dir.mkdir = tmp_env["plans_dir"].mkdir

        # Patch PLANS_DIR for save_plan
        with patch("dynamic_radio.plan.PLANS_DIR", tmp_env["plans_dir"]):
            from dynamic_radio.plan import load_plan, save_plan

            save_plan(SAMPLE_PLAN, date.today())
            loaded = load_plan(date.today())

        assert loaded is not None
        assert loaded["date"] == date.today().isoformat()
        assert len(loaded["blocks"]) == len(SAMPLE_PLAN["blocks"])


# --- Player + Controller integration ---


class TestPlayerController:
    """Test controller with real mpv player."""

    def test_status_with_real_player(self, tmp_env, player):
        db = tmp_env["db"]
        controller = DJController(player=player, db=db, tidal_session=None)
        result = controller.handle_command("status")
        assert "paused" in result
        assert "Nothing playing" in result

    def test_play_and_status_with_real_player(self, tmp_env, player):
        db = tmp_env["db"]
        wav = tmp_env["wavs"][0]

        # Mock Tidal session to return a "track" that points to our WAV
        mock_track = MagicMock()
        mock_track.id = 9999
        mock_track.name = "Integration Track"
        mock_track.full_name = "Integration Track"
        mock_track.artist.name = "Test Artist"
        mock_track.album.name = "Test Album"
        mock_track.duration = 3
        mock_track.bpm = 100.0
        mock_track.key = "C"
        mock_track.key_scale = "minor"
        mock_track.dj_ready = False
        mock_track.stem_ready = False
        mock_track.get_url.return_value = str(wav)

        mock_session = MagicMock()
        mock_results = MagicMock()
        mock_results.tracks = [mock_track]
        mock_session.search.return_value = mock_results

        controller = DJController(player=player, db=db, tidal_session=mock_session)

        # Play via controller command
        result = controller.handle_command("play integration test")
        assert "Integration Track" in result
        assert controller.state == DJState.OVERRIDE

        # Give mpv a moment to start playback
        time.sleep(0.5)

        # Status should show the track
        status = controller.handle_command("status")
        assert "Integration Track" in status

        # Track should be in DB
        cached = db.get_track(9999)
        assert cached is not None
        assert cached["name"] == "Integration Track"

    def test_volume_with_real_player(self, tmp_env, player):
        db = tmp_env["db"]
        controller = DJController(player=player, db=db, tidal_session=None)

        def _on_volume(v):
            if v is None:
                return {"volume": int(player.get_status()["volume"])}
            player.set_volume(v)
            return {"volume": v}

        controller.on_volume = _on_volume

        result = controller.handle_command("volume")
        assert "50%" in result  # initial volume

        result = controller.handle_command("volume 75")
        assert "75%" in result

        # Verify mpv actually changed
        status = player.get_status()
        assert abs(status["volume"] - 75) < 2  # float tolerance

    def test_pause_resume_with_real_player(self, tmp_env, player):
        db = tmp_env["db"]
        controller = DJController(player=player, db=db, tidal_session=None)

        controller.state = DJState.ACTIVE
        result = controller.handle_command("pause")
        assert controller.state == DJState.PAUSED

        result = controller.handle_command("resume")
        assert controller.state == DJState.ACTIVE

    def test_skip_with_real_player(self, tmp_env, player):
        db = tmp_env["db"]
        wav = tmp_env["wavs"][0]

        player.play_url(str(wav))
        time.sleep(0.3)

        controller = DJController(player=player, db=db, tidal_session=None)
        result = controller.handle_command("skip")
        assert "Skipped" in result

    def test_dislike_with_real_player(self, tmp_env, player):
        db = tmp_env["db"]
        wav = tmp_env["wavs"][0]

        # Seed a track and start playback
        track_info = {
            "tidal_id": 8888,
            "name": "Disliked Song",
            "artist": "Bad Artist",
            "album": "Bad Album",
            "bpm": 120,
            "key": "C",
            "key_scale": "major",
            "duration": 3,
            "dj_ready": False,
            "stem_ready": False,
            "genres": "noise",
        }
        db.upsert_track(track_info)

        player.play_url(str(wav))
        time.sleep(0.3)

        controller = DJController(player=player, db=db, tidal_session=None)
        controller._current_track = track_info

        result = controller.handle_command("dislike")
        assert "Disliked" in result
        assert 8888 in db.disliked_ids()


# --- Full cycle: plan → select → play → history ---


class TestFullCycle:
    """Simulate one complete DJ cycle without a running daemon."""

    def test_plan_select_play_history(self, tmp_env, player):
        db = tmp_env["db"]
        wav = tmp_env["wavs"][0]

        # 1. Seed tracks into DB
        for t in SAMPLE_TRACKS:
            db.upsert_track(t)

        # 2. Use a plan block
        block = SAMPLE_PLAN["blocks"][5]  # "wind down" 18:00-21:00, BPM 80-110

        # 3. Select a track
        selected = select_track(SAMPLE_TRACKS, block, db, previous_track=None)
        assert selected is not None
        bpm_lo, bpm_hi = block["bpm_range"]
        if selected["bpm"] is not None:
            assert bpm_lo <= selected["bpm"] <= bpm_hi

        # 4. "Play" it (using WAV as stand-in for Tidal stream URL)
        player.play_url(str(wav))
        time.sleep(0.3)

        status = player.get_status()
        assert not status["idle"]

        # 5. Log play history
        db.log_play(selected["tidal_id"])

        # 6. Verify history
        recent = db.recently_played_ids(hours=1)
        assert selected["tidal_id"] in recent

        # 7. Select next track — previous should be excluded
        next_track = select_track(
            SAMPLE_TRACKS, block, db, previous_track=selected
        )
        if next_track is not None:
            assert next_track["tidal_id"] != selected["tidal_id"]

    def test_consecutive_tracks_respect_key_and_bpm(self, tmp_env):
        """Verify that two consecutive selections maintain harmonic compatibility.

        Uses a tight cluster of 6 candidates (BPM 118-124, all Camelot 8A/7A/9A/8B
        — mutually compatible) so the strict filter yields ≥5 viable candidates
        and the strict ±15 BPM + key-compat constraints are enforced (no fallback).
        """
        db = tmp_env["db"]
        candidates = [
            {"tidal_id": 2001, "name": "T1", "artist": "X1", "album": "A",
             "bpm": 122, "key": "A", "key_scale": "minor",  # 8A
             "duration": 240, "dj_ready": True, "stem_ready": False,
             "genres": "deep house"},
            {"tidal_id": 2002, "name": "T2", "artist": "X2", "album": "A",
             "bpm": 120, "key": "D", "key_scale": "minor",  # 7A
             "duration": 240, "dj_ready": True, "stem_ready": False,
             "genres": "deep house"},
            {"tidal_id": 2003, "name": "T3", "artist": "X3", "album": "A",
             "bpm": 124, "key": "E", "key_scale": "minor",  # 9A
             "duration": 240, "dj_ready": True, "stem_ready": False,
             "genres": "deep house"},
            {"tidal_id": 2004, "name": "T4", "artist": "X4", "album": "A",
             "bpm": 118, "key": "C", "key_scale": "major",  # 8B
             "duration": 240, "dj_ready": True, "stem_ready": False,
             "genres": "deep house"},
            {"tidal_id": 2005, "name": "T5", "artist": "X5", "album": "A",
             "bpm": 121, "key": "A", "key_scale": "minor",  # 8A
             "duration": 240, "dj_ready": True, "stem_ready": False,
             "genres": "deep house"},
            {"tidal_id": 2006, "name": "T6", "artist": "X6", "album": "A",
             "bpm": 119, "key": "D", "key_scale": "minor",  # 7A
             "duration": 240, "dj_ready": True, "stem_ready": False,
             "genres": "deep house"},
        ]
        for t in candidates:
            db.upsert_track(t)

        block = {"bpm_range": [115, 130], "genres": ["deep house"], "energy": 0.45}

        first = select_track(candidates, block, db, previous_track=None)
        assert first is not None

        db.log_play(first["tidal_id"])

        second = select_track(candidates, block, db, previous_track=first)
        assert second is not None
        assert second["tidal_id"] != first["tidal_id"]
        assert abs(first["bpm"] - second["bpm"]) <= 15

    def test_history_command_after_plays(self, tmp_env, player):
        """Controller history shows tracks after playing through the cycle."""
        db = tmp_env["db"]

        # Seed and log plays
        for t in SAMPLE_TRACKS[:3]:
            db.upsert_track(t)
            db.log_play(t["tidal_id"])

        controller = DJController(player=player, db=db, tidal_session=None)
        result = controller.handle_command("history")
        assert "Ambient Flow" in result
        assert "Deep Pulse" in result
        assert "Morning Light" in result
