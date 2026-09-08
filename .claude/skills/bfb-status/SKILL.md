---
name: bfb-status
description: >
  Quick status check for the Binance Futures bot. Trigger on: "what's now?",
  "check the bot", "is the bot running?", "what's happening?", "any open positions?",
  "any signals?", "what's the bot doing?".
---

# BFB — Quick Status Check

SSH alias: `ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105`

## Run these 4 commands in parallel

```bash
# 1. Container health
ssh ... "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.RunningFor}}'"

# 2. Last meaningful log lines (skip cache noise)
ssh ... "grep -v 'Kline cache\|Cache has' /opt/bot/logs/bot.log | tail -12"

# 3. Open real positions
ssh ... "cat /opt/bot/data/open_positions_test.json 2>/dev/null | python3 -m json.tool 2>/dev/null || echo 'none'"

# 4. Last 5 trades log entries
ssh ... "tail -5 /opt/bot/logs/trades.log"
```

## Report format

- Bot up/down + uptime
- Open positions: symbol, side, entry, SL, TP, preset
- Last signal type seen (from trades.log CANDIDATE/BEST entries)
- Any ERROR lines in recent log

## Current active symbols (update when changed)

| Symbol | Weight | Locked preset |
|---|---|---|
| SOLUSDT | 20 | — |
| TIAUSDT | 15 | hl_buy_trail15 |
| MEMEUSDT | 8 | sl_adjust_rr_tp95 |
| EIGENUSDT | 5 | lh_sell_trail15 |
| DOGEUSDT | 1 | r6_arm15_rr4 |

Global filters active: `trend_regime_filter`, `min_rr=3.0`, `max_rr=4.0`, `min_sl_pct=0.7`,
`entry_zone_max_pct=0.75`, `trading_blackout_hours=[17,18,19]`

## Log path note

Container writes to `/app/logs/` — host mounts it at `/opt/bot/logs/`. Both paths work.
For inside-container commands: `docker exec bot tail -10 /app/logs/bot.log`
