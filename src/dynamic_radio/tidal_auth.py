"""Tidal authentication with session persistence for 24/7 operation.

Handles OAuth device-flow login, session file persistence, and automatic
token refresh. On first run, prints a login URL for the user to visit.
On subsequent runs, loads the saved session and refreshes if needed.
"""

import logging
from pathlib import Path

import tidalapi

logger = logging.getLogger(__name__)

DEFAULT_SESSION_FILE = Path.home() / ".local" / "share" / "dynamic-radio" / "tidal-session.json"

# Use LOSSLESS (16-bit FLAC) by default — works with OAuth device flow.
# HI_RES_LOSSLESS requires PKCE auth (browser-based initial login).
DEFAULT_QUALITY = tidalapi.Quality.high_lossless


def get_session(
    session_file: Path = DEFAULT_SESSION_FILE,
    quality: tidalapi.Quality = DEFAULT_QUALITY,
) -> tidalapi.Session:
    """Get an authenticated Tidal session.

    Attempts to load from session file first. If no saved session exists
    or the saved session is invalid, initiates OAuth device-flow login.

    Args:
        session_file: Path to persist session credentials.
        quality: Audio quality tier.

    Returns:
        An authenticated tidalapi.Session.

    Raises:
        TimeoutError: If the user doesn't complete OAuth login in time.
    """
    config = tidalapi.Config(quality=quality)
    session = tidalapi.Session(config)

    session_file.parent.mkdir(parents=True, exist_ok=True)

    # Try loading existing session
    if session_file.exists():
        try:
            session.login_session_file(session_file)
            if session.check_login():
                logger.info("Loaded Tidal session for %s", session.user.first_name)
                return session
            logger.warning("Saved session invalid, re-authenticating")
        except Exception:
            logger.warning("Failed to load session file, re-authenticating", exc_info=True)

    # Fresh login via OAuth device flow
    logger.info("Starting Tidal OAuth login...")
    session.login_oauth_simple()

    if not session.check_login():
        raise RuntimeError("Tidal login failed")

    # Persist session for next startup using tidalapi's built-in format
    session.save_session_to_file(session_file)
    logger.info("Logged in as %s, session saved to %s", session.user.first_name, session_file)
    return session



def verify_session(session: tidalapi.Session) -> bool:
    """Verify the session is still valid, attempting refresh if needed.

    tidalapi auto-refreshes expired tokens on API calls, but this
    can be called proactively (e.g. on a health check timer).
    """
    try:
        return session.check_login()
    except Exception:
        logger.error("Session verification failed", exc_info=True)
        return False


def refresh_session(
    session: tidalapi.Session,
    session_file: Path = DEFAULT_SESSION_FILE,
) -> bool:
    """Proactively refresh the Tidal access token and persist the session.

    tidalapi's reactive auto-refresh only triggers on the v1 error body shape
    (userMessage 'The token has expired.'); v2 openapi endpoints return a
    different shape and never trigger refresh. Calling this on a timer before
    expiry keeps the token valid regardless of which endpoint is next hit.
    """
    if not session.refresh_token:
        logger.warning("No refresh_token available; cannot refresh")
        return False
    try:
        ok = session.token_refresh(session.refresh_token)
    except Exception:
        logger.error("Token refresh failed", exc_info=True)
        return False
    if not ok:
        logger.warning("Tidal token_refresh returned False; re-login required")
        return False
    try:
        session.save_session_to_file(session_file)
    except Exception:
        logger.warning("Failed to persist refreshed session", exc_info=True)
    logger.info("Tidal access token refreshed, new expiry=%s", session.expiry_time)
    return True
