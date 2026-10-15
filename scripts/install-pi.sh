#!/bin/bash
# Run this once on a fresh Raspberry Pi to set up Vinyl Detector.
# Usage: bash scripts/install-pi.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE="vinyl-detector"
CURRENT_USER="$(whoami)"

echo "==> Installing system dependencies..."
sudo apt update -q
sudo apt install -y libportaudio2 python3-pip python3-venv git

echo ""
read -rp "Enter your Tuneshine ID (the 4 letters/numbers on the back of your device, e.g. A6E0): " TUNESHINE_ID
read -rp "Golemio API key for tram mode (free at https://api.golemio.cz/api-keys, Enter to skip): " GOLEMIO_KEY
{
  echo "TUNESHINE_HOST=tuneshine-${TUNESHINE_ID}.local"
  echo "GOLEMIO_API_KEY=${GOLEMIO_KEY}"
  echo "PORTAL_PORT=8080"
} > "$REPO_DIR/.env"
echo "    -> Set to tuneshine-${TUNESHINE_ID}.local"
echo ""

echo "==> Setting up Python virtual environment..."
cd "$REPO_DIR"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -q

echo "==> Generating systemd service file..."
cat > "$REPO_DIR/vinyl-detector.generated.service" <<EOF
[Unit]
Description=Tuneshine companion (tram / vinyl modes + portal)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=-$REPO_DIR/.env
ExecStart=$REPO_DIR/.venv/bin/python app.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "==> Installing systemd service..."
sudo cp "$REPO_DIR/vinyl-detector.generated.service" /etc/systemd/system/vinyl-detector.service
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl start "$SERVICE"

echo "==> Granting passwordless sudo for service restart (required for sync script)..."
echo "$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE" | sudo tee /etc/sudoers.d/vinyl-detector > /dev/null

chmod +x "$REPO_DIR/scripts/sync.sh"

echo ""
echo "All done! Vinyl Detector is running. Check status with:"
echo "  sudo systemctl status $SERVICE"
echo ""
echo "To enable auto-sync from GitHub, run:"
echo "  (crontab -l 2>/dev/null; echo \"* * * * * $REPO_DIR/scripts/sync.sh >> $REPO_DIR/sync.log 2>&1\") | crontab -"
