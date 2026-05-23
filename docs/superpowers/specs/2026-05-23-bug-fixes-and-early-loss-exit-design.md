# Design: IP Ban Fix, Duplicate Close Guard, Notional Cap, Early Loss Exit

**Date:** 2026-05-23  
**Status:** Approved  
**Affects:** `main.py`, `bot/data_feed.py`, `bot/order_executor.py`, `bot/fake_order.py`, `config/settings.py`, `config/presets.py`, `config/risk_config.py`, `bot/backtester.py`, `bot/virtual_order_simulator.py`

---

## Issue 1 — IP Ban: Staggered Kline Refresh

### Problem

The bot refreshes klines for all symbols simultaneously every candle close. With 15 symbols this fires 15 REST requests in a burst — enough to trigger Binance's -1003 IP ban (28 bans observed in logs since deploy).

### Solution: Option B — Refresh every 4 candles, staggered + gap detection

**`main.py`:**
- Add module-level constants:
  ```python
  KLINE_REFRESH_EVERY = 4   # candles between normal refreshes
  KLINE_STAGGER_SECS = 2    # seconds between each symbol's refresh task
  ```
- Add `_kline_refresh_counters: dict[str, int] = {}` tracking candles-since-last-refresh per symbol.
- On `on_candle_close(symbol, ...)`:
  1. Call `feed.has_gap(symbol, timeframe, incoming_open_ms)`.
  2. If gap detected: fire `asyncio.create_task(_refresh_klines(symbol, count=100, stagger=0))` — immediate, fetch 100 candles.
  3. If no gap: increment counter. If counter reaches `KLINE_REFRESH_EVERY`, reset to 0 and fire `asyncio.create_task(_refresh_klines(symbol, count=20, stagger=symbol_index * KLINE_STAGGER_SECS))`.
- `_refresh_klines(symbol, count, stagger)` is an async helper: sleeps `stagger` seconds then calls `feed.refresh_klines(symbol, timeframe, fetch_count=count)` in a thread.

**`bot/data_feed.py`:**
- Add method:
  ```python
  def has_gap(self, symbol: str, timeframe: str, incoming_open_ms: int) -> bool:
  ```
  Reads the last timestamp from the cached klines file for that symbol. Computes the expected next open_ms from the timeframe interval. Returns `True` if `incoming_open_ms > expected_next_ms + tolerance` (one interval tolerance to account for slight clock drift).
- Returns `False` if the cache file does not exist or cannot be read (no gap assumed — safe default).

### Behavior

Normal operation: at most 1 symbol refreshes per 4 candles, spread 2 s apart — maximum ~1 request per 2 s. After a cold start or server restart with a gap: immediate refresh with 100 candles, no stagger (urgency outweighs rate limit concern here since gaps are rare).

---

## Issue 2 — Duplicate Trade Write: Closing Guard

### Problem

`check_symbol_price` and `check_symbol_candle` run concurrently in asyncio. Both can see the same order in a closing state simultaneously. The `await` inside `_market_close` yields the event loop, allowing a second coroutine to pass the "is order closing?" check before the first has committed the close — resulting in two close attempts and two trade file writes.

### Solution: `_closing` Set Guard

**`bot/order_executor.py`:**
- Add `self._closing: set[str] = set()` to `OrderExecutor.__init__`.
- At the top of every path that triggers a close (`check_symbol_price`, `check_symbol_candle`, `check_all_orders`, or the inner close-dispatch logic):
  ```python
  if symbol in self._closing:
      return
  self._closing.add(symbol)
  ```
- In a `try/finally` block, remove symbol on completion:
  ```python
  finally:
      self._closing.discard(symbol)
  ```
- This is synchronous (no `await` before the add), so it is race-free under asyncio's cooperative multitasking model.

### Behavior

The first coroutine to reach a close decision claims the symbol. All subsequent coroutines for that symbol return immediately until the close completes. Normal operation is unaffected (symbol is removed from `_closing` immediately after close finishes).

---

## Issue 3 — MEMEUSDT Position Sizing: Notional Cap

### Problem

Low-price tokens (e.g. MEMEUSDT at ~$0.0044) combined with high leverage result in very large base-asset quantities. At 10× leverage and ~$50 USDT collateral, the bot was opening ~114,000 MEME with a $504 notional — far larger than intended.

### Solution: `max_order_notional_usdt` in risk config

**`config/risk_config.py`:**
- Add `max_order_notional_usdt: float = 500.0` to the risk config schema and default value.

**`bot/order_executor.py` — `_submit_to_exchange`:**
- After computing `quantity`, compute `notional = quantity * price`.
- If `notional > max_order_notional_usdt` and `max_order_notional_usdt > 0`:
  ```python
  quantity = max_order_notional_usdt / price
  quantity = round_step(quantity, lot_step)
  ```
- Log a warning when the cap is applied.

### Behavior

Orders are capped at `max_order_notional_usdt` USDT face value. Setting to `0` disables the cap entirely. Default `500.0` matches current intent. Does not affect backtester (backtester uses `FakeOrder` directly with pre-computed quantity).

---

