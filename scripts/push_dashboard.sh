#!/usr/bin/env bash
# Deploy dashboard-only changes to the VPS.
# The bot container is never touched — trading continues uninterrupted.
# Usage: bash scripts/push_dashboard.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "ERROR: .env not found at $ROOT_DIR/.env"
  exit 1
fi

set -a
source "$ROOT_DIR/.env"
set +a

HOST="${SERVER_USER:-root}@${SERVER_HOST:?SERVER_HOST not set in .env}"
DIR="${SERVER_DIR:-/opt/bot}"
SSH="ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no"

echo "==> Dashboard-only deploy to $HOST:$DIR"
echo "==> Bot will NOT be restarted."

echo "==> Pulling latest code..."
$SSH "$HOST" "cd $DIR && git reset --hard HEAD && git pull origin main"

echo "==> Rebuilding and restarting dashboard container only..."
$SSH "$HOST" "cd $DIR && docker compose up -d --build --no-deps dashboard 2>&1 | tail -20"

echo "==> Done. Bot is still running."
