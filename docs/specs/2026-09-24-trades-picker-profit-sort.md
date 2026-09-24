# Trades page symbol picker — sort by top preset's Profit%

## What it does

The symbol picker on the **Trades page only** (`components/SymbolPicker.tsx`, rendered by
`app/trades/page.tsx`) is ordered DESC by the Profit% of each symbol's **top preset**
over the active date shortcut (Today / Last 24h / Last 7 days / Last 30 days / All
history), for the instance (mode) being viewed. The header `SymbolSwitcher` is untouched.

"Top preset" = the preset in the Rank-1 row of the Preset Efficiency table: the locked
preset if one is locked for (symbol, mode), otherwise the highest effective score.
Profit% = exactly the figure in that row's Profit% column for the same window.

## Why

The picker currently shows registry order, so the symbols worth looking at are scattered.
Sorting by what the top preset actually earns puts the money-makers (and the bleeders, at
the bottom) where the eye lands first.

## Approach (chosen)

A dashboard-side cache, `data/symbol_sort_scores_{mode}.json`:

```json
{ "SOLUSDT": { "7d": { "pct": 12.4, "preset": "l2_trend_buy",
                        "fp": "<real mtime>|<rank1 mtime>", "at": 1790000000000 } } }
```

One entry per symbol × mode × shortcut. Served and maintained by a new route,
`GET /api/trades/symbol-scores?mode=&range=&from=&ensure=`.

**Filling:** an entry is computed the first time a symbol is clicked while that shortcut
is active (`ensure=<symbol>`), as the user specified. Uncomputed symbols sit below the
computed ones in registry order.

**Invalidation — self-detecting, no hooks, no bot changes.** On every request each
existing entry for the requested (mode, shortcut) is recomputed if any of:

1. **Top preset changed** — `entry.preset` ≠ current top preset. Covers lock, unlock, and
   the unlocked rank-1 preset changing on its own when scores reorder.
2. **A top-preset order closed** — `entry.fp` ≠ current mtimes of
   `real_orders_{SYM}_{mode}.json` and `virtual_orders_rank1_{SYM}_{mode}.json`.
   Verified in the bot: a real order is always the top/locked preset's
   (`order_executor._record_real_order_close`), and rank 1 only ever holds the top/locked
   preset when the real slot is free (`virtual_order_simulator.on_candle_close`, rank-1
   branch). Both files are written **only on close** (`_append_rank_closed` appends closed
   records; open state is in memory). Ranks ≥ 2 never hold the top preset, so their
   constant churn (~70 files/hour) triggers nothing.
3. **Window slid** — sliding windows go stale with time even without closes. TTL:
   today / 24h = 10 min, 7d / 30d = 60 min, all = never.

**Reordering:** the picker refetches scores when the shortcut changes, the viewed
instance changes, a symbol is clicked, a preset is locked/unlocked, and after a manual
close. Hand-editing the date pickers does **not** refetch or reorder: the order of the last
shortcut stays (fallback `30d`, which matches the default ~1-month range).

**Timezone:** "Today" is local midnight in the browser, so the client sends the window
start (`from`, epoch seconds, empty for "all") computed by the existing `presetRange()`.
The server never guesses a timezone.

**Single source of truth for the maths.** The Profit% formula moves from `page.tsx` into
`lib/presetProfit.ts`; the top-preset/rank logic moves from `api/trades/route.ts` into
`api/trades/_top-preset.ts`. Page, `/api/trades` and the new route all call the same
code, so the sort key cannot drift from the number on screen.

## Rejected

- **Compute every symbol on every load** — ~88 rank files × 22 symbols ≈ 150 MB of JSON
  per mode per page load. Too slow.
- **Hooks in lock routes + a bot-side "dirty" marker on close** — touches order-execution
  code for a view feature, and misses the automatic rank-1 change. The fingerprint covers
  all three triggers with zero bot changes.
- **Delete-on-invalidate** — the symbol would fall to the bottom until clicked again.
  Stale entries are recomputed server-side instead, so they keep their place.
- **Client computes and POSTs the number** — it would need the whole payload in the
  browser for stale symbols the user did not click; the server already has the files.

## Touch points

| File | Change |
|---|---|
| `dashboard/lib/presetProfit.ts` (new) | `orderMarginPct`, `presetProfitPct(real, virt)` — extracted from `buildPresetRows` |
| `dashboard/lib/tradesDateRange.ts` | export `orderInRange` |
| `dashboard/app/api/trades/_top-preset.ts` (new) | `presetRanksFor(efficiency, locked, riskConfig)`, `topPresetFor(...)` — extracted from `route.ts` |
| `dashboard/app/api/trades/route.ts` | use the extracted helpers (behaviour unchanged) |
| `dashboard/app/api/trades/symbol-scores/route.ts` (new) | cache read / validate / recompute / atomic write |
| `dashboard/app/trades/page.tsx` | use `presetProfitPct`; fetch scores; sort `symbolsWithOrders` |

`SymbolPicker.tsx` needs no change — it renders `symbols` in the order given. It gets an
optional `scores` prop so each button's tooltip shows the Profit% it is sorted by.

## Risk flags

- **No bot code touched; no order path touched.** Worst case is a wrong picker order.
- The cache write is atomic (tmp + rename). Concurrent requests: last writer wins — the
  loser's entries are recomputed next time. Any read/compute failure falls back to
  registry order; the page never breaks on it.
- The Rank-1 preset is the **dashboard's** ranking (`effectiveScore`), which is what the
  table shows; the bot's tier-aware rank key can occasionally pick differently when
  unlocked. Sorting follows what the user sees.
- Known and **not changed here**: the page's Profit% excludes rank-1 virtual orders
  (`/api/trades` reads ranks 2..max). The sort key matches the table as-is.
