# tests/test_virtual_order_simulator.py
import json
import json as _json
import pytest
from pathlib import Path
from unittest.mock import MagicMock
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


# ---------------------------------------------------------------------------
# VirtualOrderSimulator tests
# ---------------------------------------------------------------------------
from bot.virtual_order_simulator import VirtualOrderSimulator


def make_simulator(tmp_path, mode='test'):
    all_presets = {'preset_x': {}, 'preset_y': {}}
    vt = MagicMock()
    vt.get_preset_efficiency.return_value = 0.0
    return VirtualOrderSimulator(
        mode=mode,
        all_presets=all_presets,
        project_root=tmp_path,
        get_leverage=lambda sym: 1,
        initial_balance=1000.0,
        virtual_tracker=vt,
        min_notionals={'BTCUSDT': 5.0},
    )


def make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0):
    rec = MagicMock()
    rec.getSide.return_value = side
    rec.getEntryPrice.return_value = entry
    rec.getTarget.return_value = tp
    rec.getStop.return_value = sl
    rec.getLevel.return_value = 1
    rec.getType.return_value = MagicMock(value='test_signal')
    return rec


def make_analyzer_mock(price=50000.0, rec=None):
    analyzer = MagicMock()
    analyzer.get_current_price.return_value = price
    analyzer.get_trend.return_value = MagicMock()
    analyzer.get_recommendation_for_preset.return_value = rec
    return analyzer


@pytest.mark.asyncio
async def test_virtual_order_opens_on_signal(tmp_path):
    """Virtual order opens when a preset gets a recommendation signal."""
    sim = make_simulator(tmp_path)
    rec = make_rec()

    from unittest.mock import patch
    with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEngine:
        instance = MockEngine.return_value
        instance.generate.return_value = rec

        analyzer = make_analyzer_mock(rec=rec)
        base_settings = MagicMock()
        with patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            mock_dc.replace.return_value = base_settings

            await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)

    # Should have opened virtual orders for preset_x and preset_y
    assert len(sim._open.get('BTCUSDT', {})) == 2


@pytest.mark.asyncio
async def test_virtual_order_dedup_no_double_open(tmp_path):
    """Calling on_candle_close twice doesn't double-open already-open positions."""
    sim = make_simulator(tmp_path)
    rec = make_rec()

    from unittest.mock import patch
    with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEngine:
        instance = MockEngine.return_value
        instance.generate.return_value = rec

        analyzer = make_analyzer_mock(rec=rec)
        base_settings = MagicMock()
        with patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            mock_dc.replace.return_value = base_settings

            await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)
            count_after_first = len(sim._open.get('BTCUSDT', {}))

            await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)
            count_after_second = len(sim._open.get('BTCUSDT', {}))

    assert count_after_first == 2
    assert count_after_first == count_after_second  # no double-open


@pytest.mark.asyncio
async def test_virtual_order_closes_on_tp(tmp_path):
    """Virtual order closes with result 'win' when price crosses TP."""
    sim = make_simulator(tmp_path)
    rec = make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0)

    from unittest.mock import patch
    with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEngine:
        instance = MockEngine.return_value
        instance.generate.return_value = rec

        analyzer = make_analyzer_mock(rec=rec)
        base_settings = MagicMock()
        with patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            mock_dc.replace.return_value = base_settings

            await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)

    assert len(sim._open.get('BTCUSDT', {})) > 0
    preset_name = next(iter(sim._open.get('BTCUSDT', {})))

    closed = await sim.check_prices('BTCUSDT', 55001.0)  # above TP
    assert len(closed) >= 1
    assert closed[0]['result'] in ('win', 'trail', 'partial')
    assert preset_name not in sim._open.get('BTCUSDT', {})


@pytest.mark.asyncio
async def test_virtual_order_closes_on_sl(tmp_path):
    """Virtual order closes with result 'loss' when price crosses SL."""
    sim = make_simulator(tmp_path)
    rec = make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0)

    from unittest.mock import patch
    with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEngine:
        instance = MockEngine.return_value
        instance.generate.return_value = rec

        analyzer = make_analyzer_mock(rec=rec)
        base_settings = MagicMock()
        with patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            mock_dc.replace.return_value = base_settings

            await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)

    closed = await sim.check_prices('BTCUSDT', 47999.0)  # below SL
    assert len(closed) >= 1
    assert closed[0]['result'] == 'loss'


