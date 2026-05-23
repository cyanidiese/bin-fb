# Session Requests — May 15 2026

Six independent improvements requested in one session. Each has its own section with root-cause analysis, design decision, and exact files to touch.

---

## Request 1 — Reduce /api/public-file request explosion on Backtest page

### Root cause
`useSymbols.ts` polls `/symbols.json?t=${Date.now()}` every **3 s**. Each call passes a new array to `setSymbols()` — even when the symbol list is identical — because React compares by reference. This causes `availableSymbols` in `SymbolContext` to change reference every 3 s, which triggers the `useEffect([availableSymbols, symbol])` in `backtest/page.tsx` that fires **15 parallel `/api/public-file`** requests (one per symbol). Result: 876 requests visible in DevTools within seconds.

Secondary contributors: `symbols.json?t=...` cache-busting itself (adds 1 req/3 s), `bot_state.json` (10 s), `alert_state.json` (10 s).

### Fix
**`dashboard/lib/useSymbols.ts`** — after fetching, compare the new list against the current state with a joined-string equality check. Only call `setSymbols` when the list actually changed. This is the single change that stops the cascade.

Also change the URL from `/symbols.json?t=${Date.now()}` to `/api/public-file?f=symbols.json` (no cache-buster needed — the API route reads from disk at request time) and raise the poll interval from **3 s → 15 s** (symbols change only when the user adds/removes one).

**`backtest/page.tsx`** — remove `symbol` from the dependency array of the cross-symbol `useEffect`. That effect fetches ALL symbols' data; the active symbol tab is irrelevant.

**Files:** `dashboard/lib/useSymbols.ts`, `dashboard/app/backtest/page.tsx`

---

## Request 2 — Don't auto-archive history on bot start; add manual "Clear History" button

### Current behaviour
`main.py` lines 234–243 call `virtual_tracker.clear_session_data(symbols)` and rename every `real_orders_*_test.json` to an archive file on every bot start. This means each restart loses the previous session's trades on the Trades page.

### Fix
**`main.py`** — remove the `virtual_tracker.clear_session_data()` call and the real-orders rename loop from the startup sequence. History now persists across restarts.

**New API route `dashboard/app/api/bot/clear-history/route.ts`** — POST handler that:
1. Reads all active symbols from `symbol_registry.json`
2. For each symbol, renames `real_orders_{sym}_{mode}.json` → timestamped archive (same logic as before)
3. Calls `virtual_tracker.clear_session_data()` — but this is Python, so instead the API writes a `data/clear_history.signal` file
4. The bot's main loop checks for this signal file each candle tick, runs the clear, deletes the signal

Actually simpler: the API just deletes/archives the JSON files directly from disk (dashboard has filesystem access via BOT_ROOT). Virtual tracker state (`preset_efficiency_{mode}.json`) also gets cleared.

**`dashboard/components/settings/BotControl.tsx`** — add a **"Clear History"** button below the Start/Stop button. States: idle → loading → success/error (3 s flash). Requires confirmation dialog. Works regardless of whether the bot is running.

**Files:** `main.py`, `dashboard/app/api/bot/clear-history/route.ts`, `dashboard/components/settings/BotControl.tsx`

---

## Request 3 — Datepickers in "Visual Preset" widget filter the "Orders" list

### Current behaviour
`dashboard/app/create/page.tsx` has `fromDate`/`toDate` state that filters `filteredKlines` and `chartTrades` for the chart. The `BacktestTradeList` component receives `chartTrades` (already filtered by kline index range that corresponds to the date window). So the trade list IS already filtered — but only by kline index, not by calendar date displayed to the user.

### Verification needed
Confirm whether `BacktestTradeList` already shows the filtered trades or whether it receives the full unfiltered `activeResult.trades`. If the former, the feature is already working and just needs a UX label clarification. If the latter, pass `chartTrades` instead.

Looking at `page.tsx` line 147–169: `chartTrades` is already the date-filtered subset. The trade list rendering should receive `chartTrades` not `activeResult.trades`.

**Files:** `dashboard/app/create/page.tsx` — verify `BacktestTradeList` receives `chartTrades`; fix if it receives the full list.

---

## Request 4 & 5 — Trades page: Presets Efficiency overhaul

### Current problems
- Only shows presets that appear in `virtual_summary` (presets with ≥1 virtual trade)
- Virtual-only presets show "—" for PnL (hides losses)
- The single filter "hide virtual-only" is too coarse; user wants separate filters for "no real orders" and "no virtual orders"
- Click on a preset does nothing — user wants to filter the orders table to that preset
- Widget is less informative than the Backtest page's Presets table (no win%, avg PnL, etc.)
- "Real Orders" heading should become "Trading Orders"

