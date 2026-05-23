# Bug Fixes & Early Loss Exit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix IP bans, duplicate close writes, MEMEUSDT position sizing, and add three early-loss exit settings (`max_losing_pct`, `max_losing_amount_usdt`, `max_losing_candles`) that work in both live trading and backtesting.

**Architecture:** Four independent fixes (Tasks 1–3 are isolated, no dependencies between them). The early loss exit (Tasks 4–9) threads a new concept through `FakeOrder` → `Backtester` → `VirtualOrderSimulator` → `OrderExecutor` → `main.py`. Tasks 4 and 5 must be done before 6–9. Tasks 6, 7, 8 are independent of each other once 4–5 are done. Task 9 depends on Task 8.

**Tech Stack:** Python 3.11, asyncio, pytest, python-binance, dataclasses

**Spec:** `docs/superpowers/specs/2026-05-23-bug-fixes-and-early-loss-exit-design.md`

---

## File map

| File | Change |
|---|---|
| `bot/order_executor.py` | Task 1: add `_closing` set; Task 2: notional cap; Task 8: new params + `early_loss_sl` |
| `config/risk_config.py` | Task 2: add `max_order_notional_usdt` |
| `bot/data_feed.py` | Task 3: add `has_gap()` |
| `main.py` | Task 3: staggered kline refresh; Task 9: pass new params to `place_order` |
| `config/settings.py` | Task 4: add 3 new fields |
| `config/presets.py` | Task 4: add 3 keys to `PresetOverrides` |
| `bot/fake_order.py` | Task 5: `max_losing_pct`, `max_losing_candles`, `early_loss_sl`, counter, `_early_exit_price` |
| `bot/backtester.py` | Task 6: pass new `FakeOrder` params; pass candle open/close to `check()` |
| `bot/virtual_order_simulator.py` | Task 7: compute `early_loss_sl`, pass new `FakeOrder` params |
| `tests/test_order_executor.py` | Tasks 1, 2, 8 |
| `tests/test_data_feed.py` | Task 3 |
| `tests/test_fake_order_early_exit.py` | Task 5 (new file) |

---

## Task 1: `_closing` guard in `OrderExecutor`

Prevents two concurrent coroutines from both passing the "trigger detected" check and each firing a market-close + trade-write for the same symbol.

**Files:**
- Modify: `bot/order_executor.py`
- Modify: `tests/test_order_executor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_order_executor.py`:

```python
@pytest.mark.asyncio
async def test_check_symbol_price_skips_if_already_closing():
    """Second concurrent close attempt must be silently dropped."""
    ex = make_executor()
    from bot.fake_order import FakeOrder
    fake = FakeOrder(
        side='BUY', entry_price=100.0, tp=110.0, sl=90.0,
        level=1, signal_type='test', candle_index=0,
    )
    ex._fake_orders['BTCUSDT'] = fake
    ex._open_orders['BTCUSDT'] = OpenOrder(
        symbol='BTCUSDT', preset_name='p', side='BUY',
        entry_price=100.0, tp_price=110.0, sl_price=90.0,
        quantity=1.0, leverage=5,
    )
    ex._states['BTCUSDT'] = OrderState.OPEN

    # Simulate a concurrent close already in progress
    ex._closing.add('BTCUSDT')

    # Even though price is below SL (85 < 90), guard must short-circuit
    result = await ex.check_symbol_price('BTCUSDT', 85.0)
    assert result == []


@pytest.mark.asyncio
async def test_check_symbol_candle_skips_if_already_closing():
    ex = make_executor()
    from bot.fake_order import FakeOrder
    fake = FakeOrder(
        side='BUY', entry_price=100.0, tp=110.0, sl=90.0,
        level=1, signal_type='test', candle_index=0,
    )
    ex._fake_orders['BTCUSDT'] = fake
    ex._open_orders['BTCUSDT'] = OpenOrder(
        symbol='BTCUSDT', preset_name='p', side='BUY',
        entry_price=100.0, tp_price=110.0, sl_price=90.0,
        quantity=1.0, leverage=5,
    )
    ex._states['BTCUSDT'] = OrderState.OPEN
    ex._closing.add('BTCUSDT')
    result = await ex.check_symbol_candle('BTCUSDT', high=105.0, low=85.0,
                                          candle_open=102.0, candle_close=88.0)
    assert result == []
```

- [ ] **Step 2: Run test — expect `AttributeError: '_closing'`**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -m pytest tests/test_order_executor.py::test_check_symbol_price_skips_if_already_closing -v
```

Expected: `AttributeError` or `AssertionError`

- [ ] **Step 3: Add `_closing` set to `OrderExecutor.__init__`**

In `bot/order_executor.py`, at the end of `__init__` (after `self._symbol_candle_index: dict[str, int] = {}`):

```python
self._closing: set[str] = set()
```

- [ ] **Step 4: Guard `check_symbol_price`**

In `bot/order_executor.py`, in `check_symbol_price`, replace the block that starts with `open_order = self._open_orders.get(symbol)` (after `if result is None: return []`) so the entire close path is guarded:

```python
    result = fake_order.check_price(current_price)
    if result is None:
        return []

    if symbol in self._closing:
        return []
    self._closing.add(symbol)
    try:
        open_order = self._open_orders.get(symbol)
        if open_order is None:
            del self._fake_orders[symbol]
            return []

        software_close_price = fake_order.close_price or open_order.entry_price
        try:
            actual_close_price = await self._market_close(symbol, open_order, fallback=software_close_price)
        except Exception as exc:
            logger.error(f"Market close failed for {symbol}: {exc}")
            self._notifier.notify("warning", f"Failed to close {symbol}", str(exc), "order_executor")
            actual_close_price = software_close_price

        pnl = self._calc_pnl(open_order, actual_close_price)
        self._record_real_order_close(symbol, open_order, actual_close_price, result, pnl)
        del self._open_orders[symbol]
        del self._fake_orders[symbol]
        self._states[symbol] = OrderState.IDLE
        self._record_success(symbol)
        logger.info(
            f"Order closed (price tick): {symbol} result={result} "
            f"entry={open_order.entry_price} close={actual_close_price:.4f} pnl={pnl:.2f} USDT"
        )
        return [{
            "symbol": symbol,
            "preset_name": open_order.preset_name,
            "result": result,
            "pnl_usdt": pnl,
            "side": open_order.side,
            "entry_price": open_order.entry_price,
            "close_price": actual_close_price,
            "leverage": open_order.leverage,
        }]
    finally:
        self._closing.discard(symbol)
