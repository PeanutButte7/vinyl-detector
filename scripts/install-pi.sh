#!/bin/bash
# Run this once on a fresh Raspberry Pi to set up Vinyl Detector.
# Usage: bash scripts/install-pi.sh

set -e

REPO_DIR="/home/raspberrypi/vinyl-detector"
SERVICE="vinyl-detector"

echo "==> Installing system dependencies..."
sudo apt update -q
sudo apt install -y libportaudio2 python3-pip python3-venv git

echo ""
read -rp "Enter your Tuneshine ID (the 4 letters/numbers on the back of your device, e.g. A6E0): " TUNESHINE_ID
echo "TUNESHINE_HOST=tuneshine-${TUNESHINE_ID}.local" > "$REPO_DIR/.env"
echo "    -> Set to tuneshine-${TUNESHINE_ID}.local"
echo ""

echo "==> Setting up Python virtual environment..."
cd "$REPO_DIR"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -q

echo "==> Installing systemd service..."
sudo cp "$REPO_DIR/vinyl-detector.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl start "$SERVICE"

echo "==> Granting passwordless sudo for service restart (required for sync script)..."
echo "raspberrypi ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE" | sudo tee /etc/sudoers.d/vinyl-detector > /dev/null

chmod +x "$REPO_DIR/scripts/sync.sh"

echo ""
echo "All done! Vinyl Detector is running. Check status with:"
echo "  sudo systemctl status $SERVICE"
echo ""
echo "To enable auto-sync from GitHub, run:"
echo "  (crontab -l 2>/dev/null; echo \"* * * * * $REPO_DIR/scripts/sync.sh >> $REPO_DIR/sync.log 2>&1\") | crontab -"
