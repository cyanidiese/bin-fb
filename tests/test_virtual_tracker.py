import json
import pytest
from pathlib import Path
from unittest.mock import patch
from bot.virtual_tracker import VirtualTracker


def _seed_factor() -> float:
    """The live backtest_seed_leverage_factor seed_from_backtest will apply."""
    from config.risk_config import load_risk_config
    return float(load_risk_config().get("backtest_seed_leverage_factor", 1.0))


def _make_tracker(tmp_path, mode='test', min_trades=3):
    return VirtualTracker(
        mode=mode,
        orders_path=tmp_path / f"virtual_orders_{mode}.json",
        efficiency_path=tmp_path / f"preset_efficiency_{mode}.json",
        get_min_trades=lambda _: min_trades,
    )


def _patch_config(min_trades=3, window_size=10, floor=-20.0):
    """Patch load_risk_config so tests don't depend on a risk_config.json on disk."""
    return patch(
        'bot.virtual_tracker.load_risk_config',
        return_value={
            'min_trades_for_ranking': min_trades,
            'ranking_window_size': window_size,
            'virtual_only_floor': floor,
        },
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
    # seeded USD is scaled by backtest_seed_leverage_factor so it is comparable
    # to live PnL earned at real leverage. Pin the factor rather than reading the
    # project's risk_config.json, which made this test environment-dependent.
    assert eff["seeded_winning_usdt"] == pytest.approx(33.0 * _seed_factor())
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


# ── Window-based ranking tests ─────────────────────────────────────────────


def test_window_score_used_when_warmed_up(tmp_path):
    # trade_count=10 >= min_trades=3, recent_trades has 10 entries summing to 42.0.
    # Score must come from the window (42.0), not from cumulative total_winning_usdt (200.0).
    with _patch_config(min_trades=3, window_size=10):
        tracker = _make_tracker(tmp_path)
        recent = [3.0, 5.0, -1.0, 4.0, 6.0, 2.0, 7.0, 8.0, 4.0, 4.0]  # sum = 42.0
        assert sum(recent) == pytest.approx(42.0)
        tracker._set_efficiency("BTCUSDT", "p1", total_winning=200.0, count=10, recent_trades=recent)
        score = tracker.get_preset_efficiency("BTCUSDT", "p1")
        assert score == pytest.approx(42.0)


def test_window_falls_back_to_cumulative_during_warmup(tmp_path):
    # trade_count=5 >= min_trades=3 but window_size=10 and recent_trades only has 5 entries.
    # Score must fall back to cumulative total_winning_usdt (80.0).
    with _patch_config(min_trades=3, window_size=10):
        tracker = _make_tracker(tmp_path)
        recent = [1.0, 2.0, 3.0, 4.0, 5.0]  # 5 entries, sum=15, but window not yet full
        tracker._set_efficiency("BTCUSDT", "p1", total_winning=80.0, count=5, recent_trades=recent)
        score = tracker.get_preset_efficiency("BTCUSDT", "p1")
        assert score == pytest.approx(80.0)


def test_record_closed_trade_fills_and_trims_window(tmp_path):
    # Call record_closed_trade 12 times. Window size = 10, so only last 10 entries kept.
    with _patch_config(min_trades=3, window_size=10):
        tracker = _make_tracker(tmp_path)
        tracker._set_efficiency("BTCUSDT", "p1", total_winning=0.0, count=0)
        profits = [float(i) for i in range(1, 13)]  # 1.0 … 12.0
        for p in profits:
            tracker.record_closed_trade("BTCUSDT", "p1", p)
        eff = tracker.get_efficiency("BTCUSDT", "p1")
        assert len(eff["recent_trades"]) == 10
        # Last 10 values are 3.0 … 12.0
        assert sum(eff["recent_trades"]) == pytest.approx(sum(range(3, 13)))


def test_is_virtual_only_true_when_score_below_floor(tmp_path):
    # Best preset has trade_count >= min_trades and window sum = -25.0 < floor (-20.0).
    with _patch_config(min_trades=3, window_size=5, floor=-20.0):
        tracker = _make_tracker(tmp_path)
        recent = [-5.0, -5.0, -5.0, -5.0, -5.0]  # sum = -25.0
        tracker._set_efficiency("BTCUSDT", "p1", total_winning=-25.0, count=5, recent_trades=recent)
        assert tracker.is_virtual_only("BTCUSDT") is True


def test_is_virtual_only_false_when_score_above_floor(tmp_path):
    # Window sum = +5.0 > floor (-20.0): gate does not activate.
    with _patch_config(min_trades=3, window_size=5, floor=-20.0):
        tracker = _make_tracker(tmp_path)
        recent = [1.0, 1.0, 1.0, 1.0, 1.0]  # sum = 5.0
        tracker._set_efficiency("BTCUSDT", "p1", total_winning=5.0, count=5, recent_trades=recent)
        assert tracker.is_virtual_only("BTCUSDT") is False


def test_is_virtual_only_false_before_min_trades(tmp_path):
    # trade_count=1 < min_trades=3: floor gate must not activate regardless of score.
    with _patch_config(min_trades=3, window_size=5, floor=-20.0):
        tracker = _make_tracker(tmp_path)
        recent = [-100.0]  # very negative, but count too low to trigger gate
        tracker._set_efficiency("BTCUSDT", "p1", total_winning=-100.0, count=1, recent_trades=recent)
        assert tracker.is_virtual_only("BTCUSDT") is False


def test_cold_start_missing_recent_trades_key(tmp_path):
    # Write efficiency JSON without the recent_trades field (old format).
    # After loading + one record_closed_trade, recent_trades must have exactly 1 entry.
    eff_path = tmp_path / "preset_efficiency_test.json"
    eff_path.write_text(json.dumps({
        "BTCUSDT": {
            "p1": {
                "total_winning_usdt": 10.0,
                "trade_count": 3,
                "seeded_winning_usdt": 5.0,
                # no recent_trades key — old format
            }
        }
    }))
    with _patch_config(min_trades=3, window_size=10):
        tracker = VirtualTracker(
            mode='test',
            orders_path=tmp_path / "virtual_orders_test.json",
            efficiency_path=eff_path,
            get_min_trades=lambda _: 3,
        )
        tracker.record_closed_trade("BTCUSDT", "p1", profit_usdt=7.0)
        eff = tracker.get_efficiency("BTCUSDT", "p1")
        assert len(eff["recent_trades"]) == 1
        assert eff["recent_trades"][0] == pytest.approx(7.0)
