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
