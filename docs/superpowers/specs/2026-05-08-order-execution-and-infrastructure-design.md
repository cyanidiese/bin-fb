# Spec: Order Execution, Mode Switching, Alerting, and Supporting Infrastructure
**Date:** 2026-05-08  
**Branch target:** feature/order-execution  
**Status:** Awaiting implementation plan

---

## 0. Architecture Scalability Audit — 15 Symbols

**Conclusion: 15 symbols is maintainable without structural changes.**

### Reasoning

| Concern | Current design | 15-symbol load | Verdict |
|---|---|---|---|
| Async workers | One `asyncio` task per symbol | 15 coroutines, all I/O-bound | ✅ No issue |
| WebSocket connections | One connection per symbol | Binance combined stream: all 15 symbols on one WS URL (`/stream?streams=s1@kline_15m/s2@kline_15m/...`) | ✅ Use combined stream |
| REST kline polling | Only on startup + gap detection | 15 × startup fetches, staggered by `asyncio` scheduling | ✅ No issue |
| JSON file I/O | One write per symbol per candle close | 15 writes per 15-minute period, staggered | ✅ No contention |
| Dashboard polling | Next.js polls `results_{symbol}.json` per symbol | Client-side; no server load | ✅ No issue |
| Rate limits | 2400 weight/min REST limit | 15 symbols × 1 ticker call/15s ≈ 1 req/s = 60 weight/min | ✅ Well within limits |

**Action required:** Migrate from per-symbol WebSocket connections to the Binance combined stream endpoint.  
**No other structural changes needed.**

---

## 1. Mode Model

### 1.1 Two Modes

| | Test mode | Live mode |
|---|---|---|
| REST klines | `testnet.binancefuture.com` | `fapi.binance.com` |
| WebSocket price stream | `stream.binancefuture.com` | `fstream.binance.com` |
| Order placement | `testnet.binancefuture.com` (real API calls, no real money) | `fapi.binance.com` (real API calls, real money) |
| Credentials | `TESTNET_API_KEY` / `TESTNET_API_SECRET` | `API_KEY` / `API_SECRET` |
| Balance | Virtual — `test_starting_balance_usdt` from `risk_config.json` | Real account balance queried via API |

`TRADING_MODE` in `.env` sets the **default mode** at first startup only. Runtime mode is then persisted to `data/bot_mode.json` and managed at runtime.

### 1.2 Obligatory Backtest Gate

**Triggers:** every bot start and every mode switch.

Before any orders are placed, the bot:
1. Fetches fresh klines for all active symbols from the mode's REST endpoint.
2. Runs `backtest.py` across all symbols using those klines.
3. Reseeds `data/preset_efficiency_{mode}.json` from the results.

If the backtest fails (network error, kline fetch timeout): the bot **aborts startup / blocks the mode switch**, fires an emergency alert, and waits for manual retry. It does not fall back to stale efficiency data.

### 1.3 Mode Persistence

`data/bot_mode.json`:
```json
{ "mode": "test", "switched_at": "2026-05-08T10:00:00Z" }
```

### 1.4 Testnet API Retirement

The `.env` variable `TRADING_MODE=testnet` is renamed semantically to `TRADING_MODE=test`. Existing `.env` files with `testnet` are accepted and treated as `test` during migration. `LIVE_MODE_CONFIRMED=yes` guard is removed — mode switching is handled through the dashboard confirmation flow instead.

---

## 2. Command Channel — Bot ↔ Dashboard

### 2.1 Design

Option C: file-based commands with a dedicated 2-second poll loop in the bot, independent of the candle loop.

**Files:**
- `data/bot_command.json` — written atomically by Next.js API routes (write to `.tmp`, rename)
- `data/bot_command_result.json` — written by bot after processing

**Command structure:**
```json
{ "id": "uuid4", "type": "switch_mode|stop_bot", "payload": {}, "issued_at": "ISO8601" }
```

