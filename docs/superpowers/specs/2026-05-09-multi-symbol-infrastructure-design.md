# Multi-Symbol Infrastructure — Design Spec
**Date:** 2026-05-09
**Status:** Approved, pending implementation plan
**Supersedes:** Portions of `2026-05-04-multi-symbol-design.md` relating to `main.py` concurrency model and DataFeed streaming. That spec covers configuration, file naming, and backtest/paper layers — this spec covers the live bot runtime.

---

## Goal

Extend `main.py` and its supporting modules to handle 15 active symbols on a single combined WebSocket stream with per-symbol isolated state, automatic symbol health monitoring, and correct order sizing with leverage progression.

---

## 1. Architecture Assessment — 15 Symbols

### Verdict: viable with two targeted changes

| Concern | Current | After this spec |
|---|---|---|
| WebSocket connections | 1 per symbol (N connections) | 1 combined stream for all symbols |
| Order price check | `check_all_orders_price(price)` applies one price to all symbols | `check_symbol_price(symbol, price)` scoped per symbol |
| Kline cache / Analyzer | Already per-symbol | Unchanged |
| File I/O (export) | Single symbol per candle close | Per-symbol on each symbol's candle close, sequential |
| REST API rate limit | ~30 weight at startup, ~2/candle | Unchanged; 15 symbols still well within 1200/min |
| Balance fetch | Once per candle close | Debounced: once per candle period (30s minimum gap) |

No thread pool changes, no process-level changes. Pure asyncio is sufficient.

---

## 2. DataFeed — Combined Stream

### 2.1 New method: `stream_combined`

```python
async def stream_combined(
    self,
    get_symbols: Callable[[], list[str]],   # called on each reconnect
    timeframe: str,
    on_candle_close: Callable[[str, list], Awaitable[None]],
    on_price_update: Callable[[str, float], Awaitable[None]],
) -> None:
```

`get_symbols` is a callable (not a captured list) so that reconnects after symbol add/remove use the current active symbol set from `SymbolRegistry`.

Combined stream message format from Binance:
```json
{"stream": "btcusdt@kline_15m", "data": {"k": {...}}}
```

Symbol is parsed from `msg["stream"].split("@")[0].upper()`.

`on_candle_close(symbol, kline)` and `on_price_update(symbol, price)` both receive the symbol as the first argument.

Reconnect logic: same exponential backoff as `stream_klines`. On each reconnect, call `get_symbols()` to rebuild the combined URL — this naturally picks up runtime symbol changes.

### 2.2 Existing `stream_klines` unchanged

Kept for single-symbol use (backtest API, tests). Not used by the live bot after this change.

### 2.3 Candle deduplication guard

`stream_combined` maintains `_last_candle_open: dict[str, int]` (symbol → open_time_ms). A candle is only dispatched to `on_candle_close` if its open time is greater than the last seen for that symbol. This prevents the watchdog (Section 4) and the WebSocket from double-firing the same candle.

---

## 3. DataFeed — Price & Candle Watchdog

A background task started alongside `stream_combined`.

### 3.1 Price watchdog

Runs every 5 seconds. For each active symbol: if `monotonic_time - last_price_ts[symbol] > price_stale_threshold_s` (from `risk_config.json`, default 15s), fetch current price via `futures_symbol_ticker(symbol)` and call `on_price_update(symbol, price)`.

### 3.2 Candle watchdog

Runs every 30 seconds. For each active symbol: if `monotonic_time - last_candle_ts[symbol] > 1.5 × timeframe_ms`, fetch the latest closed kline via `futures_klines(symbol, limit=2)` and call `on_candle_close(symbol, kline)` if the kline is closed and its open time is newer than `_last_candle_open[symbol]` (dedup guard applies).

### 3.3 Method signature

```python
async def start_watchdog(
    self,
    get_symbols: Callable[[], list[str]],
    timeframe: str,
    on_candle_close: Callable[[str, list], Awaitable[None]],
    on_price_update: Callable[[str, float], Awaitable[None]],
    stale_threshold_s: float = 15.0,
) -> None:
```

Started as an `asyncio.create_task` alongside `stream_combined`. Cancelled in the same `finally` block.

`last_price_ts` and `last_candle_ts` are updated by `stream_combined` on every message received, so the watchdog only activates during true silence.

---

## 4. OrderExecutor — Per-Symbol Price Check

### 4.1 Rename `check_all_orders_price` → `check_symbol_price`

```python
async def check_symbol_price(self, symbol: str, current_price: float) -> list[dict]:
```

Checks only the FakeOrder for the given symbol. Does not iterate other symbols.

`check_all_orders_price` is removed. Callers (including tests) updated to use `check_symbol_price`.

### 4.2 `check_all_orders` unchanged

Used by backtester. Not affected.

---