### Data available
`/api/trades?symbol=` returns:
- `real_orders[]` — full closed order records with `preset_name`, `pnl_usdt`, `result`, timestamps
- `virtual_summary` — `{preset_name: {trade_count, total_winning_usdt}}`
- `virtual_orders[]` — full virtual order records (same shape as real)
- `best_preset` — current best

The API needs to return the full **all-presets list** so the Efficiency table can show every preset (even those with 0 trades). The backtest results file has the full preset list; read it server-side and merge.

### Design

**API change (`dashboard/app/api/trades/route.ts`)** — add reading of `backtest_results_{symbol}_{mode}.json` (or just `backtest_results_{symbol}.json`). Return `all_preset_names: string[]` — the full list of known presets regardless of whether they have trades.

**Trades page state:**
- `selectedPreset: string | null` — clicking a row sets this; clicking again deselects
- `hideNoReal: boolean` — filter toggle (default false)
- `hideNoVirtual: boolean` — filter toggle (default false)

**Presets Efficiency table columns** (matching Backtest table richness):
| Column | Source |
|--------|--------|
| Preset | name |
| Type | Real / Virtual / Both / — |
| Trades | real_count + virtual_count |
| Real trades | count of `real_orders` for this preset |
| Win% | real wins / real trades (or virtual if no real) |
| Total PnL | sum of real `pnl_usdt` (negative shown in red) |
| Winning USDT | sum of positive real `pnl_usdt` |
| Avg PnL | total PnL / trade count |
| Virtual USDT | `total_winning_usdt` from virtual_summary (negative if losses) |

Clicking a row → highlights it and filters "Trading Orders" widget to show only that preset's orders. Clicking again deselects (show all).

**"Trading Orders" widget** (renamed from "Real Orders"):
- Shows `real_orders` filtered by `selectedPreset` (or all if none selected)
- Title: "Trading Orders ({count})" or "Trading Orders — {preset} ({count})" when filtered

**Filter controls** (in the Preset Efficiency section header):
- Checkbox: "Hide no-real" — hides presets with 0 real trades
- Checkbox: "Hide no-virtual" — hides presets with 0 virtual trades
- Existing "Hide virtual-only" becomes redundant; remove it or merge

**Files:** `dashboard/app/api/trades/route.ts`, `dashboard/app/trades/page.tsx`, `dashboard/lib/types.ts`

---

## Request 6 — Investigate: backtest shows orders but no real orders

### Finding
**Root cause: `hard_stop_active = True` in the running risk_manager.**

Evidence:
- `risk_state.json` on server: `balance=5000, peak=10000, drawdown=50%, hard_stop=True`
- `bot.log`: "Order skipped: hard_stop_active" for every BEST signal on TIAUSDT, 1000PEPEUSDT, DOGEUSDT since bot restart
- The bot IS detecting signals and logging BEST candidates — it's the risk guard that blocks actual order placement

### Why the virtual balance reset didn't fix it
`main.py`'s `order_executor.fetch_account_balance()` calls the **Binance testnet API** to get the real testnet account balance, which is **5,000 USDT**. This is the balance used to update `risk_manager`. Our reset of `virtual_balance_test.json` only affects the `VirtualOrderSimulator`'s separate internal counter — not the RiskManager.

The testnet account balance is 5,000 because actual testnet orders were placed in previous sessions, running the virtual "best preset" strategy with real Binance testnet money.

### Fix options
**Option A (immediate)**: Add a **"Reset Hard Stop"** button to the Risk page. When clicked, it writes a `data/reset_hard_stop.signal` file. The bot checks for this file each candle, calls `risk_manager.reset_hard_stop()`, and deletes the signal.

**Option B (structural, test mode only)**: In test mode, `fetch_account_balance()` should return the `VirtualOrderSimulator`'s balance instead of calling the Binance API. This makes the risk system track the virtual portfolio, not the testnet account. Requires passing a reference to the simulator into the OrderExecutor or providing a callback.

**Decision: Implement Option A** (least invasive, works immediately, doesn't change production trade logic). Option B is a larger refactor.

**Files for Option A:**
- `dashboard/app/api/risk/route.ts` — POST `{ action: 'reset_hard_stop' }` → write signal file
- `dashboard/app/risk/page.tsx` — add Reset Hard Stop button (only visible when `hard_stop_active` is true)
- `main.py` — check for `data/reset_hard_stop.signal` in the candle tick, call `risk_manager.reset_hard_stop()`, delete file

---

## Implementation order

1. Request 1 — useSymbols fix (pure frontend, immediate user-visible win)
2. Request 6 — Reset Hard Stop button (unblocks real orders right now)
3. Request 2 — Remove auto-archive + manual clear history button
4. Request 3 — Verify/fix datepicker → trade list filtering
5. Request 4/5 — Trades page Presets Efficiency overhaul (largest change)
