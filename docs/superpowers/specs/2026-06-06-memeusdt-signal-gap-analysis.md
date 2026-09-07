# MEMEUSDT Signal Gap Analysis — Why the Oscillating Zone Is Missed

**Date:** 2026-06-06
**Preset analysed:** partial_70 (also cross-checked hl_buy_trail15)
**Time window:** May 22 – June 6 2026 chart (actual klines April 21 – May 14 2026)

## Overview

ALL partial_70 trades cluster in the low-volatility flat zone (pre-peak). ZERO trades appear in the high-amplitude oscillating zone (post-peak), where price swings repeatedly between peaks and valleys — exactly where SELL at peaks and BUY at valleys should be ideal.

Four blockers stack simultaneously in the post-peak oscillating zone.

---

## Primary Blocker 1 — L2 DESCENDING kills all BUY continuation signals

After the big price peak, L2 trend locks to DESCENDING.
Recommendation engine parent-alignment gate (`recommendation_engine.py:126`):

    if rec.getType() in _CONTINUATION_TYPES and self._parent_is_opposing(trend, rec.getSide()):
        continue

With L2 = DESCENDING, every RISING_BELOW_LAST_HIGH (BUY continuation) signal is unconditionally rejected — regardless of L1 state. Half the signal universe is gone the moment price peaks.

---

## Primary Blocker 2 — L1 history pruned below 3 points → all signals silenced

`trend.py:434-436`:

    if len(self._highs) < 3 or len(self._lows) < 3:
        return None, None

Every Break of Structure triggers `removePointsUpTo()` which wipes all L1 swing points older than the anchor. In a sustained descent with consecutive BoS crossings, prunings stack. New confirmed swings on the other side number < 3, so `getSupposedNextPoints()` returns `(None, None)`, and `getRecommendation()` immediately returns `None` at `trend.py:492`. ALL signal types go silent — not just continuations.

---

## Secondary Blocker 3 — min_profit_pct=0.5% rejects low-amplitude projections

When the post-peak range narrows, the projected `supposed_next_low` / `supposed_next_high` (averaged from pruned 3-point history) sits within 0.5% of entry price. The profit gate (`recommendation_engine.py:115`) rejects the candidate before any order is attempted.

---

## Secondary Blocker 4 — range_position_max=0.50 (partial_70 specific)

`partial_70` requires entry in the upper 50% of the L1 swing range for a SELL signal. When L1's live range collapses to ~1% after pruning, off-ideal entries fail this gate.

---

## Why hl_buy_trail15 does better

`higher_low_buy=True` unlocks `ASCENDING_NEAR_HIGHER_LOW` signal — fires during brief windows when L1 briefly flips ascending from a bounce. These are the oscillating-zone entries. 61 trades vs 37 for partial_70. But still misses most of the zone because Blocker 1 and 2 still apply to non-hl_buy signal types.

---

## Key file/line citations

- `bot/trend.py:434-436` — 3-point minimum gate (primary silence)
- `bot/trend.py:487-493` — getRecommendation() returns None on None projection
- `bot/trend.py:283-294`, `296-308` — removePointsUpTo() in BoS crossings (prunes L1)
- `bot/recommendation_engine.py:126-127` — parent gate blocks BUY continuations when L2 DESC
- `bot/recommendation_engine.py:131-132` — range_position_max gate
- `bot/recommendation_engine.py:115-117` — min_profit_pct rejection
- `bot/trend.py:507-529` — higher_low_buy / ASCENDING_NEAR_HIGHER_LOW signal (absent in partial_70)
- `config/presets.py:157-161` — partial_70 definition
- `config/settings.py:149` — default min_profit_pct=0.5
