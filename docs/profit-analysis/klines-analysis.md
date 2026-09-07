# Klines Analysis — Retrospective Order Analysis

**Load this doc when:** SL is being hit too frequently, TP appears undersized, or you want to understand whether a pattern of losses is structural (wrong entry context) vs parametric (SL/TP values too tight/wide).

**Goal:** Use actual kline data to evaluate whether past orders used the right SL/TP for what the market actually did, and identify pre-entry chart patterns that predict wins vs losses for this bot's presets.

---

## Step 1 — Pull klines for relevant symbols

```bash
# Discover available klines on server
cat /tmp/server_data_index.txt | grep -i kline

# Pull for a specific symbol (replace THETAUSDT_1h with actual filenames)
scp -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no \
  root@185.237.14.105:/opt/bot/data/klines/THETAUSDT_1h.csv /tmp/klines_THETA_1h.csv

scp -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no \
  root@185.237.14.105:/opt/bot/data/klines/1000PEPEUSDT_1h.csv /tmp/klines_PEPE_1h.csv
```

If the klines path differs, check:
```bash
ssh -i ~/.ssh/id_ed25519 root@185.237.14.105 \
  "find /opt/bot -name '*.csv' -o -name '*kline*' 2>/dev/null | head -30"
```

---

## Analysis 1 — SL Retrospective: Was the stop loss too tight?

For each real closed order with `pnl < 0`, check whether the price recovered to the TP target within 10–20 candles after the SL was hit.

**What to extract from logs:**
```bash
# Get all losing real closed orders
grep -E "order_close.*pnl=-|Closed.*loss" /tmp/bot_latest.log | grep -v virtual
```

For each losing trade, you need:
- `entry_price`, `sl_price`, `tp_price` (or `tp_multiplier` from preset to reconstruct)
- `entry_time` (candle timestamp)
- `exit_time` (when SL hit)
- `symbol`, `side` (BUY or SELL)

**Python analysis script (run locally):**
```python
import pandas as pd

# Load klines for the symbol
df = pd.read_csv('/tmp/klines_THETA_1h.csv')
# Expected columns: timestamp, open, high, low, close, volume
# Adjust column names as needed after inspecting the file

# For a specific losing BUY trade:
entry_time = '2026-06-09 22:00:00'  # from log
entry_price = 0.1507
sl_price = 0.1489      # from log (SL that was hit)
tp_price = 0.1557      # from log or compute: entry + (entry - sl) * tp_multiplier

exit_idx = df[df['timestamp'] >= entry_time].index[0]
lookahead = df.loc[exit_idx : exit_idx + 20]  # 20 candles after entry

# BUY: did high ever reach TP?
would_have_hit_tp = (lookahead['high'] >= tp_price).any()
# BUY: what was the max favorable move?
max_favorable = lookahead['high'].max() - entry_price
# BUY: what was the max adverse move before recovery?
max_adverse = entry_price - lookahead['low'].min()

print(f"Would have hit TP: {would_have_hit_tp}")
print(f"Max favorable move: {max_favorable:.4f} ({max_favorable/entry_price*100:.2f}%)")
print(f"SL distance was: {(entry_price - sl_price)/entry_price*100:.2f}%")
```

**Aggregate across all losses:**
```python
# For each losing trade, record:
# - sl_pct: how far SL was from entry
# - recovered_to_tp: did price reach TP within 20 candles after entry?
# - recovery_time: how many candles after entry did price recover?
# - max_favorable_pct: best price reached in direction of trade

results = []
# ... loop over all logged losing trades ...

df_results = pd.DataFrame(results)

# Key question 1: What % of losses would have won with 2x the SL width?
narrow_sl_losses = df_results[df_results['sl_pct'] < 0.5]
print(f"Losses with SL < 0.5%: {len(narrow_sl_losses)}")
print(f"Of those, would have recovered: {narrow_sl_losses['recovered_to_tp'].mean():.1%}")

# Key question 2: What's the average recovery time for trades that DID recover?
recovered = df_results[df_results['recovered_to_tp'] == True]
print(f"Avg recovery time: {recovered['recovery_time'].mean():.1f} candles")
```

**Decision rule from SL analysis:**
- If >40% of losses would have recovered within 10 candles → SL is structurally too tight for this symbol's volatility
- Action: raise `min_sl_pct` for this symbol in `per_symbol_settings`, or raise `global_min_sl_pct`
- If <20% would have recovered → SL size is not the problem; the entry direction is wrong

---

## Analysis 2 — TP Retrospective: Was the take profit too small?

For each real closed order with `pnl > 0` where exit reason was TP hit (not trailing stop), check how far price continued in the trade direction after the TP was filled.

```bash
# Get all winning trades
grep -E "order_close.*pnl=\+|Closed.*profit|partial_take|TP hit" /tmp/bot_latest.log | grep -v virtual
```

```python
# For a BUY trade that hit TP:
tp_exit_time = '2026-06-08 14:00:00'  # when TP was hit
tp_price = 0.1557
entry_price = 0.1507

exit_idx = df[df['timestamp'] >= tp_exit_time].index[0]
lookahead = df.loc[exit_idx : exit_idx + 10]

# How far did price continue after TP?
continuation_pct = (lookahead['high'].max() - tp_price) / tp_price * 100
profit_captured_pct = (tp_price - entry_price) / entry_price * 100

print(f"Profit captured: {profit_captured_pct:.2f}%")
print(f"Price continued another: {continuation_pct:.2f}% after TP")
print(f"Left on table: {continuation_pct:.2f}%")
```

