# Multi-Symbol Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-symbol WebSocket loop in `main.py` with a combined stream for up to 15 symbols, per-symbol isolated state, a price/candle watchdog, correct MIN_NOTIONAL-aware leverage computation, and automatic symbol disable on exchange errors.

**Architecture:** `DataFeed` gains `stream_combined()` (one WS connection for all symbols) and `start_watchdog()` (REST fallback for stale price/candle data). `OrderExecutor` gains per-symbol price checking, MIN_NOTIONAL/leverage-bracket awareness, and runtime auto-disable. `main.py` is refactored to hold `analyzers: dict[str, Analyzer]` and `sym_settings: dict[str, Settings]`, dispatch callbacks by symbol, and use the optimal leverage formula before each order.

**Tech Stack:** Python asyncio, `websockets`, `python-binance` client (`futures_leverage_bracket`, `futures_exchange_info`, `futures_symbol_ticker`, `futures_klines`), `SymbolRegistry` (already exists in `bot/symbol_registry.py`).

---

## File Map

| File | Action |
|---|---|
| `bot/risk_manager.py` | Add `get_balance() → float` public method |
| `bot/order_executor.py` | Rename `check_all_orders_price` → `check_symbol_price`; extend lot cache with `min_notional`; add `get_min_notional()`, `get_bracket_max()`, `fetch_leverage_brackets()`, `check_symbols_on_exchange()`; add `symbol_registry` param; classify errors in `_submit_to_exchange`; add `_auto_disable()` |
| `bot/data_feed.py` | Add `stream_combined()`, `start_watchdog()`, per-symbol dedup/timestamp dicts in `__init__` |
| `main.py` | Full refactor: SymbolRegistry, per-symbol dicts, combined stream, watchdog, optimal leverage, balance debounce, remove `display.show()` from candle callback |
| `tests/test_order_executor.py` | Update `check_all_orders_price` calls → `check_symbol_price`; add tests for `get_min_notional`, scope isolation, funds-error no-disable |
| `tests/test_data_feed.py` | New file: test `stream_combined` message parsing, dedup guard |

---

## Task 1: RiskManager — add `get_balance()`

**Files:**
- Modify: `bot/risk_manager.py:180-184`
- Test: `tests/test_risk_manager.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_risk_manager.py`:

```python
def test_get_balance_returns_current_balance(tmp_path):
    from bot.risk_manager import RiskManager
    from unittest.mock import patch, MagicMock
    cfg = make_cfg()
    with patch('bot.risk_manager.load_risk_config', return_value=cfg):
        rm = RiskManager('test', initial_balance=500.0, state_path=tmp_path / 's.json')
    assert rm.get_balance() == pytest.approx(500.0)
    rm.update_balance(750.0)
    assert rm.get_balance() == pytest.approx(750.0)
```

(`make_cfg` already exists in the test file — check it returns the standard fixture dict.)

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -m pytest tests/test_risk_manager.py::test_get_balance_returns_current_balance -v
```

Expected: `FAILED — AttributeError: 'RiskManager' object has no attribute 'get_balance'`

- [ ] **Step 3: Add `get_balance()` to RiskManager**

In `bot/risk_manager.py`, add after the `get_allocation` method (around line 151):

```python
def get_balance(self) -> float:
    """Current wallet balance in USDT."""
    with self._lock:
        return self._balance
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python -m pytest tests/test_risk_manager.py::test_get_balance_returns_current_balance -v
```

Expected: `PASSED`

- [ ] **Step 5: Run the full risk manager test suite**

```bash
python -m pytest tests/test_risk_manager.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/risk_manager.py tests/test_risk_manager.py
git commit -m "feat: add RiskManager.get_balance() public accessor"
```

---

## Task 2: OrderExecutor — rename `check_all_orders_price` → `check_symbol_price`

**Files:**
- Modify: `bot/order_executor.py:218-258`
- Modify: `tests/test_order_executor.py:154-209`

`check_all_orders_price(price)` iterates all fake orders with one price — wrong in multi-symbol context.
`check_symbol_price(symbol, price)` checks only the given symbol's fake order.

- [ ] **Step 1: Update the two existing price-tick tests**

In `tests/test_order_executor.py`, replace `check_all_orders_price` calls with `check_symbol_price`:

```python
@pytest.mark.asyncio
async def test_check_symbol_price_sl_hit():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    async def fake_submit(symbol, side, quantity, leverage):
        return 'id_sl'

    ex._submit_to_exchange = fake_submit
    await ex.place_order(
        symbol='BTCUSDT', preset_name='default', side='BUY',
        entry=50000, tp=55000, sl=48000, quantity=0.01, leverage=10,
    )
    closed = await ex.check_symbol_price('BTCUSDT', 47500)
    assert len(closed) == 1
    assert closed[0]['result'] == 'loss'
    assert ex.get_state('BTCUSDT') == OrderState.IDLE


@pytest.mark.asyncio
async def test_check_symbol_price_tp_hit():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    async def fake_submit(symbol, side, quantity, leverage):
        return 'id_tp'

    ex._submit_to_exchange = fake_submit
    await ex.place_order(
        symbol='BTCUSDT', preset_name='default', side='BUY',
        entry=50000, tp=55000, sl=48000, quantity=0.01, leverage=10,
    )
    closed = await ex.check_symbol_price('BTCUSDT', 55100)
    assert len(closed) == 1
    assert closed[0]['result'] == 'win'
    assert ex.get_state('BTCUSDT') == OrderState.IDLE
