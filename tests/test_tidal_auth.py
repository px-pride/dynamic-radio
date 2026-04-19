"""Tests for Tidal auth module (no real Tidal credentials needed)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from dynamic_radio.tidal_auth import DEFAULT_SESSION_FILE, get_session


def test_default_session_file_path():
    """Session file should be in XDG-standard location."""
    assert "dynamic-radio" in str(DEFAULT_SESSION_FILE)
    assert str(DEFAULT_SESSION_FILE).endswith("tidal-session.json")


@patch("dynamic_radio.tidal_auth.tidalapi")
def test_get_session_loads_existing(mock_tidalapi, tmp_path: Path):
    """get_session loads from session file when it exists and is valid."""
    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps({
        "token_type": {"data": "Bearer"},
        "access_token": {"data": "tok"},
        "refresh_token": {"data": "ref"},
    }))

    mock_session = MagicMock()
    mock_session.check_login.return_value = True
    mock_session.user.first_name = "TestUser"
    mock_tidalapi.Config.return_value = MagicMock()
    mock_tidalapi.Session.return_value = mock_session

    result = get_session(session_file=session_file)

    mock_session.login_session_file.assert_called_once_with(session_file)
    assert result is mock_session
    mock_session.login_oauth_simple.assert_not_called()


@patch("dynamic_radio.tidal_auth.tidalapi")
def test_get_session_falls_back_to_oauth(mock_tidalapi, tmp_path: Path):
    """get_session starts OAuth when no session file exists."""
    session_file = tmp_path / "nonexistent" / "session.json"

    mock_session = MagicMock()
    mock_session.check_login.return_value = True
    mock_session.user.first_name = "TestUser"
    mock_tidalapi.Config.return_value = MagicMock()
    mock_tidalapi.Session.return_value = mock_session

    result = get_session(session_file=session_file)

    mock_session.login_oauth_simple.assert_called_once()
    mock_session.save_session_to_file.assert_called_once_with(session_file)
    assert result is mock_session


@patch("dynamic_radio.tidal_auth.tidalapi")
def test_get_session_reauths_on_invalid_session(mock_tidalapi, tmp_path: Path):
    """get_session re-authenticates when saved session is invalid."""
    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps({
        "token_type": {"data": "Bearer"},
        "access_token": {"data": "expired"},
        "refresh_token": {"data": "bad"},
    }))

    mock_session = MagicMock()
    mock_session.check_login.side_effect = [False, True]
    mock_session.user.first_name = "TestUser"
    mock_tidalapi.Config.return_value = MagicMock()
    mock_tidalapi.Session.return_value = mock_session

    result = get_session(session_file=session_file)

    mock_session.login_oauth_simple.assert_called_once()
    mock_session.save_session_to_file.assert_called_once_with(session_file)
    assert result is mock_session
