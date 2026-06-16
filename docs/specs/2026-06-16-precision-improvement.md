# Signal Precision & Order Placement Improvement

**Date**: 2026-06-16  
**Status**: Draft — awaiting approval before implementation  
**Dataset**: 167 real orders, 11,825 backtest trades, 6,312 BEST signals

---

## Problem statement

The precision score (0–1) that ranks and selects signals is broken as a predictor. The **0.6+ bucket has a 6.1% win rate** — the worst of all buckets — while the 0.0–0.3 bucket wins at 23.8%. High-precision signals are currently *losing more* than low-precision ones. At the same time the bot is sitting in long signal droughts because legitimate setups don't have an order-placement window during certain market conditions.

Net PnL on 167 real orders: **−$227**. Three changes address the majority of this loss directly.

---

## Root cause analysis

### Why the precision formula misfires

Current formula:
```
precision = reliability × 0.40
           + alignment (0 / 0.175 / 0.35)
           + entry_quality × 0.25
           + correction_bonus × correction_weight
```

Max score breakdown: reliability=0.40 + alignment=0.35 + entry_quality=0.25 = 1.00.

**The problem**: `entry_quality` is the weakest factor (0.25 weight) yet it is by far the strongest predictor of outcome. The backtest data (11,825 trades split into max_adverse_pct quartiles) shows:

| Quartile | Win rate | Avg profit% |
|---|---|---|
| Q1 — barely moved against entry | **76.7%** | +0.788% |
| Q2 | 37.5% | +0.197% |
| Q3 | 13.4% | −0.455% |
| Q4 — price ran hard against entry | 9.4% | −1.056% |

The 67 percentage-point gap between Q1 and Q4 is the single strongest predictor in the dataset. `reliability` (CV of swing amplitudes) and `alignment` (parent trend direction) can outweigh entry quality in the current formula, allowing a well-aligned but late entry to score 0.67 while a perfectly-timed but orphaned signal scores only 0.37.

**Why 0.6+ fails**: all 33 high-precision real orders are SELL-side at level 2 with correction_bonus active. The correction_bonus (awarded for 50% Fibonacci retracements in a downswing) is inflating scores on setups that historically precede continued selling — but the bot was entering these during an ascending market regime, and the regime filter was not applied early enough in the scoring path.

### Why H17–20 UTC loses

44 real orders placed between 17:00–20:00 UTC: 8% win rate, **−$176.51** total. This covers the US equity market open (13:00–15:00 ET), which injects volatility that whipsaws swing entries before trends establish. H11 UTC (London session) shows +$103.46 on positive win rate. The pattern is consistent.

---

## Proposed changes

### Change 1 — Entry zone hard gate (new config key)

**What**: Add `entry_zone_max_pct` to risk_config (float, 0.0–1.0, default 1.0 = disabled).  
When set (e.g., 0.5), any entry where `how_close > proximity_zone_pct × entry_zone_max_pct` is **rejected before scoring**, regardless of other signal quality.

Example with proximity_zone_pct=10% and entry_zone_max_pct=0.5: only entries within the inner 5% of the swing boundary are accepted.

**Why**: Q1 entries (barely any adverse move) win at 76.7%. Q3/Q4 entries lose at <14%. A hard gate is more robust than reweighting because it cannot be overridden by a high reliability or alignment score. It directly enforces "only enter close to the level."

**Tradeoff**: Fewer entries — some good setups that enter slightly deeper in the zone will be rejected. Start at 0.75 (top three-quarters of zone), not 0.50, to avoid excessive signal drought.

**Files**: `bot/recommendation_engine.py` `_score_and_filter()`, read `cfg.get('entry_zone_max_pct', 1.0)`, add check after the direction gate:
```python
if g_entry_zone_max < 1.0:
    max_how_close = self._s.proximity_zone_pct * g_entry_zone_max
    if rec.getHowClose() > max_how_close:
        continue
```

**Risk**: none — it is additive to existing filters, and default=1.0 keeps current behavior.

---

### Change 2 — Entry quality weight increase

**What**: Increase `entry_quality` coefficient from 0.25 to 0.40. Decrease `reliability` from 0.40 to 0.25. Max score remains 1.00 (0.25 + 0.35 + 0.40).

