#!/usr/bin/env bash
# Download bot logs and state files from the VPS to your local machine.
# Usage: bash download-logs.sh <user@host> [remote-dir]
set -euo pipefail

HOST="${1:-}"
REMOTE_DIR="${2:-/opt/bot}"

if [[ -z "$HOST" ]]; then
  echo "Usage: $0 <user@host> [remote-dir]"
  echo "Example: $0 root@123.456.789.0"
  echo "         $0 root@123.456.789.0 /opt/bot"
  exit 1
fi

OUT="./bot-export-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT/logs" "$OUT/data"

echo "Connecting to $HOST..."

echo "→ Downloading logs/"
rsync -avz --progress "$HOST:$REMOTE_DIR/logs/" "$OUT/logs/"

echo "→ Downloading data/ (state files, excluding kline cache and raw backtests)"
rsync -avz --progress \
  --include="*.json" \
  --exclude="*_15m_*.json" \
  --exclude="backtest_*.json" \
  --exclude="discovery/" \
  "$HOST:$REMOTE_DIR/data/" "$OUT/data/" || true

echo ""
echo "Done. Export saved to: $OUT"
echo ""
du -sh "$OUT/logs" "$OUT/data" 2>/dev/null