## 5. OrderExecutor — MIN_NOTIONAL and Leverage Computation

### 5.1 Extend lot cache

`_ensure_lot_size` already calls `futures_exchange_info()` and caches `step_size` and `min_qty`. Add `min_notional` from the `MIN_NOTIONAL` filter in the same API response.

Lot cache entry:
```python
{'step_size': float, 'min_qty': float, 'min_notional': float}
```

Add public helper:
```python
async def get_min_notional(self, symbol: str) -> float:
    lot = await self._ensure_lot_size(symbol)
    return lot.get('min_notional', 0.0)
```

### 5.2 Leverage bracket fetch

On startup and mode switch, fetch `/fapi/v1/leverageBracket` for all active symbols via `futures_leverage_bracket()`. Cache as:
```python
_bracket_max: dict[str, int]  # symbol → max leverage from first bracket
```

Add public helper:
```python
def get_bracket_max(self, symbol: str) -> int:
    return self._bracket_max.get(symbol, 20)  # safe default
```

```python
async def fetch_leverage_brackets(self, symbols: list[str]) -> None:
```

### 5.3 Optimal leverage computation (in `main.py` before `place_order`)

```python
allocation    = min(risk_manager.get_allocation(symbol), last_known_balance)
min_notional  = await order_executor.get_min_notional(symbol)
bracket_max   = order_executor.get_bracket_max(symbol)
target_lev    = risk_manager.get_leverage(symbol)

# Minimum leverage to meet min_notional
import math
min_viable_lev = math.ceil(min_notional / allocation) if allocation > 0 else 999

if min_viable_lev > bracket_max:
    logger.info(f"[{symbol}] Cannot meet min_notional even at max leverage, skipping")
    return

actual_lev = max(min_viable_lev, min(target_lev, bracket_max))
quantity   = allocation * actual_lev / entry
```

No retries. Optimal leverage is computed directly.

---

## 6. Symbol Auto-Disable

### 6.1 `SymbolRegistry` passed to `OrderExecutor`

```python
OrderExecutor(
    ...,
    symbol_registry: SymbolRegistry | None = None,
)
```

### 6.2 Startup proactive check

After `OrderExecutor` is constructed, before the combined stream starts:

```python
await order_executor.check_symbols_on_exchange(symbols)
```

This calls `futures_exchange_info()`, iterates active symbols, and for any symbol where `status != "TRADING"` or `contractType != "PERPETUAL"`, calls `symbol_registry.disable(symbol, reason)` and `notifier.notify("emergency", ...)` for each disabled symbol.

### 6.3 Runtime disable on order error

In `_submit_to_exchange`, catch `BinanceAPIException`. Classify the error:

| Error code / message | Action |
|---|---|
| `-1121` (invalid symbol) | Disable symbol |
| `-2010` + "not available for perpetual" | Disable symbol |
| Any message containing `"is not available"` | Disable symbol |
| `-2019` insufficient margin | Do not disable — funds issue |
| `-1013` insufficient balance | Do not disable — funds issue |
| Other codes ≥ 3 consecutive failures | Disable symbol |

On disable:
1. `symbol_registry.disable(symbol, reason)` — redistribution handled inside `SymbolRegistry`
2. `await order_executor.close_order(symbol)` — close any open real order
3. `notifier.notify("emergency", f"Symbol {symbol} disabled: {reason}", ..., "order_executor")`

### 6.4 `main.py` disable gate

In `on_candle_close`, before signal processing:
```python
if symbol_registry.is_disabled(symbol):
    return
```

---

## 7. main.py Refactor

### 7.1 Per-symbol state

```python
symbols:      list[str]                 # from symbol_registry.get_symbols()
analyzers:    dict[str, Analyzer]       # symbol → Analyzer
sym_settings: dict[str, Settings]       # symbol → Settings (cached)
```

`RiskManager`, `OrderExecutor`, `VirtualTracker`, `Notifier`, `ModeManager` remain single shared instances.

### 7.2 Startup sequence

1. Load `SymbolRegistry` — source of truth for active symbols
2. For each symbol: `load_settings(symbol)`, construct `Analyzer`
3. Proactive exchange check: `order_executor.check_symbols_on_exchange(symbols)`
4. Fetch leverage brackets: `order_executor.fetch_leverage_brackets(symbols)`
5. For each symbol: `feed.load_klines(symbol, timeframe, limit=1500)`, `analyzer.build_from_klines(klines)`
6. For each symbol: `export(symbol, ...)` — write initial results
7. `order_executor.reconcile_with_exchange()`
8. Start `feed.stream_combined(...)` and `feed.start_watchdog(...)` as tasks

### 7.3 on_candle_close(symbol, kline)