```

Also add scope isolation test:

```python
@pytest.mark.asyncio
async def test_check_symbol_price_scope_isolation():
    """Price update for BTCUSDT must not affect ETHUSDT's open order."""
    ex = make_executor()
    for sym in ('BTCUSDT', 'ETHUSDT'):
        ex._lot_cache[sym] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    async def fake_submit(symbol, side, quantity, leverage):
        return f'id_{symbol}'

    ex._submit_to_exchange = fake_submit
    # BTCUSDT long: SL at 48000
    await ex.place_order('BTCUSDT', 'default', 'BUY', 50000, 55000, 48000, 0.01, 10)
    # ETHUSDT long: SL at 1800
    await ex.place_order('ETHUSDT', 'default', 'BUY', 2000, 2200, 1800, 0.1, 5)

    # BTCUSDT price hits SL — only BTCUSDT should close
    closed = await ex.check_symbol_price('BTCUSDT', 47500)
    assert len(closed) == 1
    assert closed[0]['symbol'] == 'BTCUSDT'
    assert ex.get_state('BTCUSDT') == OrderState.IDLE
    assert ex.get_state('ETHUSDT') == OrderState.OPEN
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_order_executor.py::test_check_symbol_price_sl_hit tests/test_order_executor.py::test_check_symbol_price_tp_hit tests/test_order_executor.py::test_check_symbol_price_scope_isolation -v
```

Expected: `FAILED — AttributeError: 'OrderExecutor' object has no attribute 'check_symbol_price'`

- [ ] **Step 3: Replace `check_all_orders_price` with `check_symbol_price` in `bot/order_executor.py`**

Remove the entire `check_all_orders_price` method (lines 218–258) and replace with:

```python
async def check_symbol_price(self, symbol: str, current_price: float) -> list[dict]:
    """Call on every price tick for a specific symbol. Checks that symbol's FakeOrder only."""
    fake_order = self._fake_orders.get(symbol)
    if fake_order is None:
        return []

    result = fake_order.check_price(current_price)
    if result is None:
        return []

    open_order = self._open_orders.get(symbol)
    if open_order is None:
        del self._fake_orders[symbol]
        return []

    software_close_price = fake_order.close_price or open_order.entry_price
    try:
        actual_close_price = await self._market_close(symbol, open_order)
    except Exception as exc:
        logger.error(f"Market close failed for {symbol}: {exc}")
        self._notifier.notify("warning", f"Failed to close {symbol}", str(exc), "order_executor")
        actual_close_price = software_close_price

    pnl = self._calc_pnl(open_order, actual_close_price)
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
    }]
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
python -m pytest tests/test_order_executor.py::test_check_symbol_price_sl_hit tests/test_order_executor.py::test_check_symbol_price_tp_hit tests/test_order_executor.py::test_check_symbol_price_scope_isolation -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Run the full order executor test suite**

```bash
python -m pytest tests/test_order_executor.py -v
```

Expected: all tests PASS. (The two old `test_check_all_orders_price_*` tests are removed and replaced by the new `test_check_symbol_price_*` tests.)

- [ ] **Step 6: Commit**

```bash
git add bot/order_executor.py tests/test_order_executor.py
git commit -m "feat: rename check_all_orders_price -> check_symbol_price, scope to one symbol"
```

---

## Task 3: OrderExecutor — extend lot cache with `min_notional` + `get_min_notional()`

**Files:**
- Modify: `bot/order_executor.py` — `_ensure_lot_size`, add `get_min_notional()`
- Modify: `tests/test_order_executor.py`

Binance futures `MIN_NOTIONAL` filter: `{"filterType": "MIN_NOTIONAL", "notional": "5"}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_order_executor.py`:

```python
@pytest.mark.asyncio
async def test_get_min_notional_from_exchange_info():
    ex = make_executor(with_feed=True)
    ex._feed.client.futures_exchange_info = MagicMock(return_value={
        'symbols': [{
            'symbol': 'BTCUSDT',
            'filters': [
                {'filterType': 'LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001'},
                {'filterType': 'MIN_NOTIONAL', 'notional': '5'},
            ]
        }]
    })
    notional = await ex.get_min_notional('BTCUSDT')
    assert notional == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_get_min_notional_defaults_zero_when_missing():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001}
    # no min_notional key in cache
    notional = await ex.get_min_notional('BTCUSDT')
    assert notional == pytest.approx(0.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_order_executor.py::test_get_min_notional_from_exchange_info tests/test_order_executor.py::test_get_min_notional_defaults_zero_when_missing -v
```

Expected: `FAILED — AttributeError: 'OrderExecutor' object has no attribute 'get_min_notional'`

- [ ] **Step 3: Extend `_ensure_lot_size` and add `get_min_notional()`**

In `bot/order_executor.py`, replace the `_ensure_lot_size` method and add `get_min_notional`:

```python
async def get_min_notional(self, symbol: str) -> float:
    lot = await self._ensure_lot_size(symbol)
    return lot.get('min_notional', 0.0)

async def _ensure_lot_size(self, symbol: str) -> dict:
    if symbol in self._lot_cache:
        return self._lot_cache[symbol]
    default = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}
    if self._feed is None:
        return default
    try:
        info = await asyncio.to_thread(self._feed.client.futures_exchange_info)
        for sym_info in info.get('symbols', []):
            sym = sym_info['symbol']
            entry: dict = {}
            for f in sym_info.get('filters', []):
                ft = f.get('filterType')
                if ft == 'LOT_SIZE':
                    entry['step_size'] = float(f['stepSize'])
                    entry['min_qty'] = float(f['minQty'])
                elif ft == 'MIN_NOTIONAL':
                    entry['min_notional'] = float(f.get('notional') or f.get('minNotional') or 0)
            self._lot_cache[sym] = {
                'step_size': entry.get('step_size', 0.001),
                'min_qty': entry.get('min_qty', 0.001),
                'min_notional': entry.get('min_notional', 0.0),
            }
    except Exception as exc:
        logger.warning(f"Failed to fetch exchange info: {exc}")
    return self._lot_cache.get(symbol, default)
```

Also update `_lot_cache` init comment in `__init__`:

```python
self._lot_cache: dict[str, dict] = {}  # {symbol: {step_size, min_qty, min_notional}}
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_order_executor.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/order_executor.py tests/test_order_executor.py
git commit -m "feat: extend lot cache with min_notional, add get_min_notional()"
```

---

## Task 4: OrderExecutor — `fetch_leverage_brackets()` + `get_bracket_max()`

**Files:**
- Modify: `bot/order_executor.py`
- Modify: `tests/test_order_executor.py`

Binance `futures_leverage_bracket(symbol=symbol)` returns:
```python
[{"symbol": "BTCUSDT", "brackets": [{"bracket": 1, "initialLeverage": 125, ...}, ...]}]
```

`get_bracket_max` defaults to 20 when data is missing — a conservative safe value.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_order_executor.py`:

```python
@pytest.mark.asyncio
async def test_fetch_leverage_brackets_caches_max():
    ex = make_executor(with_feed=True)
    ex._feed.client.futures_leverage_bracket = MagicMock(return_value=[{
        'symbol': 'BTCUSDT',
        'brackets': [
            {'bracket': 1, 'initialLeverage': 125},
            {'bracket': 2, 'initialLeverage': 100},
        ]
    }])
    await ex.fetch_leverage_brackets(['BTCUSDT'])
    assert ex.get_bracket_max('BTCUSDT') == 125