```

- [ ] **Step 5: Guard `check_symbol_candle`**

In `bot/order_executor.py`, in `check_symbol_candle`, after `if result is None: return []` and before `open_order = self._open_orders.get(symbol)`:

```python
    if result is None:
        return []

    if symbol in self._closing:
        return []
    self._closing.add(symbol)
    try:
        open_order = self._open_orders.get(symbol)
        if open_order is None:
            del self._fake_orders[symbol]
            return []

        software_close_price = fake_order.close_price or open_order.entry_price
        try:
            actual_close_price = await self._market_close(symbol, open_order, fallback=software_close_price)
        except Exception as exc:
            logger.error(f"Market close failed for {symbol}: {exc}")
            self._notifier.notify("warning", f"Failed to close {symbol}", str(exc), "order_executor")
            actual_close_price = software_close_price

        pnl = self._calc_pnl(open_order, actual_close_price)
        self._record_real_order_close(symbol, open_order, actual_close_price, result, pnl)
        closed_info = {
            "symbol": symbol,
            "preset_name": open_order.preset_name,
            "result": result,
            "pnl_usdt": pnl,
            "side": open_order.side,
            "entry_price": open_order.entry_price,
            "close_price": actual_close_price,
            "leverage": open_order.leverage,
        }
        del self._open_orders[symbol]
        del self._fake_orders[symbol]
        self._states[symbol] = OrderState.IDLE
        self._record_success(symbol)
        logger.info(
            f"Order closed (candle): {symbol} result={result} "
            f"entry={open_order.entry_price} close={actual_close_price:.4f} pnl={pnl:.2f} USDT"
        )
        return [closed_info]
    finally:
        self._closing.discard(symbol)
```

- [ ] **Step 6: Run tests — expect pass**

```bash
python -m pytest tests/test_order_executor.py -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add bot/order_executor.py tests/test_order_executor.py
git commit -m "fix: add _closing set guard to OrderExecutor to prevent duplicate market closes"
```

---

## Task 2: Notional cap (`max_order_notional_usdt`)

Prevents low-price tokens (e.g. MEMEUSDT) from opening positions with far larger USDT notional than intended.

**Files:**
- Modify: `config/risk_config.py`
- Modify: `bot/order_executor.py`
- Modify: `tests/test_order_executor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_order_executor.py`:

```python
@pytest.mark.asyncio
async def test_submit_to_exchange_caps_notional():
    """When notional > max_order_notional_usdt, quantity is reduced before the order is sent."""
    ex = make_executor(with_feed=True)
    # Simulate MEME: price=0.004, step=1.0, cap=100 USDT → max qty = 100/0.004 = 25000
    ex._lot_cache['MEMEUSDT'] = {
        'step_size': 1.0, 'min_qty': 1.0, 'min_notional': 0.0, 'tick_size': 0.0001,
    }

    submitted_qty = {}

    async def fake_create_order(**kwargs):
        submitted_qty['qty'] = float(kwargs['quantity'])
        return {'orderId': '1', 'avgPrice': '0.004'}

    ex._feed.client.futures_change_leverage = MagicMock(return_value={})
    ex._feed.client.futures_create_order = MagicMock(side_effect=lambda **kw: fake_create_order(**kw))

    with patch('bot.order_executor.asyncio.to_thread', side_effect=lambda f, **kw: f(**kw)):
        with patch('bot.order_executor.load_risk_config', return_value={
            'consecutive_failure_threshold': 3,
            'max_order_notional_usdt': 100.0,
        }):
            await ex._submit_to_exchange('MEMEUSDT', 'BUY', 50000.0, 10, entry_price=0.004)

    # 50000 units × 0.004 = 200 USDT > cap=100 → should cap to 25000 units
    assert submitted_qty.get('qty', 50000.0) <= 25001.0
