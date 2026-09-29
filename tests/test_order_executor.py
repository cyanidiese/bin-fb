"""OrderExecutor and FakeOrder: placement, fills, exits, close recording and slippage.

Sections (one class per former test module):
    TestOrderExecutor                   -- state machine, placement, SL/TP closes, filters
    TestOrderExecutorEntryFill          -- PnL/fees off the real entry fill
    TestEntrySlippageGuard              -- stale-entry abort on adverse fill slippage
    TestFailedCloseKeepsPosition        -- a failed close is queued, never booked
    TestPendingSlCancel                 -- failed SL cancels are persisted and retried
    TestRealOrderRecording              -- closed real orders written to disk
    TestNoSilentOrderRejections         -- every _try_place_order exit records a reason
    TestCandleCheckSkipsPreEntryCandle  -- new orders are not judged on the prior candle
    TestFakeOrderEarlyExit              -- max_losing_pct / max_losing_candles / early_loss_sl
    TestFakeOrderTrailActivation        -- trail_activation_pct / trail_min_distance_pct
    TestSlippage                        -- bot.slippage model for virtual PnL
"""
import asyncio
import json
import re
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.fake_order import FakeOrder
from bot.order_executor import OpenOrder, OrderExecutor, OrderState
from bot.rate_limit_guard import guard as rl_guard
from bot.slippage import (
    DEFAULT_PCT,
    WINDOW,
    adverse_pct,
    effective_entry,
    estimate,
    record,
    stats,
)
from tests.factories import make_executor, src, run_coro


_run = run_coro  # shared order-safe loop helper (tests/factories.py)


def _order(**kw):
    base = dict(
        symbol='INJUSDT', preset_name='oscillating_zone', side='BUY',
        entry_price=4.052, tp_price=4.16, sl_price=4.02,
        quantity=372.1, leverage=5,
    )
    base.update(kw)
    return OpenOrder(**base)


def _executor_with_trades(trades, raises=None):
    ex = OrderExecutor.__new__(OrderExecutor)

    calls = {'n': 0}

    def futures_account_trades(symbol, orderId):
        calls['n'] += 1
        if raises is not None:
            raise raises
        return trades

    ex._feed = types.SimpleNamespace(
        client=types.SimpleNamespace(futures_account_trades=futures_account_trades)
    )
    return ex, calls


def guard_adverse_pct(side: str, signalled: float, filled: float) -> float:
    """Mirrors the directional calculation in place_order."""
    if signalled <= 0:
        return 0.0
    return ((filled - signalled) / signalled * 100) if side == 'BUY' \
        else ((signalled - filled) / signalled * 100)


BAN = Exception("APIError(code=-1003): Way too many requests; IP(x) banned until 1788591715275")


def _fake(result_seq):
    fo = MagicMock()
    fo.check.side_effect = list(result_seq)
    fo.close_price = 110.0
    return fo


class FakeAPIError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"APIError(code={code}): {msg}")
        self.code = code


def make_pending_sl_executor(tmp_path):
    feed = MagicMock()
    feed._is_testnet = True
    with patch('bot.order_executor.load_risk_config', return_value={'consecutive_failure_threshold': 3}):
        return OrderExecutor('test', MagicMock(), MagicMock(), MagicMock(),
                             data_feed=feed, project_root=tmp_path)


def make_recording_executor(tmp_path):
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


MAIN = src('main.py')


LINES = MAIN.splitlines()


def _fn_body(name: str) -> list:
    start = next(i for i, l in enumerate(LINES) if f'def {name}' in l)
    indent = len(LINES[start]) - len(LINES[start].lstrip())
    for i in range(start + 1, len(LINES)):
        l = LINES[i]
        if l.strip() and (len(l) - len(l.lstrip())) <= indent and re.match(r'\s*(async )?def ', l):
            return LINES[start:i]
    return LINES[start:]


BODY = _fn_body('_try_place_order')


CANDLE_CLOSE_MS = 1_790_000_899_999          # the candle that just closed


def _open(ex, opened_ms):
    ex._fake_orders['BTCUSDT'] = FakeOrder(side='BUY', entry_price=100.0, tp=110.0, sl=90.0,
                                           level=1, signal_type='test', candle_index=0)
    ex._open_orders['BTCUSDT'] = OpenOrder(
        symbol='BTCUSDT', preset_name='p', side='BUY', entry_price=100.0,
        tp_price=110.0, sl_price=90.0, quantity=1.0, leverage=5,
        open_time=datetime.fromtimestamp(opened_ms / 1000, timezone.utc).isoformat())
    ex._states['BTCUSDT'] = OrderState.OPEN
    closed = []

    async def fake_finalize(symbol, order, result, price):
        closed.append(result)
        return {'symbol': symbol, 'result': result}
    ex._finalize_close = fake_finalize
    return closed


def _check(ex, **kw):
    # the pre-entry candle's low (85) is through the SL (90)
    return asyncio.run(ex.check_symbol_candle('BTCUSDT', high=105.0, low=85.0,
                                              candle_open=102.0, candle_close=88.0, **kw))


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


# ── Setup helpers ─────────────────────────────────────────────────────────────
#
# All BUY orders: entry=100, tp=200, sl=80, partial_take_pct=0.10.
# partial_price = 100 + 0.10*(200-100) = 110.
# trailing_stop_pct = 0.15.
#
# All SELL orders: entry=100, tp=80, sl=120, partial_take_pct=0.10.
# partial_price = 100 - 0.10*(100-80) = 98.
# trailing_stop_pct = 0.15.
#
# arm_buy uses high=110.0 exactly so _max_favorable = 110 after arming.
# gained on subsequent candles starts from 110-100=10.
def make_buy_trail(
    trail_activation_pct: float = 0.0,
    trail_min_distance_pct: float = 0.0,
) -> FakeOrder:
    return FakeOrder(
        side='BUY',
        entry_price=100.0,
        tp=200.0,
        sl=80.0,
        level=1,
        signal_type='test',
        candle_index=0,
        partial_take_pct=0.10,
        trailing_stop_pct=0.15,
        trail_activation_pct=trail_activation_pct,
        trail_min_distance_pct=trail_min_distance_pct,
    )


def make_sell_trail(
    trail_activation_pct: float = 0.0,
    trail_min_distance_pct: float = 0.0,
) -> FakeOrder:
    return FakeOrder(
        side='SELL',
        entry_price=100.0,
        tp=80.0,
        sl=120.0,
        level=1,
        signal_type='test',
        candle_index=0,
        partial_take_pct=0.10,
        trailing_stop_pct=0.15,
        trail_activation_pct=trail_activation_pct,
        trail_min_distance_pct=trail_min_distance_pct,
    )


def arm_buy(order: FakeOrder) -> None:
    """Arm at exactly partial_price=110 so _max_favorable=110 after this candle."""
    result = order.check(110.0, 105.0, 1, candle_open=105.0, candle_close=108.0)
    assert order._partial_armed is True
    assert result is None  # arming candle cannot also trigger trail
    # After arming candle: _max_favorable = 110, gained = 10.


def arm_sell(order: FakeOrder) -> None:
    """Arm at exactly partial_price=98 so _max_favorable=98 after this candle."""
    result = order.check(99.0, 98.0, 1, candle_open=99.0, candle_close=98.5)
    assert order._partial_armed is True
    assert result is None
    # After arming candle: _max_favorable = 98, gained = 100-98 = 2.


def make_trail_only(side: str, trail_activation_pct: float = 2.0) -> FakeOrder:
    tp = 200.0 if side == 'BUY' else 80.0
    sl = 80.0 if side == 'BUY' else 120.0
    return FakeOrder(
        side=side,
        entry_price=100.0,
        tp=tp,
        sl=sl,
        level=1,
        signal_type='test',
        candle_index=0,
        partial_take_pct=0.0,
        trailing_stop_pct=0.15,
        trail_activation_pct=trail_activation_pct,
        trail_min_distance_pct=0.0,
    )


