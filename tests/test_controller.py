"""Tests for DJ controller — command dispatch, state machine, handlers."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from dynamic_radio.controller import DJController, DJState


@pytest.fixture
def mock_player():
    player = MagicMock()
    player.get_status.return_value = {
        "idle": True,
        "paused": False,
        "position": 0.0,
        "duration": 0.0,
        "volume": 80,
        "playlist_count": 0,
        "playlist_pos": -1,
        "filename": "",
    }
    player.time_remaining.return_value = 0.0
    return player


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.conn = MagicMock()
    db.conn.execute.return_value.fetchall.return_value = []
    return db


@pytest.fixture
def mock_tidal():
    session = MagicMock()
    return session


@pytest.fixture
def controller(mock_player, mock_db, mock_tidal):
    return DJController(player=mock_player, db=mock_db, tidal_session=mock_tidal)


# --- State machine ---


class TestState:
    def test_initial_state_is_paused(self, controller):
        assert controller.state == DJState.PAUSED

    def test_status_emoji_paused(self, controller):
        assert controller.status_emoji == "🔇"

    def test_status_emoji_active(self, controller):
        controller.state = DJState.ACTIVE
        assert controller.status_emoji == "🎵"

    def test_status_emoji_override(self, controller):
        controller.state = DJState.OVERRIDE
        assert controller.status_emoji == "⏸️"


# --- Command dispatch ---


class TestDispatch:
    def test_empty_text_shows_status(self, controller):
        result = controller.handle_command("")
        assert "Dynamic Radio" in result

    def test_status_command(self, controller):
        result = controller.handle_command("status")
        assert "Dynamic Radio" in result

    def test_unknown_command(self, controller):
        result = controller.handle_command("foobar")
        assert "Unknown command" in result
        assert "foobar" in result

    def test_command_case_insensitive(self, controller):
        result = controller.handle_command("PAUSE")
        assert "paused" in result.lower()

    def test_handler_exception_returns_error(self, controller, mock_player):
        mock_player.get_status.side_effect = RuntimeError("mpv died")
        result = controller.handle_command("skip")
        assert "Error" in result


# --- Status ---


class TestStatus:
    def test_idle_status(self, controller):
        result = controller._cmd_status()
        assert "Nothing playing" in result
        assert "paused" in result

    def test_playing_status(self, controller, mock_player):
        mock_player.get_status.return_value = {
            "idle": False,
            "paused": False,
            "position": 30.0,
            "duration": 240.0,
            "volume": 75,
            "playlist_count": 1,
            "playlist_pos": 0,
            "filename": "test.flac",
        }
        mock_player.time_remaining.return_value = 210.0
        controller.state = DJState.ACTIVE
        controller._current_track = {"name": "Test Song", "artist": "Test Artist"}

        result = controller._cmd_status()
        assert "Test Song" in result
        assert "Test Artist" in result
        assert "3:30 remaining" in result
        assert "75%" in result

    @patch("dynamic_radio.controller.load_plan")
    def test_status_shows_plan_block(self, mock_load, controller):
        mock_load.return_value = {
            "date": "2026-04-07",
            "blocks": [
                {
                    "start": "00:00",
                    "end": "23:59",
                    "mood": "chill",
                    "energy": 0.3,
                    "genres": ["ambient"],
                    "bpm_range": [80, 110],
                }
            ],
        }
        result = controller._cmd_status()
        assert "chill" in result


# --- Play ---


class TestPlay:
    def test_play_no_query(self, controller):
        result = controller._cmd_play("")
        assert "Usage" in result

    def test_play_no_tidal(self, mock_player, mock_db):
        c = DJController(player=mock_player, db=mock_db, tidal_session=None)
        result = c._cmd_play("some song")
        assert "not connected" in result

    def test_play_no_results(self, controller, mock_tidal):
        mock_results = MagicMock()
        mock_results.tracks = []
        mock_tidal.search.return_value = mock_results
        result = controller._cmd_play("nonexistent song")
        assert "No results" in result

    def test_play_success(self, controller, mock_tidal, mock_player, mock_db):
        mock_track = MagicMock()
        mock_track.id = 12345
        mock_track.name = "Cool Track"
        mock_track.full_name = "Cool Track"
        mock_track.artist.name = "Cool Artist"
        mock_track.album.name = "Cool Album"
        mock_track.duration = 240
        mock_track.get_url.return_value = "https://tidal.example/stream"

        mock_results = MagicMock()
        mock_results.tracks = [mock_track]
        mock_tidal.search.return_value = mock_results

        result = controller._cmd_play("cool track")

        assert "Cool Track" in result
        assert "Cool Artist" in result
        mock_player.play_url.assert_called_once_with("https://tidal.example/stream")
        assert controller.state == DJState.OVERRIDE
        mock_db.upsert_track.assert_called_once()
        mock_db.log_play.assert_called_once_with(12345)


# --- Queue ---


class TestQueue:
    def test_queue_no_query(self, controller):
        result = controller._cmd_queue("")
        assert "Usage" in result

    def test_queue_no_tidal(self, mock_player, mock_db):
        c = DJController(player=mock_player, db=mock_db, tidal_session=None)
        result = c._cmd_queue("some song")
        assert "not connected" in result

    def test_queue_success(self, controller, mock_tidal, mock_player):
        mock_track = MagicMock()
        mock_track.id = 99
        mock_track.name = "Queued Song"
        mock_track.full_name = "Queued Song"
        mock_track.artist.name = "Queue Artist"
        mock_track.album.name = "Album"
        mock_track.duration = 180
        mock_track.get_url.return_value = "https://tidal.example/queued"

        mock_results = MagicMock()
        mock_results.tracks = [mock_track]
        mock_tidal.search.return_value = mock_results

        result = controller._cmd_queue("queued song")

        assert "Queued Song" in result
        mock_player.append_url.assert_called_once_with("https://tidal.example/queued")


# --- Skip ---


class TestSkip:
    def test_skip_when_idle(self, controller):
        result = controller._cmd_skip("")
        assert "Nothing playing" in result

    def test_skip_when_playing(self, controller, mock_player):
        mock_player.get_status.return_value["idle"] = False
        result = controller._cmd_skip("")
        assert "Skipped" in result
        mock_player.skip.assert_called_once()


# --- Pause / Resume ---


class TestPauseResume:
    def test_pause(self, controller, mock_player):
        controller.state = DJState.ACTIVE
        result = controller._cmd_pause("")
        assert controller.state == DJState.PAUSED
        mock_player.pause.assert_called_once()
        assert "paused" in result.lower()

    def test_resume(self, controller, mock_player):
        controller.state = DJState.PAUSED
        result = controller._cmd_resume("")
        assert controller.state == DJState.ACTIVE
        mock_player.resume.assert_called_once()
        assert "resumed" in result.lower()


# --- Mood ---


class TestMood:
    def test_mood_no_description(self, controller):
        result = controller._cmd_mood("")
        assert "Usage" in result

    def test_mood_queues_adjustment(self, controller):
        result = controller._cmd_mood("more energy")
        assert "more energy" in result


# --- History ---


class TestHistory:
    def test_history_empty(self, controller):
        result = controller._cmd_history("")
        assert "No play history" in result

    def test_history_with_entries(self, controller, mock_db):
        mock_db.conn.execute.return_value.fetchall.return_value = [
            {"name": "Song A", "artist": "Artist A", "played_at": "2026-04-07T14:30:00"},
            {"name": "Song B", "artist": "Artist B", "played_at": "2026-04-07T14:00:00"},
        ]
        result = controller._cmd_history("")
        assert "Song A" in result
        assert "Artist A" in result
        assert "14:30" in result


# --- Plan ---


class TestPlan:
    @patch("dynamic_radio.controller.load_plan")
    def test_plan_no_plan(self, mock_load, controller):
        mock_load.return_value = None
        result = controller._cmd_plan("")
        assert "No plan" in result

    @patch("dynamic_radio.controller.load_plan")
    def test_plan_shows_blocks(self, mock_load, controller):
        mock_load.return_value = {
            "date": "2026-04-07",
            "blocks": [
                {
                    "start": "09:00",
                    "end": "12:00",
                    "mood": "focused",
                    "energy": 0.45,
                    "genres": ["minimal", "deep house"],
                    "bpm_range": [110, 128],
                    "description": "Deep work.",
                },
            ],
        }
        result = controller._cmd_plan("")
        assert "2026-04-07" in result
        assert "focused" in result
        assert "110" in result


# --- Like / Dislike ---


class TestLikeDislike:
    def test_like_nothing_playing(self, controller):
        result = controller._cmd_like("")
        assert "Nothing playing" in result

    def test_like_current_track(self, controller):
        controller._current_track = {"name": "Liked Song", "artist": "Artist"}
        result = controller._cmd_like("")
        assert "Liked Song" in result

    def test_dislike_nothing_playing(self, controller):
        result = controller._cmd_dislike("")
        assert "Nothing playing" in result

    def test_dislike_skips_and_marks(self, controller, mock_player, mock_db):
        controller._current_track = {"tidal_id": 42, "name": "Bad Song", "artist": "Artist"}
        result = controller._cmd_dislike("")
        assert "Bad Song" in result
        mock_db.dislike.assert_called_once_with(42)
        mock_player.skip.assert_called_once()


# --- Volume ---


class TestVolume:
    def test_volume_no_arg_shows_current(self, controller, mock_player):
        mock_player.get_status.return_value["volume"] = 65
        result = controller._cmd_volume("")
        assert "65%" in result

    def test_volume_set(self, controller, mock_player):
        controller.on_volume = MagicMock()
        result = controller._cmd_volume("42")
        controller.on_volume.assert_called_once_with(42)
        assert "42" in result

    def test_volume_invalid(self, controller):
        result = controller._cmd_volume("loud")
        assert "Usage" in result


# --- Helpers ---


class TestHelpers:
    def test_track_display_none(self, controller):
        assert controller._current_track_display() == "Unknown"

    def test_track_display_with_track(self, controller):
        controller._current_track = {"name": "Song", "artist": "Artist"}
        assert controller._current_track_display() == "Song — Artist"

    def test_cache_tidal_track(self, controller, mock_db):
        mock_track = MagicMock()
        mock_track.id = 555
        mock_track.name = "Cached"
        mock_track.full_name = "Cached"
        mock_track.artist.name = "Cache Artist"
        mock_track.album.name = "Cache Album"
        mock_track.duration = 300

        info = controller._cache_tidal_track(mock_track)

        assert info["tidal_id"] == 555
        assert info["name"] == "Cached"
        assert info["artist"] == "Cache Artist"
        mock_db.upsert_track.assert_called_once()
        mock_db.log_play.assert_called_once_with(555)
