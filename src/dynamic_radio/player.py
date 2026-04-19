"""mpv playback controller via JSON IPC.

Manages an mpv subprocess for headless audio playback. Communicates
via Unix domain socket using mpv's JSON IPC protocol. Supports gapless
playback by appending the next track URL before the current finishes.
"""

import json
import logging
import socket as _socket_mod
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_IPC_PATH = "/tmp/dynamic-radio-mpv.sock"


class MpvPlayer:
    """Controls an mpv instance via JSON IPC.

    Lifecycle:
        player = MpvPlayer()
        player.start()
        player.play_url("https://...")
        player.append_url("https://...")  # gapless next track
        ...
        player.stop()
    """

    def __init__(
        self,
        ipc_path: str = DEFAULT_IPC_PATH,
        volume: int = 80,
        audio_output: str | None = None,
        audio_device: str | None = None,
    ):
        self.ipc_path = ipc_path
        self.initial_volume = volume
        self.audio_output = audio_output  # e.g. "null" for testing
        self.audio_device = audio_device  # e.g. "pulse/dynamicradio-sink" for streaming
        self._process: subprocess.Popen | None = None
        self._request_id = 0

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """Start mpv in idle mode with IPC."""
        if self.is_running:
            logger.debug("mpv already running (pid=%d)", self._process.pid)
            return

        # Clean up stale socket
        sock_path = Path(self.ipc_path)
        if sock_path.exists():
            sock_path.unlink()

        cmd = [
            "mpv",
            "--idle=yes",
            "--no-video",
            "--no-terminal",
            f"--input-ipc-server={self.ipc_path}",
            f"--volume={self.initial_volume}",
            "--really-quiet",
        ]
        if self.audio_output:
            cmd.append(f"--ao={self.audio_output}")
        if self.audio_device:
            cmd.append(f"--audio-device={self.audio_device}")

        self._process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        logger.info("Started mpv (pid=%d, ipc=%s)", self._process.pid, self.ipc_path)

        # Wait for IPC to become available
        for _ in range(50):  # 5 seconds max
            if Path(self.ipc_path).exists():
                return
            time.sleep(0.1)
        raise RuntimeError("mpv IPC did not become available within 5 seconds")

    def stop(self) -> None:
        """Stop mpv and clean up."""
        if self._process is not None:
            try:
                self._command("quit")
            except Exception:
                pass
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None

        sock_path = Path(self.ipc_path)
        if sock_path.exists():
            sock_path.unlink()
        logger.info("Stopped mpv")

    def play_url(self, url: str) -> None:
        """Play a URL immediately (replaces current playback)."""
        self._command("loadfile", url, "replace")
        logger.debug("Playing: %s", url[:80])

    def append_url(self, url: str) -> None:
        """Append a URL to the playlist for gapless playback."""
        self._command("loadfile", url, "append")
        logger.debug("Appended: %s", url[:80])

    def pause(self) -> None:
        """Pause playback."""
        self._set_property("pause", True)

    def resume(self) -> None:
        """Resume playback."""
        self._set_property("pause", False)

    def skip(self) -> None:
        """Skip to next track in playlist."""
        self._command("playlist-next", "force")

    def set_volume(self, volume: int) -> None:
        """Set volume (0-100)."""
        self._set_property("volume", max(0, min(100, volume)))

    def clear_playlist(self) -> None:
        """Clear the playlist (stops playback)."""
        self._command("playlist-clear")
        self._command("stop")

    def get_status(self) -> dict[str, Any]:
        """Get current playback status.

        Returns dict with:
            idle: bool — True if nothing is playing
            paused: bool
            position: float — seconds into current track
            duration: float — total track length in seconds
            volume: int
            playlist_count: int — items in playlist
            playlist_pos: int — current position in playlist (0-indexed, -1 if idle)
            filename: str — current file/URL
        """
        try:
            idle = self._get_property("idle-active")
            if idle:
                return {
                    "idle": True,
                    "paused": False,
                    "position": 0.0,
                    "duration": 0.0,
                    "volume": self._get_property("volume") or 0,
                    "playlist_count": self._get_property("playlist-count") or 0,
                    "playlist_pos": -1,
                    "filename": "",
                }

            return {
                "idle": False,
                "paused": self._get_property("pause") or False,
                "position": self._get_property("time-pos") or 0.0,
                "duration": self._get_property("duration") or 0.0,
                "volume": self._get_property("volume") or 0,
                "playlist_count": self._get_property("playlist-count") or 0,
                "playlist_pos": self._get_property("playlist-pos") or 0,
                "filename": self._get_property("filename") or "",
            }
        except Exception:
            logger.warning("Failed to get mpv status", exc_info=True)
            return {
                "idle": True, "paused": False, "position": 0.0,
                "duration": 0.0, "volume": 0, "playlist_count": 0,
                "playlist_pos": -1, "filename": "",
            }

    def time_remaining(self) -> float:
        """Get seconds remaining in current track, or 0 if idle."""
        status = self.get_status()
        if status["idle"]:
            return 0.0
        return max(0.0, status["duration"] - status["position"])

    def _command(self, *args: Any) -> Any:
        """Send a command to mpv via IPC."""
        self._request_id += 1
        msg = {"command": list(args), "request_id": self._request_id}
        return self._send(msg)

    def _get_property(self, name: str) -> Any:
        """Get an mpv property."""
        self._request_id += 1
        msg = {"command": ["get_property", name], "request_id": self._request_id}
        result = self._send(msg)
        if result and "data" in result:
            return result["data"]
        return None

    def _set_property(self, name: str, value: Any) -> None:
        """Set an mpv property."""
        self._command("set_property", name, value)

    def _send(self, msg: dict) -> dict | None:
        """Send a JSON message to mpv's IPC via Unix domain socket."""
        try:
            sock = _socket_mod.socket(_socket_mod.AF_UNIX, _socket_mod.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect(self.ipc_path)

            payload = json.dumps(msg) + "\n"
            sock.sendall(payload.encode())

            buf = b""
            target_id = msg.get("request_id")
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                for line in buf.split(b"\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        resp = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if resp.get("request_id") == target_id:
                        sock.close()
                        return resp
                if buf.endswith(b"\n"):
                    buf = b""
                else:
                    buf = buf.split(b"\n")[-1]

            sock.close()
        except OSError as e:
            logger.error("mpv IPC error: %s", e)
        return None

