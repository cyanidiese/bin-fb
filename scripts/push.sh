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

echo "==> Deploying to $HOST:$DIR"
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no \
  "$HOST" \
  "cd $DIR && git pull origin main && docker compose up -d --build 2>&1 | tail -30"

echo "==> Done"