def test_get_bracket_max_defaults_to_20_when_unknown():
    ex = make_executor()
    assert ex.get_bracket_max('UNKNOWNUSDT') == 20
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_order_executor.py::test_fetch_leverage_brackets_caches_max tests/test_order_executor.py::test_get_bracket_max_defaults_to_20_when_unknown -v
```

Expected: `FAILED — AttributeError: 'OrderExecutor' object has no attribute 'fetch_leverage_brackets'`

- [ ] **Step 3: Add bracket cache to `__init__` and implement the methods**

In `bot/order_executor.py`, in `__init__` after `self._lot_cache`:

```python
self._bracket_max: dict[str, int] = {}  # symbol → max leverage from first bracket
```

After the `get_min_notional` method, add:

```python
def get_bracket_max(self, symbol: str) -> int:
    return self._bracket_max.get(symbol, 20)

async def fetch_leverage_brackets(self, symbols: list[str]) -> None:
    if self._feed is None:
        return
    for symbol in symbols:
        try:
            result = await asyncio.to_thread(
                self._feed.client.futures_leverage_bracket,
                symbol=symbol,
            )
            if result:
                brackets = result[0].get('brackets', [])
                if brackets:
                    self._bracket_max[symbol] = int(brackets[0]['initialLeverage'])
        except Exception as exc:
            logger.warning(f"[{symbol}] Failed to fetch leverage bracket: {exc}")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_order_executor.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/order_executor.py tests/test_order_executor.py
git commit -m "feat: add fetch_leverage_brackets() and get_bracket_max() to OrderExecutor"
```

---

## Task 5: OrderExecutor — `symbol_registry` param + `check_symbols_on_exchange()` + auto-disable

**Files:**
- Modify: `bot/order_executor.py`
- Modify: `tests/test_order_executor.py`

This task wires `SymbolRegistry` into `OrderExecutor` and implements automatic symbol disable on fatal exchange errors.

**Error classification:**
- `-2019` (insufficient margin), `-1013` (insufficient balance) → `FundsError` — do NOT count toward failure counter, do NOT disable
- `-1121` (invalid symbol), `-2010` + "perpetual", any "is not available" message → `SymbolError` — disable immediately
- Other `BinanceAPIException` ≥ 3 consecutive → disable

- [ ] **Step 1: Add `import sys` and the two exception classes at the top of `order_executor.py`**

At the top of `bot/order_executor.py`, add `import sys` to the existing imports, then add after the `OrderState` enum:

```python
class FundsError(Exception):
    """Wraps exchange errors due to insufficient margin/balance. Does not count as a failure."""

class SymbolError(Exception):
    """Wraps exchange errors that mean the symbol should be disabled."""
    def __init__(self, symbol: str, reason: str) -> None:
        super().__init__(reason)
        self.symbol = symbol
        self.reason = reason
```

- [ ] **Step 2: Add `symbol_registry` to `__init__`**

In `OrderExecutor.__init__`, add after `self._notifier = notifier`:

```python
from bot.symbol_registry import SymbolRegistry as _SymbolRegistry
self._symbol_registry: _SymbolRegistry | None = symbol_registry
```

And update the constructor signature:

```python
def __init__(
    self,
    mode: Literal["test", "live"],
    settings: Settings,
    risk_manager: RiskManager,
    notifier: Notifier,
    data_feed: 'DataFeed | None' = None,
    symbol_registry: 'SymbolRegistry | None' = None,
) -> None:
```

(Import `SymbolRegistry` with `TYPE_CHECKING` to avoid circular imports. Add to the `if TYPE_CHECKING:` block: `from bot.symbol_registry import SymbolRegistry`.)

- [ ] **Step 3: Add `_auto_disable()` helper and `check_symbols_on_exchange()`**

After `reset_for_mode_switch`, add:

```python
async def check_symbols_on_exchange(self, symbols: list[str]) -> None:
    """Startup check: disable any symbol that is not TRADING/PERPETUAL."""
    if self._feed is None or self._symbol_registry is None:
        return
    try:
        info = await asyncio.to_thread(self._feed.client.futures_exchange_info)
        exchange_map = {s['symbol']: s for s in info.get('symbols', [])}
        for symbol in list(symbols):
            sym_info = exchange_map.get(symbol)
            if sym_info is None:
                await self._auto_disable(symbol, "symbol not found on exchange")
                continue
            status = sym_info.get('status', '')
            contract_type = sym_info.get('contractType', '')
            if status != 'TRADING' or contract_type != 'PERPETUAL':
                reason = f"status={status} contractType={contract_type}"
                await self._auto_disable(symbol, reason)
    except Exception as exc:
        logger.warning(f"check_symbols_on_exchange failed: {exc}")

async def _auto_disable(self, symbol: str, reason: str) -> None:
    """Disable symbol, close any open order, notify. Exit if all symbols are now disabled."""
    logger.error(f"Auto-disabling {symbol}: {reason}")
    if self._symbol_registry is not None:
        self._symbol_registry.disable(symbol, reason)
        if self._symbol_registry.all_disabled():
            self._notifier.notify(
                "emergency",
                "All symbols disabled — bot cannot continue",
                reason,
                "order_executor",
            )
            sys.exit(1)
    await self.close_order(symbol)
    self._notifier.notify("emergency", f"Symbol {symbol} disabled", reason, "order_executor")
```

- [ ] **Step 4: Refactor `_submit_to_exchange` to classify errors**

Replace the existing `_submit_to_exchange` method with:

```python
async def _submit_to_exchange(self, symbol: str, side: str, quantity: float, leverage: int) -> str | None:
    if self._feed is None:
        return None
    client = self._feed.client
    try:
        await asyncio.to_thread(
            client.futures_change_leverage,
            symbol=symbol,
            leverage=leverage,
        )
    except Exception as exc:
        logger.warning(f"[{symbol}] Could not set leverage={leverage}: {exc}")

    try:
        result = await asyncio.to_thread(
            client.futures_create_order,
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=str(quantity),
        )
        return str(result.get('orderId'))
    except Exception as exc:
        exc_str = str(exc).lower()
        exc_code = getattr(exc, 'code', None)

        if exc_code in (-2019, -1013):
            raise FundsError(str(exc)) from exc

        is_symbol_error = (
            exc_code == -1121
            or (exc_code == -2010 and "perpetual" in exc_str)
            or "is not available" in exc_str
        )
        if is_symbol_error:
            raise SymbolError(symbol, str(exc)) from exc

        raise  # other errors → caller records failure
