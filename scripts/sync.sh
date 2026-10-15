#!/bin/bash
# Polls GitHub for changes and restarts the service if there are any.
# Set up as a cron job: * * * * * /path/to/vinyl-detector/scripts/sync.sh >> /path/to/vinyl-detector/sync.log 2>&1

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
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
