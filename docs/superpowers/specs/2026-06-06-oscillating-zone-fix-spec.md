# Oscillating Zone Fix Spec

**Date:** 2026-06-06
**Status:** Draft — ready for implementation review
**Research reference:** `docs/superpowers/specs/2026-06-06-memeusdt-signal-gap-analysis.md`

---

## Problem Statement

After a large price peak, MEMEUSDT (and any symbol exhibiting sustained post-peak oscillation) generates zero signals where the chart shows clear tradeable swings. Four blockers stack simultaneously:

1. L2 trend locks DESCENDING — kills all BUY continuation signals via parent-alignment gate
2. L1 history falls below 3 highs or 3 lows after BoS-triggered pruning — silences ALL signal types
3. `min_profit_pct=0.5%` rejects low-amplitude projections from pruned 3-point history
4. `range_position_max=0.50` in `partial_70` rejects entries outside the narrowed range

The fix targets all four blockers. Blockers 1 and 2 are the primary issues. Blockers 3 and 4 are compounding filters that must be addressed for signals to actually survive to the engine.

---

## Blocker 1 — L2 DESCENDING kills all BUY continuation signals

### Current code

`bot/recommendation_engine.py:126-127`:

```python
if rec.getType() in _CONTINUATION_TYPES and self._parent_is_opposing(trend, rec.getSide()):
    continue
```

`_CONTINUATION_TYPES` contains:
- `RISING_BELOW_LAST_HIGH` (BUY)
- `LOWERING_ABOVE_LAST_LOW` (SELL)

`_parent_is_opposing()` at line 211-216 returns True whenever a defined parent trend opposes the signal direction.

### Mechanism of the problem

When price oscillates after a peak, L1 may flip ascending on each bounce (generating `RISING_BELOW_LAST_HIGH` BUY signals), but L2 is stuck DESCENDING. The gate at line 126 is unconditional: if L2 is defined and opposes, the signal is dropped. There is no presettable way to relax this.

### Fix: make the parent gate opt-out via preset flag

Add a boolean Settings field `ignore_parent_alignment` (default: `False`). When True, skip the continuation gate entirely. This lets oscillating-zone presets trade counter-trend on L1 while the L2 alignment **penalty** still applies via `_parent_alignment()` in the precision score (already docks 0.35 precision points when parent opposes — no change needed there).

**File:** `config/settings.py`

Add to the `Settings` dataclass after `higher_low_buy`:
```python
# When True: allow continuation signals even when the parent trend explicitly
# opposes the signal direction. The alignment penalty in precision scoring
# still applies — this only disables the hard gate. Default False.
ignore_parent_alignment: bool
```

Add to `load_settings()` in the defaults block:
```python
ignore_parent_alignment=os.getenv('IGNORE_PARENT_ALIGNMENT', 'false').lower() in ('1', 'true', 'yes'),
```

**File:** `config/presets.py`

Add to `PresetOverrides`:
```python
ignore_parent_alignment: bool
```

**File:** `bot/recommendation_engine.py`

Change lines 126-127 from:
```python
if rec.getType() in _CONTINUATION_TYPES and self._parent_is_opposing(trend, rec.getSide()):
    continue
```
To:
```python
if (
    rec.getType() in _CONTINUATION_TYPES
    and not self._s.ignore_parent_alignment
    and self._parent_is_opposing(trend, rec.getSide())
):
    continue
```

### Mechanism (why this allows more signals)

With `ignore_parent_alignment=True`, BUY continuation signals from L1 ascending phases are no longer hardblocked by L2 DESCENDING. The precision score still penalizes counter-trend signals — they score 0.0 on the alignment component (0.35 points lost) versus aligned signals. So the fix does not equalize quality scoring; it only removes the hard discard gate.

### Risk

In a genuinely DESCENDING L2 trend, enabling this flag could open BUY continuations that fight the macro trend. In a MEMEUSDT oscillation scenario this is correct behavior. In a clean bear trend on BTCUSDT where L1 bounces are short-lived, this increases false entry rate.