```

- [ ] **Step 5: Update `place_order` to handle the new exception types**

In `place_order`, replace the single `except Exception as exc:` block with three handlers:

```python
            except FundsError as exc:
                self._states[symbol] = OrderState.IDLE
                logger.warning(f"[{symbol}] Order skipped — insufficient funds: {exc}")
                return False
            except SymbolError as sym_exc:
                self._states[symbol] = OrderState.IDLE
                await self._auto_disable(sym_exc.symbol, sym_exc.reason)
                return False
            except Exception as exc:
                self._states[symbol] = OrderState.IDLE
                threshold_hit = self._record_failure(symbol)
                if threshold_hit:
                    await self._auto_disable(symbol, f"consecutive_failures: {exc}")
                logger.error(f"Order placement failed for {symbol}: {exc}")
                return False
```

- [ ] **Step 6: Update `_record_failure` to return bool (threshold reached)**

Replace `_record_failure`:

```python
def _record_failure(self, symbol: str) -> bool:
    """Increment failure counter. Returns True if consecutive threshold is reached."""
    self._failure_counts[symbol] = self._failure_counts.get(symbol, 0) + 1
    reached = self._failure_counts[symbol] >= self._consecutive_failure_threshold
    if reached:
        self._notifier.notify(
            "emergency",
            f"Order placement threshold reached: {symbol}",
            f"{self._failure_counts[symbol]} consecutive failures",
            "order_executor",
        )
    return reached
```

- [ ] **Step 7: Write tests for the new behavior**

Add to `tests/test_order_executor.py`:

```python
@pytest.mark.asyncio
async def test_funds_error_does_not_increment_failure_counter():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    from bot.order_executor import FundsError

    async def funds_failing_submit(*a, **kw):
        raise FundsError("insufficient margin")

    ex._submit_to_exchange = funds_failing_submit
    await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
    assert ex._failure_counts.get('BTCUSDT', 0) == 0


@pytest.mark.asyncio
async def test_symbol_error_calls_auto_disable():
    from bot.order_executor import SymbolError
    from unittest.mock import AsyncMock

    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}
    ex._auto_disable = AsyncMock()

    async def symbol_failing_submit(symbol, *a, **kw):
        raise SymbolError(symbol, "invalid symbol")

    ex._submit_to_exchange = symbol_failing_submit
    await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
    ex._auto_disable.assert_awaited_once_with('BTCUSDT', 'invalid symbol')
```

- [ ] **Step 8: Run tests**

```bash
python -m pytest tests/test_order_executor.py -v
```

Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add bot/order_executor.py tests/test_order_executor.py
git commit -m "feat: add symbol_registry param, auto-disable, check_symbols_on_exchange to OrderExecutor"
```

---

## Task 6: DataFeed — `stream_combined()` with candle dedup guard

**Files:**
- Modify: `bot/data_feed.py`
- Test: `tests/test_data_feed.py` (new, created in Task 9)

The combined stream URL format (`combined_stream_url` static method) already exists in `DataFeed`. This task adds instance state and the `stream_combined` coroutine.

- [ ] **Step 1: Add `import time` and per-symbol state dicts to `DataFeed.__init__`**

In `bot/data_feed.py`, add `import time` with the existing imports.

In `DataFeed.__init__`, after the last existing `self._ws_base` line, add:

```python
# Combined stream / watchdog shared state (updated by stream_combined, read by watchdog)
self._last_candle_open: dict[str, int] = {}   # symbol → open_time_ms of last dispatched candle
self._last_price_ts: dict[str, float] = {}    # symbol → monotonic time of last price tick
self._last_candle_ts: dict[str, float] = {}   # symbol → monotonic time of last candle close
```

Also update `reinit` to reset these dicts (add after `self._ws_base = ...`):

```python
self._last_candle_open.clear()
self._last_price_ts.clear()
self._last_candle_ts.clear()
```

- [ ] **Step 2: Add `stream_combined()` method**

Add after `stream_klines` in `bot/data_feed.py`:

```python
async def stream_combined(
    self,
    get_symbols: Callable[[], list[str]],
    timeframe: str,
    on_candle_close: Callable[[str, list], Awaitable[None]],
    on_price_update: Callable[[str, float], Awaitable[None]],
) -> None:
    """
    Single combined WebSocket for all active symbols.
    get_symbols() is called on each reconnect so disabled symbols are excluded.
    """
    backoff = 1
    while True:
        symbols = get_symbols()
        url = self.combined_stream_url(symbols, timeframe, self._is_testnet)
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                logger.info(f"Combined stream connected ({len(symbols)} symbols): {', '.join(symbols)}")
                backoff = 1
                async for raw in ws:
                    msg = json.loads(raw)
                    stream = msg.get("stream", "")
                    k = msg.get("data", {}).get("k", {})
                    if not k or not stream:
                        continue
                    symbol = stream.split("@")[0].upper()
                    price = float(k["c"])
                    now = time.monotonic()
                    self._last_price_ts[symbol] = now

                    try:
                        await on_price_update(symbol, price)
                    except Exception as exc:
                        logger.warning(f"[{symbol}] on_price_update error: {exc}")

                    if k.get("x"):
                        open_time = int(k["t"])
                        if open_time > self._last_candle_open.get(symbol, -1):
                            self._last_candle_open[symbol] = open_time
                            self._last_candle_ts[symbol] = now
                            candle = [
                                int(k["t"]), k["o"], k["h"], k["l"], k["c"], k["v"],
                                int(k["T"]),
                            ]
                            try:
                                await on_candle_close(symbol, candle)
                            except Exception as exc:
                                logger.warning(f"[{symbol}] on_candle_close error: {exc}")
        except asyncio.CancelledError:
            logger.info("Combined stream cancelled")
            return
        except Exception as exc:
            logger.warning(f"Combined stream error: {exc}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
```

- [ ] **Step 3: Verify no test regressions**

```bash
python -m pytest tests/ -v --ignore=tests/test_data_feed.py
```

Expected: all existing tests PASS.

- [ ] **Step 4: Commit**

```bash
git add bot/data_feed.py
git commit -m "feat: add stream_combined() with per-symbol dedup guard to DataFeed"
```

---

## Task 7: DataFeed — `start_watchdog()`

**Files:**
- Modify: `bot/data_feed.py`

Price watchdog: every 5s, if `now - last_price_ts[symbol] > stale_threshold_s`, fetch via `futures_symbol_ticker`.
Candle watchdog: every 30s (every 6th price-watchdog tick), if `now - last_candle_ts[symbol] > 1.5 × timeframe_seconds`, fetch 3 recent klines and dispatch the newest closed one (dedup guard applies).

