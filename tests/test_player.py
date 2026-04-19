"""Tests for mpv playback controller.

Uses --ao=null so no real audio hardware is needed.
Tests spawn real mpv processes and communicate via IPC.
"""

import shutil
import struct
import time
import wave
from pathlib import Path

import pytest

from dynamic_radio.player import MpvPlayer

pytestmark = pytest.mark.skipif(
    shutil.which("mpv") is None,
    reason="mpv not installed",
)


def make_test_wav(path: Path, duration_s: float = 2.0, sample_rate: int = 44100) -> Path:
    """Generate a short silent WAV file for testing."""
    n_frames = int(sample_rate * duration_s)
    wav_path = path / "test.wav"
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return wav_path


@pytest.fixture
def player(tmp_path: Path):
    """Create and start an mpv player with null audio output."""
    p = MpvPlayer(ipc_path=str(tmp_path / "test-mpv.sock"), volume=0, audio_output="null")
    p.start()
    yield p
    p.stop()


@pytest.fixture
def wav_file(tmp_path: Path) -> Path:
    return make_test_wav(tmp_path, duration_s=5.0)


class TestLifecycle:
    def test_start_creates_socket(self, tmp_path: Path):
        sock = tmp_path / "lifecycle.sock"
        p = MpvPlayer(ipc_path=str(sock), volume=0, audio_output="null")
        try:
            p.start()
            assert sock.exists()
            assert p.is_running
        finally:
            p.stop()

    def test_stop_removes_socket(self, tmp_path: Path):
        sock = tmp_path / "lifecycle.sock"
        p = MpvPlayer(ipc_path=str(sock), volume=0, audio_output="null")
        p.start()
        p.stop()
        assert not sock.exists()
        assert not p.is_running

    def test_start_idempotent(self, player: MpvPlayer):
        pid = player._process.pid
        player.start()
        assert player._process.pid == pid


class TestStatus:
    def test_idle_status(self, player: MpvPlayer):
        status = player.get_status()
        assert status["idle"] is True
        assert status["playlist_pos"] == -1

    def test_volume_control(self, player: MpvPlayer):
        player.set_volume(50)
        time.sleep(0.1)
        status = player.get_status()
        assert status["volume"] == pytest.approx(50, abs=1)

    def test_time_remaining_when_idle(self, player: MpvPlayer):
        assert player.time_remaining() == 0.0


class TestPlayback:
    def test_play_url_starts_playback(self, player: MpvPlayer, wav_file: Path):
        player.play_url(str(wav_file))
        time.sleep(0.5)
        status = player.get_status()
        assert status["idle"] is False

    def test_append_url_adds_to_playlist(self, player: MpvPlayer, wav_file: Path):
        player.play_url(str(wav_file))
        time.sleep(0.3)
        player.append_url(str(wav_file))
        time.sleep(0.3)
        status = player.get_status()
        assert status["playlist_count"] >= 2

    def test_pause_resume(self, player: MpvPlayer, wav_file: Path):
        player.play_url(str(wav_file))
        time.sleep(0.3)

        player.pause()
        time.sleep(0.1)
        assert player.get_status()["paused"] is True

        player.resume()
        time.sleep(0.1)
        assert player.get_status()["paused"] is False

    def test_skip(self, player: MpvPlayer, wav_file: Path):
        player.play_url(str(wav_file))
        player.append_url(str(wav_file))
        time.sleep(0.3)

        player.skip()
        time.sleep(0.3)
        status = player.get_status()
        # After skip, either at next playlist pos or idle (if second file ended)
        assert status["playlist_pos"] >= 1 or status["idle"]

    def test_clear_playlist(self, player: MpvPlayer, wav_file: Path):
        player.play_url(str(wav_file))
        player.append_url(str(wav_file))
        time.sleep(0.3)

        player.clear_playlist()
        time.sleep(0.3)
        status = player.get_status()
        assert status["idle"] is True

    def test_time_remaining_during_playback(self, player: MpvPlayer, wav_file: Path):
        player.play_url(str(wav_file))
        time.sleep(0.5)
        remaining = player.time_remaining()
        assert remaining > 0.0
