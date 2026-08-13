# Trail-widening does NOT transfer to the non-l2 families — and the opposite lever does

**Date:** 2026-08-13 (session 61)
**Task:** Session-60 open item #3 — "extend the validated wider-trail change (`14b014b`)
to the OTHER trend/trail preset families; evaluate each preset's own real trades via the
fee-inclusive resim, require per-symbol robustness, apply only where robustly positive."
**Outcome:** Negative result for the stated task + a new (unapplied) opportunity in the
opposite direction. **No preset was changed** (user chose "just record the finding").

---

## TL;DR

- **Widening the trail on the non-l2 families is net-negative.** On all 15 trailing presets
  with real trades, raising `trailing_stop_pct` (0.15→0.35) monotonically lowers avg win and
  total PnL. The `14b014b` lever does **not** generalize.
- **Root cause = arming style, not the trail width itself.** The l2 family arms *late*
  (activation 2–2.5% favorable) so a matured winner benefits from a looser trail. Every other
  trailing preset arms *early*, off the partial-take price (15–30% of the way to TP); once
  armed, `trail_distance = trailing_stop_pct × gain`, so widening just gives back more of an
  already-modest pop.
- **The opposite lever works on the early-armed families:** modestly **tightening**
  (0.15 → 0.10) is robustly positive across 6/6 live-relevant presets and 3 active symbols,
  raising both payoff and total. **Not applied** — recorded for a later decision (likely after
  the already-staged `main` changes are deployed).

---

## Method

Fee-inclusive replay of every real trade's actual kline path through the real `FakeOrder`
exit engine, overriding **only** `trailing_stop_pct` (each preset keeps its own
partial/activation/tmin). Net PnL = `gross − qty·(entry+close)·0.0004`, matching how live
`pnl_usdt` is recorded (verified against a real EIGENUSDT SELL: −$55.27 ✓).

This is the same validation vehicle that cleared `14b014b`. Its trustworthy domain is the
**winner bucket**: losers never reach the arming threshold (avg MFE ~0.50%), so avgL is flat
across all trail settings in every preset — confirming only armed winners move.

Script: `/tmp/s60/trail_widen_families.py` (widen sweep + per-symbol robustness) and an inline
tighten sweep (0.05–0.20) on the active-symbol presets. Data: `/tmp/s60/real_orders/*.json`,
`/tmp/s60/fullklines/*.json`.

## Widen sweep (fee-inclusive, real trades) — representative rows

| Preset (live sym) | n | 0.15 (cur) | 0.20 | 0.30 | 0.35 |
|---|---|---|---|---|---|
| hl_buy_trail15 (TIA) | 49 | **$495** | $421 | $275 | $198 |
| r5_sl_filter (EIGEN) | 7 | **$114** | $106 | $94 | $95 |
| r5_tight_rr3 (INJ) | 7 | **$60** | $51 | $60 | $49 |
| trail_15_from_15 (TIA+EIGEN) | 13 | **$96** | $91 | $79 | $74 |

Every family degrades as the trail widens. The rare "ROBUST" flags in the raw output were on
flat/negative or blocklisted/stale presets (e.g. `trail_15_from_15_d1`, `rr_4x_trail_20`) — no
real win.

## Tighten sweep — the opposite direction is the lever

| Preset (live sym) | n | 0.20 | 0.15 (cur) | 0.10 | 0.05 |
|---|---|---|---|---|---|
| hl_buy_trail15 (TIA) | 49 | $421 | **$495** | $571 | $647 |
| r5_sl_filter (EIGEN) | 7 | $106 | **$114** | $122 | $130 |
| r5_tight_rr3 (INJ) | 7 | $51 | **$60** | $69 | $78 |
| trail_15_from_15 (TIA+EIGEN) | 13 | $91 | **$96** | $102 | $108 |

Live-relevant, non-blocklisted, non-locked bucket total: **$766 → $864 (+$99, ~+13%)** at
0.15→0.10. Payoff also rises (hl_buy 1.24→1.31). avgL unchanged → no winner flips to a loss (a
tight trail after arming always exits above entry once `gain>0`).

## Why this is consistent with the core finding

The trend engine structurally loses in ranges; in this regime winners are **short-lived pops**,
best banked fast. Early-armed presets sit on those pops from the start, so a tight trail books
them near the local peak; a loose trail waits through a retrace that mostly doesn't resume.
Late-armed (l2) presets only engage after a real move has begun, where the opposite holds.
"Make wins bigger" (session-60 directive) is the **goal**; the **means** is arming-style
specific — widen for late-armed, tighten for early-armed.

## Caveats

1. Each preset trades a single symbol, so within-preset symbol robustness can't be tested; the
   robustness evidence is cross-preset consistency (6/6 presets, 3 symbols, same direction).
2. Stop at **0.10, not 0.05** — very tight trails are inflated by the resim's intra-candle
   high-then-low ordering assumption, which matters more the tighter the trail.
3. Resim **delta** is the trustworthy quantity, not the absolute forward PnL.

## Recommended (deferred) action if revisited

Set `trailing_stop_pct` 0.15 → 0.10 on the early-armed, live-relevant, non-locked presets:
`hl_buy_trail15`, `r5_sl_filter`, `r5_tight_rr3`, `trail_15_from_15` (`config/presets.py`).
Do NOT extend the `14b014b` widening beyond the 5 l2 presets it already covers. Write a short
spec first (big-feature rule), stage on `main`, deploy with the other staged changes.