### Guard

Gate behind `ignore_parent_alignment` (default `False`). Existing presets are unaffected. Only opt-in presets for oscillating symbols would set this to `True`. This is therefore symbol-appropriate, not universal.

---

## Blocker 2 — L1 history pruned below 3 points → all signals silenced

### Current code

`bot/trend.py:434-436`:
```python
def getSupposedNextPoints(self) -> Tuple[Optional[float], Optional[float]]:
    if len(self._highs) < 3 or len(self._lows) < 3:
        return None, None
```

`bot/trend.py:490-492` (inside `getRecommendation()`):
```python
supposed_next_high, supposed_next_low = self.getSupposedNextPoints()
if supposed_next_high is None or supposed_next_low is None:
    return None
```

Pruning happens at `bot/trend.py:179-181`:
```python
def removePointsUpTo(self, timestamp: int) -> None:
    self._highs = [p for p in self._highs if p.getTime() > timestamp]
    self._lows = [p for p in self._lows if p.getTime() > timestamp]
```

Called from `checkIfHigherThanDescBreakOfStructure()` (line 294) and `checkIfLowerThanAscBreakOfStructure()` (line 308) when a BoS crossing occurs. Already guarded: only prunes when `time_of_last_high/low is not None` (comment at line 292 explains this).

### Mechanism of the problem

In a sustained oscillating descent with 3–4 consecutive BoS crossings, prunings stack. Each crossing wipes all L1 points older than the anchor. After 2 crossings the count drops below 3 on one side. The 3-point gate returns `(None, None)` immediately, and `getRecommendation()` returns `None` before checking any signal conditions.

### Fix option A: lower the minimum to 2 points (with a 1-point average fallback)

The 3-point minimum exists because `getAvgDifference()` needs at least 2 differences, which requires at least 3 points. With only 2 highs and 2 lows, there is 1 difference per side — enough for a projection, just less statistically stable.

The fix allows operation at 2+ points per side by removing the hard gate and letting the existing `getAvgDifference()` function handle the edge case (it already returns 0.0 on empty diffs, which would break projection, so we need a floor).

**File:** `bot/trend.py`

Change `getSupposedNextPoints()` lines 434-436 from:
```python
if len(self._highs) < 3 or len(self._lows) < 3:
    return None, None
```
To:
```python
if len(self._highs) < 2 or len(self._lows) < 2:
    return None, None
```

This allows projection with as few as 2 highs and 2 lows (1 difference per side). The average of a single value is the value itself, so projection quality degrades gracefully — the amplitude prediction is based on the single observed swing rather than an average of multiple. The precision scoring naturally discounts these: `_projection_reliability()` at line 175-197 computes coefficient-of-variation, and with only 1 diff the computation at line 188 (`len(all_diffs) < 2`) returns 0.0 reliability → 0.0 reliability component in precision score. A signal from a 2-point history has zero reliability weight but can still fire if other score components pass.

### Risk of Fix A

Projections from 2 points are less stable. In a genuinely trending market with only 2 swings, the estimated amplitude may be the first (often outlier) swing. This can produce TP/SL levels that are miscalibrated.

However: the precision scoring already discounts 2-point projections to zero reliability. And the signal still must pass `min_profit_pct`, `min_profit_loss_ratio`, and structural sanity checks. The risk is manageable.

### Fix option B: gate on a new Settings field `min_swing_points_projection`

More surgical: add a separate minimum specifically for projection computation, distinct from `min_swing_points` (which governs whether `getRecommendation()` is called at all via the `_collect()` gate in `recommendation_engine.py:69`).

Add `min_swing_points_projection: int` (default `3`, which preserves current behavior). Presets that want oscillating-zone coverage set it to `2`.

**File:** `config/settings.py` — add field:
```python
# Minimum number of confirmed swing highs and lows (each) required before
# getSupposedNextPoints() computes a projection. Lowering to 2 allows
# projection from a single amplitude observation (less stable, naturally
# scored lower by _projection_reliability). Default 3 = current behavior.
min_swing_points_projection: int
```