**Result structure:**
```json
{ "id": "uuid4", "ok": true|false, "error": "...", "completed_at": "ISO8601" }
```

### 2.2 Poll Loop

A dedicated `asyncio` task in `main.py` polls `data/bot_command.json` every 2 seconds. On finding a command:
1. Acquire a `asyncio.Lock` (`_command_lock`) — prevents concurrent command execution.
2. Delete / clear `bot_command.json` immediately (prevents re-processing on crash).
3. Execute the command.
4. Write result to `bot_command_result.json`.
5. Release lock.

### 2.3 Dashboard Command Flow

Next.js writes the command file, then polls `bot_command_result.json` every 2 seconds until it sees a result with a matching `id` (timeout: 60 seconds). If timeout is hit, show an error.

---

## 3. Bot State — RUNNING and STOPPED

### 3.1 STOPPED Is a First-Class State

STOPPED is a fully supported operating state, not an error condition. In STOPPED state:
- No orders are placed, no WebSocket connections are open, no symbol workers are running.
- The dashboard is fully usable: backtests can be run, settings can be changed, logs can be viewed.
- The bot can be started from the dashboard (Start Bot button) or from the terminal (`python main.py`).

### 3.2 Process Management

The Next.js server is the always-running layer. It spawns and manages the bot process using the same `child_process.spawn` pattern already used for `backtest.py` and `discover.py`:

- **Start bot**: `POST /api/bot/start` → Node.js `spawn('python', ['main.py'], { detached: true, stdio: 'ignore' })` → store PID in `data/bot_pid.json` → return immediately.
- **Stop bot**: write `data/bot_command.json` (bot reads via 2s poll and shuts down cleanly) **or** send SIGTERM to PID from `data/bot_pid.json` if command file goes unacknowledged within 10s.
- **Bot started from CLI**: PID is written to `data/bot_pid.json` by the bot itself on startup. Dashboard detects it via `bot_state.json` heartbeat regardless of who started the process.

The Next.js server is **not required** for the bot to run — the bot functions independently if started from the terminal. The dashboard is a UI layer, not a hard dependency.

### 3.3 Heartbeat File

`dashboard/public/bot_state.json` (written by bot every 10 seconds):
```json
{
  "running": true,
  "pid": 12345,
  "mode": "test",
  "started_at": "ISO8601",
  "last_heartbeat": "ISO8601",
  "symbols_active": 15,
  "symbols_disabled": 0
}
```

Dashboard considers bot **RUNNING** if `last_heartbeat` is less than 30 seconds old.  
On clean shutdown, bot writes `"running": false` before exiting.  
If the process is killed, the file goes stale naturally — dashboard shows STOPPED after 30s.

### 3.4 Backtest While Stopped

"Run Backtest" on the Backtest page works regardless of bot status — Next.js spawns `backtest.py` directly (existing behaviour, preserved). The bot does not need to be running for manual backtests.

---

## 4. Test / Live Mode Switching (§1 of prompt)

### 4.1 Mode Switcher — Settings Page

A clearly labelled toggle on the Settings page. On click:

1. Dashboard queries open orders (from `bot_state.json` / order state file).
2. If open orders exist: confirmation dialog shows unrealised P&L sum — "Switching mode will close all open orders at market price. Current unrealised P&L: +$X.XX / −$X.XX. Continue?"
3. If no open orders: "Switch to [LIVE / TEST] mode?"
4. On confirm: write `bot_command.json` with `type: "switch_mode"`, poll for result.

### 4.2 Bot-Side Mode Switch Sequence

1. Acquire `_command_lock`.
2. Acquire `_order_placing_lock` (wait until any in-progress PLACING state resolves, max 30s timeout).
3. Call `close_all_orders_at_market()` — shared utility (see §7.3).
4. Discard all open virtual orders (mark as `abandoned`, not counted in efficiency stats).
5. Stop all symbol worker tasks.
6. Update `data/bot_mode.json`.
7. Re-initialize `DataFeed` with new mode credentials and endpoints.
8. Clear kline cache for all symbols (rename old cache files with `_retired` suffix, do not delete).
9. Run obligatory backtest (§1.2). If fails → revert mode, write error result, alert.
10. Reseed efficiency data.
11. Restart symbol worker tasks.
12. Update `dashboard/public/bot_state.json`.
13. Write success result to `bot_command_result.json`.

