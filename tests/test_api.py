"""Tests for the HTTP API."""

from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dynamic_radio.api import create_app
from dynamic_radio.controller import DJState


def _make_controller():
    ctrl = MagicMock()
    ctrl.state = DJState.ACTIVE
    ctrl._current_track = {"tidal_id": 123, "name": "Test Track", "artist": "Test Artist"}
    ctrl.player.get_status.return_value = {"idle": False, "volume": 80}
    ctrl.player.time_remaining.return_value = 120.0
    ctrl.handle_command.return_value = "Command executed"
    return ctrl


@pytest.mark.asyncio
async def test_health():
    ctrl = _make_controller()
    async with TestClient(TestServer(create_app(ctrl))) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True


@pytest.mark.asyncio
async def test_command_play():
    ctrl = _make_controller()
    async with TestClient(TestServer(create_app(ctrl))) as client:
        resp = await client.post("/command", json={"text": "play brostep"})
        assert resp.status == 200
        data = await resp.json()
        assert data["response"] == "Command executed"
        ctrl.handle_command.assert_called_once_with("play brostep")


@pytest.mark.asyncio
async def test_command_empty_text():
    ctrl = _make_controller()
    async with TestClient(TestServer(create_app(ctrl))) as client:
        resp = await client.post("/command", json={"text": ""})
        assert resp.status == 200
        ctrl.handle_command.assert_called_once_with("")


@pytest.mark.asyncio
async def test_command_missing_body():
    ctrl = _make_controller()
    async with TestClient(TestServer(create_app(ctrl))) as client:
        resp = await client.post("/command", data=b"not json")
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data


@pytest.mark.asyncio
async def test_status_active():
    ctrl = _make_controller()
    async with TestClient(TestServer(create_app(ctrl))) as client:
        with patch("dynamic_radio.api.load_plan") as mock_load, \
             patch("dynamic_radio.api.get_current_block") as mock_block:
            mock_load.return_value = {"blocks": []}
            mock_block.return_value = {"mood": "focused", "start": "09:00", "end": "12:00", "energy": 0.45}

            resp = await client.get("/status")
            assert resp.status == 200
            data = await resp.json()

            assert data["state"] == "active"
            assert data["idle"] is False
            assert data["volume"] == 80
            assert data["time_remaining"] == 120.0
            assert data["current_track"]["name"] == "Test Track"
            assert data["current_block"]["mood"] == "focused"


@pytest.mark.asyncio
async def test_status_no_plan():
    ctrl = _make_controller()
    async with TestClient(TestServer(create_app(ctrl))) as client:
        with patch("dynamic_radio.api.load_plan", return_value=None):
            resp = await client.get("/status")
            data = await resp.json()
            assert data["current_block"] is None


@pytest.mark.asyncio
async def test_command_skip():
    ctrl = _make_controller()
    async with TestClient(TestServer(create_app(ctrl))) as client:
        resp = await client.post("/command", json={"text": "skip"})
        assert resp.status == 200
        ctrl.handle_command.assert_called_once_with("skip")


@pytest.mark.asyncio
async def test_command_volume():
    ctrl = _make_controller()
    async with TestClient(TestServer(create_app(ctrl))) as client:
        resp = await client.post("/command", json={"text": "volume 50"})
        assert resp.status == 200
        ctrl.handle_command.assert_called_once_with("volume 50")
