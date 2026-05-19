#!/usr/bin/env bash
# ============================================================
# NotiTFK — Oracle Cloud Always Free setup (Ubuntu 22.04/24.04)
# Run once on a fresh VM as the default user (ubuntu / opc).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/GregoireF/notiftk/main/infra/oracle/setup.sh | \
#     DOMAIN=notiftk.duckdns.org bash
#
#   Or interactively:
#   export DOMAIN=notiftk.duckdns.org
#   bash infra/oracle/setup.sh
# ============================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/GregoireF/notiftk.git}"
APP_DIR="${APP_DIR:-/opt/notiftk}"
DATA_DIR="${DATA_DIR:-/data/notiftk}"
APP_USER="${APP_USER:-$(whoami)}"
DOMAIN="${DOMAIN:-}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn ]${NC} $*"; }
die()  { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

[ -z "$DOMAIN" ] && die "DOMAIN is required. Export it before running: export DOMAIN=notiftk.duckdns.org"

log "Starting NotiTFK setup for domain: $DOMAIN"

# ── 1. System packages ─────────────────────────────────────────────────────────
log "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3 python3-venv python3-pip \
    git curl wget \
    iptables-persistent \
    --no-install-recommends

# ── 2. Caddy (official repo) ───────────────────────────────────────────────────
if ! command -v caddy &>/dev/null; then
    log "Installing Caddy..."
    sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | sudo tee /etc/apt/sources.list.d/caddy-stable.list
    sudo apt-get update -qq
    sudo apt-get install -y caddy
fi

# ── 3. Oracle iptables fix ─────────────────────────────────────────────────────
# Oracle Cloud Ubuntu blocks ports 80/443 in iptables by default.
log "Opening ports 80 and 443 in iptables..."
sudo iptables -C INPUT -p tcp --dport 80  -j ACCEPT 2>/dev/null || \
    sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || \
    sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

# ── 4. Data directory (persistent volume mount point) ─────────────────────────
log "Creating data directory: $DATA_DIR"
sudo mkdir -p "$DATA_DIR"
sudo chown "$APP_USER:$APP_USER" "$DATA_DIR"

# ── 5. Clone / update app ─────────────────────────────────────────────────────
if [ -d "$APP_DIR/.git" ]; then
    log "Pulling latest code in $APP_DIR..."
    sudo git -C "$APP_DIR" pull --ff-only
else
    log "Cloning repo to $APP_DIR..."
    sudo git clone "$REPO_URL" "$APP_DIR"
    sudo chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

# ── 6. Python virtual environment ─────────────────────────────────────────────
log "Setting up Python venv..."
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip --quiet
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

# ── 7. Environment file ────────────────────────────────────────────────────────
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    log "Creating $ENV_FILE from template..."
    cp "$APP_DIR/.env.example" "$ENV_FILE"
    # Override DB path to persistent volume
    sed -i "s|WEBHOOKS_DB=.*|WEBHOOKS_DB=$DATA_DIR/webhooks.db|" "$ENV_FILE"
    warn ">>> Edit $ENV_FILE and set API_KEYS, ADMIN_SECRET, etc. before starting."
else
    log ".env already exists — keeping existing configuration"
fi

# ── 8. systemd service ─────────────────────────────────────────────────────────
log "Installing systemd service..."
sudo cp "$APP_DIR/infra/oracle/notiftk.service" /etc/systemd/system/notiftk.service
# Patch user and app dir in the service file
sudo sed -i "s|/opt/notiftk|$APP_DIR|g" /etc/systemd/system/notiftk.service
sudo sed -i "s|^User=.*|User=$APP_USER|" /etc/systemd/system/notiftk.service
sudo systemctl daemon-reload
sudo systemctl enable notiftk

# ── 9. Caddy config ────────────────────────────────────────────────────────────
log "Configuring Caddy for domain: $DOMAIN"
sudo tee /etc/caddy/Caddyfile > /dev/null <<EOF
$DOMAIN {
    reverse_proxy localhost:8080
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
    }
    log {
        output file /var/log/caddy/access.log {
            roll_size 10mb
            roll_keep 5
        }
    }
}
EOF
sudo mkdir -p /var/log/caddy
sudo chown caddy:caddy /var/log/caddy
sudo systemctl enable caddy
sudo systemctl restart caddy

# ── 10. Summary ────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  NotiTFK setup complete!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo "Next steps:"
echo "  1. Edit secrets:    nano $ENV_FILE"
echo "     → Set API_KEYS, ADMIN_SECRET, LOG_LEVEL=INFO"
echo "  2. Start the app:   sudo systemctl start notiftk"
echo "  3. Check status:    sudo systemctl status notiftk"
echo "  4. Test locally:    curl http://localhost:8080/health"
echo "  5. Test public:     curl https://$DOMAIN/health"
echo ""
echo "Useful commands:"
echo "  sudo journalctl -u notiftk -f     # live logs"
echo "  sudo journalctl -u caddy -f       # caddy logs"
echo "  sudo systemctl restart notiftk    # restart app"
echo "  bash $APP_DIR/infra/oracle/deploy.sh  # pull + restart"
