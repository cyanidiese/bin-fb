#!/usr/bin/env bash
# Deploy latest main branch to the VPS.
# Usage: bash scripts/push.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load credentials from .env
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

echo "==> Deploying to $HOST:$DIR"

# Step 1 — Gracefully stop the bot (SIGTERM triggers close_all_open + close_all_orders)
echo "==> Stopping bot gracefully..."
$SSH "$HOST" \
  "docker exec bot-app-1 pkill -SIGTERM -f 'python3 main.py' 2>/dev/null && echo 'Sent SIGTERM to bot' || echo 'Bot was not running'"

# Wait up to 25 s for cleanup (close_all_open + market close of real orders)
echo "==> Waiting for order cleanup..."
$SSH "$HOST" \
  "for i in \$(seq 1 25); do docker exec bot-app-1 pgrep -f 'python3 main.py' > /dev/null 2>&1 || { echo \"Bot exited after \${i}s\"; exit 0; }; sleep 1; done; echo 'Timeout — proceeding anyway'"

# Step 2 — Pull latest code and rebuild container
echo "==> Pulling code and rebuilding container..."
$SSH "$HOST" \
  "cd $DIR && git pull origin main && docker compose up -d --build 2>&1 | tail -30"

# Step 3 — Start bot in the new container
echo "==> Starting bot..."
$SSH "$HOST" \
  "docker exec -d bot-app-1 sh -c 'cd /app && .venv/bin/python3 main.py >> /app/logs/bot.log 2>&1'"

echo "==> Done"
