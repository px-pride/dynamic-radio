"""Tests for the DJ daemon loop."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dynamic_radio.controller import DJController, DJState
from dynamic_radio.daemon import DJDaemon, POLL_INTERVAL, PREFETCH_THRESHOLD


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
    db.recently_played_ids.return_value = set()
    db.recently_played_artists.return_value = set()
    db.disliked_ids.return_value = set()
    db.play_count.return_value = 0
    db.last_played_at.return_value = None
    return db


@pytest.fixture
def mock_tidal():
    session = MagicMock()
    return session


@pytest.fixture
def controller(mock_player, mock_db, mock_tidal):
    return DJController(player=mock_player, db=mock_db, tidal_session=mock_tidal)


@pytest.fixture
def daemon(controller):
    return DJDaemon(controller)


SAMPLE_PLAN = {
    "date": date.today().isoformat(),
    "blocks": [
        {
            "start": "00:00",
            "end": "23:59",
            "mood": "chill",
            "energy": 0.3,
            "genres": ["ambient", "downtempo"],
            "bpm_range": [80, 110],
            "description": "All day chill.",
        }
    ],
}


class TestDaemonLifecycle:
    def test_initial_state(self, daemon):
        assert not daemon._running
        assert not daemon._next_queued

    def test_stop_sets_flag(self, daemon):
        daemon._running = True
        daemon.stop()
        assert not daemon._running


class TestTick:
    @pytest.mark.asyncio
    async def test_tick_paused_does_nothing(self, daemon, mock_player):
        daemon.controller.state = DJState.PAUSED
        daemon._plan_date = date.today()
        await daemon._tick()
        mock_player.get_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_override_resumes_when_idle(self, daemon, mock_player):
        daemon.controller.state = DJState.OVERRIDE
        daemon._plan_date = date.today()
        mock_player.get_status.return_value["idle"] = True
        await daemon._tick()
        assert daemon.controller.state == DJState.ACTIVE

    @pytest.mark.asyncio
    async def test_tick_override_waits_when_playing(self, daemon, mock_player):
        daemon.controller.state = DJState.OVERRIDE
        daemon._plan_date = date.today()
        mock_player.get_status.return_value["idle"] = False
        mock_player.time_remaining.return_value = 120.0
        await daemon._tick()
        assert daemon.controller.state == DJState.OVERRIDE

    @pytest.mark.asyncio
    @patch("dynamic_radio.daemon.load_plan")
    @patch("dynamic_radio.daemon.get_current_block")
    async def test_tick_active_idle_plays_next(
        self, mock_block, mock_plan, daemon, mock_player, mock_tidal, mock_db
    ):
        daemon.controller.state = DJState.ACTIVE
        daemon._plan_date = date.today()
        mock_player.get_status.return_value["idle"] = True

        mock_plan.return_value = SAMPLE_PLAN
        mock_block.return_value = SAMPLE_PLAN["blocks"][0]

        # Set up Tidal search to return a track
        mock_track = MagicMock()
        mock_track.id = 5555
        mock_track.name = "Test Track"
        mock_track.artist.name = "Test Artist"
        mock_track.album.name = "Test Album"
        mock_track.duration = 240
        mock_track.bpm = 95.0
        mock_track.key = "C"
        mock_track.key_scale = "minor"
        mock_track.dj_ready = False
        mock_track.stem_ready = False

        mock_results = MagicMock()
        mock_results.tracks = [mock_track]
        mock_tidal.search.return_value = mock_results

        # Mock the stream URL fetch
        mock_tidal_track = MagicMock()
        mock_tidal_track.get_url.return_value = "https://tidal.example/stream"
        mock_tidal.track.return_value = mock_tidal_track

        await daemon._tick()

        mock_player.play_url.assert_called_once_with("https://tidal.example/stream")
        mock_db.log_play.assert_called()

    @pytest.mark.asyncio
    @patch("dynamic_radio.daemon.load_plan")
    @patch("dynamic_radio.daemon.get_current_block")
    async def test_tick_active_prefetches_near_end(
        self, mock_block, mock_plan, daemon, mock_player, mock_tidal, mock_db
    ):
        daemon.controller.state = DJState.ACTIVE
        daemon._plan_date = date.today()
        mock_player.get_status.return_value["idle"] = False
        mock_player.time_remaining.return_value = 10.0  # < PREFETCH_THRESHOLD

        mock_plan.return_value = SAMPLE_PLAN
        mock_block.return_value = SAMPLE_PLAN["blocks"][0]

        mock_track = MagicMock()
        mock_track.id = 6666
        mock_track.name = "Next Track"
        mock_track.artist.name = "Next Artist"
        mock_track.album.name = "Next Album"
        mock_track.duration = 200
        mock_track.bpm = 100.0
        mock_track.key = "D"
        mock_track.key_scale = "minor"
        mock_track.dj_ready = False
        mock_track.stem_ready = False

        mock_results = MagicMock()
        mock_results.tracks = [mock_track]
        mock_tidal.search.return_value = mock_results

        mock_tidal_track = MagicMock()
        mock_tidal_track.get_url.return_value = "https://tidal.example/next"
        mock_tidal.track.return_value = mock_tidal_track

        await daemon._tick()

        mock_player.append_url.assert_called_once_with("https://tidal.example/next")
        assert daemon._next_queued

    @pytest.mark.asyncio
    async def test_tick_resets_queued_flag_when_far(self, daemon, mock_player):
        daemon.controller.state = DJState.ACTIVE
        daemon._plan_date = date.today()
        daemon._next_queued = True
        mock_player.get_status.return_value["idle"] = False
        mock_player.time_remaining.return_value = 180.0  # > PREFETCH_THRESHOLD

        await daemon._tick()

        assert not daemon._next_queued


class TestEnsurePlan:
    @pytest.mark.asyncio
    @patch("dynamic_radio.daemon.load_plan")
    async def test_loads_existing_plan(self, mock_load, daemon):
        mock_load.return_value = SAMPLE_PLAN
        await daemon._ensure_plan()
        assert daemon._plan_date == date.today()

    @pytest.mark.asyncio
    @patch("dynamic_radio.daemon.default_plan")
    @patch("dynamic_radio.daemon.load_plan")
    async def test_uses_default_plan_when_missing(self, mock_load, mock_default, daemon):
        mock_load.return_value = None
        mock_default.return_value = SAMPLE_PLAN
        await daemon._ensure_plan()
        mock_default.assert_called_once_with(date.today())
        assert daemon._plan_date == date.today()


class TestSelectFromTidal:
    @pytest.mark.asyncio
    async def test_no_session_returns_none(self, daemon):
        daemon.controller.tidal_session = None
        result = await daemon._select_from_tidal()
        assert result is None

    @pytest.mark.asyncio
    @patch("dynamic_radio.daemon.load_plan")
    async def test_no_plan_returns_none(self, mock_load, daemon):
        mock_load.return_value = None
        result = await daemon._select_from_tidal()
        assert result is None

    @pytest.mark.asyncio
    @patch("dynamic_radio.daemon.get_current_block")
    @patch("dynamic_radio.daemon.load_plan")
    async def test_no_block_returns_none(self, mock_load, mock_block, daemon):
        mock_load.return_value = SAMPLE_PLAN
        mock_block.return_value = None
        result = await daemon._select_from_tidal()
        assert result is None

    @pytest.mark.asyncio
    @patch("dynamic_radio.daemon.get_current_block")
    @patch("dynamic_radio.daemon.load_plan")
    async def test_search_failure_returns_none(self, mock_load, mock_block, daemon, mock_tidal):
        mock_load.return_value = SAMPLE_PLAN
        mock_block.return_value = SAMPLE_PLAN["blocks"][0]
        mock_tidal.search.side_effect = Exception("API error")
        result = await daemon._select_from_tidal()
        assert result is None
