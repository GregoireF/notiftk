#!/usr/bin/env bash
# Pull latest code and restart — run on the Oracle VM.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/notiftk}"

echo "[deploy] Pulling latest code..."
git -C "$APP_DIR" pull --ff-only

echo "[deploy] Updating dependencies..."
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

echo "[deploy] Restarting service..."
sudo systemctl restart notiftk

echo "[deploy] Status:"
sudo systemctl status notiftk --no-pager -l

echo ""
echo "[deploy] Health check:"
sleep 2
curl -sf http://localhost:8080/health | python3 -m json.tool || echo "Health check failed — check logs: journalctl -u notiftk -n 50"