**File:** `bot/trend.py` — `getSupposedNextPoints()` does not take a settings argument. The cleanest change is to make the minimum configurable by passing it as a parameter.

Change signature of `getSupposedNextPoints()`:
```python
def getSupposedNextPoints(self, min_pts: int = 3) -> Tuple[Optional[float], Optional[float]]:
    if len(self._highs) < min_pts or len(self._lows) < min_pts:
        return None, None
```

Then in `getRecommendation()` at line 490:
```python
# No change needed here — getRecommendation already takes kwargs
```

But `getRecommendation()` does not currently thread `min_pts` down. The call chain is:

- `recommendation_engine._collect()` → `trend.getRecommendation()` → `trend.getSupposedNextPoints()`

So `getRecommendation()` needs a new parameter too.

**File:** `bot/trend.py` — `getRecommendation()` signature (line 472):
```python
def getRecommendation(
    self,
    point: Optional[Point] = None,
    entry_price: Optional[float] = None,
    proximity_zone_pct: float = 10.0,
    lower_high_sell: bool = False,
    higher_low_buy: bool = False,
    min_swing_points_projection: int = 3,
) -> Optional[Recommendation]:
```

At line 490, change:
```python
supposed_next_high, supposed_next_low = self.getSupposedNextPoints()
```
To:
```python
supposed_next_high, supposed_next_low = self.getSupposedNextPoints(min_pts=min_swing_points_projection)
```

And `getRecommendations()` (line 626) needs the same parameter threaded through.

**File:** `bot/recommendation_engine.py` — `_collect()` at line 70:
```python
rec = current.getRecommendation(
    entry_price=entry_price,
    proximity_zone_pct=pct,
    lower_high_sell=self._s.lower_high_sell,
    higher_low_buy=self._s.higher_low_buy,
    min_swing_points_projection=self._s.min_swing_points_projection,
)
```

**File:** `config/presets.py` — add to `PresetOverrides`:
```python
min_swing_points_projection: int
```

### Recommendation: use Fix B

Fix B is strictly more surgical. Fix A is a global behavior change affecting all presets. Fix B is opt-in and preserves current behavior by default.

### Risk of Fix B

Calling `getRecommendations()` from `analyzer.py:102` also needs to thread the parameter (or use the settings object). Check the call site:

`bot/analyzer.py:102-105`:
```python
return self._trend.getRecommendations(
    entry_price=self._current_price,
    proximity_zone_pct=pct,
)
```

This call does NOT go through the engine; it's the raw unfiltered list for display. It uses the default `min_swing_points_projection=3`, which is correct — the display path should remain conservative. The engine path already passes the correct value.

Similarly `analyzer.py:124-128` (`get_recommendations()`) — same: display path, safe to leave at default.

---

## Blocker 3 — min_profit_pct=0.5% rejects low-amplitude projections

### Current code

`bot/recommendation_engine.py:115-117`:
```python
profit_pct = profit_dist / entry * 100
if profit_pct < self._s.min_profit_pct:
    continue
```

`config/settings.py:149`:
```python
min_profit_pct=float(os.getenv('MIN_PROFIT_PCT', '0.5')),
```

### Mechanism of the problem

After pruning to 2-3 points, the projected amplitude (average of 1 observed swing) may be small — 0.2-0.4% on MEMEUSDT's post-peak oscillations. Even when the projection is structurally valid, it fails the 0.5% floor.

### Fix

This is already presettable. No code changes needed for the gate itself. The fix is a new preset that sets `min_profit_pct` to a lower value.

Suggested value: `0.2` for oscillating symbols. This is consistent with the existing `loose_entry` preset which already uses `0.3`.

`min_profit_pct=0.2` is safe because:
- It is a per-preset override; existing presets are unaffected
- The RR filter (`min_profit_loss_ratio`) still applies — a 0.2% TP with a matching small SL still needs to meet RR ≥ 1.5 (or whatever the preset requires)
- Backtesting can verify whether these small-profit signals actually produce positive EV