### 4.3 Global Mode Badge

Rendered in `layout.tsx`, before the nav menu. Reads `bot_state.json` polled every 10 seconds.

| State | Display | Style |
|---|---|---|
| Test + Running | `TEST · RUNNING` | Neutral gray border |
| Test + Stopped | `TEST · STOPPED` | Gray, dimmed |
| Live + Running | `LIVE · RUNNING` | Amber border, pulsing dot |
| Live + Stopped | `LIVE · STOPPED` | Amber border, no pulse |

Clicking the badge navigates to the Settings page.

### 4.4 Settings Page — UI Preview Section

Three checkboxes (purely cosmetic, no backend effect):
- "Imitate live mode running" — renders badge as LIVE + RUNNING, shows amber styling across page
- "Imitate test mode running" — renders badge as TEST + RUNNING
- "Imitate emergency notice" — renders a sample red alert banner with dummy content

These override the real state locally in the browser only.

---

## 5. Start Bot / Stop Bot Control (§4 of prompt)

### 5.1 Settings Page Buttons

- **Start Bot** button — visible only when bot is STOPPED. No confirmation required. On click: `POST /api/bot/start` → Next.js spawns `python main.py` detached, stores PID in `data/bot_pid.json`, returns immediately. Dashboard polls `bot_state.json` until `running: true` appears (timeout 30s → show error).
- **Stop Bot** button — visible only when bot is RUNNING. Confirmation dialog: shows unrealised P&L if orders are open.

### 5.2 Bot-Side Stop Sequence

1. Acquire `_command_lock`.
2. Acquire `_order_placing_lock` (max 30s timeout).
3. Call `close_all_orders_at_market()`.
4. Discard all virtual orders (mark `abandoned`).
5. Stop all symbol worker tasks.
6. Write `bot_state.json: { running: false }`.
7. Write success result to `bot_command_result.json`.
8. Call `sys.exit(0)`.

If the bot does not acknowledge the stop command within 10s, the dashboard falls back to sending SIGTERM to the PID in `data/bot_pid.json`.

### 5.3 Shared Utility — `close_all_orders_at_market()`

Implemented once in `bot/order_executor.py`. Used by both Stop Bot and mode switching.

- In live mode: calls `client.futures_cancel_all_open_orders(symbol=s)` + market close for each open position.
- In test mode: calls testnet API equivalents.
- Returns: list of `{ symbol, side, entry_price, close_price, pnl_usdt }` for the confirmation dialog and log.
- If any close fails: logs the error, fires a warning alert, continues closing remaining symbols (does not abort the stop).

---

## 6. Emergency Alerting & Telegram (§2 of prompt)

### 6.1 `bot/notifier.py`

Public interface:
```python
def notify(level: Literal["info", "warning", "emergency"], title: str, body: str, source: str) -> None
```

Never throws. On call:
1. Appends entry to `data/system_log.json` (capped at 100 entries — see §7.1).
2. Appends to `dashboard/public/alert_state.json` if level is `warning` or `emergency`.
3. Sends Telegram message if token + chat_id are configured (non-blocking, fire-and-forget `asyncio.create_task`).
4. If Telegram send fails: logs locally only, does **not** re-call `notify` (prevents loops).

### 6.2 Emergency Conditions

All wired to `notifier.notify("emergency", ...)`:

| Condition | Source | Detail |
|---|---|---|
| Binance API rate limit hit (HTTP 429) | `DataFeed`, `OrderExecutor` | Symbol, endpoint, retry-after value |
| Symbol no longer tradeable | `OrderExecutor` on placement | Symbol name, HTTP error code → triggers §9 disable flow |
| WebSocket disconnected AND REST polling failing | `DataFeed` per symbol | Symbol, last successful update timestamp |
| Order placement failed N consecutive times | `OrderExecutor` per symbol | Symbol, preset, failure count, last error |
| Drawdown hard stop triggered | `RiskManager` (already exists, wire here) | Balance at trigger, peak balance, drawdown % |
| Unhandled exception in main loop | `main.py` top-level `try/except` | Exception type, traceback (first 500 chars) |
| Account balance below `min_balance_usdt` floor | `RiskManager.update_balance()` | Current balance, configured floor |
| All symbols disabled simultaneously | `bot/mode_manager.py` | Reason summary → also triggers auto-stop |
| `close_all_orders_at_market()` partial failure | `OrderExecutor` | Which symbols failed to close, errors |

`consecutive_failure_threshold` (default 3) is stored in `risk_config.json`.  
`min_balance_usdt` (default 0.0 = disabled) is stored in `risk_config.json`.

### 6.3 Alert State File

`dashboard/public/alert_state.json`:
```json
{
  "alerts": [
    {
      "id": "uuid4",
      "level": "emergency",
      "title": "Drawdown hard stop triggered",
      "body": "Balance dropped 15.2% from peak. Hard stop active.",
      "source": "RiskManager",
      "timestamp": "ISO8601"
    }
  ],
  "dismissed_ids": ["uuid4-of-dismissed-alert"]
}
```

Dashboard shows all alerts whose `id` is not in `dismissed_ids`.

### 6.4 Dashboard Alert Banner

- Persistent red bar at the top of every page (rendered in `layout.tsx`), above the mode badge and nav.
- Shows each non-dismissed emergency/warning alert: timestamp, level chip, title, "Details" expand.
- Each alert has an X button. On click: POST to `/api/alerts/dismiss` with `{ id }` → server appends to `dismissed_ids` in `alert_state.json`.
- Banner disappears entirely when all alerts are dismissed.
- The banner re-appears on page reload until explicitly dismissed (server-side persistence).

### 6.5 Telegram Setup

`TELEGRAM_SETUP.md` documents:
1. Create bot via @BotFather, copy token.
2. Send any message to the bot.
3. Call `https://api.telegram.org/bot<TOKEN>/getUpdates` in browser.
4. Read `result[0].message.chat.id`.
5. Enter token + chat ID in Settings page → "Send test notification" button.

Token and chat ID stored in `risk_config.json` under `telegram: { token, chat_id }`.

### 6.6 Settings Page — Telegram Section

- Token input + chat ID input (saved on blur or Save button).
- "Send test notification" button → POST `/api/telegram/test` → bot sends a test message → inline ✅ or ❌ feedback.

---

## 7. System Log (§3 of prompt)

### 7.1 `data/system_log.json`

Append-only, capped at **100 entries** (oldest dropped on overflow).

Entry schema:
```json
{
  "id": "uuid4",
  "timestamp": "ISO8601",
  "level": "info|warning|emergency",
  "title": "string",
  "detail": "string",
  "source": "module name"
}
```

Written by `notifier.notify()` on every call (all levels).

### 7.2 Log Page — `/log`

New dashboard page. Reads `data/system_log.json` via `GET /api/log`.

| Column | Notes |
|---|---|
| Timestamp | Local timezone, formatted |
| Level | Color-coded chip: gray (info), amber (warning), red (emergency) |
| Source | Module name |
| Title | Short description |
| Detail | Collapsed by default, expandable row |

Filterable by level (multi-select chips) and date range (two datetime-local pickers).  
Default sort: newest first.

### 7.3 Unread Badge

Nav link "Log" shows a badge with count of `warning` + `emergency` entries since `last_read_timestamp`, stored in `localStorage`. Cleared (set to 0) when the user visits the Log page.

---

## 8. Order Execution System (§5 of prompt)

### 8.1 `bot/order_executor.py` — Replaces `bot/paper_trader.py`

