# Trades Page & Virtual Order Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/trades` dashboard page showing real order history and preset efficiency, backed by a virtual order simulation engine that independently tracks all non-best presets against live prices.

**Architecture:** `VirtualOrderSimulator` runs one `FakeOrder` per open virtual position (reusing the existing trailing-stop/TP/SL logic), persists orders to `virtual_orders_{symbol}_{mode}.json`, and feeds efficiency data into the existing `VirtualTracker`. `OrderExecutor` gains real order recording. The `/trades` Next.js page reads these files via a new API route and renders a preset efficiency table, a close-price chart with trade overlays, and a real orders list.

**Tech Stack:** Python asyncio, `FakeOrder` (existing), `dataclasses.replace` for preset settings, Next.js 15 App Router, Chart.js, TypeScript.

---

## File Map

| Action | File | What changes |
|--------|------|-------------|
| Modify | `bot/virtual_tracker.py` | `seed_from_backtest` — conditional seeding + fix dict iteration bug |
| Modify | `bot/analyzer.py` | Add `get_recommendation_for_preset(overrides)` |
| Create | `bot/virtual_order_simulator.py` | `VirtualOrderSimulator` class |
| Modify | `bot/order_executor.py` | Add `project_root` param, `_record_real_order_close`, `_last_opened_preset` |
| Modify | `main.py` | Wire `VirtualOrderSimulator` into candle close, price update, stop, mode switch |
| Create | `tests/test_virtual_order_simulator.py` | Tests for VirtualOrderSimulator + seed fix |
| Create | `tests/test_real_order_recording.py` | Tests for real order recording |
| Create | `dashboard/app/api/trades/route.ts` | GET /api/trades?symbol=X |
| Modify | `dashboard/lib/types.ts` | Add `RealOrder`, `VirtualSummaryEntry`, `TradesData` |
| Modify | `dashboard/components/NavBar.tsx` | Add Trades link |
| Create | `dashboard/components/TradesChart.tsx` | Close-price chart with trade entry/exit markers |
| Create | `dashboard/app/trades/page.tsx` | Full trades page |

---

## Task 1: Fix `seed_from_backtest` and add `Analyzer.get_recommendation_for_preset`

**Files:**
- Modify: `bot/virtual_tracker.py:25-37`
- Modify: `bot/analyzer.py`
- Test: `tests/test_virtual_order_simulator.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_virtual_order_simulator.py`:

```python
# tests/test_virtual_order_simulator.py
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from bot.virtual_tracker import VirtualTracker


def make_tracker(tmp_path):
    return VirtualTracker(
        mode='test',
        orders_path=tmp_path / 'virtual_orders_test.json',
        efficiency_path=tmp_path / 'preset_efficiency_test.json',
    )


def make_backtest_file(tmp_path, symbol='BTCUSDT'):
    data = {
        'presets': {
            'preset_a': {
                'balance_start': 1000.0,
                'total_trades': 3,
                'trades': [
                    {'profit_pct': 1.0},
                    {'profit_pct': -0.5},
                    {'profit_pct': 2.0},
                ],
            },
            'preset_b': {
                'balance_start': 1000.0,
                'total_trades': 2,
                'trades': [
                    {'profit_pct': -1.0},
                    {'profit_pct': -0.5},
                ],
            },
        }
    }
    p = tmp_path / f'backtest_results_{symbol}.json'
    p.write_text(json.dumps(data))
    return p


def test_seed_from_backtest_populates_efficiency(tmp_path):
    tracker = make_tracker(tmp_path)
    bt_path = make_backtest_file(tmp_path)
    tracker.seed_from_backtest('BTCUSDT', bt_path)
    eff = tracker.get_efficiency('BTCUSDT', 'preset_a')
    assert eff['trade_count'] == 3
    assert eff['total_winning_usdt'] == pytest.approx(30.0)  # (1.0 + 2.0) / 100 * 1000


def test_seed_from_backtest_skips_if_symbol_already_seeded(tmp_path):
    tracker = make_tracker(tmp_path)
    bt_path = make_backtest_file(tmp_path)
    tracker.seed_from_backtest('BTCUSDT', bt_path)
    # Corrupt the backtest file — should not be read again
    bt_path.write_text('{"presets": {}}')
    tracker.seed_from_backtest('BTCUSDT', bt_path)
    eff = tracker.get_efficiency('BTCUSDT', 'preset_a')
    assert eff['trade_count'] == 3  # still the original value


def test_seed_from_backtest_seeds_new_symbol_even_if_other_exists(tmp_path):
    tracker = make_tracker(tmp_path)
    bt_path_btc = make_backtest_file(tmp_path, 'BTCUSDT')
    bt_path_eth = make_backtest_file(tmp_path, 'ETHUSDT')
    tracker.seed_from_backtest('BTCUSDT', bt_path_btc)
    tracker.seed_from_backtest('ETHUSDT', bt_path_eth)
    assert tracker.get_efficiency('ETHUSDT', 'preset_a')['trade_count'] == 3
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -m pytest tests/test_virtual_order_simulator.py::test_seed_from_backtest_populates_efficiency tests/test_virtual_order_simulator.py::test_seed_from_backtest_skips_if_symbol_already_seeded tests/test_virtual_order_simulator.py::test_seed_from_backtest_seeds_new_symbol_even_if_other_exists -v
```

Expected: FAIL (`AssertionError` — current code iterates dict keys, not dict items)

- [ ] **Step 3: Fix `seed_from_backtest` in `bot/virtual_tracker.py`**

Replace lines 25–37 with:

```python
def seed_from_backtest(self, symbol: str, backtest_path: Path) -> None:
    if symbol in self._efficiency:
        return  # already seeded — don't overwrite runtime data
    if not backtest_path.exists():
        logger.warning(f"No backtest file for {symbol}: {backtest_path}")
        return
    try:
        data = json.loads(backtest_path.read_text())
        for preset_name, preset_data in data.get("presets", {}).items():
            balance_start = float(preset_data.get("balance_start", 1000.0))
            trades = preset_data.get("trades", [])
            winning_usdt = sum(
                t.get("profit_pct", 0.0) / 100.0 * balance_start
                for t in trades
                if t.get("profit_pct", 0.0) > 0
            )
            self._set_efficiency(symbol, preset_name, total_winning=winning_usdt, count=len(trades))
    except Exception as exc:
        logger.error(f"Failed to seed efficiency for {symbol}: {exc}")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_virtual_order_simulator.py::test_seed_from_backtest_populates_efficiency tests/test_virtual_order_simulator.py::test_seed_from_backtest_skips_if_symbol_already_seeded tests/test_virtual_order_simulator.py::test_seed_from_backtest_seeds_new_symbol_even_if_other_exists -v
```