- [ ] **Step 1: Add `start_watchdog()` after `stream_combined()` in `bot/data_feed.py`**

```python
async def start_watchdog(
    self,
    get_symbols: Callable[[], list[str]],
    timeframe: str,
    on_candle_close: Callable[[str, list], Awaitable[None]],
    on_price_update: Callable[[str, float], Awaitable[None]],
    stale_threshold_s: float = 15.0,
) -> None:
    """
    Background REST fallback for stale price/candle data.
    Initialises timestamps so the stream gets a grace period before any fallback fires.
    """
    now = time.monotonic()
    for symbol in get_symbols():
        self._last_price_ts.setdefault(symbol, now)
        self._last_candle_ts.setdefault(symbol, now)

    timeframe_s = self._timeframe_to_ms(timeframe) / 1000.0
    candle_tick = 0
    CANDLE_EVERY = 6  # 6 × 5s = 30s

    while True:
        try:
            await asyncio.sleep(5)
            now = time.monotonic()
            now_ms = int(time.time() * 1000)
            symbols = get_symbols()

            for symbol in symbols:
                if now - self._last_price_ts.get(symbol, now) > stale_threshold_s:
                    try:
                        ticker = await asyncio.to_thread(
                            self._client.futures_symbol_ticker, symbol=symbol
                        )
                        price = float(ticker.get("price", 0))
                        if price > 0:
                            self._last_price_ts[symbol] = now
                            await on_price_update(symbol, price)
                    except Exception as exc:
                        logger.warning(f"[{symbol}] Price watchdog fetch failed: {exc}")

            candle_tick += 1
            if candle_tick < CANDLE_EVERY:
                continue
            candle_tick = 0

            for symbol in symbols:
                if now - self._last_candle_ts.get(symbol, now) <= 1.5 * timeframe_s:
                    continue
                try:
                    klines = await asyncio.to_thread(
                        self._client.futures_klines,
                        symbol=symbol, interval=timeframe, limit=3,
                    )
                    for kline in reversed(klines):
                        if int(kline[6]) < now_ms:
                            open_time = int(kline[0])
                            if open_time > self._last_candle_open.get(symbol, -1):
                                self._last_candle_open[symbol] = open_time
                                self._last_candle_ts[symbol] = now
                                candle = [
                                    int(kline[0]), kline[1], kline[2], kline[3],
                                    kline[4], kline[5], int(kline[6]),
                                ]
                                try:
                                    await on_candle_close(symbol, candle)
                                except Exception as exc:
                                    logger.warning(f"[{symbol}] Watchdog candle error: {exc}")
                            break
                except Exception as exc:
                    logger.warning(f"[{symbol}] Candle watchdog fetch failed: {exc}")

        except asyncio.CancelledError:
            logger.info("Watchdog cancelled")
            return
        except Exception as exc:
            logger.warning(f"Watchdog outer error: {exc}")
```

- [ ] **Step 2: Run existing tests to verify no regressions**

```bash
python -m pytest tests/ -v --ignore=tests/test_data_feed.py
```

Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add bot/data_feed.py
git commit -m "feat: add start_watchdog() price and candle fallback to DataFeed"
```

---

## Task 8: `main.py` — full refactor

**Files:**
- Modify: `main.py`

This is a complete replacement of `run()` and the module-level helpers. Read the current `main.py` carefully before making changes.

Key changes vs current code:
- Import `SymbolRegistry`, `math`, `time`
- Module-level balance debounce: `_last_balance_poll`, `_BALANCE_POLL_INTERVAL`, `_should_poll_balance()`
- `run()`: load SymbolRegistry, fail-fast if empty, per-symbol `analyzers` / `sym_settings` dicts, combined stream + watchdog replacing `stream_klines`
- `on_candle_close(symbol, kline)` — symbol-scoped
- `on_price_update(symbol, price)` — symbol-scoped, calls `check_symbol_price`
- `_try_place_order(symbol, best, settings)` helper — optimal leverage formula
- `_heartbeat_loop` — pass `symbol_registry` for active/disabled counts
- Remove `display.show()` from candle callback
- `on_switch_mode` — fetch brackets + refresh klines for each symbol

- [ ] **Step 1: Write the new `main.py`**

Replace the entire content of `main.py` with:

```python
import asyncio
import dataclasses
import json
import logging
import logging.handlers
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from backtest import PRESETS, LOCKED_PRESETS
from config.settings import load_settings
from bot.analyzer import Analyzer
from bot.data_feed import DataFeed
from bot.recommendation_engine import RecommendationEngine
from bot.exporter import export, write_symbols_json
from bot.mode_manager import ModeManager
from bot.notifier import Notifier
from bot.order_executor import OrderExecutor, OrderState
from bot.symbol_registry import SymbolRegistry
from bot.virtual_tracker import VirtualTracker
from bot.risk_manager import RiskManager
from config.risk_config import load_risk_config

_PROJECT_ROOT = Path(__file__).resolve().parent
_BOT_PID_PATH = _PROJECT_ROOT / "data" / "bot_pid.json"
_BOT_STATE_PATH = _PROJECT_ROOT / "dashboard" / "public" / "bot_state.json"
_HEARTBEAT_INTERVAL = 10  # seconds

_last_balance_poll: float = 0.0
_BALANCE_POLL_INTERVAL = 30.0


def _should_poll_balance() -> bool:
    global _last_balance_poll
    now = time.monotonic()
    if now - _last_balance_poll >= _BALANCE_POLL_INTERVAL:
        _last_balance_poll = now
        return True
    return False


def _write_pid() -> None:
    _BOT_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BOT_PID_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"pid": os.getpid()}))
    tmp.replace(_BOT_PID_PATH)


def _write_bot_state(running: bool, mode: str, started_at: str,
                     symbols_active: int = 0, symbols_disabled: int = 0) -> None:
    _BOT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BOT_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "running": running,
        "pid": os.getpid(),
        "mode": mode,
        "started_at": started_at,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "symbols_active": symbols_active,
        "symbols_disabled": symbols_disabled,
    }))
    tmp.replace(_BOT_STATE_PATH)


async def _heartbeat_loop(mode_manager: ModeManager, started_at: str,
                          symbol_registry: SymbolRegistry) -> None:
    while True:
        active = len(symbol_registry.get_symbols())
        disabled = len(symbol_registry.get_disabled())
        _write_bot_state(True, mode_manager.current_mode, started_at,
                         symbols_active=active, symbols_disabled=disabled)
        await asyncio.sleep(_HEARTBEAT_INTERVAL)


