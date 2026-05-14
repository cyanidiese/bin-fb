#!/usr/bin/env bash
# One-shot setup for a fresh Ubuntu 22.04/24.04 VPS (EU region — required for Binance API).
# Run as root: bash deploy.sh <git-repo-url>
set -euo pipefail

REPO_URL="${1:-}"
INSTALL_DIR="/opt/bot"

if [[ -z "$REPO_URL" ]]; then
  echo "Usage: $0 <git-repo-url>"
  echo "Example: $0 https://github.com/yourname/bin-futures-bot.git"
  exit 1
fi

echo "=== Installing Docker (official repo) ==="
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | tee /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker

echo "=== Cloning repo to $INSTALL_DIR ==="
git clone "$REPO_URL" "$INSTALL_DIR"
cd "$INSTALL_DIR"

echo "=== Preparing environment file ==="
cp .env.example .env

echo ""
echo "============================================================"
echo " Setup complete. Next steps:"
echo ""
echo " 1. Fill in your API credentials:"
echo "      nano $INSTALL_DIR/.env"
echo ""
echo " 2. Build and start:"
echo "      cd $INSTALL_DIR && docker compose up -d --build"
echo ""
echo " 3. Open the dashboard:"
echo "      http://<your-server-ip>:3000"
echo ""
echo " 4. View live logs:"
echo "      docker compose -f $INSTALL_DIR/docker-compose.yml logs -f"
echo "============================================================"