Expected: PASS

- [ ] **Step 5: Add `get_recommendation_for_preset` to `bot/analyzer.py`**

Add after the `get_best_recommendation` method (after line 128):

```python
def get_recommendation_for_preset(self, overrides: dict):
    """Run the recommendation engine with preset-overridden settings. Returns None if no signal."""
    if self._trend is None or self._engine is None:
        return None
    import dataclasses
    from bot.recommendation_engine import RecommendationEngine
    s = dataclasses.replace(self._engine._s, **overrides)
    return RecommendationEngine(s).generate(self._trend, self._current_price)
```

- [ ] **Step 6: Verify analyzer helper works**

```bash
python -c "
from unittest.mock import MagicMock, patch
from bot.analyzer import Analyzer
from bot.recommendation_engine import RecommendationEngine
from config.settings import load_settings
import os
os.environ.setdefault('SYMBOL', 'BTCUSDT')
s = MagicMock()
s.proximity_zone_pct = 10.0
s.swing_neighbours = 2
engine = MagicMock(spec=RecommendationEngine)
engine._s = s
a = Analyzer(2, engine)
print('get_recommendation_for_preset exists:', hasattr(a, 'get_recommendation_for_preset'))
print('returns None when trend is None:', a.get_recommendation_for_preset({}) is None)
"
```

Expected: both `True`

- [ ] **Step 7: Commit**

```bash
git add bot/virtual_tracker.py bot/analyzer.py tests/test_virtual_order_simulator.py
git commit -m "fix: conditional seed_from_backtest + fix dict iteration; add Analyzer.get_recommendation_for_preset"
```

---

## Task 2: Build `VirtualOrderSimulator`

**Files:**
- Create: `bot/virtual_order_simulator.py`
- Test: `tests/test_virtual_order_simulator.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_virtual_order_simulator.py`:

```python
import asyncio
from bot.virtual_order_simulator import VirtualOrderSimulator
from backtest import PRESETS, LOCKED_PRESETS


def make_simulator(tmp_path, mode='test'):
    all_presets = {**LOCKED_PRESETS, **PRESETS}
    risk_manager = MagicMock()
    risk_manager.get_allocation.return_value = 100.0
    risk_manager.get_leverage.return_value = 5
    return VirtualOrderSimulator(
        mode=mode,
        all_presets=all_presets,
        project_root=tmp_path,
        risk_manager=risk_manager,
    )


def make_analyzer_mock(price=50000.0, rec=None):
    analyzer = MagicMock()
    analyzer.get_current_price.return_value = price
    analyzer.get_recommendation_for_preset.return_value = rec
    return analyzer


def make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0):
    rec = MagicMock()
    rec.getSide.return_value = side
    rec.getEntryPrice.return_value = entry
    rec.getTarget.return_value = tp
    rec.getStop.return_value = sl
    rec.getLevel.return_value = 1
    rec.getType.return_value = MagicMock(value='test_signal')
    return rec


@pytest.mark.asyncio
async def test_virtual_order_opens_on_signal(tmp_path):
    sim = make_simulator(tmp_path)
    rec = make_rec()
    analyzer = make_analyzer_mock(rec=rec)
    from config.settings import Settings
    base_settings = MagicMock(spec=Settings)

    await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)

    # Should have open virtual orders for presets other than 'some_best_preset'
    assert len(sim._open.get('BTCUSDT', {})) > 0


@pytest.mark.asyncio
async def test_virtual_order_dedup_no_double_open(tmp_path):
    sim = make_simulator(tmp_path)
    rec = make_rec()
    analyzer = make_analyzer_mock(rec=rec)
    base_settings = MagicMock()

    await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)
    count_after_first = len(sim._open.get('BTCUSDT', {}))

    await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)
    count_after_second = len(sim._open.get('BTCUSDT', {}))

    assert count_after_first == count_after_second  # no double-open


@pytest.mark.asyncio
async def test_virtual_order_closes_on_tp(tmp_path):
    sim = make_simulator(tmp_path)
    rec = make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0)
    analyzer = make_analyzer_mock(rec=rec)
    base_settings = MagicMock()

    await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)
    preset_name = next(iter(sim._open.get('BTCUSDT', {})))

    closed = await sim.check_prices('BTCUSDT', 55001.0)  # above TP
    assert len(closed) >= 1
    assert closed[0]['result'] in ('win', 'trail', 'partial')
    assert preset_name not in sim._open.get('BTCUSDT', {})


@pytest.mark.asyncio
async def test_virtual_order_closes_on_sl(tmp_path):
    sim = make_simulator(tmp_path)
    rec = make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0)
    analyzer = make_analyzer_mock(rec=rec)
    base_settings = MagicMock()

    await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)

    closed = await sim.check_prices('BTCUSDT', 47999.0)  # below SL
    assert len(closed) >= 1
    assert closed[0]['result'] == 'loss'


@pytest.mark.asyncio
async def test_close_all_open_marks_closed_early(tmp_path):
    sim = make_simulator(tmp_path)
    rec = make_rec()
    analyzer = make_analyzer_mock(rec=rec)
    base_settings = MagicMock()

    await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)
    assert len(sim._open.get('BTCUSDT', {})) > 0

    feed_mock = MagicMock()
    feed_mock.client.futures_symbol_ticker.return_value = {'price': '51000.0'}

    await sim.close_all_open(['BTCUSDT'], feed_mock)
    assert len(sim._open.get('BTCUSDT', {})) == 0


@pytest.mark.asyncio
async def test_virtual_order_persists_to_file(tmp_path):
    sim = make_simulator(tmp_path)
    rec = make_rec()
    analyzer = make_analyzer_mock(rec=rec)
    base_settings = MagicMock()

    await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)
    await sim.check_prices('BTCUSDT', 55001.0)

    file = tmp_path / 'data' / 'virtual_orders_BTCUSDT_test.json'
    assert file.exists()
    records = json.loads(file.read_text())
    closed = [r for r in records if r['status'] == 'closed']
    assert len(closed) >= 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_virtual_order_simulator.py -k "virtual_order" -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'bot.virtual_order_simulator'`