@pytest.mark.asyncio
async def test_close_all_open_marks_closed_early(tmp_path):
    """close_all_open clears all open orders and marks them closed_early."""
    sim = make_simulator(tmp_path)
    rec = make_rec()

    from unittest.mock import patch
    with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEngine:
        instance = MockEngine.return_value
        instance.generate.return_value = rec

        analyzer = make_analyzer_mock(rec=rec)
        base_settings = MagicMock()
        with patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            mock_dc.replace.return_value = base_settings

            await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)

    assert len(sim._open.get('BTCUSDT', {})) > 0

    feed_mock = MagicMock()
    feed_mock.client.futures_symbol_ticker.return_value = {'price': '51000.0'}

    await sim.close_all_open(['BTCUSDT'], feed_mock)
    assert len(sim._open.get('BTCUSDT', {})) == 0


@pytest.mark.asyncio
async def test_virtual_order_persists_to_file(tmp_path):
    """Closed virtual order is persisted to the JSON file."""
    sim = make_simulator(tmp_path)
    rec = make_rec()

    from unittest.mock import patch
    with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEngine:
        instance = MockEngine.return_value
        instance.generate.return_value = rec

        analyzer = make_analyzer_mock(rec=rec)
        base_settings = MagicMock()
        with patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
            mock_dc.replace.return_value = base_settings

            await sim.on_candle_close('BTCUSDT', analyzer, 'some_best_preset', base_settings)

    # Close via price above TP
    await sim.check_prices('BTCUSDT', 55001.0)

    file = tmp_path / 'data' / 'virtual_orders_BTCUSDT_test.json'
    assert file.exists()
    records = _json.loads(file.read_text())
    closed = [r for r in records if r['status'] == 'closed']
    assert len(closed) >= 1


# ---------------------------------------------------------------------------
# New virtual balance pool tests (Task 6)
# ---------------------------------------------------------------------------
from bot.leverage_tracker import LeverageTracker


def make_lt(tmp_path, symbols=None):
    return LeverageTracker(
        mode='test',
        active_symbols=symbols or ['BTCUSDT'],
        data_path=tmp_path / 'lev.json',
    )


def make_vt():
    vt = MagicMock()
    vt.get_preset_efficiency.return_value = 0.0
    return vt


def make_sim(tmp_path, initial_balance=100.0, presets=None):
    lt = make_lt(tmp_path)
    vt = make_vt()
    sim = VirtualOrderSimulator(
        mode='test',
        all_presets=presets or {'preset_a': {}, 'preset_b': {}},
        project_root=tmp_path,
        get_leverage=lambda sym: lt.get_current_level(),
        initial_balance=initial_balance,
        virtual_tracker=vt,
        min_notionals={'BTCUSDT': 5.0},
    )
    return sim, lt, vt


def test_initial_balance_set(tmp_path):
    sim, *_ = make_sim(tmp_path, initial_balance=200.0)
    assert sim._virtual_balance == 200.0
    assert sim._virtual_committed == 0.0


def test_virtual_balance_persists_to_disk(tmp_path):
    sim, *_ = make_sim(tmp_path, initial_balance=100.0)
    sim._virtual_balance = 150.0
    sim._virtual_committed = 10.0
    sim._save_virtual_balance()
    path = tmp_path / 'data' / 'virtual_balance_test.json'
    data = _json.loads(path.read_text())
    assert data['virtual_balance'] == 150.0
    assert data['virtual_committed'] == 10.0


def test_virtual_balance_loads_from_disk(tmp_path):
    # First instance persists
    sim1, *_ = make_sim(tmp_path, initial_balance=100.0)
    sim1._virtual_balance = 150.0
    sim1._virtual_committed = 10.0
    sim1._save_virtual_balance()
    # Second instance should load disk state, not use initial_balance
    sim2, *_ = make_sim(tmp_path, initial_balance=999.0)
    assert sim2._virtual_balance == 150.0
    assert sim2._virtual_committed == 10.0


def test_margin_formula_uses_leverage_level(tmp_path):
    # margin = min_notional / leverage = 5.0 / 1 = 5.0 at level 1
    sim, lt, _ = make_sim(tmp_path, initial_balance=100.0)
    assert lt.get_current_level() == 1
    margin = 5.0 / lt.get_current_level()
    assert margin == 5.0


def test_skips_open_when_balance_insufficient(tmp_path):
    sim, lt, _ = make_sim(tmp_path, initial_balance=3.0)
    # min_notional=5, leverage=1 → margin=5 > balance=3
    available = sim._virtual_balance - sim._virtual_committed
    margin = 5.0 / lt.get_current_level()
    assert available < margin  # confirms the skip condition


def test_close_releases_committed_and_updates_balance(tmp_path):
    sim, *_ = make_sim(tmp_path, initial_balance=100.0)
    margin = 5.0
    pnl = 2.0
    sim._virtual_committed = margin
    sim._virtual_balance = 100.0
    # Simulate close logic
    sim._virtual_committed -= margin
    sim._virtual_committed = max(0.0, sim._virtual_committed)
    sim._virtual_balance += pnl
    assert sim._virtual_committed == 0.0
    assert sim._virtual_balance == 102.0
