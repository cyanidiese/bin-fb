# Slippage modelling in virtual orders

**Date:** 2026-09-13
**Status:** approved, not yet implemented
**Author:** session 70

---

## Why

Virtual orders are the evidence base for deciding which symbols to enable for real
trading. Today they assume the position opens at the exact signalled price. Real orders
are MARKET orders and fill at whatever the book gives.

Measured over 137 real fills (60 days, `fill_entry_price` vs `entry_price`, signed so
positive means we paid worse):

| scope | n | mean | median | p90 |
|---|---:|---:|---:|---:|
| all symbols | 137 | **+0.0980%** | +0.0183% | +0.3575% |
| EIGENUSDT | 36 | +0.0240% | +0.0224% | +0.0490% |
| ETHFIUSDT | 28 | +0.1356% | +0.0423% | +0.3825% |
| INJUSDT | 18 | +0.1690% | +0.0190% | +0.5462% |
| TIAUSDT | 17 | +0.1775% | +0.0000% | +0.5348% |
| REZUSDT | 12 | +0.0134% | +0.0000% | +0.0323% |
| MEMEUSDT | 10 | +0.1299% | +0.1814% | +0.1923% |
| SOLUSDT | 9 | +0.0472% | +0.0082% | +0.1399% |
| AVAXUSDT | 7 | +0.1179% | +0.0122% | +0.3484% |

Two facts drive the design:

1. **Slippage is never favourable.** Not one of the 137 fills came in better than
   signalled. The distribution is one-sided, so ignoring it is a pure, systematic
   overstatement of virtual results — not noise that averages out.
2. **It varies ~13x across symbols** (REZUSDT +0.013% vs TIAUSDT +0.178%), so a single
   global constant would misprice most symbols.

At 5x leverage, +0.098% of notional is roughly **0.49% of margin per trade**. That is an
order of magnitude larger than the whole measured virtual edge (−0.052%/trade), which is
why symbol-selection decisions taken off unadjusted virtual numbers are unsafe.

## What it does

Records the slippage of every real fill, derives a per-symbol estimate, and charges that
estimate against virtual PnL so virtual and real are measured on the same basis.

## Chosen approach

Apply slippage **to the money only, never to the trigger geometry.**

This mirrors exactly what the real path already does. In `order_executor.place_order`:

* `FakeOrder` is constructed with the **signalled** `entry` — the comment at
  bot/order_executor.py:301 is explicit that the reconciled fill "deliberately does NOT
  feed the FakeOrder or the SL/TP geometry — those stay on the signalled entry so trigger
  levels are unchanged".
* `fill_entry_price` is stored separately and reaches PnL/fees only through
  `_effective_entry()` (bot/order_executor.py:1485).

So the virtual simulator must do the same: keep `FakeOrder` on the signalled entry, and
use an *effective* entry inside `_calc_pnl` alone. TP/SL still trigger at the same prices;
only the realised PnL is charged for the worse fill. Any other placement would change
which virtual orders win or lose, which is not what real slippage does.

Effective entry:

```
BUY :  eff = entry * (1 + slip_pct/100)
SELL:  eff = entry * (1 - slip_pct/100)
```

### Estimate selection, in order

1. `slippage_per_symbol[SYMBOL]` from risk_config, if set (manual override).
2. The symbol's own measured mean, once it has >= `slippage_min_samples` real fills.
3. `slippage_default_pct` otherwise.

Step 3 is the important one: the symbols we most want to judge are precisely the ones with
no real fills. The default must be conservative (charge at least the cross-symbol mean)
so a new symbol is never flattered into looking tradeable.

**Mean, not median.** The median is near zero for most symbols while the p90 is 10-20x
larger. The tail is what actually costs money, and the mean is the only statistic that
carries it into expected PnL.

## Rejected alternatives

* **Flat global haircut on all virtual PnL.** Simplest, but mispriced by up to 13x across
  symbols, and it would never improve as evidence accumulates.
* **Order-book depth modelling.** Most accurate, but we store no depth snapshots and it
  would need significant new plumbing on the hot path. Not justified for the size of the
  effect.
* **Shifting the virtual entry price itself.** Superficially more "realistic", but it
  moves the entry relative to fixed TP/SL and so changes win/loss outcomes. Real slippage
  does not do this — real trigger levels stay put. Rejected as unfaithful.
* **Modelling exit slippage too.** Deliberately out of scope — see below.

## Known gap: exit slippage is not modelled

Exit slippage cannot be calibrated from current data. Of 282 `result == 'loss'` closes,
**64% closed better than the stop** (mean −0.53%), because the early-loss exit
(`max_losing_pct` / `early_loss_sl`) also records `result='loss'`. Only 61 closed at the
level and 40 past it. There is no field distinguishing a true stop-out from an early exit,
so any exit haircut would be a guess calibrated on contaminated data.

Modelling entry-only is therefore a deliberate under-correction: virtual stays slightly
optimistic, but by a smaller and honest margin. Closing this gap needs an explicit exit
reason recorded on close, which is separate work.

## Touch points

| File | Change |
|---|---|
| `bot/slippage.py` | **new.** `record()`, `estimate()`, `stats()`, rolling per-symbol store |
| `bot/order_executor.py` | after `_reconcile_entry_fill` (~line 305), persist the sample it already computes as `_adverse_pct` |
| `bot/virtual_order_simulator.py` | `_calc_pnl` uses the effective entry; resolve the estimate per symbol |
| `config/risk_config.py` | 4 new defaults |
| `main.py` | wire the store path, mode-scoped |

## Config (hot-reloadable)

```json
"slippage_model_enabled": true,
"slippage_default_pct": 0.10,
"slippage_min_samples": 5,
"slippage_per_symbol": {}
```

`slippage_default_pct` 0.10 ≈ the measured cross-symbol mean (+0.098%).
Setting `slippage_model_enabled: false` restores today's behaviour exactly.

Storage: `data/slippage_{mode}.json`, rolling window of the last 50 samples per symbol,
so the estimate tracks changing liquidity instead of being anchored to old fills.

## Risk flags

* **Virtual PnL will drop across the board.** Expect roughly −0.5% of margin per virtual
  trade at the default. This is a correction, not a regression — but every preset's
  `total_winning_usdt` shifts, so the efficiency scoreboard is re-based. Relative ranking
  is preserved because the charge is uniform within a symbol.
* **Do not compare pre- and post-change virtual numbers directly.** Historical virtual
  orders on disk are not retro-adjusted.
* Writes on the close path must never raise — the store follows the same
  never-throw discipline as `balance_history`.

## Test plan

* slippage never improves a fill (one-sided) for both BUY and SELL
* estimate falls back: override → measured → default; `min_samples` respected
* rolling window caps at 50 and drops oldest
* `_calc_pnl` charges the effective entry; `FakeOrder` entry/TP/SL untouched
* disabled flag reproduces current PnL exactly
* corrupt/missing store returns the default and never raises