- [ ] **Step 3: Create `bot/virtual_order_simulator.py`**

```python
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from bot.fake_order import FakeOrder

if TYPE_CHECKING:
    from bot.analyzer import Analyzer
    from bot.data_feed import DataFeed
    from bot.risk_manager import RiskManager
    from config.settings import Settings

logger = logging.getLogger(__name__)

_MAX_CLOSED = 500


class VirtualOrderSimulator:
    """
    Tracks virtual (paper) positions for all non-best presets.
    Each preset independently signals and manages its own position.
    Uses FakeOrder for TP/SL/trailing logic (same as real orders).
    Persists to data/virtual_orders_{symbol}_{mode}.json.
    """

    def __init__(
        self,
        mode: str,
        all_presets: dict,
        project_root: Path,
        risk_manager: 'RiskManager',
    ) -> None:
        self._mode = mode
        self._all_presets = all_presets
        self._project_root = project_root
        self._risk_manager = risk_manager
        # symbol -> {preset_name: order_record_dict}
        self._open: dict[str, dict[str, dict]] = {}
        # symbol -> {preset_name: FakeOrder}
        self._fake_orders: dict[str, dict[str, FakeOrder]] = {}

    # ------------------------------------------------------------------ #
    # Candle close — open new virtual orders for non-best presets          #
    # ------------------------------------------------------------------ #

    async def on_candle_close(
        self,
        symbol: str,
        analyzer: 'Analyzer',
        best_preset_name: Optional[str],
        base_settings: 'Settings',
    ) -> None:
        open_for_symbol = self._open.setdefault(symbol, {})
        fake_for_symbol = self._fake_orders.setdefault(symbol, {})
        import dataclasses
        from bot.recommendation_engine import RecommendationEngine

        for preset_name, overrides in self._all_presets.items():
            if preset_name == best_preset_name:
                continue  # handled as real order
            if preset_name in open_for_symbol:
                continue  # already open

            try:
                preset_settings = dataclasses.replace(base_settings, **overrides)
                engine = RecommendationEngine(preset_settings)
                rec = engine.generate(analyzer.get_trend(), analyzer.get_current_price())
            except Exception as exc:
                logger.debug(f"[{symbol}][{preset_name}] Recommendation error: {exc}")
                continue

            if rec is None:
                continue

            entry = rec.getEntryPrice()
            tp = rec.getTarget()
            sl = rec.getStop() or 0.0
            if entry <= 0 or tp <= 0:
                continue

            allocation = self._risk_manager.get_allocation(symbol)
            leverage = self._risk_manager.get_leverage(symbol)
            quantity = (allocation * leverage / entry) if entry > 0 else 0.0
            if quantity <= 0:
                continue

            side = rec.getSide()
            partial_pct = float(getattr(preset_settings, 'partial_take_pct', 0.0))
            trail_pct = float(getattr(preset_settings, 'trailing_stop_pct', 0.0))

            record = {
                'preset_name': preset_name,
                'side': side,
                'entry_price': entry,
                'tp': tp,
                'sl': sl,
                'quantity': quantity,
                'leverage': leverage,
                'open_time': datetime.now(timezone.utc).isoformat(),
                'status': 'open',
                'close_price': None,
                'close_time': None,
                'pnl_usdt': None,
                'result': None,
            }
            open_for_symbol[preset_name] = record
            fake_for_symbol[preset_name] = FakeOrder(
                side=side,
                entry_price=entry,
                tp=tp,
                sl=sl if sl else entry * (0.99 if side == 'BUY' else 1.01),
                level=rec.getLevel(),
                signal_type=rec.getType().value,
                candle_index=0,
                partial_take_pct=partial_pct,
                trailing_stop_pct=trail_pct,
            )
            logger.debug(f"[{symbol}] Virtual order opened: {preset_name} {side} @ {entry}")

    # ------------------------------------------------------------------ #
    # Price update — check TP/SL for all open virtual orders              #
    # ------------------------------------------------------------------ #

    async def check_prices(self, symbol: str, price: float) -> list[dict]:
        closed = []
        open_for_symbol = self._open.get(symbol, {})
        fake_for_symbol = self._fake_orders.get(symbol, {})

        for preset_name in list(open_for_symbol.keys()):
            fake = fake_for_symbol.get(preset_name)
            if fake is None:
                open_for_symbol.pop(preset_name, None)
                continue

            result = fake.check_price(price)
            if result is None:
                continue

            record = open_for_symbol.pop(preset_name)
            fake_for_symbol.pop(preset_name, None)

            close_price = fake.close_price or price
            pnl = self._calc_pnl(record, close_price)
            record.update({
                'status': 'closed',
                'close_price': close_price,
                'close_time': datetime.now(timezone.utc).isoformat(),
                'pnl_usdt': pnl,
                'result': result,
            })
            self._append_closed(symbol, record)
            closed.append({
                'preset_name': preset_name,
                'pnl_usdt': pnl,
                'result': result,
                'entry_price': record['entry_price'],
                'close_price': close_price,
                'side': record['side'],
            })
            logger.debug(f"[{symbol}] Virtual order closed: {preset_name} {result} pnl={pnl:.2f}")

        return closed

    # ------------------------------------------------------------------ #
    # Bot stop / mode switch — close all open virtual orders at market    #
    # ------------------------------------------------------------------ #

    async def close_all_open(self, symbols: list[str], feed: 'DataFeed') -> None:
        for symbol in symbols:
            open_for_symbol = self._open.get(symbol, {})
            if not open_for_symbol:
                continue
            try:
                ticker = await asyncio.to_thread(
                    feed.client.futures_symbol_ticker, symbol=symbol
                )
                close_price = float(ticker.get('price', 0) or 0)
            except Exception as exc:
                logger.warning(f"[{symbol}] Failed to fetch price for virtual close: {exc}")
                close_price = 0.0

            for preset_name, record in list(open_for_symbol.items()):
                if close_price > 0:
                    pnl = self._calc_pnl(record, close_price)
                else:
                    pnl = 0.0
                    close_price = record['entry_price']
                record.update({
                    'status': 'closed',
                    'close_price': close_price,
                    'close_time': datetime.now(timezone.utc).isoformat(),
                    'pnl_usdt': pnl,
                    'result': 'closed_early',
                })
                self._append_closed(symbol, record)

            open_for_symbol.clear()
            self._fake_orders.get(symbol, {}).clear()
            logger.info(f"[{symbol}] All virtual orders closed (bot stop/mode switch)")

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _calc_pnl(self, record: dict, close_price: float) -> float:
        entry = record['entry_price']
        qty = record['quantity']
        if record['side'] == 'BUY':
            return (close_price - entry) * qty
        return (entry - close_price) * qty

    def _path(self, symbol: str) -> Path:
        return self._project_root / 'data' / f'virtual_orders_{symbol}_{self._mode}.json'

    def _append_closed(self, symbol: str, record: dict) -> None:
        path = self._path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                existing = []
        # Keep open orders + append new closed record, trim old closed to _MAX_CLOSED
        open_records = [r for r in existing if r.get('status') == 'open']
        closed_records = [r for r in existing if r.get('status') != 'open']
        closed_records.append(record)
        if len(closed_records) > _MAX_CLOSED:
            closed_records = closed_records[-_MAX_CLOSED:]
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(open_records + closed_records))
        tmp.replace(path)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_virtual_order_simulator.py -k "virtual_order" -v
```

