"""Icecast streaming with server-side jitter buffer.

Two-stage pipeline that decouples PipeWire capture timing from stream
delivery, absorbing the irregular audio scheduling on headless systems:

  Stage 1 (capture): PulseAudio monitor → raw PCM → pipe buffer
  Stage 2 (encoder): pipe (-re steady rate) → codec → Icecast

Two parallel pipelines serve different formats:
  /dynamicradio     — FLAC lossless (Ogg container, Chrome/Firefox)
  /dynamicradio.mp3 — MP3 320kbps CBR (Safari/iOS fallback)

The pipe is pre-filled before the encoder starts, creating a jitter
buffer. The stream has a fixed delay equal to the pre-fill duration.
"""

import fcntl
import logging
import os
import subprocess
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# PCM format constants (must match ffmpeg args)
_PCM_RATE = 48000
_PCM_CHANNELS = 2
_PCM_SAMPLE_BYTES = 2  # 16-bit
_PCM_BYTES_PER_SEC = _PCM_RATE * _PCM_CHANNELS * _PCM_SAMPLE_BYTES  # 192000

# Linux pipe buffer ioctl
_F_SETPIPE_SZ = 1031
_F_GETPIPE_SZ = 1032


class IcecastStreamer:
    """Two-stage ffmpeg pipelines with jitter buffer.

    Runs two independent capture→encode pipelines: FLAC (lossless) and
    MP3 (320kbps, Safari/iOS compatible). Both capture from the same
    PulseAudio monitor source.

    Lifecycle:
        streamer = IcecastStreamer()
        streamer.start()
        streamer.update_metadata("Track", "Artist")
        ...
        streamer.stop()
    """

    def __init__(
        self,
        icecast_host: str = "localhost",
        icecast_port: int = 8000,
        source_password: str | None = None,
        admin_password: str | None = None,
        prefill_seconds: int = 3,
    ):
        source_password = source_password or os.environ.get("ICECAST_SOURCE_PASSWORD")
        admin_password = admin_password or os.environ.get("ICECAST_ADMIN_PASSWORD")
        if not source_password or not admin_password:
            raise RuntimeError(
                "ICECAST_SOURCE_PASSWORD and ICECAST_ADMIN_PASSWORD must be set "
                "(see deploy/linux/secrets.env.example)"
            )
        self.icecast_host = icecast_host
        self.icecast_port = icecast_port
        self.source_password = source_password
        self.admin_password = admin_password
        self.prefill_seconds = prefill_seconds
        # FLAC pipeline (lossless, Ogg container)
        self._flac_capture: subprocess.Popen | None = None
        self._flac_encoder: subprocess.Popen | None = None
        # MP3 pipeline (320kbps CBR fallback)
        self._mp3_capture: subprocess.Popen | None = None
        self._mp3_encoder: subprocess.Popen | None = None

    @property
    def is_running(self) -> bool:
        return (
            self._flac_capture is not None and self._flac_capture.poll() is None
            and self._flac_encoder is not None and self._flac_encoder.poll() is None
        )

    def start(self) -> None:
        """Start FLAC + MP3 streaming pipelines."""
        if self.is_running:
            logger.debug("Streamer already running")
            return

        monitor = self._get_monitor_source()

        # FLAC pipeline (lossless)
        self._flac_capture, self._flac_encoder = self._start_pipeline(
            monitor=monitor,
            mount="/dynamicradio",
            encoder_args=["-c:a", "flac", "-content_type", "audio/ogg", "-f", "ogg"],
            label="FLAC",
        )

        # MP3 pipeline (320kbps CBR — highest quality MP3)
        self._mp3_capture, self._mp3_encoder = self._start_pipeline(
            monitor=monitor,
            mount="/dynamicradio.mp3",
            encoder_args=[
                "-c:a", "libmp3lame", "-b:a", "320k",
                "-content_type", "audio/mpeg", "-f", "mp3",
            ],
            label="MP3 320k",
        )

        logger.info(
            "Streaming FLAC + MP3 to icecast://%s:%d (%ds jitter buffer)",
            self.icecast_host, self.icecast_port, self.prefill_seconds,
        )

    def stop(self) -> None:
        """Stop all ffmpeg processes."""
        for proc in (
            self._mp3_encoder, self._mp3_capture,
            self._flac_encoder, self._flac_capture,
        ):
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        self._flac_capture = self._flac_encoder = None
        self._mp3_capture = self._mp3_encoder = None
        logger.info("Streamer stopped")

    def update_metadata(self, title: str, artist: str) -> None:
        """Push now-playing metadata.

        Pushes ICY metadata to the MP3 mount via Icecast admin API.
        FLAC/Ogg does not support ICY metadata — track info for that
        stream is available via the daemon's /status endpoint.
        """
        song = f"{artist} - {title}" if artist else title
        logger.info("Now playing: %s", song)

        # Push ICY metadata to MP3 mount
        try:
            params = urllib.parse.urlencode({
                "mount": "/dynamicradio.mp3",
                "mode": "updinfo",
                "song": song,
            })
            url = f"http://{self.icecast_host}:{self.icecast_port}/admin/metadata?{params}"
            req = urllib.request.Request(url)
            # Basic auth for Icecast admin
            import base64
            credentials = base64.b64encode(f"admin:{self.admin_password}".encode()).decode()
            req.add_header("Authorization", f"Basic {credentials}")
            urllib.request.urlopen(req)
            logger.debug("MP3 metadata updated: %s", song)
        except Exception as e:
            logger.debug("MP3 metadata update failed (non-critical): %s", e)

    def _get_monitor_source(self) -> str:
        """Detect the default audio output's monitor source name."""
        try:
            result = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, check=True,
            )
            sink_name = result.stdout.strip()
            monitor = f"{sink_name}.monitor"
            logger.info("Using monitor source: %s", monitor)
            return monitor
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to detect default sink: {e.stderr}") from e

    def _start_pipeline(
        self,
        monitor: str,
        mount: str,
        encoder_args: list[str],
        label: str,
    ) -> tuple[subprocess.Popen, subprocess.Popen]:
        """Start a capture → pipe → encoder pipeline. Returns (capture, encoder)."""
        icecast_url = (
            f"icecast://source:{self.source_password}"
            f"@{self.icecast_host}:{self.icecast_port}{mount}"
        )

        # Create pipe with enlarged buffer
        read_fd, write_fd = os.pipe()
        pipe_size = min(
            self.prefill_seconds * _PCM_BYTES_PER_SEC * 2,
            1048576,  # unprivileged limit
        )
        try:
            fcntl.fcntl(write_fd, _F_SETPIPE_SZ, pipe_size)
            actual = fcntl.fcntl(write_fd, _F_GETPIPE_SZ)
            logger.info(
                "[%s] Pipe buffer: %d bytes (~%.1fs of PCM)",
                label, actual, actual / _PCM_BYTES_PER_SEC,
            )
        except OSError:
            logger.warning("[%s] Could not enlarge pipe buffer", label)

        # Stage 1: Capture PulseAudio monitor → raw PCM → pipe
        capture = subprocess.Popen(
            [
                "ffmpeg", "-nostdin",
                "-f", "pulse",
                "-i", monitor,
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ar", str(_PCM_RATE),
                "-ac", str(_PCM_CHANNELS),
                "pipe:1",
            ],
            stdout=write_fd,
            stderr=subprocess.DEVNULL,
        )
        os.close(write_fd)

        # Verify capture started
        time.sleep(0.5)
        if capture.poll() is not None:
            os.close(read_fd)
            raise RuntimeError(
                f"[{label}] Capture ffmpeg exited immediately (code={capture.returncode})"
            )

        # Pre-fill jitter buffer
        logger.info("[%s] Pre-filling %ds jitter buffer...", label, self.prefill_seconds)
        time.sleep(self.prefill_seconds)

        # Stage 2: pipe → codec → Icecast
        encoder = subprocess.Popen(
            [
                "ffmpeg", "-nostdin", "-re",
                "-f", "s16le",
                "-ar", str(_PCM_RATE),
                "-ac", str(_PCM_CHANNELS),
                "-i", "pipe:0",
                *encoder_args,
                "-ice_name", "Dynamic Radio",
                "-vn",
                icecast_url,
            ],
            stdin=read_fd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(read_fd)

        # Verify encoder started
        time.sleep(0.5)
        if encoder.poll() is not None:
            capture.terminate()
            raise RuntimeError(
                f"[{label}] Encoder ffmpeg exited immediately (code={encoder.returncode})"
            )

        logger.info(
            "[%s] Pipeline: capture (pid=%d) → %ds buffer → encoder (pid=%d) → %s",
            label, capture.pid, self.prefill_seconds, encoder.pid, mount,
        )
        return capture, encoder