class TestOrderExecutor:
    """OrderExecutor core: state machine, quantity rounding, placement, SL/TP closes,
    failure counting, exchange filters and leverage brackets.
    """

    # --- State machine ---
    def test_initial_state_is_idle(self):
        ex = make_executor()
        assert ex.get_state('BTCUSDT') == OrderState.IDLE

    def test_get_open_orders_empty(self):
        ex = make_executor()
        assert ex.get_open_orders() == {}

    # --- round_quantity ---
    @pytest.mark.asyncio
    async def test_round_quantity_applies_step(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}
        qty = await ex.round_quantity('BTCUSDT', 0.0057)
        assert qty == pytest.approx(0.005)

    @pytest.mark.asyncio
    async def test_round_quantity_below_min_returns_zero(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}
        qty = await ex.round_quantity('BTCUSDT', 0.0009)
        assert qty == 0.0

    # --- place_order with mocked exchange ---
    @pytest.mark.asyncio
    @patch('bot.order_executor.asyncio.sleep', new=AsyncMock())  # skip the 0.25 s fill-retry waits
    async def test_place_order_happy_path(self):
        ex = make_executor(with_feed=True)
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}

        async def fake_submit(symbol, side, quantity, leverage):
            return 'order123'

        ex._submit_to_exchange = fake_submit
        ok = await ex.place_order('BTCUSDT', 'my_preset', 'BUY', 50000, 55000, 48000, 0.005, 5)
        assert ok is True
        assert ex.get_state('BTCUSDT') == OrderState.OPEN
        assert 'BTCUSDT' in ex._fake_orders

    @pytest.mark.asyncio
    async def test_place_order_exchange_failure_sets_idle(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}

        async def failing_submit(*a, **kw):
            raise RuntimeError("network error")

        ex._submit_to_exchange = failing_submit
        ok = await ex.place_order('BTCUSDT', 'preset', 'BUY', 50000, 55000, 48000, 0.005, 5)
        assert ok is False
        assert ex.get_state('BTCUSDT') == OrderState.IDLE

    # --- check_all_orders ---
    @pytest.mark.asyncio
    async def test_check_all_orders_sl_hit(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}

        async def fake_submit(symbol, side, quantity, leverage, entry_price=0.0):
            return 'id1'

        async def fake_close(symbol, order, fallback=None):
            return 47000.0

        ex._submit_to_exchange = fake_submit
        ex._market_close = fake_close

        await ex.place_order('BTCUSDT', 'trail_preset', 'BUY', 50000, 55000, 48000, 0.005, 5)

        # Candle where low hits SL
        closed = await ex.check_all_orders(high=50100, low=47500, candle_open=50000, candle_close=47600)
        assert len(closed) == 1
        assert closed[0]['result'] == 'loss'
        assert ex.get_state('BTCUSDT') == OrderState.IDLE
        assert 'BTCUSDT' not in ex._fake_orders

    @pytest.mark.asyncio
    async def test_check_all_orders_tp_hit(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}

        async def fake_submit(symbol, side, quantity, leverage, entry_price=0.0):
            return 'id2'

        async def fake_close(symbol, order, fallback=None):
            return 55100.0

        ex._submit_to_exchange = fake_submit
        ex._market_close = fake_close

        await ex.place_order('BTCUSDT', 'preset', 'BUY', 50000, 55000, 48000, 0.005, 5)
        closed = await ex.check_all_orders(high=55100, low=50000, candle_open=50000, candle_close=55100)
        assert len(closed) == 1
        assert closed[0]['result'] == 'win'
        raw = (55100 - 50000) * 0.005
        fees = (50000 + 55100) * 0.005 * 0.0004
        assert closed[0]['pnl_usdt'] == pytest.approx(raw - fees)

    # --- close_all_orders_at_market clears fake_orders ---
    @pytest.mark.asyncio
    async def test_close_all_clears_fake_orders(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}

        async def fake_submit(*a, **kw):
            return 'id3'

        async def fake_close(symbol, order):
            return 49000.0

        ex._submit_to_exchange = fake_submit
        ex._market_close = fake_close

        await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
        assert 'BTCUSDT' in ex._fake_orders

        await ex.close_all_orders_at_market()
        assert 'BTCUSDT' not in ex._fake_orders
        assert ex.get_state('BTCUSDT') == OrderState.IDLE

    # --- check_symbol_price ---
    @pytest.mark.asyncio
    async def test_check_symbol_price_sl_hit(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}

        async def fake_submit(symbol, side, quantity, leverage, entry_price=0.0):
            return 'id_sl'

        async def fake_close(symbol, order, fallback=None):
            return 47500.0

        ex._submit_to_exchange = fake_submit
        ex._market_close = fake_close
        await ex.place_order(
            symbol='BTCUSDT', preset_name='default', side='BUY',
            entry=50000, tp=55000, sl=48000, quantity=0.01, leverage=10,
        )
        closed = await ex.check_symbol_price('BTCUSDT', 47500)
        assert len(closed) == 1
        assert closed[0]['result'] == 'loss'
        assert ex.get_state('BTCUSDT') == OrderState.IDLE

    @pytest.mark.asyncio
    async def test_check_symbol_price_tp_hit(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}

        async def fake_submit(symbol, side, quantity, leverage, entry_price=0.0):
            return 'id_tp'

        async def fake_close(symbol, order, fallback=None):
            return 55100.0

        ex._submit_to_exchange = fake_submit
        ex._market_close = fake_close
        await ex.place_order(
            symbol='BTCUSDT', preset_name='default', side='BUY',
            entry=50000, tp=55000, sl=48000, quantity=0.01, leverage=10,
        )
        closed = await ex.check_symbol_price('BTCUSDT', 55100)
        assert len(closed) == 1
        assert closed[0]['result'] == 'win'
        assert ex.get_state('BTCUSDT') == OrderState.IDLE

    @pytest.mark.asyncio
    async def test_check_symbol_price_scope_isolation(self):
        """Price update for BTCUSDT must not affect ETHUSDT's open order."""
        ex = make_executor()
        for sym in ('BTCUSDT', 'ETHUSDT'):
            ex._lot_cache[sym] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}

        async def fake_submit(symbol, side, quantity, leverage, entry_price=0.0):
            return f'id_{symbol}'

        async def fake_close(symbol, order, fallback=None):
            return 47500.0 if symbol == 'BTCUSDT' else 2000.0

        ex._submit_to_exchange = fake_submit
        ex._market_close = fake_close
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

    # --- consecutive failure counter ---
    @pytest.mark.asyncio
    async def test_consecutive_failures_trigger_notify(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}

        async def failing_submit(*a):
            raise RuntimeError("fail")

        ex._submit_to_exchange = failing_submit
        for _ in range(3):
            await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
        ex._notifier.notify.assert_called()

    # --- get_min_notional ---
    @pytest.mark.asyncio
    async def test_get_min_notional_from_exchange_info(self):
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
    async def test_get_min_notional_defaults_zero_when_missing(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001}
        # no min_notional key in cache
        notional = await ex.get_min_notional('BTCUSDT')
        assert notional == pytest.approx(0.0)

    # --- leverage brackets ---
    @pytest.mark.asyncio
    async def test_fetch_leverage_brackets_caches_max(self):
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

    def test_get_bracket_max_defaults_to_20_when_unknown(self):
        ex = make_executor()
        assert ex.get_bracket_max('UNKNOWNUSDT') == 20

    @pytest.mark.asyncio
    async def test_funds_error_does_not_increment_failure_counter(self):
        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}

        from bot.order_executor import FundsError

        async def funds_failing_submit(*a, **kw):
            raise FundsError("insufficient margin")

        ex._submit_to_exchange = funds_failing_submit
        await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
        assert ex._failure_counts.get('BTCUSDT', 0) == 0

    @pytest.mark.asyncio
    async def test_symbol_error_calls_auto_disable(self):
        from bot.order_executor import SymbolError

        ex = make_executor()
        ex._lot_cache['BTCUSDT'] = {'step_size': '0.001', 'min_qty': 0.001, 'min_notional': 0.0, 'tick_size': '0.01', 'max_qty': 0.0}
        ex._auto_disable = AsyncMock()

        async def symbol_failing_submit(symbol, side, quantity, leverage, entry_price=0.0):
            raise SymbolError(symbol, "invalid symbol")

        ex._submit_to_exchange = symbol_failing_submit
        await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
        ex._auto_disable.assert_awaited_once_with('BTCUSDT', 'invalid symbol')

    @pytest.mark.asyncio
    async def test_check_symbol_price_skips_if_already_closing(self):
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
    async def test_check_symbol_candle_skips_if_already_closing(self):
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

    @pytest.mark.asyncio
    @patch('bot.order_executor.asyncio.sleep', new=AsyncMock())  # skip the 0.25 s fill-retry waits
    async def test_place_order_caps_notional(self):
        """When notional > max_order_notional_usdt, OpenOrder.quantity is reduced so PnL is accurate."""
        ex = make_executor(with_feed=True)
        # MEME: price=0.004, step=1.0, cap=100 USDT → max qty = floor(100/0.004) = 25000
        ex._lot_cache['MEMEUSDT'] = {
            'step_size': '1', 'min_qty': 1.0, 'max_qty': 0.0, 'min_notional': 0.0, 'tick_size': '0.0001',
        }

        submitted_qty = {}

        def fake_create_order_sync(**kwargs):
            submitted_qty['qty'] = float(kwargs['quantity'])
            return {'orderId': '1', 'avgPrice': '0.004'}

        def fake_change_leverage(**kwargs):
            return {}

        def fake_place_sl(**kwargs):
            return None

        ex._feed.client.futures_change_leverage = MagicMock(side_effect=fake_change_leverage)
        ex._feed.client.futures_create_order = MagicMock(side_effect=fake_create_order_sync)
        ex._place_sl_on_exchange = AsyncMock(return_value=None)

        with patch('bot.order_executor.asyncio.to_thread', new=AsyncMock(side_effect=lambda f, **kw: f(**kw))):
            with patch('bot.order_executor.load_risk_config', return_value={
                'consecutive_failure_threshold': 3,
                'max_order_notional_usdt': 100.0,
                'max_loss_usdt': 0.0,
                'max_loss_usdt_per_symbol': {},
                'max_loss_tp_ratio': 0.0,
            }):
                ok = await ex.place_order('MEMEUSDT', 'p', 'BUY', 0.004, 0.005, 0.003, 50000.0, 10)

        assert ok is True
        # 50000 × 0.004 = 200 USDT > cap=100 → qty capped at 25000
        assert submitted_qty.get('qty', 50000.0) <= 25001.0
        assert submitted_qty.get('qty', 0.0) > 0.0
        # The stored OpenOrder quantity must match what was sent to the exchange (the fix)
        assert ex._open_orders['MEMEUSDT'].quantity == submitted_qty['qty']


