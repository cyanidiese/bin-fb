# Valley / Peak Position Detection — Design Spec
**Date:** 2026-05-28
**Status:** Approved for implementation

---

## Problem

`RISING_BELOW_LAST_HIGH` (BUY) and `LOWERING_ABOVE_LAST_LOW` (SELL) — the two
continuation signal types — fire whenever price is below the last confirmed high (or
above the last confirmed low). There is no check for *where* in the swing range price
currently sits.

Result: the bot entered BUY positions at 70–80% of the swing range — near the peak —
where there is minimal upside to TP and immediate SL exposure on any further decline.

The alignment gate (added in session 37) blocks counter-trend continuations. This spec
adds the next layer: positional validation *within* an aligned trend.

---

## Scope

- **Signals affected:** `RISING_BELOW_LAST_HIGH` and `LOWERING_ABOVE_LAST_LOW`
  (the `_CONTINUATION_TYPES` frozenset in `recommendation_engine.py`)
- **Signals exempt:** all reversal types remain fully exempt — same as alignment gate
- **Files changed:** `bot/recommendation_engine.py`, `config/settings.py`
- **Applies to:** live trading and backtesting — both use `RecommendationEngine.generate()`

---

## Design

### Filter 1 — Range Position Gate (primary, always active)

**What it checks:** where is the entry price within the swing range of the
signal-generating trend level?

```
range_position = (entry_price - last_low) / (last_high - last_low)
```

- **BUY continuation:** block if `range_position > range_position_max` (default 0.5)
  — price is in the upper half of the range, near the peak, not the valley
- **SELL continuation:** block if `range_position < (1.0 - range_position_max)` (default 0.5)
  — price is in the lower half of the range, near the valley, not the peak

**Fallback:** if the generating trend level has no last_high or last_low (cold start),
fall back to the same check on the smaller trend. If neither is available, skip the
check (don't block).

**New setting:** `range_position_max: float` (default 0.5, disabled at 1.0)

**Why 0.5 as default:** the midpoint of the range is the most neutral threshold. A BUY
anywhere above the midpoint is definitionally closer to the peak than the valley.

---

### Filter 2 — Correction Depth Gate (secondary, when available)

**What it checks:** has the current L1 pullback within the L2 uptrend gone deep enough
to represent a real valley?

Computed via `trend.getSmallerTrend().get_correction_info()` — this returns the depth
of L1's ongoing correction within L2. Only available when:
- The signal is generated at L2 or higher (smaller trend exists)
- L1 is currently moving counter to L2 (active pullback in progress)

```
depth_pct = smaller_trend.get_correction_info()['depth_pct']
```

- Block if `depth_pct < correction_min_depth` (default 30.0) — barely started pulling
  back, still near the macro peak
- Block if `depth_pct > correction_max_depth` (default 80.0) — pulled back so far it
  may be a reversal, not a correction

**New settings:**
- `correction_min_depth: float` (default 30.0, disabled at 0.0)
- `correction_max_depth: float` (default 80.0, disabled at 100.0)

**When NOT available** (smaller trend not defined, or L1 and L2 moving same direction):
skip this check — Filter 1 alone applies.

---

### Filter 3 — L3 Trend Stack (optional, off by default)

**What it checks:** does L3 (the trend above L2) agree with the signal direction?

Extends the existing `_parent_is_opposing()` logic one level higher: if L3 is defined
and explicitly opposes the signal direction, block continuation signals regardless of
range position.

**New setting:** `require_l3_alignment: bool` (default False)

Default is off — L3 is slow to form and would reduce signal frequency significantly
during early candle history. Enable per-preset once backtest data confirms benefit.

---

## Implementation location

All three filters added to `_score_and_filter()` in `RecommendationEngine`, after the
existing alignment gate check (line 126), before precision scoring:

```python
# Filter 1: range position gate
if rec.getType() in _CONTINUATION_TYPES:
    if not self._passes_range_position(rec, trend):
        continue

# Filter 2: correction depth gate (when smaller trend in active correction)
if rec.getType() in _CONTINUATION_TYPES:
    if not self._passes_correction_depth(trend):
        continue

# Filter 3: L3 alignment (optional)
if rec.getType() in _CONTINUATION_TYPES and self._s.require_l3_alignment:
    if self._l3_is_opposing(trend, rec.getSide()):
        continue
```

New private methods:
- `_passes_range_position(rec, trend) -> bool`
- `_passes_correction_depth(trend) -> bool`
- `_l3_is_opposing(trend, side) -> bool`

New Settings fields:
- `range_position_max: float` (default 0.5)
- `correction_min_depth: float` (default 30.0)
- `correction_max_depth: float` (default 80.0)
- `require_l3_alignment: bool` (default False)

---

## Dashboard

Add to `PresetSettingsPanel.tsx` under an "Entry Position" section:
- `range_position_max` (slider 0.3–1.0, default 0.5)
- `correction_min_depth` (number input, default 30.0)
- `correction_max_depth` (number input, default 80.0)
- `require_l3_alignment` (toggle, default off)

Add abbreviations to `create/page.tsx` NAME_ABBREV:
- `rpos` → `range_position_max`
- `cdmin` → `correction_min_depth`
- `cdmax` → `correction_max_depth`
- `l3align` → `require_l3_alignment`

---

## Backtest validation

Run backtests for INJUSDT, DOGEUSDT, SOLUSDT with the new filters enabled (defaults)
before deploying. Compare:
- Number of signals generated (expect 20–40% reduction for continuation types)
- Win rate on continuation signals (expect improvement)
- Net PnL (expect improvement or neutral — fewer but better trades)

---

## Risks

- **Over-filtering in strong trends:** in a sustained uptrend, price may legitimately
  stay above the 50% midpoint. Mitigated by: reversal types are exempt and will still
  fire; `range_position_max` is configurable per-preset (raise to 0.65–0.70 for
  trending symbols).
- **Correction depth too restrictive:** if `correction_min_depth=30` blocks too many
  early-stage entries, lower to 20 per-preset.
- **L3 not yet formed:** Filter 3 defaults to off, so this is not a concern unless
  explicitly enabled.

---

## Not in scope

- Changes to reversal signal types
- Changes to `ASCENDING_NEAR_HIGHER_LOW` / `DESCENDING_NEAR_LOWER_HIGH` (these
  already have proximity zone checks built in)
- Changes to `main.py`, `bot/backtester.py`, or any order execution logic