## New Features — Early Loss Exit (`max_losing_pct`, `max_losing_amount_usdt`, `max_losing_candles`)

### Purpose

Allow orders to be closed early when they are losing — before the hard SL is hit. Useful for trimming damage on orders that are moving steadily against position.

### Three Independent Settings

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `max_losing_pct` | `float` | `0.0` | Close when adverse price reaches this % of the SL distance from entry. E.g. 50 = close at halfway between entry and SL. Range: 0–100 (values ≥100 are clamped to the SL itself and have no effect). Zero = disabled. |
| `max_losing_amount_usdt` | `float` | `0.0` | Close when unrealized loss exceeds this USDT. Callers pre-compute the equivalent adverse price threshold and pass it as `early_loss_sl`. Zero = disabled. |
| `max_losing_candles` | `int` | `0` | Close after this many consecutive candles where candle close is on the wrong side of entry (i.e. below entry for LONG, above entry for SHORT). Counter resets when price recovers above entry. Zero = disabled. |

All three can be active simultaneously. Early exit fires on whichever triggers first.

### Implementation: `FakeOrder`

**Constructor additions:**
- `max_losing_pct: float = 0.0`
- `max_losing_candles: int = 0`
- `early_loss_sl: float = 0.0` — pre-computed by caller from `max_losing_amount_usdt`. If `> 0`, used directly as an additional SL threshold alongside the pct-based one.

**`_early_exit_price` property:**  
Computes the tighter (closer-to-entry) of the pct-based threshold and `early_loss_sl`:
- Pct-based: `entry ± (sl_distance × max_losing_pct / 100)` where `±` is adverse direction.
- If both are set, take the price closer to entry (less loss before trigger).
- If only one is set, use that one.
- Returns `0.0` if neither is set.

**`_consecutive_losing_candles: int`** — counter.  
**`_last_losing_update_candle: int`** — last candle index where counter was updated, prevents double-counting on price ticks.

**`check(price, candle_index, update_losing_candles: bool = True)` (was `check(price)`):**
1. If not yet armed, check `_early_exit_price`:
   - For LONG: if `price <= _early_exit_price` and `_early_exit_price > 0`: return `result='loss'`, `close_price=_early_exit_price`.
   - For SHORT: if `price >= _early_exit_price` and `_early_exit_price > 0`: return `result='loss'`, `close_price=_early_exit_price`.
2. If `update_losing_candles` and `max_losing_candles > 0` and candle_index != `_last_losing_update_candle`:
   - Update `_last_losing_update_candle = candle_index`.
   - If candle close is on wrong side of entry: increment `_consecutive_losing_candles`.
   - Else: reset `_consecutive_losing_candles = 0`.
   - If `_consecutive_losing_candles >= max_losing_candles`: return `result='loss'`, `close_price=price`.
3. Continue with normal SL / TP / trailing logic.

**`check_price(price, candle_index=0)`** — calls `check(price, candle_index, update_losing_candles=False)`. Default `candle_index=0` preserves backward compatibility for callers that don't track candle indices.  
**`check_candle(candle, candle_index=0)`** — calls `check(candle_close, candle_index, update_losing_candles=True)`. Backtester passes the loop index; live executor passes `0` (only one candle arrives at a time, so duplicate-update guard is not needed in live context).

### Caller changes

**`config/settings.py`:** Add the three fields with zero defaults.

**`config/presets.py`:** Add three zero-default entries to every preset dict (so existing presets are unaffected). Callers that want early-exit behavior set non-zero values.

**`bot/order_executor.py`:** When constructing `FakeOrder` for a live order, compute `early_loss_sl`:
```python
early_loss_sl = 0.0
if preset.max_losing_amount_usdt > 0 and quantity > 0:
    early_loss_sl = entry_price - (preset.max_losing_amount_usdt / quantity)  # LONG
    # or entry_price + (preset.max_losing_amount_usdt / quantity)  # SHORT
```
Pass `max_losing_pct`, `max_losing_candles`, `early_loss_sl` to `FakeOrder`.

**`bot/backtester.py` and `bot/virtual_order_simulator.py`:** Same `early_loss_sl` computation before constructing `FakeOrder`.

### Behavior

- All three settings default to `0.0` / `0` — no existing behavior changes.
- Early exit fires before the hard SL. Result is always `'loss'`.
- `max_losing_candles` counter is only updated on real candle closes (not price ticks), preventing inflated counts from the same-candle repeated price updates.
- All three are fully backtestable.

---

## Implementation Order

1. Issue 2 (duplicate close guard) — lowest risk, high value
2. Issue 3 (notional cap) — isolated to `_submit_to_exchange`
3. Issue 1 (IP ban stagger) — touches `main.py` + `data_feed.py`
4. Early loss exit features — most complex, touches 6 files

---

## Testing Notes

- IP ban fix: verify by checking that `_refresh_klines` tasks fire at staggered intervals in logs.
- Duplicate close: confirm no duplicate entries in `data/trades_*.json` after close events.
- Notional cap: verify via backtest that MEME-like symbols no longer open >500 USDT notional.
- Early loss exit: backtest with non-zero values for each setting independently, then all three combined; verify zero-value defaults produce identical results to current behavior.