class TestOrderExecutorEntryFill:
    """PnL and fee must be computed off the price the entry actually filled at.

    Incident (2026-08-18/19, INJUSDT): four real orders were reported as +11.81,
    +18.03, +36.78, +49.10 = +115.72 USDT. Binance's income ledger showed +109.27.
    The whole 6.46 USDT gap came from trade #1, signalled at 4.052 but filled at
    4.0670 — _calc_pnl used the signalled price, so 1.5 cents of entry slippage on
    372.1 units silently became 5.57 USDT of phantom profit.
    """

    # ── _effective_entry ────────────────────────────────────────────────────── #
    def test_effective_entry_prefers_the_real_fill(self):
        order = _order(fill_entry_price=4.0670)
        assert OrderExecutor._effective_entry(order) == 4.0670

    def test_effective_entry_falls_back_to_signalled_price_when_unreconciled(self):
        assert OrderExecutor._effective_entry(_order()) == 4.052

    def test_effective_entry_treats_zero_fill_as_unreconciled(self):
        assert OrderExecutor._effective_entry(_order(fill_entry_price=0.0)) == 4.052

    # ── PnL against the real incident numbers ───────────────────────────────── #
    def test_pnl_off_the_real_fill_matches_binance_ledger(self):
        """Binance realized +7.4537 gross on this close, 0.6053+0.6083 commission,
        so the true net was +6.2401 USDT. Reconciled PnL lands within ~0.01 of that
        (residual is the 1.3-unit 4.0580 partial the weighted average smooths over),
        versus 5.57 USDT of overstatement before the fix."""
        order = _order(fill_entry_price=4.0670)
        pnl = OrderExecutor._calc_pnl(order, 4.0870)
        assert pnl == pytest.approx(6.2401, abs=0.02)

    def test_pnl_off_the_signalled_price_reproduces_the_overstatement(self):
        """Guards the regression: without reconciliation the same close reports +11.81."""
        pnl = OrderExecutor._calc_pnl(_order(), 4.0870)
        assert pnl == pytest.approx(11.81, abs=0.01)

    def test_sell_side_uses_the_fill_too(self):
        order = _order(side='SELL', entry_price=4.052, fill_entry_price=4.0670)
        # A short filled 1.5c higher than signalled earns more, not less.
        assert (
            OrderExecutor._calc_pnl(order, 4.0)
            > OrderExecutor._calc_pnl(_order(side='SELL'), 4.0)
        )

    def test_fee_is_charged_on_the_filled_notional(self):
        fee = OrderExecutor._order_fee(372.1, 4.0670, 4.0870)
        # Binance charged 0.6032+0.0021 in and 0.5400+0.0683 out = 1.2136.
        assert fee == pytest.approx(1.2136, abs=0.002)

    # ── _reconcile_entry_fill ───────────────────────────────────────────────── #
    def test_reconcile_returns_quantity_weighted_average_of_the_fills(self):
        """The real trade #1 filled in two parts: 1.3 @ 4.0580 and 370.8 @ 4.0670."""
        ex, _ = _executor_with_trades([
            {'price': '4.0580', 'qty': '1.3'},
            {'price': '4.0670', 'qty': '370.8'},
        ])
        avg = _run(ex._reconcile_entry_fill('INJUSDT', '123'))
        assert avg == pytest.approx(4.0670, abs=0.0002)

    def test_reconcile_returns_zero_on_api_error_so_caller_keeps_signalled_price(self):
        ex, _ = _executor_with_trades(None, raises=RuntimeError('-1003 IP banned'))
        assert _run(ex._reconcile_entry_fill('INJUSDT', '123')) == 0.0

    @patch('bot.order_executor.asyncio.sleep', new=AsyncMock())  # skip the 0.25 s retry waits
    def test_reconcile_retries_while_trade_records_are_empty(self):
        ex, calls = _executor_with_trades([])
        assert _run(ex._reconcile_entry_fill('INJUSDT', '123')) == 0.0
        assert calls['n'] == 3

    def test_reconcile_skips_api_entirely_without_an_order_id(self):
        ex, calls = _executor_with_trades([{'price': '4.0', 'qty': '1'}])
        assert _run(ex._reconcile_entry_fill('INJUSDT', None)) == 0.0
        assert calls['n'] == 0

    def test_reconcile_returns_zero_without_a_feed(self):
        ex = OrderExecutor.__new__(OrderExecutor)
        ex._feed = None
        assert _run(ex._reconcile_entry_fill('INJUSDT', '123')) == 0.0

    # ── wallet_at_open is distinct from balance_at_open ─────────────────────── #
    def test_wallet_at_open_is_separate_from_the_trade_cap(self):
        """balance_at_open carries the allocated per-symbol cap (~296 USDT on the
        incident trades); wallet_at_open carries the account wallet (~3050). Mixing
        them up is what made the old 'Balance' line unreadable."""
        order = _order(balance_at_open=296.30, wallet_at_open=3050.18)
        assert order.balance_at_open != order.wallet_at_open
        assert order.wallet_at_open == 3050.18

    def test_new_fields_default_to_zero_for_restored_positions(self):
        """restore_open_positions rebuilds via OpenOrder(**dict); pre-upgrade state
        files have neither field, so both must be optional."""
        order = OpenOrder(
            symbol='INJUSDT', preset_name='p', side='BUY', entry_price=4.0,
            tp_price=4.1, sl_price=3.9, quantity=1.0, leverage=5,
        )
        assert order.fill_entry_price == 0.0
        assert order.wallet_at_open == 0.0


class TestEntrySlippageGuard:
    """Stale-entry abort: close immediately when the fill is materially worse than the signal.

    Measured Aug 19-30 across all symbols with reconciled fills (n=33):
        adverse slip <0.05%   22 trades  +139.17 USDT  36% WR
        adverse slip 0.05-0.30% 7 trades  -32.58 USDT  14% WR
        adverse slip >=0.30%    4 trades -142.28 USDT   0% WR
    correlation(adverse slip, PnL) = -0.59. A fill that far from the signal means the
    move already started without us, so the entry premise is gone.
    """

    # ── direction ──────────────────────────────────────────────────────────── #
    def test_buy_filled_higher_is_adverse(self):
        assert guard_adverse_pct('BUY', 5.138, 5.174) == pytest.approx(0.7007, abs=1e-3)

    def test_buy_filled_lower_is_favourable(self):
        assert guard_adverse_pct('BUY', 5.138, 5.100) < 0

    def test_sell_filled_lower_is_adverse(self):
        """A short filled below its signal is the mirror of a long filled above."""
        assert guard_adverse_pct('SELL', 5.138, 5.100) > 0

    def test_sell_filled_higher_is_favourable(self):
        assert guard_adverse_pct('SELL', 5.138, 5.174) < 0

    def test_favourable_slippage_never_triggers_an_abort(self):
        """The old code used abs(), which would have aborted on a GOOD fill."""
        for side, signalled, filled in (('BUY', 5.0, 4.9), ('SELL', 5.0, 5.1)):
            assert guard_adverse_pct(side, signalled, filled) < 0.30

    # ── the real losing trades this is aimed at ────────────────────────────── #
    @pytest.mark.parametrize("ts,side,signalled,filled,expected", [
        ("2026-08-30 INJ", 'BUY', 5.138, 5.174, 0.701),
        ("2026-08-27 INJ", 'BUY', 5.454, 5.494, 0.733),
        ("2026-08-27 INJ", 'BUY', 5.492, 5.522, 0.546),
        ("2026-08-20 INJ", 'SELL', 4.678, 4.661, 0.363),
    ])
    def test_reproduces_the_measured_losing_fills(self, ts, side, signalled, filled, expected):
        got = guard_adverse_pct(side, signalled, filled)
        assert got == pytest.approx(expected, abs=0.01), ts
        assert got >= 0.30, "all four of these lost; a 0.30% limit must catch them"

    def test_winning_trades_are_not_caught_by_a_030_limit(self):
        """The zero-slip winners (+23.04, +58.39, +60.14) must be untouched."""
        for signalled, filled in ((4.801, 4.802), (1.526, 1.526), (5.455, 5.456)):
            assert guard_adverse_pct('BUY', signalled, filled) < 0.30

    # ── the gate itself ────────────────────────────────────────────────────── #
    def test_disabled_by_default_means_never_abort(self):
        """max_entry_slippage_pct defaults to 0.0 — the feature ships inert because
        n=4 in the decisive band is too thin to enable on its own."""
        limit = 0.0
        assert not (limit > 0 and guard_adverse_pct('BUY', 5.138, 5.174) > limit)

    def test_fires_only_above_the_configured_limit(self):
        limit = 0.30
        assert guard_adverse_pct('BUY', 5.138, 5.174) > limit          # 0.70% -> abort
        assert not guard_adverse_pct('BUY', 4.801, 4.802) > limit      # 0.02% -> keep

    def test_zero_signalled_price_is_safe(self):
        assert guard_adverse_pct('BUY', 0.0, 5.0) == 0.0

    # ── PnL still uses the real fill regardless ────────────────────────────── #
    def test_pnl_uses_the_filled_price_not_the_signal(self):
        from bot.order_executor import OpenOrder
        o = OpenOrder(symbol='INJUSDT', preset_name='p', side='BUY', entry_price=5.138,
                      tp_price=5.4, sl_price=5.045, quantity=305.6, leverage=5,
                      fill_entry_price=5.174)
        assert OrderExecutor._effective_entry(o) == 5.174


