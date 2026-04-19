#!/bin/bash
# Dynamic Radio Linux deployment setup (127.0.0.1)
# Run with: sudo bash setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$HOME/.local/share/dynamic-radio"

echo "=== Dynamic Radio Linux Setup ==="

# Install icecast if missing
if ! command -v icecast &>/dev/null; then
    echo "Installing icecast..."
    pacman -S --noconfirm icecast
else
    echo "icecast already installed"
fi

# Ensure PipeWire + PulseAudio compatibility layer
for pkg in pipewire pipewire-pulse; do
    if ! pacman -Q "$pkg" &>/dev/null; then
        echo "Installing $pkg..."
        pacman -S --noconfirm "$pkg"
    else
        echo "$pkg already installed"
    fi
done

# Create data directories
mkdir -p "$DATA_DIR/plans"
mkdir -p /tmp/icecast

# Copy icecast config
echo "Installing icecast config..."
mkdir -p "$HOME/.config/dynamic-radio"
cp "$SCRIPT_DIR/icecast.xml" "$HOME/.config/dynamic-radio/icecast.xml"

# Install systemd user services
echo "Installing systemd user services..."
mkdir -p "$HOME/.config/systemd/user"
cp "$SCRIPT_DIR/dynamic-radio.service" "$HOME/.config/systemd/user/"
cp "$SCRIPT_DIR/dynamic-radio-icecast.service" "$HOME/.config/systemd/user/"

systemctl --user daemon-reload
systemctl --user enable dynamic-radio-icecast.service
systemctl --user enable dynamic-radio.service

echo ""
echo "=== Setup complete ==="
echo ""
echo "Start services:"
echo "  systemctl --user start dynamic-radio-icecast"
echo "  systemctl --user start dynamic-radio"
echo ""
echo "Stream URL: http://<tailscale-ip>:8000/dynamicradio"
echo ""
echo "Make sure PipeWire is running:"
echo "  systemctl --user start pipewire pipewire-pulse"