```

- [ ] **Step 2: Run test — expect failure (signature mismatch or cap not applied)**

```bash
python -m pytest tests/test_order_executor.py::test_submit_to_exchange_caps_notional -v
```

Expected: `TypeError` (wrong args) or assertion failure

- [ ] **Step 3: Add `max_order_notional_usdt` to risk_config default**

In `config/risk_config.py`, add to `DEFAULT_CONFIG`:

```python
"max_order_notional_usdt": 500.0,
```

Place it after `"price_stale_threshold_s": 15,` for logical grouping.

- [ ] **Step 4: Add `entry_price` param and cap logic to `_submit_to_exchange`**

In `bot/order_executor.py`, change the `_submit_to_exchange` signature:

```python
async def _submit_to_exchange(self, symbol: str, side: str, quantity: float, leverage: int, entry_price: float = 0.0) -> str | None:
```

Then, inside `_submit_to_exchange`, after `lot = await self._ensure_lot_size(symbol)` and before `qty_str = self._qty_str(quantity, lot['step_size'])`, add:

```python
        if entry_price > 0:
            _cfg = load_risk_config()
            _max_notional = _cfg.get("max_order_notional_usdt", 0.0)
            if _max_notional > 0 and quantity * entry_price > _max_notional:
                _capped = float(
                    Decimal(str(_max_notional / entry_price)).quantize(
                        Decimal(str(lot['step_size'])), rounding=ROUND_DOWN
                    )
                )
                logger.warning(
                    f"[{symbol}] Notional cap: qty {quantity:.4f} → {_capped:.4f} "
                    f"(notional {quantity * entry_price:.2f} > cap {_max_notional:.2f} USDT)"
                )
                quantity = _capped
```

- [ ] **Step 5: Pass `entry_price` from `place_order` to `_submit_to_exchange`**

In `bot/order_executor.py`, in `place_order`, find:

```python
                order_id = await asyncio.wait_for(
                    self._submit_to_exchange(symbol, side, rounded_qty, leverage),
                    timeout=self.PLACING_TIMEOUT,
                )
```

Replace with:

```python
                order_id = await asyncio.wait_for(
                    self._submit_to_exchange(symbol, side, rounded_qty, leverage, entry_price=entry),
                    timeout=self.PLACING_TIMEOUT,
                )
```

- [ ] **Step 6: Run tests — expect pass**

```bash
python -m pytest tests/test_order_executor.py -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add config/risk_config.py bot/order_executor.py tests/test_order_executor.py
git commit -m "feat: add max_order_notional_usdt cap to prevent oversized orders on low-price tokens"
```

---

## Task 3: Staggered kline refresh + gap detection

Replaces the per-candle synchronous REST refresh (one request per symbol per candle) with background staggered tasks, eliminating the IP ban burst.

**Files:**
- Modify: `bot/data_feed.py`
- Modify: `main.py`
- Modify: `tests/test_data_feed.py`

- [ ] **Step 1: Write failing tests for `has_gap()`**

Add to `tests/test_data_feed.py`:

```python
import json, tempfile, os
from pathlib import Path


def make_kline(open_ms: int, interval_ms: int) -> list:
    """Returns a minimal kline: [open_ms, o, h, l, c, v, close_ms]."""
    return [open_ms, '1.0', '1.0', '1.0', '1.0', '1.0', open_ms + interval_ms - 1]


def test_has_gap_no_cache_returns_false(tmp_path, monkeypatch):
    """Missing cache → no gap assumed (safe default)."""
    feed = make_feed()
    monkeypatch.chdir(tmp_path)
    os.makedirs('data', exist_ok=True)
    assert feed.has_gap('BTCUSDT', '15m', 1_000_000) is False


def test_has_gap_sequential_candle_returns_false(tmp_path, monkeypatch):
    """Incoming candle opens exactly one interval after last cache close → no gap."""
    feed = make_feed()
    monkeypatch.chdir(tmp_path)
    os.makedirs('data', exist_ok=True)
    interval_ms = 15 * 60 * 1000
    k = make_kline(0, interval_ms)         # close_ms = interval_ms - 1
    cache_path = tmp_path / 'data' / 'BTCUSDT_15m_test.json'
    cache_path.write_text(json.dumps([k]))
    # Next candle opens at interval_ms (immediately after previous close)
    assert feed.has_gap('BTCUSDT', '15m', interval_ms) is False


def test_has_gap_with_gap_returns_true(tmp_path, monkeypatch):
    """Incoming candle opens more than one interval after last cache close → gap."""
    feed = make_feed()
    monkeypatch.chdir(tmp_path)
    os.makedirs('data', exist_ok=True)
    interval_ms = 15 * 60 * 1000
    k = make_kline(0, interval_ms)
    cache_path = tmp_path / 'data' / 'BTCUSDT_15m_test.json'
    cache_path.write_text(json.dumps([k]))
    # Two intervals later = definitely a gap
    assert feed.has_gap('BTCUSDT', '15m', interval_ms * 2 + 1) is True
```

- [ ] **Step 2: Run tests — expect `AttributeError` (method not found)**

```bash
python -m pytest tests/test_data_feed.py::test_has_gap_no_cache_returns_false tests/test_data_feed.py::test_has_gap_sequential_candle_returns_false tests/test_data_feed.py::test_has_gap_with_gap_returns_true -v
```

Expected: `AttributeError: 'DataFeed' object has no attribute 'has_gap'`

- [ ] **Step 3: Implement `has_gap()` in `DataFeed`**

In `bot/data_feed.py`, add after `refresh_klines()` (before `_fetch`):

```python
    def has_gap(self, symbol: str, timeframe: str, incoming_open_ms: int) -> bool:
        """Return True if incoming_open_ms is more than one candle-interval after the
        last cached candle's close time. Returns False if the cache is missing or unreadable."""
        cache_path = self._cache_path(symbol, timeframe)
        cached = self._read_cache(cache_path)
        if not cached:
            return False
        last_close_ms = int(cached[-1][6])
        candle_ms = self._timeframe_to_ms(timeframe)
        return incoming_open_ms > last_close_ms + candle_ms
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_data_feed.py -v
```

Expected: all pass

- [ ] **Step 5: Replace synchronous kline refresh in `main.py`**

In `main.py`, add two constants near the top of the file (after `_HEARTBEAT_INTERVAL = 10`):

```python
KLINE_REFRESH_EVERY = 4   # refresh once every N candles per symbol
KLINE_STAGGER_SECS = 2    # seconds between each symbol's background refresh task
```

In `run()`, add this dict near where other per-symbol dicts are initialized:

```python
_kline_refresh_counters: dict[str, int] = {}
```

In `run()`, add this async helper (define it just before `on_candle_close`):

```python
    async def _refresh_klines_bg(symbol: str, count: int, stagger: float) -> None:
        if stagger > 0:
            await asyncio.sleep(stagger)
        try:
            await asyncio.to_thread(feed.refresh_klines, symbol, timeframe, count)
        except Exception as _e:
            logger.debug(f"[{symbol}] Background kline refresh failed: {_e}")