Mode is injected at construction. All execution logic is identical regardless of mode — only the exchange interaction layer differs.

**Constructor:**
```python
OrderExecutor(mode: Literal["test", "live"], settings: Settings, risk_manager: RiskManager, notifier: Notifier)
```

### 8.2 Order State Machine (per symbol)

```
IDLE → PLACING → OPEN → PARTIAL_EXIT → CLOSED → IDLE
```

- `PLACING`: atomic lock. Mode switch and Stop Bot commands wait for this state to resolve (max 30s timeout → emergency alert + force-close attempt + back to IDLE).
- `OPEN`: TP/SL placed. Checked on every price tick.
- `PARTIAL_EXIT`: trailing stop armed or partial take triggered.
- `CLOSED`: trade complete. P&L recorded. Transition back to IDLE.

One real/simulated order per symbol at a time.

### 8.3 Order Placement Priority

On each candle close, for all symbols not in `PLACING` or `OPEN` state:

**First pass — leverage 1:**
1. Load efficiency stats: `data/preset_efficiency_{mode}.json`.
2. Sort active symbols by efficiency score (descending).
3. For each symbol:
   - Skip if disabled in registry.
   - Skip if no eligible preset (< 4 trades in efficiency data → use backtest data as fallback seed).
   - Check `RiskManager.can_open(symbol, size_at_leverage_1)`.
   - Check allocated USDT ≥ symbol's minimum notional (from leverage bracket data).
   - If all pass → call `_place_order(symbol, preset, leverage=1)`.

**Second pass — leverage escalation:**
4. After first pass, for remaining eligible symbols with no order:
   - Retrieve cached leverage brackets (fetched on startup via `/fapi/v1/leverageBracket`).
   - For each symbol in efficiency order: try next valid leverage tier.
   - If `RiskManager.can_open()` fails at new tier → retry at previous tier.
   - If both fail → skip symbol.
   - Continue until no escalation possible or capital exhausted.
   - Never exceed `max_leverage` from `risk_config.json`.

### 8.4 `_place_order()` Internals

In **live mode**: `futures_create_order(MARKET)` → on fill confirmation → place TP (`TAKE_PROFIT_MARKET`) + SL (`STOP_MARKET`) simultaneously. State → `OPEN`.

In **test mode**: same API calls against testnet.binancefuture.com. Identical code path.

On any API error during placement: increment `consecutive_failure_count[symbol]`. If count ≥ `consecutive_failure_threshold` → fire emergency alert, trigger symbol disable flow (§9).

### 8.5 Price Check Loop

On every WebSocket price event (or REST poll fallback, see §11):
- For each symbol in `OPEN` or `PARTIAL_EXIT` state: call `FakeOrder.check(price)` equivalent logic.
- `FakeOrder` is reused as the TP/SL/trailing logic layer for both modes.
- In test mode: `FakeOrder` checks drive testnet order cancellation API calls when TP/SL fires.
- In live mode: the exchange fires TP/SL orders itself; the bot reconciles state from order fill events.

### 8.6 Startup Reconciliation

On every start (after obligatory backtest completes):
- Query exchange for open positions per symbol.
- If stale position found with no matching bot state: close immediately, log as emergency, continue.

---

## 9. Symbol Disable Flow (§6 of prompt)

### 9.1 Trigger Conditions

- Symbol no longer tradeable on exchange (HTTP error on order attempt).
- `consecutive_failure_count[symbol]` ≥ `consecutive_failure_threshold`.

### 9.2 Disable Sequence (atomic)

1. Cancel any open real/simulated order for the symbol at market price.
2. Stop symbol's async worker task (kline polling, trend engine, WebSocket listener).
3. Mark symbol disabled in `symbol_registry.json`: `{ "disabled": true, "disabled_reason": "...", "disabled_at": "ISO8601" }`.
4. Redistribute the symbol's allocation weight proportionally among remaining active symbols with `weight > 0`. Update `RiskManager` allocation immediately.
5. Call `notifier.notify("emergency", ...)` with symbol, reason, closed order details (side, entry, close price, P&L).
6. If all remaining symbols are now disabled: call `stop_bot()` automatically after notification.

