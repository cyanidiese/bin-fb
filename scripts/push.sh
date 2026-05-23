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
# Loop over PIDs individually — the container has no standalone kill binary, only the shell builtin.
echo "==> Stopping bot gracefully..."
$SSH "$HOST" \
  "docker exec bot /bin/sh -c 'for PID in \$(grep -rl main.py /proc/*/cmdline 2>/dev/null | grep -o \"[0-9]*\"); do kill -TERM \$PID 2>/dev/null && echo \"Sent SIGTERM to \$PID\"; done' 2>/dev/null || echo 'Bot was not running'"

# Wait up to 25 s for cleanup (close_all_open + market close of real orders)
echo "==> Waiting for order cleanup..."
$SSH "$HOST" \
  "for i in \$(seq 1 25); do PIDS=\$(docker exec bot /bin/sh -c 'grep -rl main.py /proc/*/cmdline 2>/dev/null | grep -o \"[0-9]*\"' 2>/dev/null); [ -z \"\$PIDS\" ] && echo \"All bot instances exited after \${i}s\" && break; sleep 1; done; echo 'Proceeding with deploy'"

# Explicitly stop the bot container to prevent restart: unless-stopped from
# relaunching main.py on the old image during the git pull + docker build cycle.
echo "==> Stopping bot container..."
$SSH "$HOST" "docker stop bot 2>/dev/null || true"

# Step 2 — Reset any server-side runtime file modifications, pull latest code and rebuild container
echo "==> Pulling code and rebuilding container..."
$SSH "$HOST" \
  "cd $DIR && git reset --hard HEAD && git clean -f dashboard/public/ && git pull origin main && docker compose up -d --build 2>&1 | tail -30"

echo "==> Done"
