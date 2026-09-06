# tests/test_real_order_recording.py
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


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
async def test_real_order_records_append_across_trades(tmp_path):
    ex = make_executor(tmp_path)
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
async def test_real_order_recorded_via_check_all_orders(tmp_path):
    """check_all_orders also records closed orders (candle-based close path)."""
    ex = make_executor(tmp_path)
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
    async def fake_close(symbol, order, fallback=None):
        return 55000.0

    ex._submit_to_exchange = fake_submit
    ex._market_close = fake_close

    await ex.place_order('BTCUSDT', 'p', 'BUY', 50000, 55000, 48000, 0.005, 5)
    # Should not raise even though project_root is None
    closed = await ex.check_symbol_price('BTCUSDT', 55001.0)
    assert len(closed) == 1
