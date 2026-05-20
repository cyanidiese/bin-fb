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

# Step 1 — Gracefully stop ALL bot instances (SIGTERM triggers close_all_open + close_all_orders)
# Use /proc to find PIDs since the slim container has no pkill/pgrep.
# Kill ALL matching PIDs (not just head -1) to prevent stale instances accumulating across deploys.
echo "==> Stopping bot gracefully..."
$SSH "$HOST" \
  "PIDS=\$(docker exec bot-app-1 /bin/sh -c 'grep -rl main.py /proc/*/cmdline 2>/dev/null | grep -o \"[0-9]*\"' 2>/dev/null); if [ -n \"\$PIDS\" ]; then docker exec bot-app-1 /bin/sh -c \"kill -TERM \$PIDS\" 2>/dev/null && echo \"Sent SIGTERM to PIDs: \$PIDS\"; else echo 'Bot was not running'; fi"

# Wait up to 25 s for cleanup (close_all_open + market close of real orders)
echo "==> Waiting for order cleanup..."
$SSH "$HOST" \
  "for i in \$(seq 1 25); do PIDS=\$(docker exec bot-app-1 /bin/sh -c 'grep -rl main.py /proc/*/cmdline 2>/dev/null | grep -o \"[0-9]*\"' 2>/dev/null); [ -z \"\$PIDS\" ] && echo \"All bot instances exited after \${i}s\" && break; sleep 1; done; echo 'Proceeding with deploy'"

# Step 2 — Reset any server-side runtime file modifications, pull latest code and rebuild container
echo "==> Pulling code and rebuilding container..."
$SSH "$HOST" \
  "cd $DIR && git reset --hard HEAD && git clean -f dashboard/public/ && git pull origin main && docker compose up -d --build 2>&1 | tail -30"

# Step 3 — Start bot in the new container
echo "==> Starting bot..."
$SSH "$HOST" \
  "docker exec -d bot-app-1 sh -c 'cd /app && .venv/bin/python3 main.py >> /app/logs/bot.log 2>&1'"

echo "==> Done"