### 9.3 Re-enable Flow

From the Symbols / Settings page, a disabled symbol shows a `DISABLED` badge with reason and timestamp.

"Re-enable" button triggers:
1. Validate symbol is still tradeable: call `/fapi/v1/exchangeInfo` (or testnet equivalent), check symbol status = `TRADING` and `contractType = PERPETUAL`.
2. If valid: remove `disabled` flag, restore allocation weight (split proportionally from active symbols), restart worker task.
3. If invalid: show error — symbol cannot be re-enabled.

---

## 10. Virtual Orders & Preset Efficiency Tracking (§7 of prompt)

### 10.1 `bot/virtual_tracker.py`

Runs one `FakeOrder` per `(symbol, preset)` simultaneously in both test and live modes.  
Uses the same price feed as the main bot (testnet prices in test mode, real prices in live mode).  
No exchange API calls. No capital consumed.

### 10.2 State Files (per mode)

- `data/virtual_orders_{mode}.json` — current open virtual orders per (symbol, preset)
- `data/preset_efficiency_{mode}.json` — cumulative efficiency stats per (symbol, preset)

On bot restart: open virtual orders are **resumed** at current price. The gap during downtime is noted as an approximation in the state entry (`resumed_at` field).

On mode switch: virtual orders are **abandoned** (marked, not counted in stats). New virtual orders start fresh after the obligatory backtest reseeds efficiency data.

### 10.3 Efficiency Metric

`total_winning_usdt`: sum of all profitable virtual trade outcomes in USDT for a given (symbol, preset).

**Seeding on first run (per mode):**
- Read `dashboard/public/backtest_results_{symbol}.json` for each symbol.
- For each preset: `total_winning_usdt = sum(trade.profit_usdt for trade in trades if trade.profit_usdt > 0)`.
- Written to `data/preset_efficiency_{mode}.json`.

**Update after each virtual trade closes:**
- Append closed trade to `data/virtual_orders_{mode}.json` history.
- Update `total_winning_usdt` in `data/preset_efficiency_{mode}.json`.

### 10.4 Most Efficient Preset Selection

For a given symbol: read `preset_efficiency_{mode}.json`, find preset with highest `total_winning_usdt` and ≥ 4 closed trades. If none qualify (new symbol, no history): default to the preset that performed best in the most recent backtest. If no backtest data: symbol is skipped in the first-pass order placement until virtual orders accumulate data.

### 10.5 Efficiency Score for New Symbols

Symbol added mid-run: `total_winning_usdt = 0`, `trades = 0` initially → placed last in order priority queue until virtual trades accumulate.

---

## 11. Price & Kline Reliability (§8 of prompt)

### 11.1 WebSocket — Combined Stream

All symbols share one WebSocket connection per mode:
- Test: `wss://stream.binancefuture.com/stream?streams=btcusdt@kline_15m/ethusdt@kline_15m/...`
- Live: `wss://fstream.binance.com/stream?streams=...`

On disconnect: exponential backoff reconnect (existing logic, already in `DataFeed`). During disconnect: fall back to REST polling (§11.2) per symbol.

### 11.2 Price Feed Fallback

If no price event received for a symbol within 15 seconds (configurable: `price_stale_threshold_s`, default 15):
- Switch that symbol's price feed to REST polling: `GET /fapi/v1/ticker/price?symbol=X` every 5 seconds.
- Log the fallback via `notifier.notify("warning", ...)`.
- When WebSocket resumes and price event received for symbol: switch back automatically, log recovery.

### 11.3 Kline Feed Gap Detection

On startup or symbol add:
- Fetch 1500 klines via REST.

On each candle close:
- Append new kline to cache.
- Check gap: if new kline's `open_time > last_cached_kline.close_time + 1 candle_interval` → gap detected → re-fetch 1500 klines, log as warning.

