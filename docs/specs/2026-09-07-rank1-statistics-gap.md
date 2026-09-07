# Rank-1 Statistics Gap — the top preset records nothing when its real order is blocked

**Date:** 2026-09-07
**Status:** spec — approved for implementation, ranking impact deferred
**Related:** `bot/virtual_order_simulator.py`, `main.py`, TODO.md session 67

---

## What it does

Gives the preset selected for real orders — the rank-1 preset, or a manually locked one
— a virtual order whenever its real order was **not** placed, so a blocked signal still
produces a data point.

The virtual orders it creates are recorded but **excluded from preset ranking** at first,
so the change can be measured against current behaviour before it influences which
preset trades real money.

## Why

`bot/virtual_order_simulator.py:210`:

```python
rank_idx = rank - (2 if is_locked else 1)
# "idx 0 = best, used for real orders → virtual starts at idx 1"
```

For an active, weighted symbol:

| case | best/locked preset gets a virtual order? |
|---|---|
| Not locked | **No** — `sorted_presets[0]` is skipped; rank 2 starts at index 1 |
| Locked | **No** — the locked preset is removed from `preset_items` entirely |
| Disabled symbol (`virtual_only=True`) | Yes — index 0 is included |

So when the top or locked preset generates a signal and the real order is blocked — by
`skip_max_sl_pct`, `skip_duplicate_sl`, `skip_profit_factor`, `skip_zero_score`,
min-notional, blackout hours, or an already-open position — **nothing is recorded
anywhere**.

### Measured cost

| symbol | real orders placed | signals blocked, no record at all |
|---|---|---|
| **TIAUSDT** | 12 | **115** |
| EIGENUSDT | 32 | 81 |
| INJUSDT | 21 | 8 |

Locked presets carry visibly thinner histories than their peers on the same symbol:

| symbol | locked preset virtual records | median for other presets |
|---|---|---|
| TIAUSDT | **95** | 195 |
| SOLUSDT | **34** | 144 |
| EIGENUSDT | 164 | 178 |

### Why it matters beyond the missing rows

The ranking key is `sum(recent_trades[-10:])`. It currently compares:

- **rank 1** — a *filtered* sample: only signals that survived every real-order filter
- **ranks 2–87** — the *unfiltered* population of everything their preset generated

That is survivorship bias in the one place it decides where money goes. It is also
self-reinforcing: the moment a preset becomes rank 1 it stops accumulating broad
evidence, so its score freezes on a thin biased sample while challengers keep moving.
TIAUSDT's locked preset recorded 12 outcomes from 127 signals.

## Chosen approach: a dedicated rank-1 virtual pool

Add rank 1 to the simulator's pools. It opens **only** when no real order was placed for
that symbol on that candle.

Rejected alternatives:

**Shift every rank down so index 0 always enters the pool.** Rank *N* would change
meaning, invalidating 163k historical orders keyed by rank and every `virtual_balance_rankN`
file. The comparison we already have is worth more than the tidiness.

**Reuse rank 2 when no real order is placed.** Rank 2 is the runner-up preset; overloading
it would mix two different presets' outcomes in one pool and one balance.

**Record the blocked signal in `preset_efficiency` directly, with no simulated order.**
There would be no exit price, so no PnL — only a count. The whole value is knowing what
the trade *would have done*.

## Design

### Opening

`on_candle_close()` gains `real_order_placed: bool = False`.

- `virtual_only=True` (disabled symbol): unchanged. Index 0 already enters at rank 2, so
  rank 1 stays empty and nothing double-counts.
- `real_order_placed=True`: rank 1 must not open, and any open rank-1 position for that
  symbol is evicted with `result='real_order_took_over'`.
- Otherwise: rank 1 opens with the preset that *would* have traded — the locked preset if
  set, else `sorted_presets[0]`.

### Balance

Rank 1 gets its own pool like every other rank, seeded by
`sync_real_balance_on_start()`. It must not share the real balance, or a virtual fill
would move real allocation.

### Ranking exclusion (first phase)

`virtual_tracker` must not count rank-1 orders towards `preset_efficiency` yet. The
records are written to `virtual_orders_rank1_{symbol}_{mode}.json` and are visible on the
dashboard, but `recent_trades` and `total_winning_usdt` are untouched.

This is deliberate: turning it on immediately would change preset selection at the same
moment the data changes, leaving no baseline. A follow-up decision, with a week of
parallel data, flips it on.

### Mirror consistency

`bot/virtual_order_simulator.py` is marked "must not change" in the mirror spec, because
the simulation has to be identical across instances or the live-vs-test comparison is
meaningless. This change lands on **both** instances at once — they share the image — so
they stay identical. Historical numbers keep their meaning because ranks 2–87 are
untouched and rank 1 is new.

## Touch points

- `bot/virtual_order_simulator.py` — rank-1 pool, `real_order_placed`, eviction on takeover
- `main.py` — pass `real_order_placed` from the placement result at both `on_candle_close`
  call sites
- `bot/virtual_tracker.py` — ignore rank 1 when updating efficiency
- `dashboard` — `closed_early` and other non-outcome results currently vanish from the
  Wins/Part/Trail/Losses columns (see below)

## Out of scope, recorded for later

Two adjacent distortions found while measuring this, both larger:

- **`rank_change` is 36.8% of all closed virtual orders** (60,266 of 163,668, avg +0.31).
  A position evicted because the preset at that rank changed did not exit on strategy.
- **`closed_early` is 3.9%** (6,460, avg +0.61) — every restart force-closes open virtual
  positions, while *real* positions are saved and restored. The mirror restarted 8 times
  on 2026-09-07, which is why its table looks so odd.

Together **41% of the ranking metric's input is a bookkeeping exit rather than a strategy
exit.** Fixing that is a separate, larger change: persist open virtual positions across
restarts the way real ones already are, and reconsider whether a rank change should close
a position at all.

## Risks

- **The ranking is what spends money.** Mitigated by excluding rank 1 from efficiency in
  this phase; nothing about preset selection changes.
- **Disk**: one more rank pool, ~1/86th more virtual order files. `data/` is 144 MB with
  5 GB free.
- **Double counting** if a real order is placed after a rank-1 virtual opened in the same
  candle. Mitigated by evicting on takeover, and by a test asserting the two never
  coexist.

## Success criteria

1. A blocked signal on a locked or rank-1 preset produces a rank-1 virtual order.
2. A placed real order produces no rank-1 virtual order for that symbol/candle.
3. `preset_efficiency` is byte-identical to current behaviour — ranking is unaffected.
4. TIAUSDT's locked preset starts recording the ~115 signals per period it currently loses.
