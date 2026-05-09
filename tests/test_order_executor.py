# tests/test_order_executor.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bot.order_executor import OrderExecutor, OrderState, OpenOrder


def make_executor(with_feed=False):
    settings = MagicMock()
    settings.partial_take_pct = 0.0
    settings.trailing_stop_pct = 0.0
    risk_manager = MagicMock()
    notifier = MagicMock()
    feed = None
    if with_feed:
        feed = MagicMock()
        feed.client = MagicMock()
    with patch('bot.order_executor.load_risk_config', return_value={'consecutive_failure_threshold': 3}):
        return OrderExecutor('test', settings, risk_manager, notifier, data_feed=feed)


# --- State machine ---

def test_initial_state_is_idle():
    ex = make_executor()
    assert ex.get_state('BTCUSDT') == OrderState.IDLE


def test_get_open_orders_empty():
    ex = make_executor()
    assert ex.get_open_orders() == {}


# --- round_quantity ---

@pytest.mark.asyncio
async def test_round_quantity_applies_step():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001}
    qty = await ex.round_quantity('BTCUSDT', 0.0057)
    assert qty == pytest.approx(0.005)


@pytest.mark.asyncio
async def test_round_quantity_below_min_returns_zero():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001}
    qty = await ex.round_quantity('BTCUSDT', 0.0009)
    assert qty == 0.0


# --- place_order with mocked exchange ---

@pytest.mark.asyncio
async def test_place_order_happy_path():
    ex = make_executor(with_feed=True)
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001}

    async def fake_submit(symbol, side, quantity, leverage):
        return 'order123'

    ex._submit_to_exchange = fake_submit
    ok = await ex.place_order('BTCUSDT', 'my_preset', 'BUY', 50000, 55000, 48000, 0.005, 5)
    assert ok is True
    assert ex.get_state('BTCUSDT') == OrderState.OPEN
    assert 'BTCUSDT' in ex._fake_orders


@pytest.mark.asyncio
async def test_place_order_exchange_failure_sets_idle():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001}

    async def failing_submit(*a, **kw):
        raise RuntimeError("network error")

    ex._submit_to_exchange = failing_submit
    ok = await ex.place_order('BTCUSDT', 'preset', 'BUY', 50000, 55000, 48000, 0.005, 5)
    assert ok is False
    assert ex.get_state('BTCUSDT') == OrderState.IDLE


# --- check_all_orders ---

@pytest.mark.asyncio
async def test_check_all_orders_sl_hit():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001}

    async def fake_submit(symbol, side, quantity, leverage):
        return 'id1'

    async def fake_close(symbol, order):
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
async def test_check_all_orders_tp_hit():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001}

    async def fake_submit(symbol, side, quantity, leverage):
        return 'id2'

    async def fake_close(symbol, order):
        return 55100.0

    ex._submit_to_exchange = fake_submit
    ex._market_close = fake_close

    await ex.place_order('BTCUSDT', 'preset', 'BUY', 50000, 55000, 48000, 0.005, 5)
    closed = await ex.check_all_orders(high=55100, low=50000, candle_open=50000, candle_close=55100)
    assert len(closed) == 1
    assert closed[0]['result'] == 'win'
    assert closed[0]['pnl_usdt'] == pytest.approx((55100 - 50000) * 0.005)


# --- close_all_orders_at_market clears fake_orders ---

@pytest.mark.asyncio
async def test_close_all_clears_fake_orders():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001}

    async def fake_submit(*a):
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


# --- consecutive failure counter ---

@pytest.mark.asyncio
async def test_consecutive_failures_trigger_notify():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001}

    async def failing_submit(*a):
        raise RuntimeError("fail")

    ex._submit_to_exchange = failing_submit
    for _ in range(3):
        await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
    ex._notifier.notify.assert_called()