### Risk

Very small TP targets (0.2-0.3%) have high broker fee sensitivity. At 0.04% fee per side, round-trip costs 0.08%. A 0.2% TP leaves only 0.12% net — any slippage erodes it. This is more a live-trading concern than a backtest concern.

**Guard:** Preset-specific override only. Document the fee-sensitivity risk in the preset comment.

---

## Blocker 4 — range_position_max=0.50 rejects entries in collapsed post-peak range

### Current code

`bot/recommendation_engine.py:131-132`:
```python
if rec.getType() in _CONTINUATION_TYPES and not self._passes_range_position(rec, trend):
    continue
```

`_passes_range_position()` (lines 218-255): computes `position = (entry - last_low) / range_size` and blocks BUY when `position > max_pos`, SELL when `position < (1 - max_pos)`.

`partial_70` in `config/presets.py:157-161`:
```python
'partial_70': {
    'partial_take_pct': 0.70,
    'max_losing_pct': 70.0,
    'range_position_max': 0.50,
},
```

### Mechanism of the problem

When the post-peak L1 range collapses to ~1% (pruned 2-3 point history), the range becomes tiny and the "ideal zone" (bottom 50% for BUY) shifts to a very narrow price band. Entries at normal price levels fail this gate because the range measurement itself is distorted by pruning.

### Fix option A: raise range_position_max in the oscillating-zone preset

The simplest fix: for oscillating-zone presets, set `range_position_max: 1.0` (disabled) or `0.80`.

No code changes needed. This is already presettable.

### Fix option B: gate range_position_max only on "stable" projections

A more principled fix: only apply the range position gate when the trend has enough history to compute a reliable range. Specifically, skip the gate when `len(highs) < min_swing_points_projection + 1` (the same threshold as the projection gate).

This would be an additional parameter `range_position_min_pts: int` (default `0` = always apply).

This is additional complexity with marginal benefit over just raising `range_position_max` in the preset. Avoid unless the simpler fix proves insufficient.

### Recommendation: use Fix option A for Blocker 4

Set `range_position_max: 1.0` in oscillating-zone presets. This is already supported, zero code changes needed. It means the range gate is fully disabled for those presets, which is correct when the range measurement is unreliable due to pruning.

---

## Preset impact table

| Preset | Blocker 1 (parent gate) | Blocker 2 (3-pt minimum) | Blocker 3 (min_profit_pct) | Blocker 4 (range_position) |
|---|---|---|---|---|
| partial_70 | Not affected (no ignore flag) | Not affected (stays at default 3) | Not affected (default 0.5%) | Affected — range_position_max=0.50 causes misses in collapsed range |
| hl_buy_trail15 | Partially affected (RISING_BELOW_LAST_HIGH still blocked) | Not affected | Not affected | Not affected (no range gate set) |
| default | Not affected | Not affected | Not affected | Affected — range_position_max=0.30 causes even more misses |
| Any new oscillating-zone preset | Fixed (ignore_parent_alignment=True) | Fixed (min_swing_points_projection=2) | Fixed (min_profit_pct=0.2) | Fixed (range_position_max=1.0) |
| trail_15_from_30_full (locked) | Not affected | Not affected | Not affected | Not affected (no range gate) |
| trail_15_from_30_cooldown (locked) | Not affected | Not affected | Not affected | Affected — range_position_max=0.50 same as partial_70 |
| All presets without new flags | Zero change — all fixes are additive, opt-in only | | | |

Presets that already set `range_position_max < 1.0` may benefit from knowing that in post-peak conditions, their range gate could fire incorrectly. The oscillating zone fix does not touch those presets; it introduces a new preset category.

---

## Implementation plan (ordered)

### Step 1 — Add `min_swing_points_projection` to Settings and PresetOverrides

**File:** `config/settings.py`
- Add field `min_swing_points_projection: int` to the `Settings` dataclass, after `min_swing_points`.
- Add to `load_settings()` defaults block: `min_swing_points_projection=int(os.getenv('MIN_SWING_POINTS_PROJECTION', '3'))`

