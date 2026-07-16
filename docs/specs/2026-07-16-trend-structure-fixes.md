# Spec: Trend-Structure Fixes — Same-Level Stops & Soft BoS Pruning

**Date:** 2026-07-16 (session 58 continuation)
**Status:** SPEC ONLY — not yet implemented. Requires backtest validation before deploy.
**Prereq reading:** session 58 entries in CLAUDE_NOTES.md; the pipeline analysis in this session.

---

## Why (data)

Across 260 real trades (May 26 – Jul 13, all symbols):

- **All profit comes from trailing stops**: 74 trail exits = +$1,456 (70% WR). TP was hit 2 times in 260 trades. The structural TP is effectively decoration.
- **The loss engine is fast stopouts**: 151 trades held <1h netted -$604 (13% WR). Trades surviving 4–12h ran 56% WR, +$386.
- **L2 signals**: -$436 all-time (-$318 in the analyst's Jun 6+ window) vs L3 -$118 (-$21).
- **SELL side**: -$594 vs BUY +$41.
- **Wide-SL artifacts**: a single TIAUSDT trade carried a 15% SL and lost -$206; EIGENUSDT/INJUSDT live positions opened with 6%/9.9% SLs and 20.5%/35.5% TPs.

Two structural root causes in the trend pipeline explain most of this.

---

## Fix A — Stops and targets must come from the generating level's own structure

### Problem (current behavior)

- `trend.py:563` and `trend.py:624`: continuation signals (`LOWERING_ABOVE_LAST_LOW`, `RISING_BELOW_LAST_HIGH`) take their **stop from `smaller_break_of_structure`** — a different level's BoS price, unrelated to the swing being traded.
- `trend.py:582-585`, `642-645`: supposed-point signals take both target and stop from `getSupposedNextPoints()`, which calls `findHighestInBiggerTrendsSince` / `findLowestInBiggerTrendsSince` (`trend.py:202-208`, `248-254`). These recurse up **every parent level** and can return the all-time extreme — especially right after L2 seeding (commit 9545794 seeds a fresh bigger trend with the single most-extreme point; `getTimeOfLastHigh()`/`getTimeOfLastLow()` can be `None` → unbounded search).
- Downstream, `recommendation_engine.py` reconciles this alien geometry into a "clean" RR ≥ 2 by clipping TP to `global_max_rr × SL-distance` — producing the 12–35% TPs that are never reached (2 TP hits in 260 trades) while risking 6–15% per trade.

### Change

1. Stop for continuation signals = the generating level's own last confirmed swing (last swing low for BUY / last swing high for SELL), not another level's BoS.
2. `findHighestInBiggerTrendsSince`/`findLowestInBiggerTrendsSince`: bound the search window to the generating level's own active swing history; never recurse to a parent whose seed timestamp is `None`.
3. Add a dispersion sanity check before trusting `avg_high_diff`/`avg_low_diff` projections (`trend.py:468-480`): if the projection sample has < 3 points or coefficient of variation above a threshold, return no signal instead of a degenerate `entry≈target≈stop` one.

### Touch points

- `bot/trend.py`: `getRecommendation()` stop assignment (~563, ~624), `getSupposedNextPoints()` (~461-480), `findHighestInBiggerTrendsSince`/`findLowestInBiggerTrendsSince` (~202-254).
- Tests: new unit tests constructing multi-level trends and asserting stop provenance.

### Rejected alternative

Blanket `max_sl_pct` cap at 4%: real data shows the 4–8% SL bucket nets positive (+$22 on n=11, includes +$117/+$174 winners); only the 8%+ artifact class is toxic. An 8% per-symbol cap was applied via hot-reload on 2026-07-16 as an interim guard; Fix A removes the artifact at the source.

---

## Fix B — Soft-prune on break of structure (keep projection context)

### Problem (current behavior)

`checkIfHigherThanDescBreakOfStructure` / `checkIfLowerThanAscBreakOfStructure` (`trend.py:279-305`) call `removePointsUpTo(...)` (`trend.py:179-181`), deleting **all** swing history up to the BoS point. `getSupposedNextPoints()` then requires ≥ `min_swing_points_projection` (default 3) highs AND lows, so all signal types go silent until 3 fresh highs + 3 fresh lows form — multi-day dead windows on 15m candles.

Documented workarounds made it worse: 5+ presets (`l2_bos_entry`, `l2_bos_trend`, `oscillating_*`, `l2_regime_aware*`) drop `min_swing_points_projection` to 1–2 and set `ignore_parent_alignment=True` to get any signal post-BoS. Those settings re-admit exactly the signal class the data condemns: 1–2-point projections produce degenerate geometry, and disabled parent alignment lets counter-trend L2 SELL continuations through (L2: -$436; SELL: -$594).

### Change

1. `removePointsUpTo` marks points `pruned=True` instead of deleting (they already carry an `active` flag for the dashboard — extend that mechanism).
2. `getSupposedNextPoints()` uses pruned points as **fallback context**: when fresh points < `min_swing_points_projection`, fill the shortfall from the most recent pruned points; weight fresh points normally.
3. Once fresh structure reaches the threshold, pruned points are garbage-collected (memory cap: keep last N pruned per level).
4. After validation: restore `min_swing_points_projection=3` and re-enable parent alignment on the workaround presets — the gates become affordable again once the drought cause is gone. This is the mechanism expected to strangle the losing L2-SELL class without any hand-blocking.

### Touch points

- `bot/point.py`: pruned flag.
- `bot/trend.py`: `removePointsUpTo` (~179), `getSupposedNextPoints` (~461), BoS handlers (~279-305).
- `config/presets.py`: post-validation preset restoration (separate follow-up commit).
- `bot/analyzer.py`: `get_all_points()` active-flag interaction.

### Risk flags

- Projection from stale (pre-BoS) points can lag a genuine regime change — mitigated because fresh points always take precedence and the fallback only fills the shortfall.
- Virtual tracker scores were earned under drought conditions; expect score shifts for the workaround presets after deploy. Re-seed from a fresh backtest at deploy time.

---

## Validation plan (both fixes)

1. Unit tests for stop provenance, bounded parent search, dispersion guard, pruned-fallback projection.
2. Full backtest sweep (all presets, 5000 klines) before/after on the 5 active symbols; compare per-preset PnL, trade counts, and the count of signals with SL > 8% (expect →0) and dead-window candles (expect large reduction).
3. Deploy behind observation: 1 week virtual-only comparison if backtest deltas are ambiguous.

## Explicitly out of scope

- Trail-arming improvements (shipped separately: commit 82ca600 + the min(partial, activation) arming change of 2026-07-16).
- Swing confirmation lag (`neighbours=2`) — second-order, revisit after A/B land.
