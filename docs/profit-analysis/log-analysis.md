# Log Analysis Protocol

**Load this doc when:** running detailed log queries beyond what the parallel agent dispatch covers, or when investigating a specific symbol or time window.

---

## Essential Grep Patterns

All commands run against `/tmp/bot_latest.log` (pulled locally via SCP — do not SSH for this).

```bash
# Real orders placed (ground truth — not virtual)
grep -E "Real order placed|Order placed:.*preset=" /tmp/bot_latest.log | grep -v virtual

# Closed orders with PnL
grep -E "order_close|pnl=|[Cc]losed.*pnl" /tmp/bot_latest.log | grep -v virtual

# Balance snapshots
grep -E "balance=|Balance:|bal=[0-9]" /tmp/bot_latest.log | tail -100

# Bot restarts (each wipes streak state)
grep -E "Starting bot|Bot started|Connecting to|streak state loaded|Streak state" /tmp/bot_latest.log

# Protection blocks firing
grep -E "loss streak|dup.*skip|streak blocked|cooldown" /tmp/bot_latest.log | tail -200

# Profit factor gate
grep -E "profit_factor|pf=.*below|pf=.*threshold" /tmp/bot_latest.log | tail -100

# SL floor applied
grep -E "SL floored|min_sl" /tmp/bot_latest.log | tail -100

# Blocklist hits
grep -E "blocklisted|preset_blocklist|blocklist" /tmp/bot_latest.log | tail -50

# Preset selection by virtual tracker
grep -E "best preset|Using preset|preset selected|locked preset" /tmp/bot_latest.log | tail -100

# For a specific symbol (e.g., THETAUSDT)
grep "THETAUSDT" /tmp/bot_latest.log | grep -E "order|preset|streak|pnl" | tail -100
```

---

## Aggregating P&L by Symbol (without sub-agent)

If sub-agents are unavailable, compute this directly:

```bash
# Extract all closed real order lines
grep -E "order_close|pnl=" /tmp/bot_latest.log | grep -v virtual > /tmp/closed_orders.txt

# Count wins vs losses per symbol (adjust regex to match actual log format)
grep "THETAUSDT" /tmp/closed_orders.txt | grep -c "pnl=-"   # losses
grep "THETAUSDT" /tmp/closed_orders.txt | grep -c "pnl=+"   # wins

# Sum PnL values (requires awk — adjust field positions to match log format)
grep "THETAUSDT" /tmp/closed_orders.txt | grep -oP "pnl=[-+]?\d+\.?\d*" | \
  awk -F= '{sum += $2} END {printf "THETAUSDT net: %.2f\n", sum}'
```

---

## Diagnosing a Specific Loss

When a loss stands out (> $10 or part of a streak), trace the full sequence:

1. **Entry signal**: What kline close triggered the signal?
2. **SL distance**: Was it natural or was `global_min_sl_pct` applied?
   ```bash
   grep -A5 "SYMBOL.*SIDE.*ORDER_TIME" /tmp/bot_latest.log
   ```
3. **Preset selection**: Was this the virtual tracker's best preset, or a fallback?
   ```bash
   grep "SYMBOL.*best preset\|SYMBOL.*preset selected" /tmp/bot_latest.log | grep "ORDER_TIME_WINDOW"
   ```
4. **Prior streak**: Were there previous losses on the same symbol:side before this entry?
   ```bash
   grep "SYMBOL.*SIDE" /tmp/bot_latest.log | grep -E "order_close|streak" | tail -20
   ```
5. **Restart before entry**: Was the bot restarted recently (check for "streak state loaded" or absence)?
6. **Hold duration**: Time between order open and order close (< 2 candles = structural bad entry)
7. **Exit reason**: SL, TP, trailing stop, `market_close`, `max_losing_candles`?

---

## Identifying Trend Misalignment

When you see a cluster of same-side losses:

```bash
# Check if losses are concentrated on one side
grep "pnl=-" /tmp/bot_latest.log | grep -v virtual | \
  grep -oP "(BUY|SELL)" | sort | uniq -c

# Check which symbols had all-loss runs (no wins in between)
grep -E "Order placed|order_close.*pnl" /tmp/bot_latest.log | grep "SYMBOL" | \
  grep -v virtual | tail -30
```

If all losses are SELL on a symbol that was trending up during that period, the preset fires SELL signals into upward momentum. No SL width change fixes this — the preset or entry filter needs changing.

---

## Checking Streak Protection Effectiveness

```bash
# How many streak blocks fired per symbol?
grep "loss streak.*blocked\|streak cooldown" /tmp/bot_latest.log | \
  grep -oP "\[.*?\]" | sort | uniq -c | sort -rn

# After each block, did the bot re-enter after cooldown?
grep "THETAUSDT.*streak\|THETAUSDT.*Order placed" /tmp/bot_latest.log | tail -30

# Were there 3+ consecutive losses with NO streak block in between?
grep "THETAUSDT.*SELL" /tmp/bot_latest.log | grep -E "order_close|loss streak" | tail -30
```

If 3+ consecutive losses occurred with no streak block logged: either `loss_streak_max` is not set on that preset (check `config/presets.py`) or streak state was reset by a restart (check for restart events before the streak).

---

## Checking Virtual Tracker Routing

```bash
# What presets is the virtual tracker selecting for each symbol?
grep -E "best preset|preset.*selected|Using.*preset" /tmp/bot_latest.log | tail -100

# Is the virtual tracker switching presets frequently?
grep "best preset" /tmp/bot_latest.log | grep "THETAUSDT" | tail -20

# Are locked presets overriding virtual tracker?
grep "locked preset\|manually locked" /tmp/bot_latest.log | tail -20
```

Frequent preset switches are a sign the virtual tracker's `recent_trades` window is too small — scores are noisy. If you see a symbol switching presets every 2–3 trades, the window needs to be larger.