def setup_logging() -> None:
    Path('logs').mkdir(exist_ok=True)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    general = logging.handlers.RotatingFileHandler(
        'logs/bot.log', maxBytes=10 * 1024 * 1024, backupCount=5
    )
    general.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(general)

    trades_fmt = logging.Formatter('%(asctime)s %(message)s')
    trades_handler = logging.handlers.RotatingFileHandler(
        'logs/trades.log', maxBytes=10 * 1024 * 1024, backupCount=5
    )
    trades_handler.setFormatter(trades_fmt)
    trades_logger = logging.getLogger('trades')
    trades_logger.setLevel(logging.INFO)
    trades_logger.addHandler(trades_handler)
    trades_logger.propagate = False


async def run() -> None:
    logger = logging.getLogger('main')
    trades_logger = logging.getLogger('trades')
    risk_cfg = load_risk_config()

    # Load symbol registry — source of truth for active symbols
    seed_symbols = [s.strip().upper() for s in os.getenv('SYMBOL', '').split(',') if s.strip()]
    symbol_registry = SymbolRegistry(seed_symbols=seed_symbols)
    symbols = symbol_registry.get_symbols()
    if not symbols:
        logger.error("No active symbols in registry — cannot start")
        sys.exit(1)

    notifier = Notifier(
        log_path=_PROJECT_ROOT / "data" / "system_log.json",
        alert_path=_PROJECT_ROOT / "dashboard" / "public" / "alert_state.json",
        telegram_token=risk_cfg.get("telegram", {}).get("token", ""),
        telegram_chat_id=risk_cfg.get("telegram", {}).get("chat_id", ""),
    )
    mode_manager = ModeManager(notifier=notifier)
    current_mode = mode_manager.current_mode

    risk_manager = RiskManager(
        mode=current_mode,
        initial_balance=risk_cfg.get("test_starting_balance_usdt", 10000.0),
        notifier=notifier,
    )

    # Load settings and build per-symbol state
    sym_settings: dict = {}
    analyzers: dict = {}
    for symbol in symbols:
        s = load_settings(symbol)
        sym_settings[symbol] = s
        engine = RecommendationEngine(s)
        analyzers[symbol] = Analyzer(s.swing_neighbours, engine)

    timeframe = sym_settings[symbols[0]].timeframe
    first_settings = sym_settings[symbols[0]]

    order_executor = OrderExecutor(
        mode=current_mode,
        settings=first_settings,
        risk_manager=risk_manager,
        notifier=notifier,
        symbol_registry=symbol_registry,
    )

    virtual_tracker = VirtualTracker(
        mode=current_mode,
        orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{current_mode}.json",
        efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{current_mode}.json",
    )

    started_at = datetime.now(timezone.utc).isoformat()
    try:
        _write_pid()
        _write_bot_state(running=True, mode=current_mode, started_at=started_at,
                         symbols_active=len(symbols))
    except Exception as exc:
        logger.warning(f"Failed to write bot state files: {exc}")

    write_symbols_json(symbols)
    logger.info(
        f"Bot starting | mode={current_mode} | "
        f"symbols={','.join(symbols)} | timeframe={timeframe}"
    )

    # Run obligatory startup backtest
    notifier.notify("info", "Running obligatory backtest", f"mode={current_mode}", "main")
    bt_result = subprocess.run(
        ["python", "backtest.py", "--mode", current_mode],
        capture_output=True,
        cwd=str(_PROJECT_ROOT),
    )
    if bt_result.returncode != 0:
        notifier.notify("emergency", "Obligatory backtest failed — cannot start",
                        bt_result.stderr.decode()[:500], "main")
        sys.exit(1)

    for sym in symbols:
        bt_path = _PROJECT_ROOT / "dashboard" / "public" / f"backtest_results_{sym}.json"
        virtual_tracker.seed_from_backtest(sym, bt_path)

    feed = DataFeed(first_settings)
    order_executor._feed = feed

    # Proactive exchange check + leverage brackets
    await order_executor.check_symbols_on_exchange(symbols)
    await order_executor.fetch_leverage_brackets(symbols)

    # Kline bootstrap + initial export
    for symbol in symbols:
        s = sym_settings[symbol]
        klines = feed.load_klines(symbol, timeframe, limit=1500)
        analyzers[symbol].build_from_klines(klines)
        recs = analyzers[symbol].get_recommendations()
        best = analyzers[symbol].get_best_recommendation()
        export(
            symbol, timeframe, current_mode,
            analyzers[symbol].get_current_price(), analyzers[symbol].get_trend(),
            analyzers[symbol].get_klines(), recs,
            analyzers[symbol].get_all_points(), best,
        )

    await order_executor.reconcile_with_exchange()
    notifier.notify("info", "Startup complete", f"{len(symbols)} symbol(s) active", "main")

    # ── Callbacks ──────────────────────────────────────────────────────── #

    async def _try_place_order(symbol: str, best, settings) -> None:
        preset_name = virtual_tracker.best_preset(symbol)
        all_presets = {**LOCKED_PRESETS, **PRESETS}
        overrides = all_presets.get(preset_name or 'default', {})
        preset_settings = dataclasses.replace(settings, **overrides)

        balance = risk_manager.get_balance()
        allocation = min(risk_manager.get_allocation(symbol), balance)
        entry = best.getEntryPrice()
        if entry <= 0 or allocation <= 0:
            return

        min_notional = await order_executor.get_min_notional(symbol)
        bracket_max = order_executor.get_bracket_max(symbol)
        target_lev = risk_manager.get_leverage(symbol)

        min_viable_lev = math.ceil(min_notional / allocation) if allocation > 0 else 999
        if min_viable_lev > bracket_max:
            logger.info(f"[{symbol}] Cannot meet min_notional at any leverage, skipping")
            return

        actual_lev = max(min_viable_lev, min(target_lev, bracket_max))
        quantity = allocation * actual_lev / entry

        allowed, reason = risk_manager.can_open_sync(symbol, allocation)
        if not allowed:
            logger.info(f"[{symbol}] Order skipped: {reason}")
            return
        if quantity <= 0:
            return

        await order_executor.place_order(
            symbol=symbol,
            preset_name=preset_name or 'default',
            side=best.getSide(),
            entry=entry,
            tp=best.getTarget(),
            sl=best.getStop() or 0.0,
            quantity=quantity,
            leverage=actual_lev,
            partial_take_pct=preset_settings.partial_take_pct,
            trailing_stop_pct=preset_settings.trailing_stop_pct,
            level=best.getLevel(),
            signal_type=best.getType().value,
        )

    async def on_candle_close(symbol: str, kline: list) -> None:
        if os.path.exists('STOP'):
            logger.info("STOP file detected — halting.")
            raise SystemExit(0)

        if symbol_registry.is_disabled(symbol):
            return

        settings = sym_settings[symbol]
        analyzer = analyzers[symbol]

        recs = analyzer.add_candle(kline)
        best = analyzer.get_best_recommendation()

        try:
            feed.refresh_klines(symbol, timeframe, fetch_count=10)
        except Exception as e:
            logger.warning(f"[{symbol}] Kline refresh failed: {e}")

        if _should_poll_balance():
            try:
                balance = await order_executor.fetch_account_balance()
                if balance > 0:
                    risk_manager.update_balance(balance)
            except Exception as exc:
                logger.warning(f"Balance fetch failed: {exc}")

        if best is not None and order_executor.get_state(symbol) == OrderState.IDLE:
            await _try_place_order(symbol, best, settings)

        export(
            symbol, timeframe, settings.trading_mode,
            analyzer.get_current_price(), analyzer.get_trend(),
            analyzer.get_klines(), recs, analyzer.get_all_points(), best,
        )

        if best:
            trades_logger.info(f"BEST | symbol={symbol} | {best}")
        for rec in recs:
            trades_logger.info(f"CANDIDATE | symbol={symbol} | {rec}")

    async def on_price_update(symbol: str, price: float) -> None:
        if symbol in analyzers:
            analyzers[symbol].update_price(price)

        closed = await order_executor.check_symbol_price(symbol, price)
        for c in closed:
            virtual_tracker.record_closed_trade(c['symbol'], c['preset_name'], c['pnl_usdt'])

    async def on_switch_mode(target_mode: str) -> None:
        nonlocal virtual_tracker
        await order_executor.close_all_orders_at_market()
        order_executor.reset_for_mode_switch(target_mode)
        risk_manager.reset_for_mode_switch(target_mode)
        settings_new = load_settings(symbols[0])
        feed.reinit(target_mode, settings_new.api_key, settings_new.api_secret)
        bt_result = await asyncio.to_thread(
            subprocess.run,
            ["python", "backtest.py", "--mode", target_mode],
            capture_output=True,
            cwd=str(_PROJECT_ROOT),
        )
        if bt_result.returncode != 0:
            notifier.notify(
                "emergency",
                f"Backtest failed during mode switch to {target_mode}",
                bt_result.stderr.decode()[:500],
                "main",
            )
            return
        await order_executor.fetch_leverage_brackets(symbols)
        for symbol in symbols:
            feed.refresh_klines(symbol, timeframe, fetch_count=1500)
        virtual_tracker = VirtualTracker(
            mode=target_mode,
            orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{target_mode}.json",
            efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{target_mode}.json",
        )
        for sym in symbols:
            bt_path = _PROJECT_ROOT / "dashboard" / "public" / f"backtest_results_{sym}.json"
            virtual_tracker.seed_from_backtest(sym, bt_path)
        notifier.notify("info", f"Mode switched to {target_mode}", "", "mode_manager")

    async def on_stop_bot() -> None:
        await order_executor.close_all_orders_at_market()
        notifier.notify("info", "Bot stopped", "Clean shutdown via dashboard", "main")
        sys.exit(0)

    # ── Task setup ─────────────────────────────────────────────────────── #

    _poll_task = asyncio.create_task(
        mode_manager.poll_loop(on_switch_mode=on_switch_mode, on_stop_bot=on_stop_bot)
    )
    _hb_task = asyncio.create_task(
        _heartbeat_loop(mode_manager, started_at, symbol_registry)
    )
    _watchdog_task = asyncio.create_task(
        feed.start_watchdog(
            get_symbols=symbol_registry.get_symbols,
            timeframe=timeframe,
            on_candle_close=on_candle_close,
            on_price_update=on_price_update,
        )
    )

    try:
        await feed.stream_combined(
            get_symbols=symbol_registry.get_symbols,
            timeframe=timeframe,
            on_candle_close=on_candle_close,
            on_price_update=on_price_update,
        )
    finally:
        for t in [_poll_task, _hb_task, _watchdog_task]:
            t.cancel()
        for t in [_poll_task, _hb_task, _watchdog_task]:
            try:
                await t
            except asyncio.CancelledError:
                pass


