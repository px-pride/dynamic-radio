"""CLI entry point for the Dynamic Radio daemon."""

import argparse
import asyncio
import logging
import signal

from dynamic_radio.daemon import create_daemon


def main() -> None:
    parser = argparse.ArgumentParser(description="Dynamic Radio — 24/7 contextual music")
    parser.add_argument("--volume", type=int, default=80, help="Initial volume (0-100)")
    parser.add_argument("--audio-output", type=str, default=None, help="mpv audio output (e.g. 'null' for testing)")
    parser.add_argument("--port", type=int, default=8420, help="HTTP API port (default: 8420)")
    parser.add_argument("--stream", action="store_true", help="Enable Icecast streaming via PipeWire sink")
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    daemon = create_daemon(
        volume=args.volume,
        audio_output=args.audio_output,
        api_port=args.port,
        stream=args.stream,
    )

    loop = asyncio.new_event_loop()

    def shutdown(sig_val: int | signal.Signals) -> None:
        sig_name = signal.Signals(sig_val).name if isinstance(sig_val, int) else sig_val.name
        logging.getLogger(__name__).info("Received %s, shutting down...", sig_name)
        daemon.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown, sig)

    try:
        loop.run_until_complete(daemon.run())
    except KeyboardInterrupt:
        pass
    finally:
        daemon.controller.player.stop()
        if daemon.streamer:
            daemon.streamer.stop()
        daemon.controller.db.close()
        loop.close()


if __name__ == "__main__":
    main()
