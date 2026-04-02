#!/bin/bash
# Polls GitHub for changes and restarts the service if there are any.
# Set up as a cron job: * * * * * /home/raspberrypi/vinyl-detector/scripts/sync.sh >> /home/raspberrypi/vinyl-detector/sync.log 2>&1

set -e

REPO_DIR="/home/raspberrypi/vinyl-detector"
SERVICE="vinyl-detector"

cd "$REPO_DIR"

git fetch origin

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[$(date)] Changes detected — pulling and restarting..."
    git pull origin main
    .venv/bin/pip install -r requirements.txt -q
    sudo systemctl restart "$SERVICE"
    echo "[$(date)] Done."
else
    echo "[$(date)] Already up to date."
fi