Expected: all 6 virtual_order tests PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all previously passing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add bot/virtual_order_simulator.py tests/test_virtual_order_simulator.py
git commit -m "feat: add VirtualOrderSimulator with FakeOrder-backed TP/SL/trailing tracking"
```

---

## Task 3: Real order recording and opening guard in `OrderExecutor`

**Files:**
- Modify: `bot/order_executor.py`
- Create: `tests/test_real_order_recording.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_real_order_recording.py
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def make_executor(tmp_path):
    from bot.order_executor import OrderExecutor
    settings = MagicMock()
    settings.partial_take_pct = 0.0
    settings.trailing_stop_pct = 0.0
    risk_manager = MagicMock()
    notifier = MagicMock()
    with patch('bot.order_executor.load_risk_config', return_value={'consecutive_failure_threshold': 3}):
        return OrderExecutor(
            'test', settings, risk_manager, notifier,
            project_root=tmp_path,
        )


@pytest.mark.asyncio
async def test_real_order_recorded_on_price_close(tmp_path):
    ex = make_executor(tmp_path)
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    async def fake_submit(*a, **kw):
        return 'id1'

    async def fake_close(symbol, order):
        return 55000.0

    ex._submit_to_exchange = fake_submit
    ex._market_close = fake_close

    await ex.place_order('BTCUSDT', 'my_preset', 'BUY', 50000, 55000, 48000, 0.005, 5)
    closed = await ex.check_symbol_price('BTCUSDT', 55001.0)
    assert len(closed) == 1

    record_file = tmp_path / 'data' / 'real_orders_BTCUSDT_test.json'
    assert record_file.exists()
    records = json.loads(record_file.read_text())
    assert len(records) == 1
    r = records[0]
    assert r['preset_name'] == 'my_preset'
    assert r['side'] == 'BUY'
    assert r['result'] == 'win'
    assert r['entry_price'] == pytest.approx(50000.0)
    assert r['close_price'] == pytest.approx(55000.0)


@pytest.mark.asyncio
async def test_real_order_records_append_across_trades(tmp_path):
    ex = make_executor(tmp_path)
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    async def fake_submit(*a, **kw):
        return 'id'

    close_prices = [55000.0, 47000.0]
    call_count = [0]

    async def fake_close(symbol, order):
        p = close_prices[call_count[0]]
        call_count[0] += 1
        return p

    ex._submit_to_exchange = fake_submit
    ex._market_close = fake_close

    await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
    await ex.check_symbol_price('BTCUSDT', 55001.0)
    await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
    await ex.check_symbol_price('BTCUSDT', 47999.0)

    records = json.loads((tmp_path / 'data' / 'real_orders_BTCUSDT_test.json').read_text())
    assert len(records) == 2
    assert records[0]['result'] == 'win'
    assert records[1]['result'] == 'loss'


@pytest.mark.asyncio
async def test_no_recording_when_project_root_none():
    from bot.order_executor import OrderExecutor
    from unittest.mock import patch, MagicMock
    settings = MagicMock()
    settings.partial_take_pct = 0.0
    settings.trailing_stop_pct = 0.0
    risk_manager = MagicMock()
    notifier = MagicMock()
    with patch('bot.order_executor.load_risk_config', return_value={'consecutive_failure_threshold': 3}):
        ex = OrderExecutor('test', settings, risk_manager, notifier)

    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    async def fake_submit(*a, **kw):
        return 'id'
    async def fake_close(symbol, order):
        return 55000.0

    ex._submit_to_exchange = fake_submit
    ex._market_close = fake_close

    await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
    # Should not raise even though project_root is None
    closed = await ex.check_symbol_price('BTCUSDT', 55001.0)
    assert len(closed) == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_real_order_recording.py -v
```

Expected: FAIL — `OrderExecutor.__init__` doesn't accept `project_root`

- [ ] **Step 3: Add `project_root`, `_record_real_order_close`, and `_last_opened_preset` to `bot/order_executor.py`**

In `OrderExecutor.__init__`, add `project_root: 'Path | None' = None` parameter and initialise fields. Find the `__init__` signature (line 63) and add:

```python
    def __init__(
        self,
        mode: Literal["test", "live"],
        settings: Settings,
        risk_manager: RiskManager,
        notifier: Notifier,
        data_feed: 'DataFeed | None' = None,
        symbol_registry: 'SymbolRegistry | None' = None,
        project_root: 'Path | None' = None,
    ) -> None:
        self._mode = mode
        self._settings = settings
        self._risk_manager = risk_manager
        self._notifier = notifier
        self._feed = data_feed
        self._symbol_registry = symbol_registry
        self._project_root = project_root

        self._states: dict[str, OrderState] = {}
        self._open_orders: dict[str, OpenOrder] = {}
        self._fake_orders: dict[str, FakeOrder] = {}
        self._placing_locks: dict[str, asyncio.Lock] = {}
        self._failure_counts: dict[str, int] = {}
        self._lot_cache: dict[str, dict] = {}
        self._bracket_max: dict[str, int] = {}
        self._candle_index: int = 0
        self._last_opened_preset: dict[str, str] = {}

        cfg = load_risk_config()
        self._consecutive_failure_threshold: int = cfg.get("consecutive_failure_threshold", 3)
