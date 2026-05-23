# Trend Cross-Level Validation — Analysis & Brainstorm
**Date**: 2026-05-22  
**Status**: Analysis only, no implementation yet

---

## 1. Klines Not Updating After Local Backtest (Root Cause)

### Symptom
`data/*_15m_test.json` files for active symbols (TIAUSDT, DOGEUSDT, 1000PEPEUSDT, REZUSDT, ETHFIUSDT, etc.) stuck at May 14 modification date, even though `backtest.py` was run multiple times on May 21–22.

### Root Cause
The failure is **silent**. In `backtest.py`:

```python
try:
    feed.refresh_klines(symbol, timeframe, fetch_count=klines_count)
    logger.info(f"[{symbol}] Kline cache refreshed from API")
except Exception as e:
    logger.warning(f"[{symbol}] Could not refresh klines: {e} — using existing cache")
```

When run from the dashboard, the process is spawned as:
```javascript
spawn(python, args, { cwd: BOT_ROOT, detached: true, stdio: 'ignore' })
```

`stdio: 'ignore'` means the warning goes nowhere. The backtest proceeds on stale data, the cache file is never written.

### Why Did The Fetch Fail?
The Binance Futures testnet (`testnet.binancefuture.com`) is not a production system — it has intermittent outages and tighter rate limits. The dashboard always passes `--mode test` (reads from `data/bot_mode.json`), forcing kline fetching from the testnet. When the testnet was unavailable during those backtest runs, `_fetch` raised an exception, which was silently caught.

### Verification
- Manual `DataFeed._fetch('TIAUSDT', '15m', limit=10)` works right now → 10 candles up to May 22 10:30 UTC
- `refresh_klines('TIAUSDT', '15m', fetch_count=1500)` run manually → merged 2190 (cached Apr 21–May 14) + 1500 fresh (May 6–22) → **2935 candles, written to file**
- **Side effect**: `data/TIAUSDT_15m_test.json` is now updated (Apr 21 → May 22)

### File State After Investigation
| File | Before | After |
|------|--------|-------|
| `TIAUSDT_15m_test.json` | 2190 candles, ends May 14 | **2935 candles, ends May 22** |
| Other active symbols | 2190 candles, ends May 14 | **Unchanged** (run backtest locally to update) |

### Symbols Updated May 18 (APTUSDT, 1000SHIBUSDT, INJUSDT, JUPUSDT)
These are **newly-added bot symbols** first initialized on the server around May 18 (session 18 restart). Their kline files were synced to local when those symbols joined the bot — not from local backtest runs. This explains why they have May 18 dates while older symbols stay at May 14.

### Immediate Fix
Run `python backtest.py` locally right now (testnet is accessible) — it will refresh all symbol caches. For a permanent fix, the dashboard backtest could pass `--mode live` for kline fetching since klines are public data and the production API is far more stable.

---

## 2. Current Trend Building — How L1→L2 Works

### Point Passing Mechanism

L2 receives points **only** when L1 experiences a BoS (Break of Structure):

**L1 ASC → DESC** (`checkIfLowerThanAscBreakOfStructure`):
```
Close < L1's ASC BoS (last L1 Low) →
  time_of_last_low = L2.getTimeOfLastLow()
  highest_since = L1.findHighestSince(time_of_last_low)   ← peak of the rally
  L2.setHighPoint(highest_since)
  L1.removePointsUpTo(highest_since.getTime())            ← prune L1 history
```

**L1 DESC → ASC** (`checkIfHigherThanDescBreakOfStructure`):
```
Close > L1's DESC BoS (last L1 High) →
  time_of_last_high = L2.getTimeOfLastHigh()
  lowest_since = L1.findLowestSince(time_of_last_high)    ← bottom of the correction
  L2.setLowPoint(lowest_since)
  L1.removePointsUpTo(lowest_since.getTime())
```

**Key property**: L2 is **frozen between L1 BoS events**. L1 can make many swing points — L2 only updates when L1 formally reverses.

L2 itself propagates the same way to L3 when L2's own BoS fires.

---

## 3. The Gap — Cross-Level BoS Violations

### Scenario A — Deep L1 correction silently violates L2 ASC structure

```
L2: ASC, BoS anchored at price 100 (last L2 Low)
L1: DESC correction (pullback within L2's uptrend)
  L1 Low = 102  → fine, above L2 BoS
  L1 Low = 99   → BELOW L2 BoS of 100! L2 ASC is broken.
  L1 Low = 96   → deeper, L1 still just DESC with no internal reversal

Current behavior:
  L2 stays ASC indefinitely.
  L2 only flips when L1 reverses (close above L1's DESC BoS) AND
  passes lowest_since (96) to L2.
  
Problem:
  If L1 never formally reverses (just keeps LH/LL without triggering
  its own BoS), L2 is permanently frozen in the wrong state.
  Even when L1 does reverse, L2 flips AFTER recovery — the early
  signal (when price first crossed 100) is missed.
```

### Scenario B — L1 bounce high breaks L2 DESC resistance