if __name__ == '__main__':
    setup_logging()
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger('main').info("Bot stopped.")
    finally:
        try:
            state_text = _BOT_STATE_PATH.read_text() if _BOT_STATE_PATH.exists() else '{}'
            state = json.loads(state_text)
            _write_bot_state(
                running=False,
                mode=state.get('mode', 'test'),
                started_at=state.get('started_at', ''),
            )
        except Exception as exc:
            logging.getLogger('main').warning(f"Failed to write shutdown state: {exc}")
```

- [ ] **Step 2: Verify the file parses cleanly**

```bash
python -m py_compile main.py && echo "OK"
```

Expected: `OK` with no output.

- [ ] **Step 3: Run all existing tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_data_feed.py
```

Expected: all tests PASS. (Nothing in the test suite exercises `main.py` directly.)

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: refactor main.py for multi-symbol combined stream and per-symbol state"
```

---

## Task 9: `tests/test_data_feed.py` — new test file

**Files:**
- Create: `tests/test_data_feed.py`

Tests for `stream_combined` message parsing and the dedup guard, using in-process fake WebSocket messages (no real network).

- [ ] **Step 1: Create `tests/test_data_feed.py`**

```python
# tests/test_data_feed.py
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from bot.data_feed import DataFeed


def make_feed(testnet: bool = True) -> DataFeed:
    settings = MagicMock()
    settings.trading_mode = 'test'
    settings.api_key = 'k'
    settings.api_secret = 's'
    settings.kline_cache_limit = 5000
    with patch('bot.data_feed.Client'):
        return DataFeed(settings)


def make_kline_msg(symbol: str, price: str, open_time: int, closed: bool) -> str:
    return json.dumps({
        "stream": f"{symbol.lower()}@kline_15m",
        "data": {
            "k": {
                "t": open_time,
                "T": open_time + 899999,
                "o": price, "h": price, "l": price, "c": price, "v": "100",
                "x": closed,
            }
        }
    })