**File:** `config/presets.py`
- Add `min_swing_points_projection: int` to `PresetOverrides`.

No behavior change yet — default is `3`, matching current hard-coded value.

### Step 2 — Thread `min_swing_points_projection` into `getSupposedNextPoints()` and `getRecommendation()`

**File:** `bot/trend.py`

2a. Change `getSupposedNextPoints()` signature at line 434:
```python
def getSupposedNextPoints(self, min_pts: int = 3) -> Tuple[Optional[float], Optional[float]]:
    if len(self._highs) < min_pts or len(self._lows) < min_pts:
        return None, None
```

2b. Change `getRecommendation()` signature at line 472, add parameter:
```python
min_swing_points_projection: int = 3,
```

2c. Change the call to `getSupposedNextPoints()` at line 490:
```python
supposed_next_high, supposed_next_low = self.getSupposedNextPoints(min_pts=min_swing_points_projection)
```

2d. Change `getRecommendations()` signature at line 626, add parameter:
```python
min_swing_points_projection: int = 3,
```

2e. Thread parameter into the recursive `bigger.getRecommendations()` call at line 646:
```python
result.extend(bigger.getRecommendations(
    entry_price=entry_price,
    proximity_zone_pct=proximity_zone_pct,
    lower_high_sell=lower_high_sell,
    higher_low_buy=higher_low_buy,
    min_swing_points_projection=min_swing_points_projection,
))
```

2f. Thread into the `current.getRecommendation()` call at line 70 of `recommendation_engine.py`:

**File:** `bot/recommendation_engine.py`
```python
rec = current.getRecommendation(
    entry_price=entry_price,
    proximity_zone_pct=pct,
    lower_high_sell=self._s.lower_high_sell,
    higher_low_buy=self._s.higher_low_buy,
    min_swing_points_projection=self._s.min_swing_points_projection,
)
```

### Step 3 — Add `ignore_parent_alignment` to Settings and PresetOverrides

**File:** `config/settings.py`
- Add field `ignore_parent_alignment: bool` to `Settings` dataclass, after `higher_low_buy`.
- Add to `load_settings()`: `ignore_parent_alignment=os.getenv('IGNORE_PARENT_ALIGNMENT', 'false').lower() in ('1', 'true', 'yes')`

**File:** `config/presets.py`
- Add `ignore_parent_alignment: bool` to `PresetOverrides`.

**File:** `bot/recommendation_engine.py`

Change lines 126-127:
```python
if (
    rec.getType() in _CONTINUATION_TYPES
    and not self._s.ignore_parent_alignment
    and self._parent_is_opposing(trend, rec.getSide())
):
    continue
```

### Step 4 — Add new oscillating-zone preset(s) in `config/presets.py`

Add to `PRESETS`:

```python
# ── Oscillating-zone: designed for symbols that oscillate post-peak ───────────
# Unlocks signals in the oscillating zone by:
# - Allowing continuation signals when L2 opposes (ignore_parent_alignment)
# - Allowing projection from 2 swing points (min_swing_points_projection=2)
# - Lower profit floor (min_profit_pct=0.2) for small-amplitude oscillations
# - Disabled range position gate (range_position_max=1.0)
# WARNING: min_profit_pct=0.2 is fee-sensitive at live trading; verify broker fees
# before deploying. All four levers can be adjusted independently per symbol.
'oscillating_zone': {
    'ignore_parent_alignment': True,
    'min_swing_points_projection': 2,
    'min_profit_pct': 0.2,
    'range_position_max': 1.0,
    'partial_take_pct': 0.30,
    'trailing_stop_pct': 0.15,
    'tp_multiplier': 0.95,
},
# Same as oscillating_zone but retains 3-point projection minimum.
# Use when the symbol has enough history but needs the parent gate removed.
'oscillating_no_parent_gate': {
    'ignore_parent_alignment': True,
    'range_position_max': 1.0,
    'partial_take_pct': 0.30,
    'trailing_stop_pct': 0.15,
    'tp_multiplier': 0.95,
},
```

