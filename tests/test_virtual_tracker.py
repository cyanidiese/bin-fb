import json
import pytest
from pathlib import Path
from bot.virtual_tracker import VirtualTracker


def _make_tracker(tmp_path, mode='test'):
    return VirtualTracker(
        mode=mode,
        orders_path=tmp_path / f"virtual_orders_{mode}.json",
        efficiency_path=tmp_path / f"preset_efficiency_{mode}.json",
    )


def test_seed_from_backtest(tmp_path):
    bt = tmp_path / "backtest_results_BTCUSDT.json"
    # presets is a dict keyed by preset name; profit is calculated as profit_pct/100 * balance_start
    # balance_start=1000: winning trades are 1.0% (10), 2.0% (20), 0.8% (8) → total = 38.0
    bt.write_text(json.dumps({
        "presets": {
            "preset_a": {
                "balance_start": 1000.0,
                "trades": [
                    {"profit_pct": 1.0},
                    {"profit_pct": -0.5},
                    {"profit_pct": 2.0},
                    {"profit_pct": 0.8},
                ],
            }
        }
    }))
    tracker = _make_tracker(tmp_path)
    tracker.seed_from_backtest("BTCUSDT", tmp_path / "backtest_results_BTCUSDT.json")
    eff = tracker.get_efficiency("BTCUSDT", "preset_a")
    assert eff["total_winning_usdt"] == pytest.approx(38.0)  # (1.0 + 2.0 + 0.8) / 100 * 1000
    assert eff["trade_count"] == 4


def test_best_preset_selection(tmp_path):
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "slow", total_winning=100.0, count=5)
    tracker._set_efficiency("BTCUSDT", "fast", total_winning=250.0, count=6)
    tracker._set_efficiency("BTCUSDT", "too_few", total_winning=999.0, count=2)
    best = tracker.best_preset("BTCUSDT")
    assert best == "fast"


def test_record_closed_trade(tmp_path):
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "p1", total_winning=100.0, count=4)
    tracker.record_closed_trade("BTCUSDT", "p1", profit_usdt=50.0)
    eff = tracker.get_efficiency("BTCUSDT", "p1")
    assert eff["total_winning_usdt"] == 150.0
    assert eff["trade_count"] == 5


def test_no_best_preset_when_below_min_trades(tmp_path):
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "p1", total_winning=999.0, count=2)
    assert tracker.best_preset("BTCUSDT") is None