WebSocket kline gap:
- If no `kline_close` event within `expected_interval × 1.5` (e.g., 22.5 minutes for 15m candles) → fetch last 10 klines via REST, merge, log.

---

## 12. Per-Symbol Allocation Updates (§9 of prompt)

### 12.1 Allocation Weight Step

All allocation weight inputs on the Risk page use `step="0.01"`.

### 12.2 One-Time Rebalance on System Start

**Deliberate action, not a side effect.** On first start of the new system (detected by absence of `data/preset_efficiency_{mode}.json`):

1. Run obligatory backtest.
2. For each symbol: compute `efficiency_score = total_winning_usdt` from best preset in backtest.
3. Normalize scores to sum to 1.0 (symbols with score 0 get a minimum weight of `1 / (n_symbols × 10)`).
4. Write these as initial allocation weights to `risk_config.json`.
5. Log this rebalance via `notifier.notify("info", "Initial allocation weights set", ...)`.

Subsequent runs: user manages allocation weights manually via the Risk page. The bot does not auto-rebalance.

---

## 13. Edge Cases and Resolutions

| # | Edge case | Resolution |
|---|---|---|
| 1 | Mode switch while order is in `PLACING` state | Acquire `_order_placing_lock` before switching; wait max 30s; on timeout → emergency alert + force-close + proceed |
| 2 | Stop Bot while mode switch in progress | Both use `_command_lock`; second command queues until first completes |
| 3 | All symbols disabled simultaneously | Auto-stop bot, fire emergency alert, wait for manual symbol re-enable |
| 4 | Obligatory backtest fails on mode switch | Abort switch, revert mode, fire emergency alert, require manual retry |
| 5 | Concurrent mode switch from two browser tabs | Dashboard polls result by `id`; bot processes commands sequentially via `_command_lock` |
| 6 | Virtual order open when bot restarts | Resume at current price; mark `resumed_at` in state; gap in tracking is an acceptable approximation |
| 7 | New symbol added mid-run with no efficiency data | Default `total_winning_usdt = 0`, placed last in order priority; virtual orders accumulate naturally |
| 8 | Symbol disable with 0-weight symbols in registry | Redistribute only among symbols with `weight > 0`; zero-weight symbols remain zero |
| 9 | Leverage bracket fetch fails on startup | Log as warning, use conservative default tiers `[1, 2, 3, 5, 10, 20]`; retry on next restart |
| 10 | `close_all_orders_at_market()` fails for one symbol | Log error, fire warning alert, continue closing remaining symbols; do not abort stop/switch |
| 11 | Binance REST rate limit during WebSocket fallback | 15 symbols × 1 ticker call / 5s = 3 req/s = 180 weight/min (well within 2400 limit); log and continue |
| 12 | Bot process killed mid-write to state file | All state writes use atomic rename; partial writes are impossible |
| 13 | Balance floor check: test mode uses virtual balance | `RiskManager.update_balance()` always compares against `min_balance_usdt` regardless of mode |
| 14 | Paper results files still in `dashboard/public/` | Explicitly deleted during migration step in implementation plan |
| 15 | `TRADING_MODE=testnet` in existing `.env` | Treated as `test` by migration shim in `config/settings.py`; log deprecation notice at startup |

---

## 14. New & Modified Files

### New Python files
| File | Purpose |
|---|---|
| `bot/notifier.py` | Telegram sender, alert state writer, log appender |
| `bot/order_executor.py` | Unified order execution (test + live), replaces paper_trader |
| `bot/virtual_tracker.py` | Virtual orders per (symbol, preset), efficiency stat updates |
| `bot/mode_manager.py` | Runtime mode state, 2s command poll loop, mode switch sequence |
| `bot/system_log.py` | Append-with-cap to `data/system_log.json` |