```

Add the recording helper method before `_submit_to_exchange`:

```python
    def _record_real_order_close(
        self,
        symbol: str,
        order: OpenOrder,
        close_price: float,
        result: str,
        pnl_usdt: float,
    ) -> None:
        if self._project_root is None:
            return
        from datetime import datetime, timezone
        from pathlib import Path
        path: Path = self._project_root / 'data' / f'real_orders_{symbol}_{self._mode}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        records: list = []
        if path.exists():
            try:
                records = json.loads(path.read_text())
            except Exception:
                records = []
        records.append({
            'preset_name': order.preset_name,
            'side': order.side,
            'entry_price': order.entry_price,
            'close_price': close_price,
            'tp': order.tp_price,
            'sl': order.sl_price,
            'quantity': order.quantity,
            'leverage': order.leverage,
            'open_time': getattr(order, 'open_time', None),
            'close_time': datetime.now(timezone.utc).isoformat(),
            'pnl_usdt': pnl_usdt,
            'result': result,
        })
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(records))
        tmp.replace(path)
```

Update `place_order` to track `open_time` on the order. After `self._states[symbol] = OrderState.OPEN`, add:

```python
                from datetime import datetime, timezone
                self._open_orders[symbol].open_time = datetime.now(timezone.utc).isoformat()
                self._last_opened_preset[symbol] = preset_name
```

Add `open_time: str | None = None` field to `OpenOrder` dataclass (after `exchange_order_id`):

```python
@dataclass
class OpenOrder:
    symbol: str
    preset_name: str
    side: str
    entry_price: float
    tp_price: float
    sl_price: float
    quantity: float
    leverage: int
    partial_take_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    exchange_order_id: str | None = None
    open_time: str | None = None
```

Call `_record_real_order_close` in `check_symbol_price` just before the `return` statement (after the `logger.info` call on line ~276):

```python
        self._record_real_order_close(symbol, open_order, actual_close_price, result, pnl)
        return [{
            "symbol": symbol,
            ...
        }]
```

Call it in `check_all_orders` too, inside the per-symbol loop after `self._states[symbol] = OrderState.IDLE`:

```python
            self._record_real_order_close(symbol, open_order, actual_close_price, result, pnl)
```

Also add a `json` import at the top of `bot/order_executor.py` if not already present:

```python
import json
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_real_order_recording.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add bot/order_executor.py tests/test_real_order_recording.py
git commit -m "feat: add real order recording and _last_opened_preset tracking to OrderExecutor"
```

---

## Task 4: Wire `VirtualOrderSimulator` into `main.py`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add imports to `main.py`**

After the existing imports, add:

```python
from bot.virtual_order_simulator import VirtualOrderSimulator
```

- [ ] **Step 2: Construct `VirtualOrderSimulator` after `virtual_tracker` is created**

In `run()`, after the line `virtual_tracker = VirtualTracker(...)` (around line 155):

```python
    all_presets = {**LOCKED_PRESETS, **PRESETS}
    virtual_order_simulator = VirtualOrderSimulator(
        mode=current_mode,
        all_presets=all_presets,
        project_root=_PROJECT_ROOT,
        risk_manager=risk_manager,
    )
```

- [ ] **Step 3: Pass `project_root` to `OrderExecutor`**

In the `OrderExecutor(...)` constructor call (around line 143), add `project_root=_PROJECT_ROOT`:

```python
    order_executor = OrderExecutor(
        mode=current_mode,
        settings=first_settings,
        risk_manager=risk_manager,
        notifier=notifier,
        symbol_registry=symbol_registry,
        project_root=_PROJECT_ROOT,
    )
```

- [ ] **Step 4: Update `on_candle_close` to call `VirtualOrderSimulator`**

After the `_try_place_order` block in `on_candle_close`, add:

```python
        await virtual_order_simulator.on_candle_close(
            symbol=symbol,
            analyzer=analyzer,
            best_preset_name=virtual_tracker.best_preset(symbol),
            base_settings=settings,
        )
```

- [ ] **Step 5: Update `on_price_update` to check virtual orders**

After the `for c in closed:` loop in `on_price_update`, add:

```python
        virtual_closed = await virtual_order_simulator.check_prices(symbol, price)
        for vc in virtual_closed:
            virtual_tracker.record_closed_trade(symbol, vc['preset_name'], vc['pnl_usdt'])
```

- [ ] **Step 6: Add virtual close to `on_stop_bot`**

In `on_stop_bot`, before `sys.exit(0)`:

```python
    async def on_stop_bot() -> None:
        current_symbols = symbol_registry.get_symbols()
        await virtual_order_simulator.close_all_open(current_symbols, feed)
        await order_executor.close_all_orders_at_market()
        notifier.notify("info", "Bot stopped", "Clean shutdown via dashboard", "main")
        sys.exit(0)
```

- [ ] **Step 7: Add virtual close to `on_switch_mode`**

In `on_switch_mode`, before `await order_executor.close_all_orders_at_market()`:

```python
        await virtual_order_simulator.close_all_open(current_symbols, feed)
        await order_executor.close_all_orders_at_market()
```

And after mode switch completes, recreate the simulator for the new mode:

```python
        virtual_order_simulator = VirtualOrderSimulator(
            mode=target_mode,
            all_presets=all_presets,
            project_root=_PROJECT_ROOT,
            risk_manager=risk_manager,
        )
```

Note: `virtual_order_simulator` must be declared `nonlocal` at the top of `on_switch_mode`:

```python
    async def on_switch_mode(target_mode: str) -> None:
        nonlocal virtual_tracker, virtual_order_simulator
        ...
```

- [ ] **Step 8: Add opening guard to `_try_place_order`**

In `_try_place_order`, before `await order_executor.place_order(...)`:

```python
        # If best preset changed, verify no open position on exchange before placing
        if order_executor._last_opened_preset.get(symbol) != preset_name:
            await order_executor.check_symbols_on_exchange([symbol])
            if order_executor.get_state(symbol) != OrderState.IDLE:
                return