**Why**: With the gate filtering out the worst entries (Change 1), the survivors within the zone should be ranked by how close to the boundary they are. Giving entry quality 40% weight (vs 25% now) makes a signal at 1% from the boundary score noticeably higher than one at 9% from the boundary.

**Why reduce reliability**: The coefficient-of-variation measure of swing consistency has low predictive power relative to entry quality based on available data. Reducing it from 0.40 to 0.25 better reflects its actual contribution.

**Files**: `bot/recommendation_engine.py` lines 249–251.

```python
reliability    = self._projection_reliability(trend) * 0.25   # was 0.40
alignment      = self._parent_alignment(trend, rec.getSide())
entry_quality  = self._entry_quality(rec.getHowClose()) * 0.40  # was 0.25
```

**Risk**: Existing presets with `min_precision_score > 0` may pass/fail differently. Verify none use a `min_precision_score` threshold that would flip behavior after reweighting. (Currently default is 0.0 — no floor — so this is low risk.)

---

### Change 3 — Trading blackout hours (new config key)

**What**: Add `trading_blackout_hours` to risk_config (list of UTC hours, default `[]`).  
During these hours, no new real orders are placed. Virtual orders continue normally (data collection unaffected).

**Why**: 44 real orders in H17–20 UTC at 8% win rate = −$176.51. This is the US open volatility window. The bot's swing-based signals fire on the pre-existing structure but price gets whipsawed before the level holds.

**Proposed initial setting**: `[17, 18, 19]`

**Files**: `main.py` in `_try_place_order()` or the candle-close handler:
```python
from datetime import datetime, timezone
_blackout = set(cfg.get('trading_blackout_hours', []))
if _blackout and datetime.now(timezone.utc).hour in _blackout:
    return  # skip real order, virtual continues
```

**Risk**: We may miss some winners in H17–20. Historical data shows 44 trades × 8% win rate = ~3–4 winners skipped. The 40 losers skipped at ~$4.50 avg loss save ~$176. Net expected value of the filter: strongly positive.

---

### Change 4 — Correction bonus scope fix

**What**: Set `correction_weight: 0.0` as the **global default** in risk_config (a new key `global_correction_weight` that overrides preset-level correction_weight). Only restore correction_weight for specific presets where data validates it.

**Why**: 33 high-precision orders all have correction_bonus active, and 28 of 33 are SELL-side with 6.1% win rate. The correction bonus identifies "a good-looking correction happened" but does not filter for whether the current regime supports the signal direction. It is currently inflating SELL signal scores in an ascending market.

The only preset with `correction_weight: 0.20` is `correction_w20_trail15_30`, which is already in the blocklist. All other presets use the default (0.0). So this change costs nothing in practice right now — it is defensive, preventing future presets from enabling correction_weight without explicit validation.

**Files**: `bot/recommendation_engine.py` `_score_and_filter()`, read `global_correction_weight = float(cfg.get('global_correction_weight', -1.0))`, and in `_precision()`, override `correction_weight` when the global value is set.

---

## What is NOT changed

- `global_min_rr=3.0` stays. The RR 2.0–3.0 winning bucket (14 trades, 42.9%) is too small a sample to lower the floor. Revisit after 50+ more trades in that range.
- `lowering_above_last_low` stays unblocked. It is the most common signal type and a key mechanism for trend-following. The −0.224% avg profit is partly driven by proximity zone issues that Change 1 addresses at the source.
- `global_trend_regime_filter` stays. It already blocks counter-trend signals; Changes 1–3 are additive to it.

---

## Implementation order

1. Change 3 (time blackout) — pure config, zero risk, immediate $176 recovery opportunity
2. Change 1 (entry zone gate) — one condition in `_score_and_filter`, easily reversible
3. Change 2 (reweighting) — one arithmetic change in `_precision`, deploy with Change 1
4. Change 4 (correction bonus global cap) — defensive only, minimal impact today

**Initial risk_config values to set after deployment**:
```json
"entry_zone_max_pct": 0.75,
"trading_blackout_hours": [17, 18, 19]
```

---

## Success metrics

After 50+ trades post-deployment:
- Overall win rate > 28% (from current 20.4%)
- H17–20 UTC: zero real orders (blackout working)
- Precision score of winning trades > precision score of losing trades (formula correlation restored)
- Net PnL turns positive over any rolling 20-trade window
