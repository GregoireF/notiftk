#!/usr/bin/env bash
# Migrate SQLite database from Fly.io to Oracle Cloud.
# Run this locally (on your dev machine), then copy the DB to Oracle.
#
# Prerequisites:
#   - flyctl installed and authenticated (fly auth login)
#   - ssh access to Oracle VM configured
#
# Usage:
#   ORACLE_HOST=<oracle-public-ip> bash infra/oracle/migrate-from-fly.sh
set -euo pipefail

FLY_APP="${FLY_APP:-notiftk}"
FLY_DB_PATH="${FLY_DB_PATH:-/data/webhooks.db}"
LOCAL_BACKUP="webhooks-fly-backup-$(date +%Y%m%d-%H%M%S).db"
ORACLE_HOST="${ORACLE_HOST:-}"
ORACLE_USER="${ORACLE_USER:-ubuntu}"
ORACLE_DB_PATH="${ORACLE_DB_PATH:-/data/notiftk/webhooks.db}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[migrate]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn  ]${NC} $*"; }
die()  { echo -e "${RED}[error ]${NC} $*" >&2; exit 1; }

[ -z "$ORACLE_HOST" ] && die "ORACLE_HOST is required. export ORACLE_HOST=<oracle-public-ip>"

# ── Step 1: Export from Fly.io ─────────────────────────────────────────────────
log "Exporting SQLite DB from Fly.io app: $FLY_APP"
log "This will open an SFTP session to download $FLY_DB_PATH..."

fly sftp get -a "$FLY_APP" "$FLY_DB_PATH" "$LOCAL_BACKUP"

log "Downloaded: $LOCAL_BACKUP ($(du -sh "$LOCAL_BACKUP" | cut -f1))"

# ── Step 2: Verify integrity ───────────────────────────────────────────────────
if command -v sqlite3 &>/dev/null; then
    log "Verifying SQLite integrity..."
    sqlite3 "$LOCAL_BACKUP" "PRAGMA integrity_check;" | grep -q "ok" && log "Integrity: OK" || warn "Integrity check failed — inspect the file before importing"
else
    warn "sqlite3 not found locally — skipping integrity check"
fi

# ── Step 3: Stop the service on Oracle ────────────────────────────────────────
log "Stopping NotiTFK on Oracle Cloud..."
ssh "$ORACLE_USER@$ORACLE_HOST" "sudo systemctl stop notiftk || true"

# ── Step 4: Copy DB to Oracle ─────────────────────────────────────────────────
log "Copying DB to $ORACLE_USER@$ORACLE_HOST:$ORACLE_DB_PATH..."
ssh "$ORACLE_USER@$ORACLE_HOST" "sudo mkdir -p $(dirname "$ORACLE_DB_PATH") && sudo chown $ORACLE_USER:$ORACLE_USER $(dirname "$ORACLE_DB_PATH")"
scp "$LOCAL_BACKUP" "$ORACLE_USER@$ORACLE_HOST:$ORACLE_DB_PATH"

# ── Step 5: Restart service on Oracle ─────────────────────────────────────────
log "Starting NotiTFK on Oracle Cloud..."
ssh "$ORACLE_USER@$ORACLE_HOST" "sudo systemctl start notiftk && sleep 2 && curl -sf http://localhost:8080/health | python3 -m json.tool"

echo ""
log "Migration complete!"
log "Local backup kept at: $LOCAL_BACKUP"
warn "You can now remove the Fly.io app when you're satisfied: fly apps destroy $FLY_APP"