```

- [ ] **Step 9: Verify syntax**

```bash
python -c "import main; print('main.py syntax OK')"
```

Expected: `main.py syntax OK`

- [ ] **Step 10: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests PASS

- [ ] **Step 11: Commit**

```bash
git add main.py
git commit -m "feat: wire VirtualOrderSimulator into main.py — candle close, price update, stop, mode switch"
```

---

## Task 5: API route `GET /api/trades`

**Files:**
- Create: `dashboard/app/api/trades/route.ts`

- [ ] **Step 1: Create the route file**

```typescript
// dashboard/app/api/trades/route.ts
import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../_utils'

function readJson(filePath: string, fallback: unknown) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return fallback
  }
}

function currentMode(): string {
  const modePath = path.join(BOT_ROOT, 'data', 'bot_mode.json')
  const data = readJson(modePath, {})
  return (data as Record<string, string>).mode ?? 'test'
}

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const symbol = searchParams.get('symbol')?.toUpperCase()
  if (!symbol) {
    return NextResponse.json({ error: 'symbol required' }, { status: 400 })
  }

  const mode = searchParams.get('mode') ?? currentMode()

  const realOrdersPath = path.join(BOT_ROOT, 'data', `real_orders_${symbol}_${mode}.json`)
  const efficiencyPath = path.join(BOT_ROOT, 'data', `preset_efficiency_${mode}.json`)
  const virtualOrdersPath = path.join(BOT_ROOT, 'data', `virtual_orders_${symbol}_${mode}.json`)

  const realOrders = readJson(realOrdersPath, []) as unknown[]
  const efficiency = readJson(efficiencyPath, {}) as Record<string, Record<string, { total_winning_usdt: number; trade_count: number }>>
  const virtualOrders = readJson(virtualOrdersPath, []) as unknown[]

  const symbolEfficiency = efficiency[symbol] ?? {}

  // Determine best preset: max total_winning_usdt with >= 4 trades
  let bestPreset: string | null = null
  let bestWinning = -Infinity
  for (const [name, stats] of Object.entries(symbolEfficiency)) {
    if (stats.trade_count >= 4 && stats.total_winning_usdt > bestWinning) {
      bestWinning = stats.total_winning_usdt
      bestPreset = name
    }
  }

  return NextResponse.json({
    symbol,
    mode,
    best_preset: bestPreset,
    real_orders: realOrders,
    virtual_summary: symbolEfficiency,
    virtual_orders: virtualOrders,
  })
}
```

- [ ] **Step 2: Test the route**

With the dev server running (`cd dashboard && npm run dev`):

```bash
curl "http://localhost:3000/api/trades?symbol=BTCUSDT"
```

Expected: JSON with `{ symbol, mode, best_preset, real_orders, virtual_summary, virtual_orders }` (arrays may be empty if no trades yet — that's fine)

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/api/trades/route.ts
git commit -m "feat: add GET /api/trades route"
```

---

## Task 6: TypeScript types and NavBar Trades link

**Files:**
- Modify: `dashboard/lib/types.ts`
- Modify: `dashboard/components/NavBar.tsx`

- [ ] **Step 1: Add types to `dashboard/lib/types.ts`**

Append to the end of `dashboard/lib/types.ts`:

```typescript
// ── Trades page types ──────────────────────────────────────────────────────

export interface RealOrder {
  preset_name: string;
  side: 'BUY' | 'SELL';
  entry_price: number;
  close_price: number;
  tp: number;
  sl: number;
  quantity: number;
  leverage: number;
  open_time: string | null;
  close_time: string;
  pnl_usdt: number;
  result: 'win' | 'loss' | 'partial' | 'trail' | 'closed_early';
}

export interface VirtualOrder {
  preset_name: string;
  side: 'BUY' | 'SELL';
  entry_price: number;
  tp: number;
  sl: number;
  quantity: number;
  leverage: number;
  open_time: string;
  status: 'open' | 'closed';
  close_price: number | null;
  close_time: string | null;
  pnl_usdt: number | null;
  result: 'win' | 'loss' | 'partial' | 'trail' | 'closed_early' | null;
}

export interface VirtualSummaryEntry {
  total_winning_usdt: number;
  trade_count: number;
}

export interface TradesData {
  symbol: string;
  mode: string;
  best_preset: string | null;
  real_orders: RealOrder[];
  virtual_summary: Record<string, VirtualSummaryEntry>;
  virtual_orders: VirtualOrder[];
}
```

- [ ] **Step 2: Add Trades link to `dashboard/components/NavBar.tsx`**

Find `NAV_LINKS` (line 9) and add the Trades entry:

```typescript
const NAV_LINKS = [
  { href: '/',         label: 'Strategy' },
  { href: '/backtest', label: 'Backtest' },
  { href: '/trades',   label: 'Trades'   },
  { href: '/create',   label: 'Create'   },
  { href: '/risk',     label: 'Risk'     },
  { href: '/settings', label: 'Settings' },
]
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add dashboard/lib/types.ts dashboard/components/NavBar.tsx
git commit -m "feat: add Trades types and NavBar link"
```

---

## Task 7: `/trades` page — preset efficiency table and real orders table

**Files:**
- Create: `dashboard/app/trades/page.tsx`

- [ ] **Step 1: Create the page**