```

In `on_candle_close`, find and **replace** the existing synchronous refresh block:

```python
        try:
            refreshed = await asyncio.to_thread(feed.refresh_klines, symbol, timeframe, 20)
        except Exception as e:
            logger.warning(f"[{symbol}] Kline refresh failed, using WebSocket candle: {e}")
            refreshed = None

        candle_to_add = refreshed[-1] if refreshed else kline
```

Replace with:

```python
        incoming_open_ms = int(kline[0])
        try:
            if feed.has_gap(symbol, timeframe, incoming_open_ms):
                asyncio.create_task(_refresh_klines_bg(symbol, count=100, stagger=0))
            else:
                _kline_refresh_counters[symbol] = _kline_refresh_counters.get(symbol, 0) + 1
                if _kline_refresh_counters[symbol] >= KLINE_REFRESH_EVERY:
                    _kline_refresh_counters[symbol] = 0
                    _syms_list = symbol_registry.get_symbols()
                    _idx = _syms_list.index(symbol) if symbol in _syms_list else 0
                    asyncio.create_task(
                        _refresh_klines_bg(symbol, count=20, stagger=_idx * KLINE_STAGGER_SECS)
                    )
        except Exception as _gap_e:
            logger.debug(f"[{symbol}] Gap check error: {_gap_e}")

        candle_to_add = kline
```

- [ ] **Step 6: Run full test suite to confirm no regressions**

```bash
python -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add bot/data_feed.py main.py tests/test_data_feed.py
git commit -m "fix: stagger kline refreshes every 4 candles to prevent IP bans; add has_gap() to DataFeed"
```

---

## Task 4: Add 3 new fields to `Settings` and `PresetOverrides`

Foundation for the early-loss exit features. Zero defaults ensure no existing behaviour changes.

**Files:**
- Modify: `config/settings.py`
- Modify: `config/presets.py`

- [ ] **Step 1: Add fields to `Settings` dataclass**

In `config/settings.py`, add after `duplicate_skip_pct: float`:

```python
    # Early loss exit (0 = disabled)
    # max_losing_pct: close when adverse move reaches this % of SL distance from entry
    max_losing_pct: float
    # max_losing_amount_usdt: close when unrealized loss exceeds this USDT (live/virtual only)
    max_losing_amount_usdt: float
    # max_losing_candles: close after N consecutive candles whose close is on wrong side of entry
    max_losing_candles: int
```

- [ ] **Step 2: Add defaults in `load_settings()`**

In `config/settings.py`, in `load_settings()`, add after `duplicate_skip_pct=float(os.getenv('DUPLICATE_SKIP_PCT', '2.0')),`:

```python
        max_losing_pct=float(os.getenv('MAX_LOSING_PCT', '0.0')),
        max_losing_amount_usdt=float(os.getenv('MAX_LOSING_AMOUNT_USDT', '0.0')),
        max_losing_candles=int(os.getenv('MAX_LOSING_CANDLES', '0')),
```

- [ ] **Step 3: Add fields to `PresetOverrides`**

In `config/presets.py`, add to the `PresetOverrides` TypedDict after `duplicate_skip_pct: float`:

```python
    # Early loss exit (0 = disabled)
    max_losing_pct: float
    max_losing_amount_usdt: float
    max_losing_candles: int
```

- [ ] **Step 4: Verify settings load without error**

```bash
python -c "from config.settings import load_settings; s = load_settings('BTCUSDT'); print(s.max_losing_pct, s.max_losing_amount_usdt, s.max_losing_candles)"
```

Expected: `0.0 0.0 0`

- [ ] **Step 5: Commit**

```bash
git add config/settings.py config/presets.py
git commit -m "feat: add max_losing_pct / max_losing_amount_usdt / max_losing_candles to Settings and PresetOverrides"
```

---

## Task 5: Implement early loss exit in `FakeOrder`

The core logic. Three independent exit conditions: price threshold (pct-based), price threshold (amount-based, pre-computed by caller), and consecutive candle counter.

**Files:**
- Modify: `bot/fake_order.py`
- Create: `tests/test_fake_order_early_exit.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_fake_order_early_exit.py`:

```python
"""Tests for FakeOrder early-loss exit: max_losing_pct, max_losing_candles, early_loss_sl."""
import pytest
from bot.fake_order import FakeOrder


def make_long(max_losing_pct=0.0, max_losing_candles=0, early_loss_sl=0.0) -> FakeOrder:
    """BUY order: entry=100, tp=120, sl=80."""
    return FakeOrder(
        side='BUY', entry_price=100.0, tp=120.0, sl=80.0,
        level=1, signal_type='test', candle_index=0,
        max_losing_pct=max_losing_pct,
        max_losing_candles=max_losing_candles,
        early_loss_sl=early_loss_sl,
    )