```python
async def on_candle_close(symbol: str, kline: list) -> None:
    if symbol_registry.is_disabled(symbol):
        return

    settings  = sym_settings[symbol]
    analyzer  = analyzers[symbol]

    recs = analyzer.add_candle(kline)
    best = analyzer.get_best_recommendation()

    try:
        feed.refresh_klines(symbol, settings.timeframe, fetch_count=10)
    except Exception as e:
        logger.warning(f"[{symbol}] Kline refresh failed: {e}")

    # Debounced balance update (once per 30s across all symbols)
    if _should_poll_balance():
        try:
            balance = await order_executor.fetch_account_balance()
            if balance > 0:
                risk_manager.update_balance(balance)
        except Exception as e:
            logger.warning(f"Balance fetch failed: {e}")

    # Signal → order placement for this symbol only
    if best is not None and order_executor.get_state(symbol) == OrderState.IDLE:
        await _try_place_order(symbol, best, settings)

    export(symbol, settings.timeframe, settings.trading_mode,
           analyzer.get_current_price(), analyzer.get_trend(),
           analyzer.get_klines(), recs, analyzer.get_all_points(), best)

    if best:
        trades_logger.info(f"BEST | symbol={symbol} | {best}")
    for rec in recs:
        trades_logger.info(f"CANDIDATE | symbol={symbol} | {rec}")
```

### 7.4 on_price_update(symbol, price)

```python
async def on_price_update(symbol: str, price: float) -> None:
    if symbol in analyzers:
        analyzers[symbol].update_price(price)

    closed = await order_executor.check_symbol_price(symbol, price)
    for c in closed:
        virtual_tracker.record_closed_trade(c['symbol'], c['preset_name'], c['pnl_usdt'])
```

### 7.5 Balance debounce

Module-level in `main.py`:
```python
_last_balance_poll: float = 0.0
_BALANCE_POLL_INTERVAL = 30.0

def _should_poll_balance() -> bool:
    global _last_balance_poll
    now = time.monotonic()
    if now - _last_balance_poll >= _BALANCE_POLL_INTERVAL:
        _last_balance_poll = now
        return True
    return False
```

### 7.6 Terminal display

`display.show()` is removed from the per-candle callback. Logging to `bot.log` and `trades.log` covers all events. The dashboard provides the visual view.

### 7.7 Mode switch

`on_switch_mode` additionally calls:
- `order_executor.fetch_leverage_brackets(symbols)` after backtest completes
- `feed.refresh_klines(symbol, timeframe, fetch_count=1500)` for each symbol

---

## 8. Kline Bootstrap Fix

`feed.load_klines(symbol, timeframe, limit=1500)` on startup — hardcoded 1500, not `settings.kline_limit`. The cache limit (`KLINE_CACHE_LIMIT`, default 5000) is separate from the bootstrap fetch count.

`feed.refresh_klines(symbol, timeframe, fetch_count=1500)` on mode switch — ensures fresh history after endpoint change.

---

## 9. Files Changed

| File | Change |
|---|---|
| `bot/data_feed.py` | Add `stream_combined()`, `start_watchdog()`, candle dedup guard |
| `bot/order_executor.py` | Rename `check_all_orders_price` → `check_symbol_price`; add `get_min_notional()`, `get_bracket_max()`, `fetch_leverage_brackets()`, `check_symbols_on_exchange()`; extend lot cache with `min_notional`; add `symbol_registry` param |
| `main.py` | Full refactor: per-symbol dicts, combined stream, watchdog, leverage computation, debounced balance, SymbolRegistry wired |
| `tests/test_order_executor.py` | Update `check_all_orders_price` calls to `check_symbol_price` |

**Unchanged:** `RiskManager`, `Analyzer`, `VirtualTracker`, `FakeOrder`, `Backtester`, `SymbolRegistry`, all dashboard files.

---

## 10. Error Handling

- All per-symbol callbacks are wrapped in `try/except` — one symbol's error does not kill the combined stream loop.
- `stream_combined` error recovery: same exponential backoff as `stream_klines`. Reconnect rebuilds the symbol list via `get_symbols()`.
- Watchdog errors (REST fetch failures) are logged as warnings and skipped for that poll cycle.

---

## 11. Testing

- `test_order_executor.py`: update method name; add test for `get_min_notional()` with mock exchange info; add test for `check_symbol_price` scope isolation (two symbols open, only the targeted one closes)
- `test_data_feed.py` (new): test combined stream message parsing; test dedup guard rejects same-open-time candle twice
- Manual: run bot with 3+ symbols on testnet, verify each symbol's candle close is independent, verify watchdog fires on artificial silence

---

## 12. Open Decisions (Deferred)

- **Virtual orders for all presets** — Sub-project 2 adds `on_price_update` virtual simulation
- **Allocation by efficiency** — Sub-project 3
- **Confirmation dialogs** — Sub-project 3
