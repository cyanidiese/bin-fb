"""VirtualTracker: efficiency scoring, window ranking, seeding, tiers and substitution.

Sections (one per former file):
- TestVirtualTracker             (test_virtual_tracker.py)
- TestSeedFromBacktestSkipLogic  (VirtualTracker tests formerly atop test_virtual_order_simulator.py)
- TestVirtualTrackerHelpers      (test_virtual_tracker_helpers.py)
- TestPresetSubstitution         (test_preset_substitution.py)
"""
import json
from unittest.mock import patch

import pytest

from bot.virtual_tracker import VirtualTracker


def _seed_factor() -> float:
    """The live backtest_seed_leverage_factor seed_from_backtest will apply."""
    from config.risk_config import load_risk_config
    return float(load_risk_config().get("backtest_seed_leverage_factor", 1.0))


# ══════════════════════ Core tracker ══════════════════════
# (was test_virtual_tracker.py)


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
        'bot.virtual_tracker.risk_config_view',
        return_value={
            'min_trades_for_ranking': min_trades,
            'ranking_window_size': window_size,
            'virtual_only_floor': floor,
        },
    )


class TestVirtualTracker:
    def test_seed_from_backtest(self, tmp_path):
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

    def test_best_preset_selection(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        # count=8,9 >= min_trades(3) → Tier 1, ranked by live. count=2 < 3 → Tier 2 (seed=0).
        tracker._set_efficiency("BTCUSDT", "slow", total_winning=100.0, count=8)
        tracker._set_efficiency("BTCUSDT", "fast", total_winning=250.0, count=9)
        tracker._set_efficiency("BTCUSDT", "too_few", total_winning=999.0, count=2)
        best = tracker.best_preset("BTCUSDT")
        assert best == "fast"

    def test_record_closed_trade(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        tracker._set_efficiency("BTCUSDT", "p1", total_winning=100.0, count=4)
        tracker.record_closed_trade("BTCUSDT", "p1", profit_usdt=50.0)
        eff = tracker.get_efficiency("BTCUSDT", "p1")
        assert eff["total_winning_usdt"] == 150.0

    def test_record_closed_trade_loss_reduces_score(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        tracker._set_efficiency("BTCUSDT", "p1", total_winning=100.0, count=4)
        tracker.record_closed_trade("BTCUSDT", "p1", profit_usdt=-30.0)
        eff = tracker.get_efficiency("BTCUSDT", "p1")
        assert eff["total_winning_usdt"] == pytest.approx(70.0)
        assert eff["trade_count"] == 5
        assert eff["trade_count"] == 5

    def test_best_preset_returned_when_score_is_zero(self, tmp_path):
        # count=2 < min_trades(3) → Tier 2, score uses seeded_winning_usdt which defaults to 0.
        # score value 0 >= 0, so best preset is returned (not None).
        tracker = _make_tracker(tmp_path)
        tracker._set_efficiency("BTCUSDT", "p1", total_winning=999.0, count=2)
        assert tracker.best_preset("BTCUSDT") == "p1"

    def test_best_preset_returns_none_only_when_all_scores_negative(self, tmp_path):
        # Only return None when the best available score is strictly negative.
        tracker = _make_tracker(tmp_path)
        tracker._set_efficiency("BTCUSDT", "p1", total_winning=-10.0, count=10)
        assert tracker.best_preset("BTCUSDT") is None

    def test_tier1_always_beats_tier2_regardless_of_seed(self, tmp_path):
        # A live-proven preset with $1 live P&L must beat a seed-only preset with $1000 seed.
        tracker = _make_tracker(tmp_path, min_trades=3)
        # Tier 2: huge seed, no live trades
        tracker._set_efficiency("BTCUSDT", "seed_giant", total_winning=0.0, count=0)
        tracker._efficiency["BTCUSDT"]["seed_giant"]["seeded_winning_usdt"] = 1000.0
        # Tier 1: modest live profit, enough trades
        tracker._set_efficiency("BTCUSDT", "live_small", total_winning=1.0, count=3)
        assert tracker.best_preset("BTCUSDT") == "live_small"

    def test_losing_champion_dethroned_by_better_live_challenger(self, tmp_path):
        # Mirrors the TIAUSDT scenario: champion has 4 real trades at -$14,
        # challenger has 5 virtual trades at +$66. Challenger must win.
        tracker = _make_tracker(tmp_path, min_trades=3)
        tracker._set_efficiency("BTCUSDT", "champion", total_winning=-14.0, count=4)
        tracker._set_efficiency("BTCUSDT", "challenger", total_winning=66.0, count=5)
        assert tracker.best_preset("BTCUSDT") == "challenger"

    def test_seed_determines_rank_when_no_preset_has_enough_trades(self, tmp_path):
        # With min_trades=3, if all presets have count < 3, seeded_winning_usdt decides.
        tracker = _make_tracker(tmp_path, min_trades=3)
        tracker._set_efficiency("BTCUSDT", "low_seed", total_winning=500.0, count=2)
        tracker._efficiency["BTCUSDT"]["low_seed"]["seeded_winning_usdt"] = 10.0
        tracker._set_efficiency("BTCUSDT", "high_seed", total_winning=0.0, count=1)
        tracker._efficiency["BTCUSDT"]["high_seed"]["seeded_winning_usdt"] = 200.0
        assert tracker.best_preset("BTCUSDT") == "high_seed"

    def test_custom_min_trades_per_symbol(self, tmp_path):
        # With min_trades=5, a preset with count=4 is still Tier 2 (seed-only).
        tracker = _make_tracker(tmp_path, min_trades=5)
        tracker._set_efficiency("BTCUSDT", "almost_live", total_winning=100.0, count=4)
        tracker._efficiency["BTCUSDT"]["almost_live"]["seeded_winning_usdt"] = 5.0
        tracker._set_efficiency("BTCUSDT", "seed_winner", total_winning=0.0, count=0)
        tracker._efficiency["BTCUSDT"]["seed_winner"]["seeded_winning_usdt"] = 50.0
        # count=4 < min_trades=5 → Tier 2 with seed=5; seed_winner has seed=50 → wins
        assert tracker.best_preset("BTCUSDT") == "seed_winner"

    # ── Window-based ranking tests ─────────────────────────────────────────────

    def test_window_score_used_when_warmed_up(self, tmp_path):
        # trade_count=10 >= min_trades=3, recent_trades has 10 entries summing to 42.0.
        # Score must come from the window (42.0), not from cumulative total_winning_usdt (200.0).
        with _patch_config(min_trades=3, window_size=10):
            tracker = _make_tracker(tmp_path)
            recent = [3.0, 5.0, -1.0, 4.0, 6.0, 2.0, 7.0, 8.0, 4.0, 4.0]  # sum = 42.0
            assert sum(recent) == pytest.approx(42.0)
            tracker._set_efficiency("BTCUSDT", "p1", total_winning=200.0, count=10, recent_trades=recent)
            score = tracker.get_preset_efficiency("BTCUSDT", "p1")
            assert score == pytest.approx(42.0)

    def test_window_falls_back_to_cumulative_during_warmup(self, tmp_path):
        # trade_count=5 >= min_trades=3 but window_size=10 and recent_trades only has 5 entries.
        # Score must fall back to cumulative total_winning_usdt (80.0).
        with _patch_config(min_trades=3, window_size=10):
            tracker = _make_tracker(tmp_path)
            recent = [1.0, 2.0, 3.0, 4.0, 5.0]  # 5 entries, sum=15, but window not yet full
            tracker._set_efficiency("BTCUSDT", "p1", total_winning=80.0, count=5, recent_trades=recent)
            score = tracker.get_preset_efficiency("BTCUSDT", "p1")
            assert score == pytest.approx(80.0)

    def test_record_closed_trade_fills_and_trims_window(self, tmp_path):
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

    def test_is_virtual_only_true_when_score_below_floor(self, tmp_path):
        # Best preset has trade_count >= min_trades and window sum = -25.0 < floor (-20.0).
        with _patch_config(min_trades=3, window_size=5, floor=-20.0):
            tracker = _make_tracker(tmp_path)
            recent = [-5.0, -5.0, -5.0, -5.0, -5.0]  # sum = -25.0
            tracker._set_efficiency("BTCUSDT", "p1", total_winning=-25.0, count=5, recent_trades=recent)
            assert tracker.is_virtual_only("BTCUSDT") is True

    def test_is_virtual_only_false_when_score_above_floor(self, tmp_path):
        # Window sum = +5.0 > floor (-20.0): gate does not activate.
        with _patch_config(min_trades=3, window_size=5, floor=-20.0):
            tracker = _make_tracker(tmp_path)
            recent = [1.0, 1.0, 1.0, 1.0, 1.0]  # sum = 5.0
            tracker._set_efficiency("BTCUSDT", "p1", total_winning=5.0, count=5, recent_trades=recent)
            assert tracker.is_virtual_only("BTCUSDT") is False

    def test_is_virtual_only_false_before_min_trades(self, tmp_path):
        # trade_count=1 < min_trades=3: floor gate must not activate regardless of score.
        with _patch_config(min_trades=3, window_size=5, floor=-20.0):
            tracker = _make_tracker(tmp_path)
            recent = [-100.0]  # very negative, but count too low to trigger gate
            tracker._set_efficiency("BTCUSDT", "p1", total_winning=-100.0, count=1, recent_trades=recent)
            assert tracker.is_virtual_only("BTCUSDT") is False

    def test_cold_start_missing_recent_trades_key(self, tmp_path):
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


# ══════════════════════ Seeding from backtest results ══════════════════════
# (was the "VirtualTracker tests" block at the top of test_virtual_order_simulator.py)


def _make_plain_tracker(tmp_path):
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


class TestSeedFromBacktestSkipLogic:
    def test_seed_from_backtest_populates_efficiency(self, tmp_path):
        tracker = _make_plain_tracker(tmp_path)
        bt_path = make_backtest_file(tmp_path)
        tracker.seed_from_backtest('BTCUSDT', bt_path)
        eff = tracker.get_efficiency('BTCUSDT', 'preset_a')
        # seed_from_backtest stores backtest score in seeded_winning_usdt;
        # trade_count stays 0 so UI won't show backtest history as live trades.
        assert eff['trade_count'] == 0
        assert eff['seeded_winning_usdt'] == pytest.approx(25.0 * _seed_factor())

    def test_seed_from_backtest_skips_if_symbol_already_seeded(self, tmp_path):
        tracker = _make_plain_tracker(tmp_path)
        bt_path = make_backtest_file(tmp_path)
        tracker.seed_from_backtest('BTCUSDT', bt_path)
        bt_path.write_text('{"presets": {}}')  # empty presets — nothing to overwrite
        tracker.seed_from_backtest('BTCUSDT', bt_path)
        eff = tracker.get_efficiency('BTCUSDT', 'preset_a')
        assert eff['seeded_winning_usdt'] == pytest.approx(25.0 * _seed_factor())  # original preserved

    def test_seed_from_backtest_seeds_new_symbol_even_if_other_exists(self, tmp_path):
        tracker = _make_plain_tracker(tmp_path)
        bt_path_btc = make_backtest_file(tmp_path, 'BTCUSDT')
        bt_path_eth = make_backtest_file(tmp_path, 'ETHUSDT')
        tracker.seed_from_backtest('BTCUSDT', bt_path_btc)
        tracker.seed_from_backtest('ETHUSDT', bt_path_eth)
        assert tracker.get_efficiency('ETHUSDT', 'preset_a')['seeded_winning_usdt'] == pytest.approx(25.0 * _seed_factor())


# ══════════════════════ Efficiency helpers ══════════════════════
# (was test_virtual_tracker_helpers.py)


class TestVirtualTrackerHelpers:
    @pytest.fixture
    def tracker(self, tmp_path):
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

    def test_get_efficiency_score_returns_best_eligible(self, tracker):
        # preset_a=10 (5 trades ok), preset_b=25 (6 trades ok), preset_c=50 but 2 trades → ineligible
        assert tracker.get_efficiency_score('BTCUSDT') == 25.0

    def test_get_efficiency_score_unknown_symbol(self, tracker):
        assert tracker.get_efficiency_score('SOLUSDT') == 0.0

    def test_get_preset_efficiency_known(self, tracker):
        # preset_a has 8 live trades (>= _MIN_TRADES), uses total_winning_usdt
        assert tracker.get_preset_efficiency('BTCUSDT', 'preset_a') == 10.0

    def test_get_preset_efficiency_uses_seeded_fallback(self, tracker):
        # preset_c has only 2 live trades, falls back to seeded_winning_usdt
        assert tracker.get_preset_efficiency('BTCUSDT', 'preset_c') == 15.0

    def test_get_preset_efficiency_unknown_preset(self, tracker):
        assert tracker.get_preset_efficiency('BTCUSDT', 'nonexistent') == 0.0

    def test_get_preset_efficiency_unknown_symbol(self, tracker):
        assert tracker.get_preset_efficiency('SOLUSDT', 'preset_a') == 0.0


# ══════════════════════ Tier-aware ranking and preset substitution ══════════════════════
# (was test_preset_substitution.py)


def _resolve(cfg, symbol):
    """Mirrors the resolution in main.py's candidate loop."""
    return cfg.get("substitution_enabled_per_symbol", {}).get(
        symbol, cfg.get("substitution_enabled", False))


def _should_substitute(cfg, symbol, best_is_none=True):
    """Mirrors the guard in main.py's candidate loop."""
    on = cfg.get("substitution_enabled_per_symbol", {}).get(
        symbol, cfg.get("substitution_enabled", False))
    locked = symbol in cfg.get("locked_presets", {})
    return best_is_none and on and not locked


class TestPresetSubstitution:
    """Tier-aware ranking and single-rank preset substitution.

    The tier bug: get_preset_efficiency returns only the VALUE half of the score, so a
    preset with no trading history (tier 0, seeded 0.00) outranked a live-proven one whose
    recent value was negative — purely because 0.00 > -2.40. Real trading results must
    always outrank a backtest guess. Ranking must use the full (tier, value) key.
    """

    @pytest.fixture
    def tracker(self, tmp_path, monkeypatch):
        # min_trades=8 matches the live config, so <8 trades => tier 0
        t = VirtualTracker(
            mode="test",
            orders_path=tmp_path / "orders.json",
            efficiency_path=tmp_path / "eff.json",
            get_min_trades=lambda _s: 8,
        )
        monkeypatch.setattr("bot.virtual_tracker.risk_config_view",
                            lambda: {"ranking_window_size": 10, "preset_blocklist": []})
        t._efficiency = {"INJUSDT": {
            # live-proven but currently negative
            "proven_negative": {"trade_count": 55, "recent_trades": [-0.24] * 10,
                                "total_winning_usdt": -2.40, "seeded_winning_usdt": 500.0},
            # live-proven and positive
            "proven_positive": {"trade_count": 90, "recent_trades": [1.0] * 10,
                                "total_winning_usdt": 60.26, "seeded_winning_usdt": 0.0},
            # never traded — only a backtest seed
            "untested": {"trade_count": 0, "recent_trades": [],
                         "total_winning_usdt": 0.0, "seeded_winning_usdt": 0.0},
            "untested_great_seed": {"trade_count": 0, "recent_trades": [],
                                    "total_winning_usdt": 0.0, "seeded_winning_usdt": 9999.0},
        }}
        return t

    # ── the tier bug ───────────────────────────────────────────────────────── #

    def test_rank_key_carries_the_tier(self, tracker):
        assert tracker.get_preset_rank_key("INJUSDT", "proven_negative")[0] == 1
        assert tracker.get_preset_rank_key("INJUSDT", "untested")[0] == 0

    def test_proven_negative_outranks_untested_zero(self, tracker):
        """The exact live case: -2.40 with 55 trades must beat 0.00 with none."""
        ranked = tracker.ranked_presets("INJUSDT")
        assert ranked.index("proven_negative") < ranked.index("untested")

    def test_proven_negative_outranks_a_huge_seed(self, tracker):
        """A backtest guess never beats real history, however large the guess."""
        ranked = tracker.ranked_presets("INJUSDT")
        assert ranked.index("proven_negative") < ranked.index("untested_great_seed")

    def test_value_only_ordering_would_have_inverted_it(self, tracker):
        """Pins why the fix is needed: the old key puts the untested preset first."""
        old = sorted(["proven_negative", "untested"],
                     key=lambda n: tracker.get_preset_efficiency("INJUSDT", n), reverse=True)
        assert old[0] == "untested"                       # the bug
        new = sorted(["proven_negative", "untested"],
                     key=lambda n: tracker.get_preset_rank_key("INJUSDT", n), reverse=True)
        assert new[0] == "proven_negative"                # the fix

    def test_best_preset_and_ranked_presets_agree_on_the_winner(self, tracker):
        assert tracker.ranked_presets("INJUSDT")[0] == tracker.best_preset("INJUSDT")

    # ── substitution ───────────────────────────────────────────────────────── #

    def test_substitute_skips_the_excluded_best(self, tracker):
        sub = tracker.substitute_preset("INJUSDT", exclude="proven_positive")
        assert sub != "proven_positive"

    def test_substitute_never_returns_an_untested_preset(self, tracker):
        """Substitution places REAL money — it must never land on a preset with no
        trading history, whatever its backtest seed says."""
        for _ in range(3):
            sub = tracker.substitute_preset("INJUSDT", exclude="proven_positive")
            assert sub not in ("untested", "untested_great_seed")

    def test_substitute_requires_a_positive_score(self, tracker):
        """With the only profitable preset excluded, there is no valid substitute —
        it must return None rather than fall through to the least-bad option."""
        assert tracker.substitute_preset("INJUSDT", exclude="proven_positive") is None

    def test_substitute_returns_the_next_profitable_preset(self, tracker):
        tracker._efficiency["INJUSDT"]["second_good"] = {
            "trade_count": 40, "recent_trades": [0.5] * 10,
            "total_winning_usdt": 30.0, "seeded_winning_usdt": 0.0,
        }
        assert tracker.substitute_preset("INJUSDT", exclude="proven_positive") == "second_good"

    def test_substitute_honours_the_blocklist(self, tracker, monkeypatch):
        tracker._efficiency["INJUSDT"]["second_good"] = {
            "trade_count": 40, "recent_trades": [0.5] * 10,
            "total_winning_usdt": 30.0, "seeded_winning_usdt": 0.0,
        }
        monkeypatch.setattr("bot.virtual_tracker.risk_config_view",
                            lambda: {"ranking_window_size": 10,
                                     "preset_blocklist": ["second_good"]})
        assert tracker.substitute_preset("INJUSDT", exclude="proven_positive") is None

    def test_substitute_on_unknown_symbol_is_none(self, tracker):
        assert tracker.substitute_preset("NOPEUSDT", exclude=None) is None

    # ── per-symbol enablement resolution ───────────────────────────────────── #
    # Substitution value varies ~400x across symbols (+12.22/trade on INJUSDT vs
    # -0.03 on MEMEUSDT measured over Jul-Aug), so it resolves per symbol with the
    # global flag as the fallback — the same shape as max_loss_usdt_per_symbol.

    def test_defaults_to_off_when_nothing_is_configured(self):
        assert _resolve({}, "INJUSDT") is False

    def test_global_flag_applies_when_no_override(self):
        assert _resolve({"substitution_enabled": True}, "INJUSDT") is True

    def test_per_symbol_override_beats_the_global_default(self):
        cfg = {"substitution_enabled": False,
               "substitution_enabled_per_symbol": {"INJUSDT": True}}
        assert _resolve(cfg, "INJUSDT") is True
        assert _resolve(cfg, "MEMEUSDT") is False      # untouched symbols stay off

    def test_per_symbol_can_disable_against_a_global_on(self):
        """The measured case: enable broadly but keep it off where it adds noise."""
        cfg = {"substitution_enabled": True,
               "substitution_enabled_per_symbol": {"MEMEUSDT": False}}
        assert _resolve(cfg, "MEMEUSDT") is False
        assert _resolve(cfg, "INJUSDT") is True

    def test_only_the_named_symbols_are_affected(self):
        cfg = {"substitution_enabled_per_symbol": {"INJUSDT": True, "TIAUSDT": True}}
        assert [_resolve(cfg, s) for s in ("INJUSDT", "TIAUSDT")] == [True, True]
        assert [_resolve(cfg, s) for s in ("EIGENUSDT", "DOGEUSDT", "SOLUSDT")] == [False, False, False]

    # ── a manual lock must never be substituted away ────────────────────────── #
    # _try_place_order takes the locked branch and uses the LOCKED preset's settings.
    # If substitution had already replaced the recommendation, entry/TP/SL would come
    # from one preset while the trail/partial rules came from another.

    def test_locked_symbol_is_never_substituted(self):
        cfg = {"substitution_enabled": True, "locked_presets": {"INJUSDT": "oscillating_zone"}}
        assert _should_substitute(cfg, "INJUSDT") is False

    def test_lock_beats_a_per_symbol_enable(self):
        cfg = {"substitution_enabled_per_symbol": {"INJUSDT": True},
               "locked_presets": {"INJUSDT": "oscillating_zone"}}
        assert _should_substitute(cfg, "INJUSDT") is False

    def test_unlocked_symbols_still_substitute_normally(self):
        cfg = {"substitution_enabled": True, "locked_presets": {"INJUSDT": "oscillating_zone"}}
        assert _should_substitute(cfg, "EIGENUSDT") is True

    def test_removing_the_lock_restores_substitution(self):
        cfg = {"substitution_enabled": True, "locked_presets": {}}
        assert _should_substitute(cfg, "INJUSDT") is True


class TestEfficiencyWritesAreCoalesced:
    """The ~800 KB efficiency file was rewritten on every closed trade (10.7 s of a 39 s
    candle batch, 2026-09-29). Now at most once per _SAVE_INTERVAL_S; flush() writes the rest."""

    def _tracker(self, tmp_path):
        from bot.virtual_tracker import VirtualTracker
        return VirtualTracker(mode='test', orders_path=tmp_path / 'vo.json',
                              efficiency_path=tmp_path / 'eff.json')

    def test_a_burst_writes_once_and_flush_writes_the_rest(self, tmp_path, monkeypatch):
        vt = self._tracker(tmp_path)
        writes = []
        real_flush = vt.flush
        monkeypatch.setattr(vt, 'flush', lambda: (writes.append(1), real_flush())[1])
        for i in range(20):
            vt.record_closed_trade('BTCUSDT', 'p', 1.0 + i)
        assert len(writes) <= 1, 'every close still rewrote the file'
        vt.flush()
        data = json.loads((tmp_path / 'eff.json').read_text())
        assert data['BTCUSDT']['p']['trade_count'] == 20

    def test_flush_without_changes_does_not_write(self, tmp_path):
        vt = self._tracker(tmp_path)
        vt.flush()
        assert not (tmp_path / 'eff.json').exists()

    def test_main_flushes_each_candle_and_on_stop(self):
        from tests.factories import src
        assert src('main.py').count('virtual_tracker.flush()') >= 3
