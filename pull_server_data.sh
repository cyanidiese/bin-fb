#!/usr/bin/env bash
# Pull all bot data from VPS for local analysis.
# Usage: ./pull_server_data.sh
# Output: ./server_data/ directory

set -euo pipefail

SSH_KEY="$HOME/.ssh/id_ed25519"
HOST="root@185.237.14.105"
REMOTE_BOT="/opt/bot"
LOCAL_DIR="$(dirname "$0")/server_data"

mkdir -p "$LOCAL_DIR/data" "$LOCAL_DIR/logs"

echo "==> Pulling data files..."
rsync -az -e "ssh -i $SSH_KEY" \
  --include="*.json" \
  --exclude="*_15m*.json" \
  --exclude="*_15m.json" \
  "$HOST:$REMOTE_BOT/data/" "$LOCAL_DIR/data/"

echo "==> Pulling logs (last 50k lines each)..."
ssh -i "$SSH_KEY" "$HOST" "tail -n 50000 $REMOTE_BOT/logs/bot.log"    > "$LOCAL_DIR/logs/bot.log"    2>/dev/null || true
ssh -i "$SSH_KEY" "$HOST" "tail -n 50000 $REMOTE_BOT/logs/trades.log" > "$LOCAL_DIR/logs/trades.log" 2>/dev/null || true

echo "==> Pulling registry and config..."
scp -i "$SSH_KEY" -q "$HOST:$REMOTE_BOT/symbol_registry.json"  "$LOCAL_DIR/" 2>/dev/null || true
scp -i "$SSH_KEY" -q "$HOST:$REMOTE_BOT/risk_config.json"       "$LOCAL_DIR/" 2>/dev/null || true

echo "==> Done. Files in $LOCAL_DIR"
ls -lh "$LOCAL_DIR/data/" | head -30
echo "---"
ls -lh "$LOCAL_DIR/logs/"
