# Weight shadow calculator — measure Profit%-driven weighting before trusting it

Date: 2026-09-28.

## Why

The user asked whether the stored Profit% (per preset × shortcut × symbol × mode) can drive
symbol weights / allocation, e.g. from the last 7 days. Measured on testnet history
(205,130 closed virtual trades, 22 symbols, 103 days):

| signal | predicts the next 1/3/7 days? |
|---|---|
| picker number (top preset's trailing 7d Profit%) | no — Spearman +0.02…+0.04; "winners" then averaged −1.9…−4.2 % |
| symbol mean across all presets | no — +0.04…+0.08 |
| one fixed preset per symbol | no — −0.03…+0.03 |
| real orders week → next week (12 symbols, 523 trades) | slightly reversed, −0.22 |
| activity (trades/7d) | yes, modestly, +0.22 |
| risk (stdev of % per trade) | yes, modestly, +0.25 |

Picking the best of 87 noisy presets selects luck, which does not repeat. Applying a
Profit%-chasing weight system on this evidence would add turnover and concentration for
no expected gain — possibly a loss. But the data is testnet, and live data was a gappy
REST-fallback feed until 2026-09-27 (see FEATURES "Kline Feed Fixes"). Production may
behave differently. So: **measure on clean live data first, change nothing yet.**

## What

Once per day per mode (the Profit% store's day rollover, Europe/Kyiv), append one JSON line
to `data/weight_shadow_{mode}.jsonl`:

```
{"day": "2026-09-28", "mode": "test", "at": <ms>,
 "symbols": {"SOLUSDT": {"w": 1, "lock": "r5_arm25",
                         "p7": [pct, n, preset], "p14": [pct, n, preset],
                         "policies": {"static": 1, "tilt": 1.12, "brake": 1}}, ...}}
```

Policies (proposed weights, never applied):
- `static` — the current weight (baseline).
- `tilt` — bounded momentum: `w × clamp(1 + 0.3·tanh(p14/50), 0.7, 1.3)` when the 14-day
  top row has ≥ 20 trades, else `w`. At most ±30 %, so even a wrong signal cannot
  concentrate the book.
- `brake` — asymmetric safety: `w × 0.5` when the 14-day top row has ≥ 30 trades and
  Profit% ≤ −30, else `w`. Cuts only on sustained, sizeable losses.

`scripts/eval_weight_shadow.py` (host, stdlib, read-only) scores each policy: for every
snapshot day and symbol, the forward 7-day rank-1 Profit% (rank 1 = the would-be-real slot)
from the order files; policy value = Σ normalised weight × forward Profit%. Prints each
policy vs `static`, per mode, with the number of days. After 3–4 weeks of clean live data
this decides whether any policy goes live (a separate, approved change).

## Chosen approach

- Dashboard-side, in the existing 30 s worker (`instrumentation.ts`) right after the
  store refresh: the store already holds every number; no bot restart, no new files read
  on the candle path. Snapshot written once per mode per store-day (idempotent: skipped if
  the last line already has that day).
- Weights from `readRiskConfig(mode).symbol_weights`; locks via `lockedPresetsFor`, so
  the top row equals the picker's number.

## Rejected

- **Applying a Profit%-weight system now**: no predictive value on the available data.
- **An automatic brake now**: real orders showed slight week-to-week reversal; a brake
  would have cut symbols just before recoveries as often as it saved. Shadow-measured
  first like the rest.
- **Reusing weight_rebalancer**: it writes risk_config every candle (real money path) and
  blends backtest scores; the question here is measurement, not control.

## Touch points

`dashboard/app/api/trades/_weight-shadow.ts` (new), `dashboard/instrumentation.ts`,
`scripts/eval_weight_shadow.py` (new), tests.

## Risk flags

None on trading: nothing reads the shadow file except the evaluation script. One small
append per mode per day.

## Addendum (2026-09-28): backfill from existing data

`scripts/backfill_weight_shadow.py <mode> [--write]` reconstructs a snapshot for every past
day from order history (only trades opened before that day; today's weights and locks),
writes `data/weight_shadow_{mode}_history.jsonl`, and prints the scores. The Risk-page
panel shows this "History (reconstructed)" record next to the live one
(`evaluateShadow(mode, 7, 'history')`), so a verdict exists without waiting weeks.

First result, testnet, 112 days (105 scored), funded book, would-be-real trades: tilt
+0.08 %/week vs current weights (±0.05 se), better on 49/105 days, ~2 weight changes a day;
brake +0.00 % (±0.01), better on 8/105. Equal-weight book of all 22 symbols: tilt +0.05 %
(±0.02) on would-be-real, −0.06 % (±0.05) on top-preset trades. Neither is worth
switching on. The same run showed the funded symbols' top preset earning +5.4 %/week on
its own virtual trades while the would-be-real trades lost −0.55 %/week — the gap between
signal and execution is where the money is.