### Step 5 — Verify in backtest

Run backtests for MEMEUSDT with:
1. `partial_70` (baseline, no changes — should produce same result as before)
2. `oscillating_zone` (new preset — should show trades in the post-peak oscillating zone)
3. `oscillating_no_parent_gate` (partial fix — parent gate removed but 3-pt minimum retained)

Compare trade distribution charts to confirm signals appear in the post-peak zone.

---

## Acceptance criteria

1. Running backtest for MEMEUSDT with `oscillating_zone` preset produces trades with open timestamps AFTER the price peak (post-peak oscillating zone).
2. Running backtest for MEMEUSDT with `partial_70` (unchanged) produces the same trade count and timestamps as before the changes.
3. Running backtest for BTCUSDT with `trail_15_from_30_full` (locked preset, no new flags) produces the same trade count as before.
4. The engine does not crash when `min_swing_points_projection=2` and a trend has exactly 2 highs and 2 lows — the single-diff average is computed and used without exception.
5. A trend with 1 high or 1 low (after extreme pruning) still returns `(None, None)` from `getSupposedNextPoints()` and generates no signals.

---

## What is NOT changed

The following are explicitly out of scope:

- `bot/kline_processor.py` — swing detection logic is not changed. The problem is downstream in trend processing, not in the point detection algorithm.
- `removePointsUpTo()` in `bot/trend.py:179-181` — the pruning logic itself is correct and intentional. We are working around the post-prune state, not changing when pruning occurs.
- The 3-point minimum in `getSupposedNextPoints()` for the DEFAULT path — it remains at 3 unless a preset explicitly sets `min_swing_points_projection` lower. All existing presets are unaffected.
- `_CONTINUATION_TYPES` — not adding or removing any signal types from the set. The parent gate is toggled, not the type classification.
- `_parent_alignment()` precision scoring — the 0.0/0.175/0.35 weights are unchanged. Counter-trend signals still receive zero alignment score. This is the correct quality penalty; only the hard DISCARD gate is made opt-out.
- `_passes_range_position()` logic — the range position calculation is correct. The fix for Blocker 4 is to set `range_position_max=1.0` in new presets, not to change the gate logic.
- `bot/analyzer.py` — the two display-path calls to `getRecommendations()` at lines 102 and 125 are not changed. They continue to use the default `min_swing_points_projection=3` so the dashboard display remains conservative.
- Any existing locked preset (`LOCKED_PRESETS`) — no locked presets are modified or receive new flags.
- `bot/backtester.py`, `bot/virtual_order_simulator.py`, or any order execution path — this spec is purely about signal generation.

---

## Notes on universality vs. symbol-specificity

The user's constraint was "fixes should ideally be universal." Assessment:

- **Blocker 2 fix (min_swing_points_projection)**: The 3→2 point reduction is safe to make universal (i.e., lower the global default from 3 to 2) IF the precision scoring discount is relied upon. Since `_projection_reliability()` already returns 0.0 for 2-point histories (the `len(all_diffs) < 2` check at line 188 returns early), 2-point signals are self-limiting — they score lower and will only win selection when no better candidate exists. Making the default `2` globally would allow signals in post-prune states for all symbols, with no false behavior on symbols that always have 3+ points (where the 2-point gate is never hit anyway). This is a judgment call; the spec implements it as opt-in for safety, but the risk of making it universal is low.

- **Blocker 1 fix (ignore_parent_alignment)**: This is NOT universal. Disabling the parent gate globally would allow counter-trend BUY entries on BTCUSDT or ETHUSDT during established bear trends. That is strategically incorrect for trend-following behavior. This MUST remain opt-in, symbol-specific.

- **Blocker 3 fix (min_profit_pct)**: Already presettable, nothing to make universal.

- **Blocker 4 fix (range_position_max)**: Already presettable, nothing to make universal.