```typescript
// dashboard/app/trades/page.tsx
'use client'

import { useState, useEffect } from 'react'
import { useSymbolContext } from '@/lib/SymbolContext'
import type { TradesData, RealOrder, VirtualSummaryEntry } from '@/lib/types'
import CollapsibleSection from '@/components/CollapsibleSection'

function formatPct(v: number): string {
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

function winRate(trades: RealOrder[]): string {
  if (trades.length === 0) return '—'
  const wins = trades.filter(t => t.result === 'win' || t.result === 'partial' || t.result === 'trail').length
  return ((wins / trades.length) * 100).toFixed(1) + '%'
}

function totalPnl(trades: RealOrder[]): number {
  return trades.reduce((s, t) => s + (t.pnl_usdt ?? 0), 0)
}

interface PresetRow {
  name: string
  type: 'Real' | 'Virtual'
  isBest: boolean
  tradeCount: number
  totalWinningUsdt: number
  winRateDisplay: string
  totalPnlDisplay: string
  totalPnlRaw: number
}

function buildPresetRows(data: TradesData): PresetRow[] {
  const rows: PresetRow[] = []

  // Best preset: stats from real_orders
  if (data.best_preset) {
    const realTrades = data.real_orders.filter(o => o.preset_name === data.best_preset)
    rows.push({
      name: data.best_preset,
      type: 'Real',
      isBest: true,
      tradeCount: realTrades.length,
      totalWinningUsdt: realTrades.filter(t => t.pnl_usdt > 0).reduce((s, t) => s + t.pnl_usdt, 0),
      winRateDisplay: winRate(realTrades),
      totalPnlDisplay: realTrades.length > 0 ? (totalPnl(realTrades) >= 0 ? '+' : '') + totalPnl(realTrades).toFixed(2) + ' USDT' : '—',
      totalPnlRaw: totalPnl(realTrades),
    })
  }

  // Other presets: stats from virtual_summary
  for (const [name, stats] of Object.entries(data.virtual_summary)) {
    if (name === data.best_preset) continue
    rows.push({
      name,
      type: 'Virtual',
      isBest: false,
      tradeCount: stats.trade_count,
      totalWinningUsdt: stats.total_winning_usdt,
      winRateDisplay: '—',
      totalPnlDisplay: '—',
      totalPnlRaw: stats.total_winning_usdt,
    })
  }

  rows.sort((a, b) => {
    if (a.isBest) return -1
    if (b.isBest) return 1
    return b.totalPnlRaw - a.totalPnlRaw
  })

  return rows
}

function resultColor(result: string): string {
  if (result === 'win' || result === 'partial' || result === 'trail') return 'text-green-400'
  if (result === 'loss') return 'text-red-400'
  return 'text-gray-400'
}

export default function TradesPage() {
  const { symbol } = useSymbolContext()
  const [data, setData] = useState<TradesData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!symbol) return
    setData(null)
    setError(null)
    fetch(`/api/trades?symbol=${symbol}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(setData)
      .catch(e => setError(String(e)))
  }, [symbol])

  if (error) return <div className="pt-16 p-4 text-red-400">{error}</div>
  if (!data) return <div className="pt-16 p-4 text-gray-400">Loading…</div>

  const presetRows = buildPresetRows(data)
  const realOrders = [...data.real_orders].reverse()

  return (
    <div className="pt-14 p-4 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold text-white">{symbol} — Trades</h1>
        <span className="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400">{data.mode}</span>
        {data.best_preset && (
          <span className="text-xs px-2 py-0.5 rounded bg-indigo-900 text-indigo-300">
            Best: {data.best_preset}
          </span>
        )}
      </div>

      {/* Preset Efficiency Table */}
      <CollapsibleSection title={`Preset Efficiency (${presetRows.length} presets)`} defaultOpen>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                <th className="py-2 pr-4">Preset</th>
                <th className="py-2 pr-4">Type</th>
                <th className="py-2 pr-4 text-right">Trades</th>
                <th className="py-2 pr-4 text-right">Win Rate</th>
                <th className="py-2 pr-4 text-right">Total PnL</th>
                <th className="py-2 text-right">Winning USDT</th>
              </tr>
            </thead>
            <tbody>
              {presetRows.map(row => (
                <tr
                  key={row.name}
                  className={`border-b border-gray-800 ${row.isBest ? 'bg-indigo-950' : ''}`}
                >
                  <td className="py-1.5 pr-4 font-mono text-xs text-white">
                    {row.name}
                    {row.isBest && <span className="ml-2 text-[10px] text-indigo-400">BEST</span>}
                  </td>
                  <td className={`py-1.5 pr-4 text-xs ${row.type === 'Real' ? 'text-green-400' : 'text-gray-400'}`}>
                    {row.type}
                  </td>
                  <td className="py-1.5 pr-4 text-right text-gray-300">{row.tradeCount || '—'}</td>
                  <td className="py-1.5 pr-4 text-right text-gray-300">{row.winRateDisplay}</td>
                  <td className={`py-1.5 pr-4 text-right ${row.totalPnlRaw >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {row.totalPnlDisplay}
                  </td>
                  <td className={`py-1.5 text-right ${row.totalWinningUsdt > 0 ? 'text-green-400' : 'text-gray-500'}`}>
                    {row.totalWinningUsdt > 0 ? '+' + row.totalWinningUsdt.toFixed(2) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CollapsibleSection>

      {/* Real Orders Table */}
      <CollapsibleSection title={`Real Orders (${data.real_orders.length})`} defaultOpen={data.real_orders.length > 0}>
        {realOrders.length === 0 ? (
          <p className="text-gray-500 text-sm py-4">No real orders recorded yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead>
                <tr className="text-gray-400 border-b border-gray-700">
                  <th className="py-2 pr-3">Preset</th>
                  <th className="py-2 pr-3">Side</th>
                  <th className="py-2 pr-3 text-right">Entry</th>
                  <th className="py-2 pr-3 text-right">Close</th>
                  <th className="py-2 pr-3 text-right">PnL USDT</th>
                  <th className="py-2 pr-3">Result</th>
                  <th className="py-2 text-right">Closed At</th>
                </tr>
              </thead>
              <tbody>
                {realOrders.map((order, i) => (
                  <tr key={i} className="border-b border-gray-800">
                    <td className="py-1.5 pr-3 font-mono text-xs text-white">{order.preset_name}</td>
                    <td className={`py-1.5 pr-3 ${order.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
                      {order.side}
                    </td>
                    <td className="py-1.5 pr-3 text-right text-gray-300">{order.entry_price.toFixed(2)}</td>
                    <td className="py-1.5 pr-3 text-right text-gray-300">{order.close_price.toFixed(2)}</td>
                    <td className={`py-1.5 pr-3 text-right font-medium ${order.pnl_usdt >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {(order.pnl_usdt >= 0 ? '+' : '') + order.pnl_usdt.toFixed(2)}
                    </td>
                    <td className={`py-1.5 pr-3 capitalize ${resultColor(order.result)}`}>
                      {order.result}
                    </td>
                    <td className="py-1.5 text-right text-gray-500 text-xs">
                      {new Date(order.close_time).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CollapsibleSection>
    </div>
  )
}
```

- [ ] **Step 2: Verify page compiles**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors

- [ ] **Step 3: Open in browser**

Start dashboard (`npm run dev`) and navigate to `http://localhost:3000/trades`. Verify:
- Page loads without error
- "Preset Efficiency" section renders (may have empty data if no trades yet)
- "Real Orders" section shows empty-state message correctly
- NavBar shows "Trades" link highlighted

- [ ] **Step 4: Commit**

```bash
git add dashboard/app/trades/page.tsx
git commit -m "feat: add /trades page with preset efficiency table and real orders list"
```

---

## Task 8: `/trades` page — close-price chart with trade overlays

**Files:**
- Create: `dashboard/components/TradesChart.tsx`
- Modify: `dashboard/app/trades/page.tsx`

- [ ] **Step 1: Create `TradesChart.tsx`**

```typescript
// dashboard/components/TradesChart.tsx
'use client'

import { useEffect, useRef } from 'react'
import {
  Chart,
  LineController,
  ScatterController,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Tooltip,
  Legend,
} from 'chart.js'
import 'chartjs-adapter-date-fns'
import type { RealOrder } from '@/lib/types'

Chart.register(LineController, ScatterController, LineElement, PointElement, LinearScale, TimeScale, Tooltip, Legend)

interface Kline {
  time: number
  close: number
}

interface Props {
  klines: Kline[]
  realOrders: RealOrder[]
}

export default function TradesChart({ klines, realOrders }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef = useRef<Chart | null>(null)

  useEffect(() => {
    if (!canvasRef.current || klines.length === 0) return

    chartRef.current?.destroy()

    const priceData = klines.map(k => ({ x: k.time * 1000, y: k.close }))

    // Build entry/exit scatter points for each real order
    const entryPoints: { x: number; y: number; side: string }[] = []
    const exitPoints: { x: number; y: number; result: string }[] = []

    for (const order of realOrders) {
      if (order.open_time) {
        const openMs = new Date(order.open_time).getTime()
        entryPoints.push({ x: openMs, y: order.entry_price, side: order.side })
      }
      const closeMs = new Date(order.close_time).getTime()
      exitPoints.push({ x: closeMs, y: order.close_price, result: order.result })
    }

    chartRef.current = new Chart(canvasRef.current, {
      type: 'line',
      data: {
        datasets: [
          {
            label: 'Close',
            type: 'line',
            data: priceData,
            borderColor: 'rgb(99,102,241)',
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0,
            order: 1,
          },
          {
            label: 'Entry',
            type: 'scatter',
            data: entryPoints.map(p => ({ x: p.x, y: p.y })),
            backgroundColor: entryPoints.map(p => p.side === 'BUY' ? 'rgb(74,222,128)' : 'rgb(248,113,113)'),
            pointRadius: 6,
            pointStyle: entryPoints.map(p => p.side === 'BUY' ? 'triangle' : 'triangleDown') as unknown as string,
            order: 0,
          },
          {
            label: 'Exit',
            type: 'scatter',
            data: exitPoints.map(p => ({ x: p.x, y: p.y })),
            backgroundColor: exitPoints.map(p =>
              ['win', 'partial', 'trail'].includes(p.result) ? 'rgb(74,222,128)' : 'rgb(248,113,113)'
            ),
            pointRadius: 5,
            pointStyle: 'cross',
            order: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: true, labels: { color: '#9ca3af', boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: ctx => `${ctx.dataset.label}: ${Number(ctx.parsed.y).toFixed(2)}`,
            },
          },
        },
        scales: {
          x: {
            type: 'time',
            time: { unit: 'hour' },
            ticks: { color: '#6b7280', maxTicksLimit: 8 },
            grid: { color: 'rgba(255,255,255,0.05)' },
          },
          y: {
            ticks: { color: '#6b7280' },
            grid: { color: 'rgba(255,255,255,0.05)' },
          },
        },
      },
    })

    return () => { chartRef.current?.destroy() }
  }, [klines, realOrders])

  return (
    <div className="relative w-full" style={{ height: 320 }}>
      <canvas ref={canvasRef} />
    </div>
  )
}
```

- [ ] **Step 2: Add chart to the trades page**

In `dashboard/app/trades/page.tsx`, add the import at the top:

```typescript
import TradesChart from '@/components/TradesChart'
```

Add a state variable and fetch for klines:

```typescript
  const [klines, setKlines] = useState<Array<{ time: number; close: number }>>([])

  useEffect(() => {
    if (!symbol) return
    fetch(`/results_${symbol}.json?t=${Date.now()}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.klines) setKlines(d.klines)
      })
      .catch(() => {})
  }, [symbol])
```

Add the chart section between the efficiency table and real orders table sections:

```typescript
      {/* Chart */}
      {klines.length > 0 && (
        <CollapsibleSection title="Price Chart + Trade Markers" defaultOpen>
          <TradesChart klines={klines} realOrders={data.real_orders} />
        </CollapsibleSection>
      )}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors

- [ ] **Step 4: Open in browser and verify chart renders**

Navigate to `http://localhost:3000/trades`. Verify:
- Chart shows close-price line (purple line over time)
- If real orders exist: entry triangles (green=BUY, red=SELL) and exit crosses appear
- Chart does not crash when `real_orders` is empty

- [ ] **Step 5: Run full test suite**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/components/TradesChart.tsx dashboard/app/trades/page.tsx
git commit -m "feat: add TradesChart with close-price line and real trade entry/exit markers"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| `seed_from_backtest` conditional + dict iteration fix | Task 1 |
| `Analyzer.get_recommendation_for_preset` | Task 1 |
| `VirtualOrderSimulator` open/close/persist | Task 2 |
| TP/SL/trailing checks via `FakeOrder` | Task 2 |
| Virtual close on bot stop/mode switch | Tasks 2 + 4 |
| File: `virtual_orders_{symbol}_{mode}.json` | Task 2 |
| Max 500 closed orders | Task 2 (`_MAX_CLOSED`) |
| Real order recording | Task 3 |
| File: `real_orders_{symbol}_{mode}.json` | Task 3 |
| Opening guard (API verify when best preset changed) | Tasks 3 + 4 |
| Wire candle close + price update + stop + mode switch | Task 4 |
| `VirtualTracker.record_closed_trade` called for virtual closes | Task 4 |
| `GET /api/trades` route | Task 5 |
| TypeScript types `RealOrder`, `VirtualSummaryEntry`, `TradesData` | Task 6 |
| NavBar Trades link | Task 6 |
| Preset efficiency table | Task 7 |
| Real orders table | Task 7 |
| Close-price chart with trade overlays | Task 8 |

All requirements covered. No placeholders. Method signatures consistent across all tasks.