# ── Combined stream message parsing ────────────────────────────────────── #

@pytest.mark.asyncio
async def test_stream_combined_dispatches_price_update():
    feed = make_feed()
    received: list = []

    async def fake_price(symbol: str, price: float) -> None:
        received.append((symbol, price))

    async def fake_candle(symbol: str, kline: list) -> None:
        pass

    msgs = [make_kline_msg("BTCUSDT", "50000.0", 1000000, False)]
    msg_iter = iter(msgs)

    class FakeWS:
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                return next(msg_iter)
            except StopIteration:
                raise asyncio.CancelledError

    with patch('websockets.connect') as mock_connect:
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=FakeWS())
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(
                feed.stream_combined(
                    get_symbols=lambda: ['BTCUSDT'],
                    timeframe='15m',
                    on_candle_close=fake_candle,
                    on_price_update=fake_price,
                ),
                timeout=1.0,
            )

    assert any(sym == 'BTCUSDT' and price == pytest.approx(50000.0) for sym, price in received)


@pytest.mark.asyncio
async def test_stream_combined_dispatches_candle_close():
    feed = make_feed()
    candles: list = []

    async def fake_price(symbol: str, price: float) -> None:
        pass

    async def fake_candle(symbol: str, kline: list) -> None:
        candles.append((symbol, kline))

    msgs = [make_kline_msg("ETHUSDT", "2000.0", 2000000, True)]
    msg_iter = iter(msgs)

    class FakeWS:
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                return next(msg_iter)
            except StopIteration:
                raise asyncio.CancelledError

    with patch('websockets.connect') as mock_connect:
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=FakeWS())
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
            await asyncio.wait_for(
                feed.stream_combined(
                    get_symbols=lambda: ['ETHUSDT'],
                    timeframe='15m',
                    on_candle_close=fake_candle,
                    on_price_update=fake_price,
                ),
                timeout=1.0,
            )

    assert len(candles) == 1
    sym, kline = candles[0]
    assert sym == 'ETHUSDT'
    assert kline[0] == 2000000


# ── Dedup guard ────────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_stream_combined_dedup_guard_rejects_same_open_time():
    """Two messages with the same open_time for the same symbol must fire only one candle_close."""
    feed = make_feed()
    candles: list = []

    async def fake_price(symbol: str, price: float) -> None:
        pass

    async def fake_candle(symbol: str, kline: list) -> None:
        candles.append((symbol, kline))

    # Same open_time 3000000 sent twice
    msgs = [
        make_kline_msg("BTCUSDT", "50000.0", 3000000, True),
        make_kline_msg("BTCUSDT", "50100.0", 3000000, True),
    ]
    msg_iter = iter(msgs)

    class FakeWS:
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                return next(msg_iter)
            except StopIteration:
                raise asyncio.CancelledError

    with patch('websockets.connect') as mock_connect:
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=FakeWS())
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
            await asyncio.wait_for(
                feed.stream_combined(
                    get_symbols=lambda: ['BTCUSDT'],
                    timeframe='15m',
                    on_candle_close=fake_candle,
                    on_price_update=fake_price,
                ),
                timeout=1.0,
            )

    assert len(candles) == 1, f"Expected 1 candle dispatch, got {len(candles)}"


@pytest.mark.asyncio
async def test_stream_combined_new_open_time_dispatches_again():
    """After a candle with open_time T fires, a candle with open_time T+1 must also fire."""
    feed = make_feed()
    candles: list = []

    async def fake_price(symbol: str, price: float) -> None:
        pass

    async def fake_candle(symbol: str, kline: list) -> None:
        candles.append((symbol, int(kline[0])))

    msgs = [
        make_kline_msg("BTCUSDT", "50000.0", 4000000, True),
        make_kline_msg("BTCUSDT", "50100.0", 4900000, True),  # newer open_time
    ]
    msg_iter = iter(msgs)

    class FakeWS:
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                return next(msg_iter)
            except StopIteration:
                raise asyncio.CancelledError

    with patch('websockets.connect') as mock_connect:
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=FakeWS())
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
            await asyncio.wait_for(
                feed.stream_combined(
                    get_symbols=lambda: ['BTCUSDT'],
                    timeframe='15m',
                    on_candle_close=fake_candle,
                    on_price_update=fake_price,
                ),
                timeout=1.0,
            )

    assert candles == [4000000, 4900000]
```

- [ ] **Step 2: Run the new tests**

```bash
python -m pytest tests/test_data_feed.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 3: Run the full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_data_feed.py
git commit -m "test: add test_data_feed.py for stream_combined parsing and dedup guard"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Task covering it |
|---|---|
| §2.1 `stream_combined` + `get_symbols` callable | Task 6 |
| §2.3 Candle dedup guard | Task 6 |
| §3.1 Price watchdog | Task 7 |
| §3.2 Candle watchdog (limit=3, iterate newest-to-oldest) | Task 7 |
| §3.3 `start_watchdog` signature + ts init | Task 7 |
| §4.1 `check_symbol_price` rename | Task 2 |
| §5.1 `min_notional` in lot cache + `get_min_notional` | Task 3 |
| §5.2 `fetch_leverage_brackets` + `get_bracket_max` | Task 4 |
| §5.3 Optimal leverage formula + `risk_manager.get_balance()` | Task 1 + Task 8 |
| §6.1 `symbol_registry` param | Task 5 |
| §6.2 `check_symbols_on_exchange` startup check | Task 5 |
| §6.3 Runtime disable: error classification, funds skip | Task 5 |
| §6.3 Consecutive failure → disable | Task 5 |
| §6.3 Post-disable `all_disabled` guard → exit | Task 5 |
| §6.4 `is_disabled` gate in `on_candle_close` | Task 8 |
| §7.1 Per-symbol `analyzers` + `sym_settings` | Task 8 |
| §7.2 Fail-fast on empty symbol list | Task 8 |
| §7.2 New symbols require restart (documented in spec, not code) | — |
| §7.3 `on_candle_close(symbol, kline)` | Task 8 |
| §7.4 `on_price_update(symbol, price)` | Task 8 |
| §7.5 Balance debounce | Task 8 |
| §7.6 Remove `display.show()` | Task 8 |
| §7.7 Mode switch: brackets + kline refresh | Task 8 |
| §8 Kline bootstrap 1500 | Task 8 |
| §9 `bot/risk_manager.py` `get_balance()` | Task 1 |
| §11 Test updates + new test_data_feed.py | Tasks 2–5 + Task 9 |

All spec requirements are covered.