def make_short(max_losing_pct=0.0, max_losing_candles=0, early_loss_sl=0.0) -> FakeOrder:
    """SELL order: entry=100, tp=80, sl=120."""
    return FakeOrder(
        side='SELL', entry_price=100.0, tp=80.0, sl=120.0,
        level=1, signal_type='test', candle_index=0,
        max_losing_pct=max_losing_pct,
        max_losing_candles=max_losing_candles,
        early_loss_sl=early_loss_sl,
    )


# ── max_losing_pct ─────────────────────────────────────────────────────────────

def test_pct_zero_no_early_exit():
    """Zero value = disabled."""
    order = make_long(max_losing_pct=0.0)
    # Price at halfway between entry and SL — would trigger at 50% but disabled
    result = order.check(91.0, 90.0, 1, candle_open=99.0, candle_close=90.0)
    assert result is None


def test_pct_50_long_fires_at_halfway():
    """50% of SL dist from entry for BUY: entry=100, sl=80 → early exit at 90."""
    order = make_long(max_losing_pct=50.0)
    # Low reaches exactly 90 — should trigger early exit
    result = order.check(99.0, 90.0, 1, candle_open=99.0, candle_close=90.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(90.0)


def test_pct_50_long_no_trigger_above_threshold():
    """Price stays above 90 — no early exit."""
    order = make_long(max_losing_pct=50.0)
    result = order.check(99.0, 91.0, 1, candle_open=99.0, candle_close=91.0)
    assert result is None


def test_pct_50_short_fires_at_halfway():
    """50% of SL dist from entry for SELL: entry=100, sl=120 → early exit at 110."""
    order = make_short(max_losing_pct=50.0)
    result = order.check(110.0, 99.0, 1, candle_open=101.0, candle_close=110.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(110.0)


def test_pct_no_exit_when_armed():
    """Once partial_price is hit (order armed), early exit must NOT fire."""
    # entry=100, tp=120, sl=80, partial_take_pct=0.3 → partial_price=106
    order = FakeOrder(
        side='BUY', entry_price=100.0, tp=120.0, sl=80.0,
        level=1, signal_type='test', candle_index=0,
        partial_take_pct=0.3, max_losing_pct=50.0,
    )
    # Candle 1: arm the order (high reaches 106)
    order.check(106.0, 101.0, 1, candle_open=101.0, candle_close=104.0)
    assert order._partial_armed is True
    # Candle 2: price drops below early-exit threshold (90) — but armed, so no early exit.
    # partial_price=106, low=88 < 106 → should close as 'partial' at 106, NOT 'loss' at 90.
    result = order.check(95.0, 88.0, 2, candle_open=95.0, candle_close=88.0)
    assert result == 'partial'
    assert order.close_price == pytest.approx(106.0)


# ── early_loss_sl (amount-based, pre-computed) ─────────────────────────────────

def test_early_loss_sl_long():
    """Pre-computed amount-based SL: early_loss_sl=95 for a BUY."""
    order = make_long(early_loss_sl=95.0)
    # Low reaches 95 — triggers
    result = order.check(99.0, 95.0, 1, candle_open=99.0, candle_close=95.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(95.0)


def test_early_loss_sl_short():
    """Pre-computed amount-based SL: early_loss_sl=105 for a SELL."""
    order = make_short(early_loss_sl=105.0)
    result = order.check(105.0, 99.0, 1, candle_open=101.0, candle_close=105.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(105.0)


def test_tighter_threshold_wins():
    """When both pct-based and amount-based are set, tighter (closer to entry) fires."""
    # entry=100, sl=80, sl_dist=20
    # pct=50% → early_exit=90
    # early_loss_sl=95 (amount-based, closer to entry)
    # Tighter for LONG = higher price = 95
    order = make_long(max_losing_pct=50.0, early_loss_sl=95.0)
    # Price at 95 — triggers on amount-based (tighter) before reaching 90 (pct-based)
    result = order.check(99.0, 95.0, 1, candle_open=99.0, candle_close=95.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(95.0)


# ── max_losing_candles ─────────────────────────────────────────────────────────

def test_losing_candles_triggers_after_n():
    """N=3 consecutive below-entry closes → exit on candle 3."""
    order = make_long(max_losing_candles=3)
    # Candles 1-2: below entry, no trigger yet
    assert order.check(99.0, 97.0, 1, candle_open=99.0, candle_close=97.0) is None
    assert order.check(98.0, 96.0, 2, candle_open=98.0, candle_close=96.0) is None
    # Candle 3: third consecutive → triggers
    result = order.check(97.0, 95.0, 3, candle_open=97.0, candle_close=95.0)
    assert result == 'loss'


def test_losing_candles_resets_on_recovery():
    """Counter resets when candle close is back above entry."""
    order = make_long(max_losing_candles=3)
    # 2 losing candles
    order.check(99.0, 97.0, 1, candle_open=99.0, candle_close=97.0)
    order.check(98.0, 96.0, 2, candle_open=98.0, candle_close=96.0)
    # Recovery: close above entry
    order.check(102.0, 99.0, 3, candle_open=99.0, candle_close=101.0)
    # 2 more losing candles — counter reset, so no trigger at candle 5
    assert order.check(99.0, 97.0, 4, candle_open=99.0, candle_close=97.0) is None
    assert order.check(98.0, 96.0, 5, candle_open=98.0, candle_close=96.0) is None


def test_losing_candles_not_updated_by_price_tick():
    """check_price() must not update the consecutive-candle counter."""
    order = make_long(max_losing_candles=1)
    # Two price ticks below entry — counter must NOT advance
    order.check_price(98.0)
    order.check_price(97.0)
    assert order._consecutive_losing_candles == 0


def test_losing_candles_zero_disabled():
    """max_losing_candles=0 → no early exit regardless of candle direction."""
    order = make_long(max_losing_candles=0)
    for i in range(10):
        result = order.check(99.0, 97.0, i, candle_open=99.0, candle_close=97.0)
        assert result is None


def test_all_zero_defaults_no_early_exit():
    """FakeOrder with default params (all zeros) behaves exactly as before."""
    order = FakeOrder(
        side='BUY', entry_price=100.0, tp=120.0, sl=80.0,
        level=1, signal_type='test', candle_index=0,
    )
    # Price ticks and candles well below entry — only SL fires, at SL price
    result = order.check(99.0, 79.0, 1, candle_open=99.0, candle_close=79.0)
    assert result == 'loss'
    assert order.close_price == pytest.approx(80.0)  # at the hard SL, not an early threshold
```

- [ ] **Step 2: Run tests — expect failures**

```bash
python -m pytest tests/test_fake_order_early_exit.py -v
```

Expected: `TypeError` (unexpected keyword argument) on all tests

- [ ] **Step 3: Add new constructor params to `FakeOrder`**

In `bot/fake_order.py`, update `__init__` signature to add after `trailing_stop_pct: float = 0.0,`:

```python
        max_losing_pct: float = 0.0,
        max_losing_candles: int = 0,
        early_loss_sl: float = 0.0,
```

Add these instance variable assignments at the end of `__init__`:

```python
        self._max_losing_pct = max_losing_pct
        self._max_losing_candles = max_losing_candles
        self._early_loss_sl = early_loss_sl
        self._consecutive_losing_candles: int = 0
        self._last_losing_candle: int = -1
```

- [ ] **Step 4: Add `_early_exit_price` property**

In `bot/fake_order.py`, add after `_check_trail()` and before the `# Stats helpers` section:

```python
    @property
    def _early_exit_price(self) -> float:
        """The tighter (closer-to-entry) of the pct-based and amount-based early exit thresholds.
        Returns 0.0 when both are disabled."""
        prices: list[float] = []
        if self._max_losing_pct > 0 and self.sl > 0:
            sl_dist = abs(self.sl - self.entry_price)
            frac = min(self._max_losing_pct, 100.0) / 100.0
            if self.side == 'BUY':
                prices.append(self.entry_price - sl_dist * frac)
            else:
                prices.append(self.entry_price + sl_dist * frac)
        if self._early_loss_sl > 0:
            prices.append(self._early_loss_sl)
        if not prices:
            return 0.0
        # Tighter = closer to entry = less loss before trigger
        # For BUY: higher price is closer to entry
        # For SELL: lower price is closer to entry
        return max(prices) if self.side == 'BUY' else min(prices)
```

- [ ] **Step 5: Add early exit logic to `check()`**

In `bot/fake_order.py`, update `check()` signature to:

```python
    def check(
        self,
        high: float,
        low: float,
        candle_index: int,
        candle_open: Optional[float] = None,
        candle_close: Optional[float] = None,
        update_losing_candles: bool = True,
    ) -> Optional[str]:
```

After the "Capture armed state" block (the line `was_armed = self._partial_armed`) and before the `if was_armed:` block, insert:

```python
        # Early loss exit — only fires when the order has not yet been armed (not yet in profit).
        # Once armed, the trailing stop / partial logic manages the exit.
        if not was_armed:
            ep = self._early_exit_price
            if ep > 0:
                if self.side == 'BUY' and low <= ep:
                    self._close('loss', ep, candle_index)
                    return 'loss'
                elif self.side == 'SELL' and high >= ep:
                    self._close('loss', ep, candle_index)
                    return 'loss'

            if (self._max_losing_candles > 0
                    and candle_close is not None
                    and update_losing_candles
                    and candle_index != self._last_losing_candle):
                self._last_losing_candle = candle_index
                wrong_side = (
                    (self.side == 'BUY' and candle_close < self.entry_price) or
                    (self.side == 'SELL' and candle_close > self.entry_price)
                )
                if wrong_side:
                    self._consecutive_losing_candles += 1
                    if self._consecutive_losing_candles >= self._max_losing_candles:
                        self._close('loss', candle_close, candle_index)
                        return 'loss'
                else:
                    self._consecutive_losing_candles = 0
```

- [ ] **Step 6: Update `check_price()` to pass `update_losing_candles=False`**

In `bot/fake_order.py`, update `check_price`:

```python
    def check_price(self, current_price: float) -> Optional[str]:
        """For live/test mode: check a single price against TP/SL/trail/early-exit.
        Never updates the consecutive-losing-candles counter."""
        return self.check(
            current_price, current_price, self.open_candle,
            current_price, current_price,
            update_losing_candles=False,
        )
```

- [ ] **Step 7: Update `get_state()` and `from_state()` for persistence**

In `bot/fake_order.py`, in `get_state()`, add these keys to the returned dict:

```python
            '_max_losing_pct': self._max_losing_pct,
            '_max_losing_candles': self._max_losing_candles,
            '_early_loss_sl': self._early_loss_sl,
            '_consecutive_losing_candles': self._consecutive_losing_candles,
            '_last_losing_candle': self._last_losing_candle,
```

In `from_state()`, add after `obj._max_favorable = state['_max_favorable']`:

```python
        obj._max_losing_pct = state.get('_max_losing_pct', 0.0)
        obj._max_losing_candles = state.get('_max_losing_candles', 0)
        obj._early_loss_sl = state.get('_early_loss_sl', 0.0)
        obj._consecutive_losing_candles = state.get('_consecutive_losing_candles', 0)
        obj._last_losing_candle = state.get('_last_losing_candle', -1)
```

- [ ] **Step 8: Run all early exit tests — expect pass**

```bash
python -m pytest tests/test_fake_order_early_exit.py -v
```

Expected: all pass

- [ ] **Step 9: Run full test suite — expect no regressions**

```bash
python -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 10: Commit**

```bash
git add bot/fake_order.py tests/test_fake_order_early_exit.py
git commit -m "feat: add early loss exit to FakeOrder (max_losing_pct, max_losing_candles, early_loss_sl)"
```

---

## Task 6: Wire early exit into `Backtester`

Passes the two settings that apply in backtesting (`max_losing_pct`, `max_losing_candles`) to `FakeOrder`, and adds candle open/close to the `check()` call so `max_losing_candles` has the data it needs.

**Files:**
- Modify: `bot/backtester.py`

- [ ] **Step 1: Update `FakeOrder` construction in `_run_preset`**

In `bot/backtester.py`, find the `FakeOrder(...)` constructor call (around line 354). Replace it with:

```python
                    open_order = FakeOrder(
                        side=side,
                        entry_price=entry_price,
                        tp=tp,
                        sl=sl,
                        level=rec.getLevel(),
                        signal_type=rec.getType().value,
                        candle_index=i + 1,
                        partial_take_pct=settings.partial_take_pct,
                        trailing_stop_pct=settings.trailing_stop_pct,
                        max_losing_pct=settings.max_losing_pct,
                        max_losing_candles=settings.max_losing_candles,
                        early_loss_sl=0.0,  # no quantity in backtester — amount-based exit not supported
                    )
```

- [ ] **Step 2: Pass candle open/close to `check()` in the main loop**

In `bot/backtester.py`, find (around line 207):

```python
                outcome = open_order.check(high, low, i)
```

Replace with:

```python
                outcome = open_order.check(high, low, i, candle_open=open_price, candle_close=close_price)
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add bot/backtester.py
git commit -m "feat: wire max_losing_pct and max_losing_candles into Backtester"
```

---

## Task 7: Wire early exit into `VirtualOrderSimulator`

Computes `early_loss_sl` from `max_losing_amount_usdt` (virtual simulator has quantity, unlike the backtester) and passes all three new params to `FakeOrder`.

**Files:**
- Modify: `bot/virtual_order_simulator.py`

- [ ] **Step 1: Update `_try_open` to compute `early_loss_sl` and pass new params**

In `bot/virtual_order_simulator.py`, in `_try_open`, find:

```python
        self._rank_fake[rank][symbol] = FakeOrder(
            side=side,
            entry_price=entry,
            tp=tp,
            sl=sl,
            level=rec.getLevel(),
            signal_type=rec.getType().value,
            candle_index=0,
            partial_take_pct=partial_pct,
            trailing_stop_pct=trail_pct,
        )
```

Replace with:

```python
        _max_losing_amt = float(getattr(preset_settings, 'max_losing_amount_usdt', 0.0))
        _early_loss_sl = 0.0
        if _max_losing_amt > 0 and quantity > 0:
            if side == 'BUY':
                _early_loss_sl = entry - _max_losing_amt / quantity
            else:
                _early_loss_sl = entry + _max_losing_amt / quantity

        self._rank_fake[rank][symbol] = FakeOrder(
            side=side,
            entry_price=entry,
            tp=tp,
            sl=sl,
            level=rec.getLevel(),
            signal_type=rec.getType().value,
            candle_index=0,
            partial_take_pct=partial_pct,
            trailing_stop_pct=trail_pct,
            max_losing_pct=float(getattr(preset_settings, 'max_losing_pct', 0.0)),
            max_losing_candles=int(getattr(preset_settings, 'max_losing_candles', 0)),
            early_loss_sl=_early_loss_sl,
        )
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add bot/virtual_order_simulator.py
git commit -m "feat: wire early loss exit into VirtualOrderSimulator"
```

---

## Task 8: Wire early exit into `OrderExecutor.place_order`

Adds the three new params to `place_order()`, computes `early_loss_sl` from actual position size, and passes everything to `FakeOrder`.

**Files:**
- Modify: `bot/order_executor.py`
- Modify: `tests/test_order_executor.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_order_executor.py`:

```python
@pytest.mark.asyncio
async def test_place_order_passes_max_losing_pct_to_fake_order():
    """max_losing_pct > 0 must reach the FakeOrder's _max_losing_pct."""
    ex = make_executor(with_feed=True)
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': 0.01}

    with patch.object(ex, '_submit_to_exchange', new=AsyncMock(return_value='123')):
        with patch.object(ex, '_place_sl_on_exchange', new=AsyncMock(return_value=None)):
            with patch('bot.order_executor.load_risk_config', return_value={
                'consecutive_failure_threshold': 3, 'max_order_notional_usdt': 0.0,
            }):
                placed = await ex.place_order(
                    symbol='BTCUSDT', preset_name='test', side='BUY',
                    entry=50000.0, tp=55000.0, sl=47000.0, quantity=0.01,
                    leverage=5, max_losing_pct=50.0,
                )
    assert placed is True
    fake = ex._fake_orders.get('BTCUSDT')
    assert fake is not None
    assert fake._max_losing_pct == 50.0


@pytest.mark.asyncio
async def test_place_order_computes_early_loss_sl_from_amount():
    """max_losing_amount_usdt=100 with qty=0.01 BUY @ 50000 → early_loss_sl = 50000 - 100/0.01 = 40000."""
    ex = make_executor(with_feed=True)
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': 0.01}

    with patch.object(ex, '_submit_to_exchange', new=AsyncMock(return_value='123')):
        with patch.object(ex, '_place_sl_on_exchange', new=AsyncMock(return_value=None)):
            with patch('bot.order_executor.load_risk_config', return_value={
                'consecutive_failure_threshold': 3, 'max_order_notional_usdt': 0.0,
            }):
                await ex.place_order(
                    symbol='BTCUSDT', preset_name='test', side='BUY',
                    entry=50000.0, tp=55000.0, sl=47000.0, quantity=0.01,
                    leverage=5, max_losing_amount_usdt=100.0,
                )
    fake = ex._fake_orders.get('BTCUSDT')
    assert fake is not None
    # entry=50000, amount=100, qty=0.01 → early_loss_sl = 50000 - 100/0.01 = 40000
    assert fake._early_loss_sl == pytest.approx(40000.0)
```

- [ ] **Step 2: Run test — expect failure**

```bash
python -m pytest tests/test_order_executor.py::test_place_order_passes_max_losing_pct_to_fake_order tests/test_order_executor.py::test_place_order_computes_early_loss_sl_from_amount -v
```

Expected: `TypeError` (unexpected keyword arguments)

- [ ] **Step 3: Add 3 new params to `place_order()` signature**

In `bot/order_executor.py`, in `place_order()`, add after `trailing_stop_pct: float = 0.0,`:

```python
        max_losing_pct: float = 0.0,
        max_losing_amount_usdt: float = 0.0,
        max_losing_candles: int = 0,
```

- [ ] **Step 4: Compute `early_loss_sl` and pass to `FakeOrder`**

In `bot/order_executor.py`, in `place_order()`, find the `FakeOrder(...)` construction (after `self._fake_orders[symbol] = FakeOrder(`). Replace the whole FakeOrder construction block with:

```python
                _early_loss_sl = 0.0
                if max_losing_amount_usdt > 0 and rounded_qty > 0:
                    if side == 'BUY':
                        _early_loss_sl = entry - max_losing_amount_usdt / rounded_qty
                    else:
                        _early_loss_sl = entry + max_losing_amount_usdt / rounded_qty

                self._fake_orders[symbol] = FakeOrder(
                    side=side,
                    entry_price=entry,
                    tp=tp,
                    sl=sl if sl else entry * (0.99 if side == 'BUY' else 1.01),
                    level=level,
                    signal_type=signal_type,
                    candle_index=self._symbol_candle_index.get(symbol, 0),
                    partial_take_pct=partial_take_pct,
                    trailing_stop_pct=trailing_stop_pct,
                    max_losing_pct=max_losing_pct,
                    max_losing_candles=max_losing_candles,
                    early_loss_sl=_early_loss_sl,
                )
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python -m pytest tests/test_order_executor.py -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add bot/order_executor.py tests/test_order_executor.py
git commit -m "feat: wire early loss exit into OrderExecutor.place_order"
```

---

## Task 9: Pass new settings from `main.py` to `place_order`

Closes the loop so preset values actually reach the live order execution path.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Update the `place_order` call**

In `main.py`, find the `placed = await order_executor.place_order(` call. Add three new keyword arguments after `trailing_stop_pct=preset_settings.trailing_stop_pct,`:

```python
            max_losing_pct=preset_settings.max_losing_pct,
            max_losing_amount_usdt=preset_settings.max_losing_amount_usdt,
            max_losing_candles=preset_settings.max_losing_candles,
```

- [ ] **Step 2: Verify import / syntax check**

```bash
python -c "import main; print('OK')"
```

Expected: `OK` (no import errors)

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: pass max_losing_pct/amount/candles from preset settings to place_order"
```

---

## Task 10: End-to-end smoke check and deploy

- [ ] **Step 1: Quick backtest smoke test (zero values — must match original behavior)**

```bash
python backtest.py --symbols BTCUSDT --klines-count 200 --no-fetch
```

Expected: backtest completes without errors; results table prints.

- [ ] **Step 2: Quick backtest with non-zero values (verify they don't crash)**

```bash
python -c "
from config.settings import load_settings
from config.presets import ALL_PRESETS
from bot.backtester import Backtester
from config.risk_config import load_risk_config, _CONFIG_PATH
import json

settings = load_settings('BTCUSDT')
# Patch a preset with early-loss settings
test_presets = {'test_early': {'max_losing_pct': 50.0, 'max_losing_candles': 3}}
import json
with open('data/BTCUSDT_15m_live.json') as f:
    klines = json.load(f)
bt = Backtester(settings, initial_balance=1000.0, risk_config_path=_CONFIG_PATH)
results = bt.run(klines[-200:], test_presets)
r = results['test_early']
print(f'trades={r.total()} wins={r.wins()} losses={r.losses()}')
print('OK')
"
```

Expected: prints trade stats and `OK`

- [ ] **Step 3: Deploy to server**

```bash
bash scripts/push.sh
```

- [ ] **Step 4: Monitor server logs for 5 minutes**

```bash
ssh -i ~/.ssh/id_ed25519 root@185.237.14.105 "docker exec bot-app-1 tail -f /app/logs/bot.log" 
```

Expected: normal operation, no tracebacks. Verify:
- No `AttributeError` or `TypeError` lines
- Kline refresh log lines appear staggered (not all at once)
- No duplicate trade entries when orders close

- [ ] **Step 5: Update notes**

Run the Librarian agent to update `CLAUDE_NOTES.md`, `TODO.md`, and `FEATURES.md`.