**Decision rule from TP analysis:**
- If average continuation > 50% of captured profit → TP multiplier is too conservative
- Action: raise `tp_multiplier` in the relevant preset, or reduce `partial_take_pct` so less position is closed at the first TP level
- If trailing stop is active for the preset, verify the trailing stop is actually capturing the continuation

---

## Analysis 3 — Entry Pattern Analysis: What context predicts wins vs losses?

For each real trade (win or loss), record the market context at entry time. Find which contexts correlate with wins.

```python
# For each trade, extract from klines at entry_time:

def get_entry_context(df, entry_time, side):
    idx = df[df['timestamp'] >= entry_time].index[0]
    if idx < 4:
        return None
    
    recent = df.loc[idx-4:idx]  # 4 candles before entry
    current = df.loc[idx]
    
    # 1. Short-term trend: is price moving in the trade direction?
    trend_4c = (recent['close'].iloc[-1] - recent['close'].iloc[0]) / recent['close'].iloc[0]
    aligned = (trend_4c > 0 and side == 'BUY') or (trend_4c < 0 and side == 'SELL')
    
    # 2. Volatility: ATR as % of price (avg range of last 4 candles)
    atr_pct = ((recent['high'] - recent['low']) / recent['close']).mean() * 100
    
    # 3. Recent momentum: was previous candle same direction as trade?
    prev_candle_bullish = recent['close'].iloc[-1] > recent['open'].iloc[-1]
    momentum_aligned = (prev_candle_bullish and side == 'BUY') or \
                       (not prev_candle_bullish and side == 'SELL')
    
    # 4. Is price near a recent high/low? (potential reversal zone)
    high_4c = recent['high'].max()
    low_4c = recent['low'].min()
    range_4c = high_4c - low_4c
    price_position = (current['close'] - low_4c) / range_4c if range_4c > 0 else 0.5
    # 0 = at bottom of range, 1 = at top
    
    return {
        'trend_aligned': aligned,
        'trend_4c_pct': trend_4c * 100,
        'atr_pct': atr_pct,
        'momentum_aligned': momentum_aligned,
        'price_position': price_position,  # 0=bottom, 1=top
    }

# Then aggregate:
wins = [t for t in trades if t['pnl'] > 0]
losses = [t for t in trades if t['pnl'] < 0]

wins_context = [get_entry_context(df, t['entry_time'], t['side']) for t in wins]
losses_context = [get_entry_context(df, t['entry_time'], t['side']) for t in losses]

# Compare:
# win_rate when trend_aligned=True vs False
# win_rate when momentum_aligned=True vs False
# win_rate by price_position bucket (bottom 20%, middle 60%, top 20%)
# win_rate by atr_pct bucket (low vol < 0.3%, mid 0.3-0.8%, high > 0.8%)
```

**Decision rules from pattern analysis:**

| Finding | Action |
|---|---|
| Win rate when trend_aligned < 30% | Preset is counter-trend — add `higher_low_buy` or `lower_high_sell` filter, or block preset for this symbol |
| Win rate when atr_pct < 0.3% < 20% | Symbol is in a low-volatility range — raise `min_sl_pct` or skip entry |
| Win rate when price_position > 0.8 and side=BUY < 25% | Buying at range top → adjust proximity_zone_pct or add range_position_max |
| Win rate when momentum_aligned = True > 60% | This is a working pattern — ensure preset doesn't have dup_skip that blocks follow-through |

---

## Analysis 4 — Duration vs Outcome: Are positions held too long or too short?

```bash
# Extract hold durations from log (if timestamps are available at open and close)
grep -E "Order placed:|order_close" /tmp/bot_latest.log | grep -v virtual | tail -200
```

```python
# For each trade pair (open → close), compute:
# - hold_candles: how many candles the position was open
# - outcome: win or loss
# - exit_reason: SL, TP, trailing_stop, market_close, max_losing

# Then:
# Average hold time for wins vs losses
# % of losses that were closed by max_losing_candles (too early cut)
# % of losses that were closed by market_close (bot restart, not strategy)
```

**If market_close exits account for >20% of losses:** These are artificial losses from restarts.
Minimize restarts. Never deploy without batching all pending code changes into one deploy.

**If most losses hit SL within 2 candles:** Structural entry problem, not SL width.
The signal is firing into momentum against the trade direction.

---

## Reporting Format

After running any of the above analyses, report as:

```
KLINES FINDING [SL/TP/ENTRY/DURATION]:
Symbol: THETAUSDT  |  Preset: pre_confirm_trail15  |  Side: SELL  |  Trades analyzed: 17
---
Observation: 12 of 17 losses would have recovered to TP within 8 candles after SL hit
Current avg SL: 0.40%  |  Required SL to survive avg adverse move: 0.82%
Trend alignment: 4 of 17 entries were trend-aligned — 76% were counter-trend SELLs during uptrend

Recommendation: Raise per_symbol_settings.THETAUSDT.min_sl_pct to 1.0
               OR block SELL entries on THETAUSDT when 4c trend is positive
Estimated impact: Would have converted ~8 losses (~$23) to wins if SL had been wider
Confidence: HIGH — 17 trades, clear directional pattern
```