```
L2: DESC, BoS at price 100 (last L2 High)
L1: ASC bounce within L2's downtrend
  L1 High = 98  → fine
  L1 High = 103 → ABOVE L2 DESC BoS of 100! Resistance broken.

Current: L2 stays DESC. Invisible to L2 until L1 breaks down again.
```

### Impact on Recommendations

When L2 trend state is stale:
- **Precision score corrupted**: `parent alignment` component gives wrong bonus/penalty
- **Wrong SL for L2/L3 signals**: BoS level used as stop-loss is stale
- **`getSupposedNextPoints()` projects from wrong anchors**
- **False signals**: L1 BUY fires during actual L2 DESC (which L2 hasn't detected yet); precision score gives this a neutral/positive parent alignment score instead of penalizing it

From TIAUSDT backtest (trail_15_from_15: 105 trades, 66.7% win, +44.21%): the 35 losses (33%) likely include cases where L1 entered against an undetected L2 reversal.

---

## 4. Proposed Enhancement — Early Parent Notification

### Core Idea
At every new L1 High or Low point, **also check if that point crosses a parent (L2, L3) BoS level directly**. If it does, immediately pass the extremal L1 point (same data as a normal L1 BoS would pass) to the parent.

### Pseudocode

```python
def check_parent_bos_from_new_point(self, point: Point) -> None:
    """Called from setHighPoint / setLowPoint after adding the point to L1."""
    if not self.hasBiggerTrend():
        return
    parent = self.getBiggerTrend()
    if not parent.hasDefinedTrend() or not parent.hasBreakOfStructure():
        return

    if parent.isAscending() and point.getLowValue() < parent.getBreakOfStructure():
        # L1 Low broke L2 ASC BoS → early notification
        time_of_last_high = parent.getTimeOfLastHigh()
        lowest_since = self.findLowestSince(time_of_last_high)
        if lowest_since is not None:
            parent.setLowPoint(lowest_since)   # same as normal L1 BoS would do

    elif parent.isDescending() and point.getHighValue() > parent.getBreakOfStructure():
        # L1 High broke L2 DESC BoS → early notification
        time_of_last_low = parent.getTimeOfLastLow()
        highest_since = self.findHighestSince(time_of_last_low)
        if highest_since is not None:
            parent.setHighPoint(highest_since)  # same as normal L1 BoS would do

    # Optionally recurse: after parent processes, it may now also violate L3
    # (handled automatically because parent.setLowPoint / setHighPoint
    #  already contains the parent→grandparent propagation logic)
```

This uses **the same extremal-point mechanism** as normal L1 BoS — not raw L1 point injection. L2 gets the same quality data, just triggered earlier.

---

## 5. Design Options

| Option | Trigger condition | Notes |
|--------|-------------------|-------|
| **A — Close crossing (recommended)** | L1 candle CLOSE crosses parent BoS | Matches existing filter in `checkIfHigherThanDescBreakOfStructure`. Avoids wick noise. |
| **B — High/Low crossing** | L1 candle wick (high or low) crosses parent BoS | Earlier detection, more false positives near round numbers. |
| **C — Hybrid (current + threshold)** | Both L1 point crosses parent BoS AND L1's direction agrees | Essentially the current system with an extra check, minimal gain. |

**Recommended start**: Option A (close-only), matching the existing BoS confirmation standard already in the codebase.

---

## 6. Edge Cases to Design Around

### Double-update when L1 BoS fires after early trigger

```
Early trigger fires: L1 Low=99 → passes lowest_since=99 to L2
Later: L1 normal BoS fires → finds lowest_since=97 (even lower) → also passes to L2
```
L2's `setLowPoint` handles this gracefully — 97 < 99 so it replaces the low. No pruning issue because the early trigger **does NOT call `removePointsUpTo`** — only the normal L1 BoS does. This is intentional.

### Cascade effect to L3

When L2 receives the early notification and its own BoS fires as a result, L2 naturally propagates to L3 via existing code. No extra logic needed — it's already recursive.

### L1 that never formally reverses

If L1 correction crosses L2 BoS → early notification fires → L2 flips DESC. Then L1 continues making LH/LL without ever triggering L1's own internal BoS. L1 pruning never happens. This is **fine** — L1 history accumulates normally. The next L1 BoS event (whenever it comes) will still prune. The early notification just gave L2 a timely update.

---

## 7. What Has NOT Been Decided Yet

- Should the check use close-price or high/low (wick)? → Open
- Should L3 also check against L4 directly? → Likely yes (same logic recurses naturally)
- How does this affect backtest results — need to measure win rate delta before/after
- The existing `removePointsUpTo` / prune timing is unchanged; confirm no history corruption

---

## 8. Next Steps (pending user approval)

1. Decide on trigger condition (Option A vs B)
2. Write a plan via `writing-plans` skill
3. Implement in `bot/trend.py` (`setHighPoint` and `setLowPoint`)
4. Run backtest on TIAUSDT with and without the change, compare results
5. If positive, deploy
