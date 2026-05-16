import json
import pytest
from bot.virtual_tracker import VirtualTracker


@pytest.fixture
def tracker(tmp_path):
    eff_path = tmp_path / 'eff.json'
    eff_data = {
        "BTCUSDT": {
            "preset_a": {"total_winning_usdt": 10.0, "trade_count": 8},
            "preset_b": {"total_winning_usdt": 25.0, "trade_count": 9},
            # preset_c: below _MIN_TRADES, uses seeded_winning_usdt fallback
            "preset_c": {"total_winning_usdt": 50.0, "trade_count": 2, "seeded_winning_usdt": 15.0},
        },
        "ETHUSDT": {
            "preset_a": {"total_winning_usdt": 8.0, "trade_count": 8},
        },
    }
    eff_path.write_text(json.dumps(eff_data))
    return VirtualTracker(
        mode='test',
        orders_path=tmp_path / 'orders.json',
        efficiency_path=eff_path,
    )


def test_get_efficiency_score_returns_best_eligible(tracker):
    # preset_a=10 (5 trades ok), preset_b=25 (6 trades ok), preset_c=50 but 2 trades → ineligible
    assert tracker.get_efficiency_score('BTCUSDT') == 25.0


def test_get_efficiency_score_unknown_symbol(tracker):
    assert tracker.get_efficiency_score('SOLUSDT') == 0.0


def test_get_preset_efficiency_known(tracker):
    # preset_a has 8 live trades (>= _MIN_TRADES), uses total_winning_usdt
    assert tracker.get_preset_efficiency('BTCUSDT', 'preset_a') == 10.0


def test_get_preset_efficiency_uses_seeded_fallback(tracker):
    # preset_c has only 2 live trades, falls back to seeded_winning_usdt
    assert tracker.get_preset_efficiency('BTCUSDT', 'preset_c') == 15.0


def test_get_preset_efficiency_unknown_preset(tracker):
    assert tracker.get_preset_efficiency('BTCUSDT', 'nonexistent') == 0.0


def test_get_preset_efficiency_unknown_symbol(tracker):
    assert tracker.get_preset_efficiency('SOLUSDT', 'preset_a') == 0.0
