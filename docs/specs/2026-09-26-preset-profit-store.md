# Preset Profit% store — every preset × shortcut × symbol × mode

Date: 2026-09-26. Supersedes the per-symbol "top only" cache (`symbol_sort_scores_{mode}.json`)
from `2026-09-24-trades-picker-profit-sort.md`.

## What

Store the Trades-page **Profit%** (and trade count) of **every preset**, for **every date
shortcut** (today, 24h, 7d, 14d, 30d, all), for **every registered symbol**, separately per
**mode** (test / live). Keep it current when orders close and as sliding windows move. The
symbol picker's sort key (the table's top row by Profit%: locked preset, else the best) is
then a lookup, not a computation.

## Why

- User request: numbers must exist for each preset, not just the current top, per mode.
- Lock / unlock no longer needs any recompute: the locked preset's number is already stored.
- One authoritative, timestamped data set that other features (or the bot, later) can read.

## Constraints measured

- The VPS has **1 CPU core**. Order files: 97 MB (test) + 18 MB (live) of JSON across
  ~88 rank files per symbol. Full parse of one symbol ≈ 100 ms in Python, ≈ 50 ms in Node.
- The mirror bot deliberately receives no `.env` (no secrets), so it cannot authenticate
  to the dashboard.

## Chosen approach

**The dashboard's Node process owns the store; the bot is not changed.**

1. `app/api/trades/_preset-profit-store.ts` computes one symbol at a time: one parse of
   `real_orders_{SYM}_{mode}.json` + every `virtual_orders_rank*_{SYM}_{mode}.json`, then
   all six windows and all listed presets from that single in-memory pass. Same inclusion
   rules as the page's `buildPresetRows` (real orders; CLOSED virtual orders with a result,
   ranks 1..max; overlap filter; presets = backtest names ∪ efficiency keys).
2. Storage: **one file per mode**, `data/preset_profit_{mode}.json`:
   ```json
   { "v": 1, "formula": 4, "mode": "test", "tz": "Europe/Kyiv",
     "symbols": { "SOLUSDT": { "fp": 1790412345678, "at": 1790412399000, "day": "2026-09-26",
       "ranges": { "7d": { "from": 1789807599, "presets": { "r5_arm25": [9.2104, 23] } } } } } }
   ```
   `presets` holds only presets with ≥ 1 trade (absent = no trades = null). ~300 KB per mode.
   Test and live never share a file, and each file is written only by the dashboard.
3. **Recompute triggers** (per symbol, all windows at once):
   - an order of the symbol closed — `fp` = newest mtime across its order files changed;
   - the "today" date in `tz` changed (midnight passed);
   - older than 10 min (sliding windows moved);
   - formula version changed; or forced (script).
4. **Background worker**: `instrumentation.ts` `register()` (Node runtime only) starts a
   30 s interval that runs the stale check for both modes. The check is ~2k `stat` calls;
   a recompute happens only for stale symbols, one at a time, yielding between symbols so
   page requests are not starved. A `globalThis` mutex serialises the worker, API routes
   and the script (route and instrumentation bundles do not share module state).
   Result: numbers are updated within ≤ 30 s of any order close, whether or not anyone has
   the dashboard open, and no CPU is spent inside the trading process.
5. **Retrieval**:
   - `GET /api/trades/symbol-scores?mode&range&ensure` (picker) — reads the store, derives
     the top row per symbol with the *current* locks, computes `ensure` if missing.
   - `GET /api/trades/preset-profit?mode[&symbol][&range]` — the stored numbers.
   - `POST /api/trades/preset-profit {mode, force}` — full rebuild (used by the script).
6. `scripts/recalc_symbol_scores.sh` → one forced rebuild per mode via the POST.
7. "Today" uses `PROFIT_TZ` (default `Europe/Kyiv`), the user's timezone. The browser's
   `from` is no longer used for the stored numbers.

## Rejected

- **Compute in the bot (Python) on close**: runs inside the trading process on a 1-core box
  (100 ms/symbol GIL-bound, bursts at candle close when orders are placed), duplicates the
  formula in a second language, needs a bot deploy for every formula change.
- **Bot calls the dashboard over HTTP on close**: the mirror has no secrets to sign with;
  adds a runtime dependency of trading on the dashboard.
- **Incremental ledger** (append each close, never re-parse): most CPU-efficient, but must
  hook every code path that writes orders (restart closes, clear-history archiving, rank
  eviction) or drift silently from the files the table reads. Not worth it at ≈ 2 s CPU
  per 10 min.
- **Host cron**: another moving part outside the repo's deploy flow.
- **One file per symbol**: more files and a 22-file read per picker request for no real
  gain at 300 KB per mode.

## Touch points

- new `dashboard/app/api/trades/_preset-profit-store.ts`, `dashboard/instrumentation.ts`,
  `dashboard/app/api/trades/preset-profit/route.ts`
- rewrite `dashboard/app/api/trades/symbol-scores/route.ts` as a reader
- `scripts/recalc_symbol_scores.sh`, `tests/test_recalc_script_ranges.py`
- `lib/tradesDateRange.ts` unchanged (RANGE_PRESETS is the shortcut list)

## Risk flags

- Dashboard-only; bots untouched. Worst case a stale or missing store → picker falls back
  to registry order, exactly as today.
- Node event-loop blocking: ≤ ~100 ms per symbol recompute, yielded between symbols.
- `instrumentation.ts` runs once per server start; interval is `unref()`'d.
