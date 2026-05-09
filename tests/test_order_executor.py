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
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}
    qty = await ex.round_quantity('BTCUSDT', 0.0057)
    assert qty == pytest.approx(0.005)


@pytest.mark.asyncio
async def test_round_quantity_below_min_returns_zero():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}
    qty = await ex.round_quantity('BTCUSDT', 0.0009)
    assert qty == 0.0


# --- place_order with mocked exchange ---

@pytest.mark.asyncio
async def test_place_order_happy_path():
    ex = make_executor(with_feed=True)
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

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
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

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
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

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
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

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
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

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


# --- check_symbol_price ---

@pytest.mark.asyncio
async def test_check_symbol_price_sl_hit():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    async def fake_submit(symbol, side, quantity, leverage):
        return 'id_sl'

    async def fake_close(symbol, order):
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
async def test_check_symbol_price_tp_hit():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    async def fake_submit(symbol, side, quantity, leverage):
        return 'id_tp'

    async def fake_close(symbol, order):
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
async def test_check_symbol_price_scope_isolation():
    """Price update for BTCUSDT must not affect ETHUSDT's open order."""
    ex = make_executor()
    for sym in ('BTCUSDT', 'ETHUSDT'):
        ex._lot_cache[sym] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    async def fake_submit(symbol, side, quantity, leverage):
        return f'id_{symbol}'

    async def fake_close(symbol, order):
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
async def test_consecutive_failures_trigger_notify():
    ex = make_executor()
    ex._lot_cache['BTCUSDT'] = {'step_size': 0.001, 'min_qty': 0.001, 'min_notional': 0.0}

    async def failing_submit(*a):
        raise RuntimeError("fail")

    ex._submit_to_exchange = failing_submit
    for _ in range(3):
        await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
    ex._notifier.notify.assert_called()


# --- get_min_notional ---

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


# --- leverage brackets ---

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