### Modified Python files
| File | Change |
|---|---|
| `bot/risk_manager.py` | `"paper"` mode → `"test"`; add `min_balance_usdt` check; wire `notify()` |
| `bot/data_feed.py` | Mode becomes runtime-injectable (not `.env`-only); combined WS stream |
| `bot/order_manager.py` | Add `PLACING` lock; connect failure counter to notifier |
| `bot/symbol_registry.py` | Add `disabled`, `disabled_reason`, `disabled_at` fields per symbol |
| `config/settings.py` | Accept `testnet` as alias for `test`; remove `LIVE_MODE_CONFIRMED` guard |
| `config/risk_config.py` | Add `telegram`, `min_balance_usdt`, `consecutive_failure_threshold`, `test_starting_balance_usdt`, `max_leverage`, `price_stale_threshold_s` |
| `backtest.py` | Accept `--mode` param; callable from `mode_manager` |
| `main.py` | Wire: mode_manager, order_executor, virtual_tracker, notifier, heartbeat writer |

### Deleted files
| File | Reason |
|---|---|
| `bot/paper_trader.py` | Replaced by `order_executor.py` |
| `paper_trade.py` | Replaced by main bot loop |
| `dashboard/app/paper/` | Entire directory |
| `dashboard/public/paper_results_*.json` | Stale data |
| `dashboard/public/paper_state_*.json` | Stale data (if present) |

### New dashboard files
| File | Purpose |
|---|---|
| `dashboard/app/log/page.tsx` | System log page |
| `dashboard/app/api/log/route.ts` | Serves `data/system_log.json` |
| `dashboard/app/api/mode/route.ts` | GET current mode, POST mode switch command |
| `dashboard/app/api/bot/start/route.ts` | POST spawn `main.py`, store PID |
| `dashboard/app/api/bot/stop/route.ts` | POST stop bot command (file + SIGTERM fallback) |
| `dashboard/app/api/alerts/dismiss/route.ts` | POST dismiss alert by ID |
| `dashboard/app/api/telegram/test/route.ts` | POST trigger test notification |
| `dashboard/components/ModeBadge.tsx` | Global mode + running status badge |
| `dashboard/components/AlertBanner.tsx` | Top-of-page emergency alert bar |

### Modified dashboard files
| File | Change |
|---|---|
| `dashboard/app/layout.tsx` | Add `AlertBanner`, `ModeBadge`, Log nav link |
| `dashboard/app/settings/page.tsx` | Mode switcher, Stop Bot, Telegram section, UI Preview section |
| `dashboard/components/NavBar.tsx` | Log link + unread badge |
| `dashboard/lib/types.ts` | New types for all new JSON shapes |

---

## 15. New Runtime Data Files (all gitignored)

| File | Purpose |
|---|---|
| `data/bot_mode.json` | Current runtime mode |
| `data/bot_pid.json` | PID of running bot process (written by bot on startup, read by dashboard for SIGTERM fallback) |
| `data/bot_command.json` | Pending command from dashboard |
| `data/bot_command_result.json` | Command execution result |
| `data/system_log.json` | Rolling 100-entry event log |
| `data/preset_efficiency_test.json` | Efficiency stats — test mode |
| `data/preset_efficiency_live.json` | Efficiency stats — live mode |
| `data/virtual_orders_test.json` | Virtual order state — test mode |
| `data/virtual_orders_live.json` | Virtual order state — live mode |
| `dashboard/public/bot_state.json` | Heartbeat + mode + symbol counts |
| `dashboard/public/alert_state.json` | Active alerts + dismissed IDs |
| `TELEGRAM_SETUP.md` | Telegram bot setup guide (committed, not gitignored) |

---

## 16. New `risk_config.json` Fields

```json
{
  "telegram": {
    "token": "",
    "chat_id": ""
  },
  "min_balance_usdt": 0.0,
  "consecutive_failure_threshold": 3,
  "test_starting_balance_usdt": 10000.0,
  "max_leverage": 20,
  "price_stale_threshold_s": 15
}
```

All fields default-merged (existing `load_risk_config()` pattern — missing keys get defaults, nothing is overwritten).
