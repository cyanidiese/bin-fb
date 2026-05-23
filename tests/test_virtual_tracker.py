import json
import pytest
from pathlib import Path
from bot.virtual_tracker import VirtualTracker


def _make_tracker(tmp_path, mode='test', min_trades=3):
    return VirtualTracker(
        mode=mode,
        orders_path=tmp_path / f"virtual_orders_{mode}.json",
        efficiency_path=tmp_path / f"preset_efficiency_{mode}.json",
        get_min_trades=lambda _: min_trades,
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
    # seed_from_backtest stores the backtest score under seeded_winning_usdt;
    # total_winning_usdt and trade_count stay at 0 so UI won't confuse backtest
    # history with live virtual trades.
    assert eff["seeded_winning_usdt"] == pytest.approx(33.0)  # (1.0 - 0.5 + 2.0 + 0.8) / 100 * 1000 net
    assert eff["trade_count"] == 0


def test_best_preset_selection(tmp_path):
    tracker = _make_tracker(tmp_path)
    # count=8,9 >= min_trades(3) → Tier 1, ranked by live. count=2 < 3 → Tier 2 (seed=0).
    tracker._set_efficiency("BTCUSDT", "slow", total_winning=100.0, count=8)
    tracker._set_efficiency("BTCUSDT", "fast", total_winning=250.0, count=9)
    tracker._set_efficiency("BTCUSDT", "too_few", total_winning=999.0, count=2)
    best = tracker.best_preset("BTCUSDT")
    assert best == "fast"


def test_record_closed_trade(tmp_path):
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "p1", total_winning=100.0, count=4)
    tracker.record_closed_trade("BTCUSDT", "p1", profit_usdt=50.0)
    eff = tracker.get_efficiency("BTCUSDT", "p1")
    assert eff["total_winning_usdt"] == 150.0


def test_record_closed_trade_loss_reduces_score(tmp_path):
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "p1", total_winning=100.0, count=4)
    tracker.record_closed_trade("BTCUSDT", "p1", profit_usdt=-30.0)
    eff = tracker.get_efficiency("BTCUSDT", "p1")
    assert eff["total_winning_usdt"] == pytest.approx(70.0)
    assert eff["trade_count"] == 5
    assert eff["trade_count"] == 5


def test_best_preset_returned_when_score_is_zero(tmp_path):
    # count=2 < min_trades(3) → Tier 2, score uses seeded_winning_usdt which defaults to 0.
    # score value 0 >= 0, so best preset is returned (not None).
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "p1", total_winning=999.0, count=2)
    assert tracker.best_preset("BTCUSDT") == "p1"


def test_best_preset_returns_none_only_when_all_scores_negative(tmp_path):
    # Only return None when the best available score is strictly negative.
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "p1", total_winning=-10.0, count=10)
    assert tracker.best_preset("BTCUSDT") is None


def test_tier1_always_beats_tier2_regardless_of_seed(tmp_path):
    # A live-proven preset with $1 live P&L must beat a seed-only preset with $1000 seed.
    tracker = _make_tracker(tmp_path, min_trades=3)
    # Tier 2: huge seed, no live trades
    tracker._set_efficiency("BTCUSDT", "seed_giant", total_winning=0.0, count=0)
    tracker._efficiency["BTCUSDT"]["seed_giant"]["seeded_winning_usdt"] = 1000.0
    # Tier 1: modest live profit, enough trades
    tracker._set_efficiency("BTCUSDT", "live_small", total_winning=1.0, count=3)
    assert tracker.best_preset("BTCUSDT") == "live_small"


def test_losing_champion_dethroned_by_better_live_challenger(tmp_path):
    # Mirrors the TIAUSDT scenario: champion has 4 real trades at -$14,
    # challenger has 5 virtual trades at +$66. Challenger must win.
    tracker = _make_tracker(tmp_path, min_trades=3)
    tracker._set_efficiency("BTCUSDT", "champion", total_winning=-14.0, count=4)
    tracker._set_efficiency("BTCUSDT", "challenger", total_winning=66.0, count=5)
    assert tracker.best_preset("BTCUSDT") == "challenger"


def test_seed_determines_rank_when_no_preset_has_enough_trades(tmp_path):
    # With min_trades=3, if all presets have count < 3, seeded_winning_usdt decides.
    tracker = _make_tracker(tmp_path, min_trades=3)
    tracker._set_efficiency("BTCUSDT", "low_seed", total_winning=500.0, count=2)
    tracker._efficiency["BTCUSDT"]["low_seed"]["seeded_winning_usdt"] = 10.0
    tracker._set_efficiency("BTCUSDT", "high_seed", total_winning=0.0, count=1)
    tracker._efficiency["BTCUSDT"]["high_seed"]["seeded_winning_usdt"] = 200.0
    assert tracker.best_preset("BTCUSDT") == "high_seed"


def test_custom_min_trades_per_symbol(tmp_path):
    # With min_trades=5, a preset with count=4 is still Tier 2 (seed-only).
    tracker = _make_tracker(tmp_path, min_trades=5)
    tracker._set_efficiency("BTCUSDT", "almost_live", total_winning=100.0, count=4)
    tracker._efficiency["BTCUSDT"]["almost_live"]["seeded_winning_usdt"] = 5.0
    tracker._set_efficiency("BTCUSDT", "seed_winner", total_winning=0.0, count=0)
    tracker._efficiency["BTCUSDT"]["seed_winner"]["seeded_winning_usdt"] = 50.0
    # count=4 < min_trades=5 → Tier 2 with seed=5; seed_winner has seed=50 → wins
    assert tracker.best_preset("BTCUSDT") == "seed_winner"
