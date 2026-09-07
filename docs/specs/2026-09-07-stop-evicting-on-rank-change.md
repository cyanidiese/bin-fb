# Stop killing practice positions when the rank table reshuffles

**Date:** 2026-09-07
**Status:** spec — approved for implementation
**Related:** `docs/specs/2026-09-07-rank1-statistics-gap.md`, TODO.md session 67

---

## What it does

Lets a virtual position at ranks 2..N run to its real exit — target, stop, or trail —
instead of being closed the moment the preset at that rank changes.

Rank 1 keeps its eviction, deliberately: it is the real-order slot's stand-in and must be
free for a real order at any moment.

## Why

`virtual_order_simulator.py`:

```python
# Evict if the preset at this rank changed
if existing and existing['preset_name'] != preset_name:
    await self._evict(symbol, rank, current_price, 'rank_change')
```

Each rank is a slot holding whichever preset is currently *N*th-best. The rankings shuffle
constantly, so a position is closed at whatever price happens to be current — not because
anything happened in the market.

Measured over 163,668 closed virtual orders:

| how it ended | n | share | average |
|---|---|---|---|
| hit stop | 61,532 | 37.6% | **−3.89** |
| **reshuffled away** | **60,266** | **36.8%** | **+0.31** |
| trailed out | 33,205 | 20.3% | **+4.63** |
| **killed by restart** | 6,765 | 3.9% | **+0.61** |
| hit target | 1,522 | 0.9% | **+11.10** |

The ranking key is `sum(recent_trades[-10:])`. Real outcomes are large in both directions;
artificial ones sit near zero, so they **dilute** — a preset with genuinely big wins and
losses reads flatter than it is, and good and bad presets look more alike. That makes
preset selection slower and noisier.

Concentration, by rank band:

| ranks | artificial share |
|---|---|
| **2–5** (competing to trade real money) | **23%** |
| 6–15 | 37% |
| 16–40 | 45% |
| 41–87 | 40% |

So the effect on real preset choice is roughly a quarter, not the headline 41%.

## Design

### 1. Ranks 2..N — no eviction on rank change

If the slot holds a position whose preset is no longer the one assigned to that rank,
**leave it running and skip the slot this candle**. `check_prices()` closes it naturally.
When it frees, the slot picks up whatever preset is then correct.

### 2. A preset may hold only one open position per symbol

Today this is guaranteed for free: eviction keeps every slot holding the currently-correct
preset, so a preset cannot appear twice. There is **no explicit guard** — verified.

Removing eviction breaks that invariant. Preset X could be running in slot 5 (now stale)
while a reshuffle also assigns X to slot 8, giving X two concurrent positions both
recording PnL into its own statistics.

So `_try_open` must skip a preset that already has an open position for that symbol.

### 3. Rank 1 keeps eviction — it must be ready to trade

Rank 1 is the real-order slot's stand-in. It must be free the instant a real order is
placed, so:

- `real_order_took_over` — already implemented; a real order evicts the rank-1 virtual.
- `rank_change` at rank 1 — **kept**. If the top or locked preset changes, the old
  position goes, because the slot has to represent whatever would trade *now*.

### 4. Promotion frees the preset

A consequence of (2): if preset X holds a position in slot 5 and is then promoted to rank 1
— or becomes the manually locked preset — the duplicate guard would stop rank 1 opening,
leaving the preset that should be ready to trade stuck holding a stale practice position.

So when a preset becomes the rank-1 / locked preset for a symbol, any open position it
holds in another slot is evicted with `promoted_to_real`, freeing it.

This is a bookkeeping exit, and it is the right trade: it happens once per promotion, and
the alternative is the real-order slot being unable to represent its own preset. Labelled
distinctly so it can be excluded from scoring.

### 5. Maximum position age

The long tail is severe: the longest practice positions ran **11 days**. Without eviction,
one stuck trade blocks a slot indefinitely. Presets have `max_losing_candles`, but not all
set it.

Practice positions older than `virtual_max_age_candles` (default **96** = 24h on 15m) are
closed with `max_age`, distinct from a natural exit so it can be excluded from scoring.

## Holding times, measured

| exit type | median | p90 | p99 | max |
|---|---|---|---|---|
| **natural** (loss/trail/win/partial) | **21 min** | 200 min | 1,171 min | — |
| reshuffled away | 45 min | 390 min | — | 7 days |

A slot will typically free within one or two candles. With 86 slots per symbol, throughput
stays ample.

## Touch points

- `bot/virtual_order_simulator.py` — remove `rank_change` eviction for ranks ≥ 2, add the
  one-position-per-preset guard, add `promoted_to_real`, add the max-age check
- `config/risk_config.py` — `virtual_max_age_candles`, default 96
- `main.py` — exclude `promoted_to_real` and `max_age` from `record_closed_trade`, as
  rank 1 already is

## Risks

- **History is not comparable across the change.** `rank_change` stops appearing as an
  outcome, so a preset's `recent_trades` window spanning the boundary mixes two regimes.
  Accepted; recorded here and in TODO.md.
- **Fewer records.** Slots turn over more slowly, so fewer positions open per candle. The
  ones recorded are real outcomes, which is the point.
- **A preset could be starved** if it is always assigned a slot already held by itself.
  Mitigated by the max-age valve and by the promotion eviction.
- **`virtual_order_simulator.py` is shared with the mirror.** Lands on both at once via
  the same image, so the comparison stays valid.

## Success criteria

1. A rank change at ranks ≥ 2 leaves the open position running; no `rank_change` record
   is produced there.
2. A preset never has two open positions for one symbol.
3. Rank 1 still evicts on a real order (`real_order_took_over`) and on a top-preset change.
4. A preset promoted to rank-1/locked has its other-slot position released
   (`promoted_to_real`).
5. A position older than `virtual_max_age_candles` closes with `max_age`.
6. `promoted_to_real` and `max_age` do not feed `preset_efficiency`.