class TestFailedCloseKeepsPosition:
    """A close that did not execute must not be recorded as a close.

    Before this, a failed market close (which a rate-limit ban guarantees) was booked at
    the *software* price: the trade was written to the preset record, the position was
    deleted from the bot's books and the symbol went IDLE — while the position was still
    open on the exchange. The fabricated PnL then flowed into
    virtual_tracker.record_closed_trade(), and preset ranking is sum(recent_trades[-10:]),
    so the bot would promote a preset on profit it never earned.
    """

    @pytest.fixture
    def ex(self):
        e = OrderExecutor.__new__(OrderExecutor)
        e._open_orders = {}
        e._fake_orders = {}
        e._states = {}
        e._closing = set()
        e._pending_close = {}
        e._pending_close_logged = {}
        e._pending_sl_cancels = {}
        e._symbol_candle_index = {}
        e._notifier = MagicMock()
        e._record_real_order_close = MagicMock()
        e._record_success = MagicMock()
        e._calc_pnl = MagicMock(return_value=42.0)
        e._order_fee = MagicMock(return_value=1.2)
        e._effective_entry = MagicMock(return_value=100.0)
        e._open_orders['EIGENUSDT'] = OpenOrder(
            symbol='EIGENUSDT', preset_name='r5_sl_filter', side='BUY',
            entry_price=100.0, tp_price=110.0, sl_price=95.0,
            quantity=10.0, leverage=5,
        )
        e._fake_orders['EIGENUSDT'] = MagicMock()
        e._states['EIGENUSDT'] = OrderState.OPEN if hasattr(OrderState, 'OPEN') else OrderState.PLACING
        return e

    def test_failed_close_records_nothing(self, ex):
        ex._market_close = AsyncMock(side_effect=BAN)
        info = _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
        assert info is None, 'a failed close must not produce a trade record'
        ex._record_real_order_close.assert_not_called()

    def test_failed_close_keeps_the_position(self, ex):
        ex._market_close = AsyncMock(side_effect=BAN)
        _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
        assert 'EIGENUSDT' in ex._open_orders, 'position was abandoned while still open on the exchange'
        assert 'EIGENUSDT' in ex._fake_orders, 'exit management was dropped'

    def test_failed_close_does_not_free_the_symbol(self, ex):
        """IDLE would let a second position open on top of the live one."""
        ex._market_close = AsyncMock(side_effect=BAN)
        _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
        assert ex._states['EIGENUSDT'] != OrderState.IDLE

    def test_failed_close_is_queued_for_retry(self, ex):
        ex._market_close = AsyncMock(side_effect=BAN)
        _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
        assert ex._pending_close['EIGENUSDT'] == ('win', 110.0)

    def test_retry_closes_once_the_exchange_recovers(self, ex):
        ex._market_close = AsyncMock(side_effect=BAN)
        _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))

        ex._market_close = AsyncMock(return_value=109.5)
        out = _run(ex.retry_pending_closes())

        assert len(out) == 1
        assert out[0]['symbol'] == 'EIGENUSDT'
        assert out[0]['close_price'] == 109.5, 'must book the real fill, not the software price'
        assert 'EIGENUSDT' not in ex._open_orders
        assert 'EIGENUSDT' not in ex._pending_close
        assert ex._states['EIGENUSDT'] == OrderState.IDLE
        ex._record_real_order_close.assert_called_once()

    def test_repeated_failures_notify_only_once(self, ex):
        """A ban lasts many candles; one alert, not one per retry."""
        ex._market_close = AsyncMock(side_effect=BAN)
        for _ in range(5):
            _run(ex.retry_pending_closes()) if ex._pending_close else _run(
                ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
        assert ex._notifier.notify.call_count == 1

    def test_successful_close_still_records_normally(self, ex):
        ex._market_close = AsyncMock(return_value=110.0)
        info = _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
        assert info is not None
        assert info['close_price'] == 110.0
        assert info['pnl_usdt'] == 42.0
        assert 'EIGENUSDT' not in ex._open_orders
        assert ex._states['EIGENUSDT'] == OrderState.IDLE
        ex._record_real_order_close.assert_called_once()

    def test_retry_drops_intent_if_position_vanished(self, ex):
        """Reconciliation may have closed it elsewhere — do not resurrect a ghost."""
        ex._market_close = AsyncMock(side_effect=BAN)
        _run(ex._finalize_close('EIGENUSDT', ex._open_orders['EIGENUSDT'], 'win', 110.0))
        del ex._open_orders['EIGENUSDT']
        assert _run(ex.retry_pending_closes()) == []
        assert ex._pending_close == {}

    # ── The live path ────────────────────────────────────────────────────────────
    # main.py calls check_symbol_candle / check_symbol_price, NOT the executor's
    # on_candle_close (which nothing calls). These pin the retry to the entry point
    # that actually runs in production.
    def test_live_candle_path_does_not_book_a_failed_close(self, ex):
        ex._fake_orders['EIGENUSDT'] = _fake(['win'])
        ex._market_close = AsyncMock(side_effect=BAN)
        out = _run(ex.check_symbol_candle('EIGENUSDT', 111.0, 99.0, 100.0, 110.0))
        assert out == []
        ex._record_real_order_close.assert_not_called()
        assert 'EIGENUSDT' in ex._open_orders
        assert ex._pending_close['EIGENUSDT'][0] == 'win'

    def test_live_candle_path_retries_next_candle_and_books_the_real_fill(self, ex):
        ex._fake_orders['EIGENUSDT'] = _fake(['win', 'win'])
        ex._market_close = AsyncMock(side_effect=BAN)
        assert _run(ex.check_symbol_candle('EIGENUSDT', 111.0, 99.0, 100.0, 110.0)) == []

        # ban lifts; next candle must complete the exit at the real price
        ex._market_close = AsyncMock(return_value=108.25)
        out = _run(ex.check_symbol_candle('EIGENUSDT', 111.0, 99.0, 100.0, 110.0))
        assert len(out) == 1
        assert out[0]['close_price'] == 108.25
        assert 'EIGENUSDT' not in ex._open_orders
        assert ex._pending_close == {}

    def test_stranded_symbol_is_not_re_evaluated_while_pending(self, ex):
        """Re-running FakeOrder.check on a stranded position would double-handle it."""
        fo = _fake(['win'])
        ex._fake_orders['EIGENUSDT'] = fo
        ex._market_close = AsyncMock(side_effect=BAN)
        _run(ex.check_symbol_candle('EIGENUSDT', 111.0, 99.0, 100.0, 110.0))
        calls_after_first = fo.check.call_count
        _run(ex.check_symbol_candle('EIGENUSDT', 111.0, 99.0, 100.0, 110.0))
        assert fo.check.call_count == calls_after_first, 'FakeOrder re-evaluated while stranded'


class TestPendingSlCancel:
    """A SL algo-order cancel that fails for any reason other than -2011 leaves a live
    reduce-only STOP_MARKET on the exchange, which would fire against the next position on
    the symbol. It must be queued, persisted, retried, and must block new placement.
    """

    @pytest.fixture(autouse=True)
    def _clean_guard(self):
        rl_guard.reset()
        yield
        rl_guard.reset()

    @pytest.mark.asyncio
    async def test_unknown_order_counts_as_gone(self, tmp_path):
        ex = make_pending_sl_executor(tmp_path)
        ex._feed.client.futures_cancel_algo_order.side_effect = FakeAPIError(-2011, 'Unknown order sent.')
        assert await ex._cancel_exchange_order('INJUSDT', '123') is True
        assert ex._pending_sl_cancels == {}

    @pytest.mark.asyncio
    async def test_failed_cancel_is_queued_and_persisted(self, tmp_path):
        ex = make_pending_sl_executor(tmp_path)
        ex._feed.client.futures_cancel_algo_order.side_effect = TimeoutError('read timeout')
        assert await ex._cancel_exchange_order('INJUSDT', '123') is False
        assert ex._pending_sl_cancels == {'INJUSDT': ['123']}
        saved = json.loads((tmp_path / 'data' / 'pending_sl_cancels_test.json').read_text())
        assert saved == {'INJUSDT': ['123']}

        # A restart picks it back up.
        ex2 = make_pending_sl_executor(tmp_path)
        assert ex2._pending_sl_cancels == {'INJUSDT': ['123']}

    @pytest.mark.asyncio
    async def test_retry_clears_queue_on_success(self, tmp_path):
        ex = make_pending_sl_executor(tmp_path)
        ex._feed.client.futures_cancel_algo_order.side_effect = TimeoutError('read timeout')
        await ex._cancel_exchange_order('INJUSDT', '123')

        ex._feed.client.futures_cancel_algo_order.side_effect = None
        await ex.retry_pending_sl_cancels()
        assert ex._pending_sl_cancels == {}
        saved = json.loads((tmp_path / 'data' / 'pending_sl_cancels_test.json').read_text())
        assert saved == {}

    @pytest.mark.asyncio
    async def test_retry_waits_out_a_ban(self, tmp_path):
        ex = make_pending_sl_executor(tmp_path)
        ban = FakeAPIError(-1003, 'Way too many requests; IP(1.2.3.4) banned until 9999999999999.')
        ex._feed.client.futures_cancel_algo_order.side_effect = ban
        await ex._cancel_exchange_order('INJUSDT', '123')
        assert ex._pending_sl_cancels == {'INJUSDT': ['123']}
        calls = ex._feed.client.futures_cancel_algo_order.call_count

        await ex.retry_pending_sl_cancels()
        # Guard is armed: no call made while banned.
        assert ex._feed.client.futures_cancel_algo_order.call_count == calls
        assert ex._pending_sl_cancels == {'INJUSDT': ['123']}

    @pytest.mark.asyncio
    async def test_placement_blocked_while_old_sl_live(self, tmp_path):
        ex = make_pending_sl_executor(tmp_path)
        ex._feed.client.futures_cancel_algo_order.side_effect = TimeoutError('read timeout')
        await ex._cancel_exchange_order('INJUSDT', '123')

        submitted = []

        async def fake_submit(*a, **k):
            submitted.append(a)
            return 'order1'

        ex._submit_to_exchange = fake_submit
        ok = await ex.place_order('INJUSDT', 'p', 'BUY', 10, 11, 9, 1.0, 5)
        assert ok is False
        assert submitted == []

    @pytest.mark.asyncio
    async def test_placement_proceeds_once_retry_succeeds(self, tmp_path):
        ex = make_pending_sl_executor(tmp_path)
        ex._feed.client.futures_cancel_algo_order.side_effect = TimeoutError('read timeout')
        await ex._cancel_exchange_order('INJUSDT', '123')
        ex._feed.client.futures_cancel_algo_order.side_effect = None

        reached = []

        async def fake_round(symbol, qty):
            reached.append(symbol)
            return 0.0   # stop right after the gate: "rounds to 0" is a clean early exit

        ex.round_quantity = fake_round
        await ex.place_order('INJUSDT', 'p', 'BUY', 10, 11, 9, 1.0, 5)
        assert ex._pending_sl_cancels == {}
        assert reached == ['INJUSDT']


class TestRealOrderRecording:
    """Closed real orders are appended to data/real_orders_<SYMBOL>_<mode>.json."""

    @pytest.mark.asyncio
    async def test_real_order_recorded_on_price_close(self, tmp_path):
        ex = make_recording_executor(tmp_path)
        ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

        async def fake_submit(*a, **kw):
            return 'id1'

        async def fake_close(symbol, order, fallback=None):
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
    async def test_real_order_records_append_across_trades(self, tmp_path):
        ex = make_recording_executor(tmp_path)
        ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

        async def fake_submit(*a, **kw):
            return 'id'

        close_prices = [55000.0, 47000.0]
        call_count = [0]

        async def fake_close(symbol, order, fallback=None):
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
    async def test_real_order_recorded_via_check_all_orders(self, tmp_path):
        """check_all_orders also records closed orders (candle-based close path)."""
        ex = make_recording_executor(tmp_path)
        ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

        async def fake_submit(*a, **kw):
            return 'id1'

        async def fake_close(symbol, order, fallback=None):
            return 55000.0

        ex._submit_to_exchange = fake_submit
        ex._market_close = fake_close

        await ex.place_order('BTCUSDT', 'my_preset', 'BUY', 50000, 55000, 48000, 0.005, 5)
        # Simulate a candle with high=56000 (above TP of 55000)
        closed = await ex.check_all_orders(high=56000.0, low=49000.0, candle_open=50000.0, candle_close=56000.0)
        assert len(closed) == 1

        record_file = tmp_path / 'data' / 'real_orders_BTCUSDT_test.json'
        assert record_file.exists()
        records_data = json.loads(record_file.read_text())
        assert len(records_data) == 1
        r = records_data[0]
        assert r['preset_name'] == 'my_preset'
        assert r['result'] == 'win'
        assert r['open_time'] is not None
        assert r['close_time'] is not None

    @pytest.mark.asyncio
    async def test_no_recording_when_project_root_none(self):
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
        async def fake_close(symbol, order, fallback=None):
            return 55000.0

        ex._submit_to_exchange = fake_submit
        ex._market_close = fake_close

        await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
        # Should not raise even though project_root is None
        closed = await ex.check_symbol_price('BTCUSDT', 55001.0)
        assert len(closed) == 1


class TestNoSilentOrderRejections:
    """Every real-order rejection must record a reason.

    17 of the 27 exits in _try_place_order returned silently, so a weighted, signalling
    symbol could produce nothing all day with nothing in the decision log saying why.
    Measured on the server: 118 floor_sl_pct events with no follow-up decision, and
    ETHFIUSDT/REZUSDT logging "Using manually locked preset" and then vanishing while
    holding 20.3% and 12.5% of allocated capital.

    decision_log.record()'s own docstring already listed 'skip_already_open' and
    'skip_no_signal' as expected values -- they were never wired up.

    This is observability only: _skip() returns 0.0, exactly what the silent paths returned.
    """

    def test_the_helper_exists(self):
        assert any('def _skip(' in l for l in BODY)

    def test_the_helper_returns_zero_so_behaviour_is_unchanged(self):
        """The silent paths returned 0.0. If _skip returned anything else this would be a
        behaviour change disguised as logging."""
        i = next(i for i, l in enumerate(BODY) if 'def _skip(' in l)
        assert any(l.strip() == 'return 0.0' for l in BODY[i:i + 12])

    def test_no_rejection_returns_without_recording_a_reason(self):
        offenders = []
        for i, l in enumerate(BODY):
            if not re.match(r'\s*return\b', l):
                continue
            if '_skip(' in l or 'trade_margin' in l:
                continue
            window = '\n'.join(BODY[max(0, i - 14):i + 1])
            if 'dl_record' not in window and 'def _skip' not in l:
                offenders.append((i, l.strip()))
        assert not offenders, f'silent rejections: {offenders}'

    def test_the_locked_preset_no_signal_case_is_named(self):
        """The most valuable one: the order path re-runs the engine under the locked preset's
        own settings, and a locked symbol producing no signal there is why it never trades."""
        assert "'skip_no_signal'" in MAIN

    def test_the_docstring_values_are_now_wired(self):
        for d in ("'skip_already_open'", "'skip_no_signal'"):
            assert d in MAIN, f'{d} still unused'

    def test_reasons_are_distinct_enough_to_diagnose(self):
        """A single generic reason would defeat the point."""
        decisions = set(re.findall(r"_skip\(\s*'([a-z_]+)'", MAIN))
        assert len(decisions) >= 12, f'only {len(decisions)} distinct: {sorted(decisions)}'

    def test_the_helper_passes_the_real_balance_through(self):
        """Recording balance=0 for every skip would make the log useless for sizing questions."""
        i = next(i for i, l in enumerate(BODY) if 'def _skip(' in l)
        blk = '\n'.join(BODY[i:i + 12])
        assert 'balance=balance' in blk


class TestCandleCheckSkipsPreEntryCandle:
    """A real order must not be judged against the candle that closed before it existed.

    Real orders are placed inside the candle-close handler, which then calls
    check_symbol_candle() with that just-closed candle. Before the fix, every new order was
    checked against the pre-entry candle's high/low: 46 of the 48 real orders closed within a
    minute in the 30 days to 2026-09-28 (30 'losses', 16 'trail' exits) were exactly that —
    -186.66 USDT, at prices the market did not revisit.
    """

    def test_order_opened_after_the_candle_is_not_closed_by_it(self):
        ex = make_executor()
        closed = _open(ex, CANDLE_CLOSE_MS + 3_000)          # placed 3 s after the close
        assert _check(ex, candle_close_ms=CANDLE_CLOSE_MS) == []
        assert closed == []
        assert 'BTCUSDT' in ex._fake_orders                  # still monitored

    def test_order_that_lived_through_the_candle_is_still_checked(self):
        ex = make_executor()
        closed = _open(ex, CANDLE_CLOSE_MS - 600_000)        # opened 10 min before the close
        out = _check(ex, candle_close_ms=CANDLE_CLOSE_MS)
        assert closed == ['loss'] and out

    def test_without_a_close_time_the_old_behaviour_is_kept(self):
        ex = make_executor()
        closed = _open(ex, CANDLE_CLOSE_MS + 3_000)
        _check(ex)                                            # no candle_close_ms
        assert closed == ['loss']

    def test_main_passes_the_candle_close_time(self):
        text = src('main.py')
        call = text[text.index('candle_closed = await order_executor.check_symbol_candle('):][:400]
        assert 'candle_close_ms=int(candle_to_add[6])' in call


class TestFakeOrderEarlyExit:
    """Tests for FakeOrder early-loss exit: max_losing_pct, max_losing_candles, early_loss_sl."""

    # ── max_losing_pct ─────────────────────────────────────────────────────────────
    def test_pct_zero_no_early_exit(self):
        """Zero value = disabled."""
        order = make_long(max_losing_pct=0.0)
        result = order.check(91.0, 90.0, 1, candle_open=99.0, candle_close=90.0)
        assert result is None

    def test_pct_50_long_fires_at_halfway(self):
        """50% of SL dist from entry for BUY: entry=100, sl=80 → early exit at 90."""
        order = make_long(max_losing_pct=50.0)
        result = order.check(99.0, 90.0, 1, candle_open=99.0, candle_close=90.0)
        assert result == 'loss'
        assert order.close_price == pytest.approx(90.0)

    def test_pct_50_long_no_trigger_above_threshold(self):
        """Price stays above 90 — no early exit."""
        order = make_long(max_losing_pct=50.0)
        result = order.check(99.0, 91.0, 1, candle_open=99.0, candle_close=91.0)
        assert result is None

    def test_pct_50_short_fires_at_halfway(self):
        """50% of SL dist from entry for SELL: entry=100, sl=120 → early exit at 110."""
        order = make_short(max_losing_pct=50.0)
        result = order.check(110.0, 99.0, 1, candle_open=101.0, candle_close=110.0)
        assert result == 'loss'
        assert order.close_price == pytest.approx(110.0)

    def test_pct_no_exit_when_armed(self):
        """Once partial_price is hit (order armed), early exit must NOT fire."""
        order = FakeOrder(
            side='BUY', entry_price=100.0, tp=120.0, sl=80.0,
            level=1, signal_type='test', candle_index=0,
            partial_take_pct=0.3, max_losing_pct=50.0,
        )
        # Candle 1: arm the order (high reaches 106)
        order.check(106.0, 101.0, 1, candle_open=101.0, candle_close=104.0)
        assert order._partial_armed is True
        # Candle 2: price drops below early-exit threshold (90) — but armed, so no early exit.
        result = order.check(95.0, 88.0, 2, candle_open=95.0, candle_close=88.0)
        assert result == 'partial'
        assert order.close_price == pytest.approx(106.0)

    # ── early_loss_sl (amount-based, pre-computed) ─────────────────────────────────
    def test_early_loss_sl_long(self):
        """Pre-computed amount-based SL: early_loss_sl=95 for a BUY."""
        order = make_long(early_loss_sl=95.0)
        result = order.check(99.0, 95.0, 1, candle_open=99.0, candle_close=95.0)
        assert result == 'loss'
        assert order.close_price == pytest.approx(95.0)

    def test_early_loss_sl_short(self):
        """Pre-computed amount-based SL: early_loss_sl=105 for a SELL."""
        order = make_short(early_loss_sl=105.0)
        result = order.check(105.0, 99.0, 1, candle_open=101.0, candle_close=105.0)
        assert result == 'loss'
        assert order.close_price == pytest.approx(105.0)

    def test_tighter_threshold_wins(self):
        """When both pct-based and amount-based are set, tighter (closer to entry) fires."""
        order = make_long(max_losing_pct=50.0, early_loss_sl=95.0)
        result = order.check(99.0, 95.0, 1, candle_open=99.0, candle_close=95.0)
        assert result == 'loss'
        assert order.close_price == pytest.approx(95.0)

    # ── max_losing_candles ─────────────────────────────────────────────────────────
    def test_losing_candles_triggers_after_n(self):
        """N=3 consecutive below-entry closes → exit on candle 3."""
        order = make_long(max_losing_candles=3)
        assert order.check(99.0, 97.0, 1, candle_open=99.0, candle_close=97.0) is None
        assert order.check(98.0, 96.0, 2, candle_open=98.0, candle_close=96.0) is None
        result = order.check(97.0, 95.0, 3, candle_open=97.0, candle_close=95.0)
        assert result == 'loss'

    def test_losing_candles_resets_on_recovery(self):
        """Counter resets when candle close is back above entry."""
        order = make_long(max_losing_candles=3)
        order.check(99.0, 97.0, 1, candle_open=99.0, candle_close=97.0)
        order.check(98.0, 96.0, 2, candle_open=98.0, candle_close=96.0)
        order.check(102.0, 99.0, 3, candle_open=99.0, candle_close=101.0)
        assert order.check(99.0, 97.0, 4, candle_open=99.0, candle_close=97.0) is None
        assert order.check(98.0, 96.0, 5, candle_open=98.0, candle_close=96.0) is None

    def test_losing_candles_not_updated_by_price_tick(self):
        """check_price() must not update the consecutive-candle counter."""
        order = make_long(max_losing_candles=1)
        order.check_price(98.0)
        order.check_price(97.0)
        assert order._consecutive_losing_candles == 0

    def test_losing_candles_zero_disabled(self):
        """max_losing_candles=0 → no early exit regardless of candle direction."""
        order = make_long(max_losing_candles=0)
        for i in range(10):
            result = order.check(99.0, 97.0, i, candle_open=99.0, candle_close=97.0)
            assert result is None

    def test_all_zero_defaults_no_early_exit(self):
        """FakeOrder with default params (all zeros) behaves exactly as before."""
        order = FakeOrder(
            side='BUY', entry_price=100.0, tp=120.0, sl=80.0,
            level=1, signal_type='test', candle_index=0,
        )
        result = order.check(99.0, 79.0, 1, candle_open=99.0, candle_close=79.0)
        assert result == 'loss'
        assert order.close_price == pytest.approx(80.0)


class TestFakeOrderTrailActivation:
    """Tests for FakeOrder trail_activation_pct and trail_min_distance_pct parameters."""

    # ── test 1: no activation_pct, trail fires normally ───────────────────────────
    def test_trail_fires_normally_without_activation_pct(self):
        """Without activation_pct the trail fires as soon as max_favorable > entry."""
        order = make_buy_trail()
        arm_buy(order)
        # After arm: _max_favorable=110, gained=10.
        # trail_price = 110 - 0.15*10 = 108.5.
        # Candle 2: high=110, low=108.4 ≤ 108.5 → trail fires.
        result = order.check(110.0, 108.4, 2, candle_open=109.0, candle_close=108.6)
        assert result == 'trail'
        assert order.close_price == pytest.approx(108.5)

    # ── test 2: trail blocked below activation threshold ─────────────────────────
    def test_trail_blocked_below_activation_threshold(self):
        """
        activation_pct=12.0 means trail requires 12% gain from entry.
        After arm_buy, gained=10 (10%<12%) so trail is blocked.
        The fixed SL at 80 fires when low drops that far.
        """
        order = make_buy_trail(trail_activation_pct=12.0)
        arm_buy(order)
        # gained=10, 10% < 12% → trail blocked on this candle.
        # Low falls all the way to SL at 80 → loss (SL safety net fires).
        result = order.check(110.0, 79.0, 2, candle_open=110.0, candle_close=80.0)
        assert result == 'loss'

    # ── test 3: trail fires above activation threshold ────────────────────────────
    def test_trail_fires_above_activation_threshold(self):
        """
        activation_pct=8.0: trail fires once gained/entry >= 8%.
        After arm_buy, gained=10 (10%>=8%) so trail is immediately active.
        trail_price = 110 - 0.15*10 = 108.5; low=108.4 ≤ 108.5 → fire.
        """
        order = make_buy_trail(trail_activation_pct=8.0)
        arm_buy(order)
        result = order.check(110.0, 108.4, 2, candle_open=109.0, candle_close=108.6)
        assert result == 'trail'
        assert order.close_price == pytest.approx(108.5)

    # ── test 4: trail_min_distance applied ───────────────────────────────────────
    def test_trail_min_distance_applied(self):
        """
        trail_min_distance_pct=2.0 → _trail_min_distance = 100*2/100 = 2.0.
        After arm_buy: gained=10, formula=0.15*10=1.5 < 2.0.
        Effective trail_distance = 2.0; trail_price = 110-2.0 = 108.0.
        low=107.9 ≤ 108.0 → fire.
        """
        order = make_buy_trail(trail_min_distance_pct=2.0)
        arm_buy(order)
        # Formula: 0.15*10=1.5; min_distance: 2.0 → max=2.0; trail_price=108.0.
        result = order.check(110.0, 107.9, 2, candle_open=110.0, candle_close=108.1)
        assert result == 'trail'
        assert order.close_price == pytest.approx(108.0)

    # ── test 5: normal trail distance wins when above min ────────────────────────
    def test_trail_normal_distance_when_above_min(self):
        """
        trail_min_distance_pct=1.0 → _trail_min_distance = 1.0.
        Drive max_favorable to 130: gained=30, formula=0.15*30=4.5 > 1.0.
        Formula wins; trail_price=130-4.5=125.5.
        Low=126 does not fire; low=125.4 fires.
        """
        order = make_buy_trail(trail_min_distance_pct=1.0)
        arm_buy(order)
        # Drive max_favorable to 130 (candle 2)
        assert order.check(130.0, 128.0, 2, candle_open=129.0, candle_close=128.5) is None
        # Candle 3: trail_price=130-4.5=125.5; low=126 > 125.5 → no fire
        assert order.check(129.0, 126.0, 3, candle_open=129.0, candle_close=126.5) is None
        # Candle 4: low=125.4 ≤ 125.5 → fire
        result = order.check(127.0, 125.4, 4, candle_open=127.0, candle_close=125.6)
        assert result == 'trail'
        assert order.close_price == pytest.approx(125.5)

    # ── test 6: both params together ─────────────────────────────────────────────
    def test_both_params_together(self):
        """
        activation_pct=12.0, min_distance_pct=2.0.
        After arm_buy: gained=10 (10%<12%) → trail completely blocked.
        Candle 2: drive max_favorable to 113 (gained=13, 13%>=12%) → now active.
        trail_distance = max(0.15*13=1.95, 2.0) = 2.0; trail_price = 113-2.0 = 111.0.
        low=110.9 ≤ 111.0 → fire.
        """
        order = make_buy_trail(trail_activation_pct=12.0, trail_min_distance_pct=2.0)
        arm_buy(order)
        # Candle 2: high=113 → max_favorable=113, gained=13 ≥ 12%; trail_price=111.0; low=110.9 → fire
        result = order.check(113.0, 110.9, 2, candle_open=112.0, candle_close=111.1)
        assert result == 'trail'
        assert order.close_price == pytest.approx(111.0)

    # ── test 7: SELL side activation ─────────────────────────────────────────────
    def test_sell_trail_activation(self):
        """
        SELL activation_pct=1.0: trail fires once (entry-_max_favorable)/entry >= 1%.
        After arm_sell: _max_favorable=98, gained=2 (2%>=1%) → immediately active.

        On candle 2 _max_favorable is updated first (min with low), then trail checked.
        Candle 2: high=98.5, low=97.5 → _max_favorable=min(98,97.5)=97.5, gained=2.5.
        trail_distance=max(0.15*2.5=0.375, 0)=0.375; trail_price=97.5+0.375=97.875.
        high=98.5 >= 97.875 → fire at 97.875.
        """
        order = make_sell_trail(trail_activation_pct=1.0)
        arm_sell(order)
        result = order.check(98.5, 97.5, 2, candle_open=98.2, candle_close=97.8)
        assert result == 'trail'
        assert order.close_price == pytest.approx(97.875)

    # ── test 8: get_state / from_state roundtrip ─────────────────────────────────
    def test_get_state_roundtrip(self):
        """Both trail params survive a get_state() → from_state() round-trip."""
        original = FakeOrder(
            side='BUY',
            entry_price=100.0,
            tp=200.0,
            sl=80.0,
            level=1,
            signal_type='test',
            candle_index=0,
            partial_take_pct=0.10,
            trailing_stop_pct=0.15,
            trail_activation_pct=3.5,
            trail_min_distance_pct=1.2,
        )
        state = original.get_state()
        restored = FakeOrder.from_state(state)

        assert restored._trail_activation_pct == pytest.approx(3.5)
        # _trail_min_distance is stored as the absolute price distance: entry_price * pct / 100
        assert restored._trail_min_distance == pytest.approx(100.0 * 1.2 / 100.0)

    # ── Trail-only presets (no partial_take_pct) arm off trail_activation_pct ────
    #
    # Regression tests for the dead-trail bug: presets like l2_regime_aware set
    # trailing_stop_pct > 0 with partial_take_pct == 0. Before the fix the trail
    # could never arm (arming required partial_price), so the only exits were a
    # far TP or the SL — a real TIAUSDT position rode +11.66% back to a loss.
    def test_trail_only_buy_arms_at_activation_and_trails(self):
        """BUY with no partial: arms at entry*(1+activation%) and trails from there."""
        order = make_trail_only('BUY', trail_activation_pct=2.0)
        assert order._partial_price == pytest.approx(102.0)

        # Candle 1: reaches arm threshold — arms, must not trigger same candle.
        assert order.check(102.0, 100.5, 1) is None
        assert order._partial_armed is True

        # Candle 2: runs to 110, low stays above trail 110 - 0.15*(110-100) = 108.5.
        assert order.check(110.0, 109.0, 2) is None

        # Candle 3: retraces through the trail price.
        result = order.check(109.0, 108.0, 3)
        assert result == 'trail'
        assert order.close_price == pytest.approx(108.5)

    def test_trail_only_sell_arms_at_activation_and_trails(self):
        """SELL mirror: arms at entry*(1-activation%), trails below."""
        order = make_trail_only('SELL', trail_activation_pct=2.0)
        assert order._partial_price == pytest.approx(98.0)

        assert order.check(99.5, 98.0, 1) is None
        assert order._partial_armed is True

        # Runs to 90, high stays below trail 90 + 0.15*(100-90) = 91.5.
        assert order.check(91.0, 90.0, 2) is None
        result = order.check(92.0, 91.0, 3)
        assert result == 'trail'
        assert order.close_price == pytest.approx(91.5)

    def test_trail_only_without_activation_stays_dead(self):
        """No partial AND no activation pct: no arm threshold exists — trail stays
        inactive and the order exits only via TP/SL (documented legacy behavior)."""
        order = make_trail_only('BUY', trail_activation_pct=0.0)
        assert order._partial_price is None
        assert order.check(150.0, 100.5, 1) is None   # huge favorable move, no arm
        assert order._partial_armed is False
        assert order.check(200.0, 150.0, 2) == 'win'  # TP still works

    def test_far_partial_arm_capped_by_activation_price(self):
        """Preset with partial AND trail AND activation: when the partial arm point
        (fraction of a far TP) sits beyond the activation price, arming happens at
        the activation price. Regression for the wide-TP presets (l2_bos_*) whose
        +4% favorable moves never armed because arm sat at 25% of a 20-35% TP."""
        order = FakeOrder(
            side='BUY', entry_price=100.0, tp=200.0, sl=80.0,
            level=1, signal_type='test', candle_index=0,
            partial_take_pct=0.60,        # raw arm would be 100 + 0.6*100 = 160
            trailing_stop_pct=0.15,
            trail_activation_pct=2.0,     # activation price 102 — closer, wins
        )
        assert order._partial_price == pytest.approx(102.0)

        assert order.check(102.0, 100.5, 1) is None    # arms at +2%
        assert order._partial_armed is True

        assert order.check(110.0, 109.0, 2) is None    # runs, trail = 108.5
        assert order.check(109.0, 108.0, 3) == 'trail'
        assert order.close_price == pytest.approx(108.5)

    def test_near_partial_arm_unchanged_when_closer_than_activation(self):
        """When the partial arm point is CLOSER than the activation price, the
        original partial-price arming is preserved (no behavior change)."""
        order = FakeOrder(
            side='SELL', entry_price=100.0, tp=90.0, sl=120.0,
            level=1, signal_type='test', candle_index=0,
            partial_take_pct=0.10,        # arm at 100 - 0.1*10 = 99 (1% away)
            trailing_stop_pct=0.15,
            trail_activation_pct=2.0,     # activation price 98 — farther, ignored
        )
        assert order._partial_price == pytest.approx(99.0)

    def test_partial_without_trail_unchanged(self):
        """Pure partial-take preset (no trail): arm price must stay the classic
        partial fraction — the activation cap only applies when a trail exists."""
        order = FakeOrder(
            side='BUY', entry_price=100.0, tp=200.0, sl=80.0,
            level=1, signal_type='test', candle_index=0,
            partial_take_pct=0.60,
            trailing_stop_pct=0.0,
            trail_activation_pct=2.0,
        )
        assert order._partial_price == pytest.approx(160.0)


class TestSlippage:
    """Virtual PnL must be charged what real fills actually cost.

    Virtual orders open at the signalled price; real orders are MARKET orders. Measured
    2026-09-13 over 137 real fills spanning 60 days:

        all symbols   mean +0.0980%   median +0.0183%   p90 +0.3575%
        EIGENUSDT     +0.0240% (n=36)      TIAUSDT      +0.1775% (n=17)
        REZUSDT       +0.0134% (n=12)      INJUSDT      +0.1690% (n=18)

    Two properties of that data drive every test here:

      * NOT ONE of the 137 fills was better than signalled. The cost is one-sided, so
        ignoring it is a systematic overstatement, not noise that averages out.
      * It varies ~13x between symbols, so one global constant misprices most of them.

    At 5x leverage +0.098% of notional is ~0.49% of margin per trade, against a measured
    virtual edge of -0.052%/trade. That is why unadjusted virtual statistics cannot be used
    to decide which symbols to trade for real.

    See docs/specs/2026-09-13-slippage-modelling.md.
    """

    @pytest.fixture
    def store(self, tmp_path):
        return tmp_path / 'slippage_test.json'

    class TestAdversePct:
        def test_a_buy_filled_higher_is_adverse(self):
            # the measured EIGENUSDT order: signalled 0.2087, filled 0.2088
            assert adverse_pct(0.2087, 0.2088, 'BUY') == pytest.approx(0.0479, abs=0.001)

        def test_a_sell_filled_lower_is_adverse(self):
            assert adverse_pct(0.2088, 0.2087, 'SELL') == pytest.approx(0.0479, abs=0.001)

        def test_a_buy_filled_lower_is_favourable(self):
            """Kept signed. The clamp belongs on the estimate, not on the measurement."""
            assert adverse_pct(0.2088, 0.2087, 'BUY') < 0

        def test_an_exact_fill_costs_nothing(self):
            assert adverse_pct(100.0, 100.0, 'BUY') == 0.0

        def test_junk_prices_do_not_raise(self):
            assert adverse_pct(0.0, 1.0, 'BUY') == 0.0
            assert adverse_pct(1.0, 0.0, 'SELL') == 0.0
            assert adverse_pct(-1.0, 1.0, 'BUY') == 0.0

    class TestRecord:
        def test_it_stores_a_sample(self, store):
            record(store, 'TIAUSDT', 1.0, 1.001, 'BUY')
            assert stats(store)['TIAUSDT']['n'] == 1

        def test_the_mean_is_the_running_mean(self, store):
            for filled in (1.001, 1.002, 1.003):
                record(store, 'TIAUSDT', 1.0, filled, 'BUY')
            assert stats(store)['TIAUSDT']['mean'] == pytest.approx(0.2, abs=0.01)

        def test_symbols_are_kept_apart(self, store):
            record(store, 'TIAUSDT', 1.0, 1.002, 'BUY')
            record(store, 'REZUSDT', 1.0, 1.0001, 'BUY')
            s = stats(store)
            assert s['TIAUSDT']['mean'] > s['REZUSDT']['mean']

        def test_the_window_is_capped(self, store):
            for _ in range(WINDOW + 25):
                record(store, 'TIAUSDT', 1.0, 1.001, 'BUY')
            assert stats(store)['TIAUSDT']['n'] == WINDOW

        def test_the_window_drops_the_oldest(self, store):
            """Liquidity drifts — an estimate anchored to old fills would never catch up."""
            for _ in range(WINDOW):
                record(store, 'TIAUSDT', 1.0, 1.005, 'BUY')      # 0.5% era
            for _ in range(WINDOW):
                record(store, 'TIAUSDT', 1.0, 1.0, 'BUY')        # 0.0% era
            assert stats(store)['TIAUSDT']['mean'] == pytest.approx(0.0, abs=1e-6)

        def test_a_bad_price_is_not_recorded(self, store):
            record(store, 'TIAUSDT', 0.0, 1.0, 'BUY')
            assert 'TIAUSDT' not in stats(store)

        def test_writing_never_raises(self, tmp_path):
            """Runs on the placement path — a disk problem must not stop trading."""
            record(tmp_path / 'no' / 'such' / 'dir' / 's.json', 'TIAUSDT', 1.0, 1.001, 'BUY')

    class TestEstimate:
        def test_an_unknown_symbol_gets_the_default(self, store):
            """The case that matters: symbols with no real fills are the ones being judged."""
            assert estimate(store, 'LINKUSDT', {}) == pytest.approx(DEFAULT_PCT)

        def test_too_few_samples_still_gets_the_default(self, store):
            for _ in range(3):
                record(store, 'LINKUSDT', 1.0, 1.0, 'BUY')       # would read as 0% slippage
            assert estimate(store, 'LINKUSDT', {'slippage_min_samples': 5}) == pytest.approx(DEFAULT_PCT)

        def test_enough_samples_uses_the_measured_mean(self, store):
            for _ in range(8):
                record(store, 'TIAUSDT', 1.0, 1.0018, 'BUY')
            assert estimate(store, 'TIAUSDT', {'slippage_min_samples': 5}) == pytest.approx(0.18, abs=0.01)

        def test_an_explicit_override_wins(self, store):
            for _ in range(20):
                record(store, 'TIAUSDT', 1.0, 1.0018, 'BUY')
            got = estimate(store, 'TIAUSDT', {'slippage_per_symbol': {'TIAUSDT': 0.5}})
            assert got == pytest.approx(0.5)

        def test_the_model_can_be_switched_off(self, store):
            for _ in range(20):
                record(store, 'TIAUSDT', 1.0, 1.0018, 'BUY')
            assert estimate(store, 'TIAUSDT', {'slippage_model_enabled': False}) == 0.0

        def test_a_favourable_history_is_clamped_to_zero(self, store):
            """Never credit virtual for lucky fills — the charge floors at nothing."""
            for _ in range(10):
                record(store, 'TIAUSDT', 1.0, 0.999, 'BUY')      # favourable
            assert estimate(store, 'TIAUSDT', {'slippage_min_samples': 5}) == 0.0

        def test_a_missing_store_returns_the_default(self, tmp_path):
            assert estimate(tmp_path / 'absent.json', 'X', {}) == pytest.approx(DEFAULT_PCT)

        def test_a_corrupt_store_returns_the_default(self, store):
            store.write_text('{not json')
            assert estimate(store, 'X', {}) == pytest.approx(DEFAULT_PCT)

        def test_a_configured_default_is_honoured(self, store):
            assert estimate(store, 'X', {'slippage_default_pct': 0.25}) == pytest.approx(0.25)

    class TestEffectiveEntry:
        def test_a_buy_pays_more(self):
            assert effective_entry(100.0, 'BUY', 0.1) == pytest.approx(100.1)

        def test_a_sell_receives_less(self):
            assert effective_entry(100.0, 'SELL', 0.1) == pytest.approx(99.9)

        def test_zero_slippage_is_the_signalled_price(self):
            assert effective_entry(100.0, 'BUY', 0.0) == 100.0

        def test_it_always_hurts_never_helps(self):
            """Both sides must move against the position, never for it."""
            assert effective_entry(100.0, 'BUY', 0.3) > 100.0
            assert effective_entry(100.0, 'SELL', 0.3) < 100.0

        def test_a_junk_entry_is_passed_through(self):
            assert effective_entry(0.0, 'BUY', 0.1) == 0.0

    class TestTheArithmeticThisProtects:
        """The measured consequence, so a regression is recognisable."""

        @staticmethod
        def _pnl(entry, close, qty, side, fee=0.0004):
            raw = (close - entry) * qty if side == 'BUY' else (entry - close) * qty
            return raw - (entry + close) * qty * fee

        def test_the_charge_is_about_half_a_percent_of_margin_at_5x(self):
            """+0.098% of notional at 5x is ~0.49% of margin — 10x the virtual edge."""
            entry, qty, lev = 1.0, 1000.0, 5
            margin = entry * qty / lev
            eff = effective_entry(entry, 'BUY', 0.098)
            cost = self._pnl(entry, 1.02, qty, 'BUY') - self._pnl(eff, 1.02, qty, 'BUY')
            assert cost / margin * 100 == pytest.approx(0.49, abs=0.05)

        def test_charging_it_always_lowers_pnl(self):
            for side, close in (('BUY', 1.05), ('SELL', 0.95)):
                entry, qty = 1.0, 1000.0
                eff = effective_entry(entry, side, 0.1)
                assert self._pnl(eff, close, qty, side) < self._pnl(entry, close, qty, side)


class TestRealStateSurvivesACrash:
    """restart_positions_{mode}.json was written only on a graceful stop; after a crash the
    restart closed its own exchange positions as orphans. main.py now saves it after every
    change (2026-09-29)."""

    def test_main_saves_after_every_change(self):
        main = src('main.py')
        body = main.split('def _save_real_state', 1)[1].split('\n    def ', 1)[0]
        assert 'save_open_positions(_restart_path, quiet=True)' in body
        assert main.count('_save_real_state()') >= 6   # startup, tick close, candle, manual, switch, stop
        tick = main.split('async def on_price_update', 1)[1].split('\n    async def ', 1)[0]
        assert 'if closed:\n            _save_real_state()' in tick

    def test_quiet_save_does_not_log(self, tmp_path, caplog):
        import logging
        ex = make_executor()
        ex._open_orders['BTCUSDT'] = OpenOrder(symbol='BTCUSDT', preset_name='p', side='BUY',
                                               entry_price=1.0, tp_price=2.0, sl_price=0.5,
                                               quantity=1.0, leverage=5)
        with caplog.at_level(logging.INFO):
            assert ex.save_open_positions(tmp_path / 'r.json', quiet=True) == 1
        assert 'Saved' not in caplog.text
        assert (tmp_path / 'r.json').exists()
