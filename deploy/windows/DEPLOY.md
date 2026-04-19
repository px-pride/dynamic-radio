# Dynamic Radio Windows Deployment

## Prerequisites

1. **Python 3.14+** — [python.org](https://www.python.org/downloads/)
2. **mpv** — [mpv.io](https://mpv.io/installation/) (add to PATH)
3. **Tailscale** — must be running (note the machine's Tailscale IP for remote access)
4. **Windows firewall** — allow inbound TCP 8420 (for Axi HTTP API)

## Setup

```powershell
# Clone or copy the project
cd C:\Users\%USERNAME%\projects
git clone <repo-url> dynamic-radio   # or copy from 127.0.0.1

# Run install script
cd dynamic-radio\deploy\windows
install.bat
```

## Tidal Authentication

First run requires OAuth login:

```powershell
cd C:\Users\%USERNAME%\projects\dynamic-radio
uv run python -c "from dynamic_radio.tidal_auth import get_session; s = get_session(); print('Logged in as', s.user.name)"
```

Follow the OAuth URL in the browser. The session is saved to `~/.local/share/dynamic-radio/tidal_session.json`.

## Running

```powershell
# Foreground (for testing)
uv run dynamic-radio

# Background (minimized window)
deploy\windows\start.bat

# Stop
deploy\windows\stop.bat
```

## Auto-Start on Boot

1. Press `Win+R`, type `shell:startup`
2. Create a shortcut to `deploy\windows\start.bat` in that folder

## Voicemeeter Potato

mpv uses the Windows default audio output. To route through Voicemeeter:

- **Option A**: Set Voicemeeter input as the default playback device in Windows Sound Settings
- **Option B**: Pass a specific device: `uv run dynamic-radio --audio-output wasapi` and configure mpv's `--audio-device` in a local mpv.conf

## Verify

From 127.0.0.1 (replace `<TAILSCALE_IP>` with the machine's Tailscale IP):
```bash
curl http://<TAILSCALE_IP>:8420/health
# Should return: {"ok": true}

curl http://<TAILSCALE_IP>:8420/status
# Should return current DJ status JSON
```

## Firewall Rule

If the API is unreachable from 127.0.0.1:

```powershell
# Run as Administrator
netsh advfirewall firewall add rule name="Dynamic Radio API" dir=in action=allow protocol=TCP localport=8420
```
