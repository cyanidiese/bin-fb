"""Risk manager, leverage progression and allocation sizing.

Sections (one per former test file):
  - RiskManager core: tiers, weights, capital gate, drawdown, leverage (test_risk_manager.py)
  - unknown symbol gets base leverage       (was test_risk_manager_unknown_symbol.py)
  - peak-balance poisoning guards           (was test_risk_manager_peak_guard.py)
  - leverage scenarios                      (was test_leverage_scenario.py)
  - leverage tracker                        (was test_leverage_tracker.py)
  - TATS sole-candidate sizing              (was test_tats_sole_candidate_sizing.py)
  - unused allocation is re-offered         (was test_unused_allocation_is_reoffered.py)
"""
import json

import pytest

from bot.backtester import Backtester
from bot.leverage_scenario import (
    DefaultScenario, AllocationScenario, FirstHasMostScenario, create_scenario
)
from bot.leverage_tracker import LeverageTracker
from bot.risk_manager import RiskManager
from config.settings import load_settings
from tests.factories import src


def make_rm(tmp_path, balance=1000.0, symbol_weights=None) -> RiskManager:
    """Backtest-mode RiskManager on a two-tier config (shared by the first two sections)."""
    cfg_path = tmp_path / "risk_config.json"
    state_path = tmp_path / "risk_state.json"
    results_dir = tmp_path
    cfg = {
        "balance_tiers": [
            {"min_balance_usdt": 0,    "max_deploy_pct": 40, "max_leverage_ceiling": 5},
            {"min_balance_usdt": 1000, "max_deploy_pct": 50, "max_leverage_ceiling": 10},
        ],
        "base_leverage": 2,
        "max_leverage": 10,
        "min_profit_factor": 1.2,
        "drawdown_warning_pct": 10.0,
        "drawdown_hard_stop_pct": 20.0,
        "backtest_initial_balance_usdt": 1000.0,
        "symbol_weights": symbol_weights or {"BTCUSDT": 1, "ETHUSDT": 1},
        "min_balance_pct": 0,
    }
    cfg_path.write_text(json.dumps(cfg))
    return RiskManager(
        mode="backtest",
        initial_balance=balance,
        config_path=cfg_path,
        state_path=state_path,
        backtest_results_dir=results_dir,
    )


# =========================================================================== #
# RiskManager core (was test_risk_manager.py)                                 #
# =========================================================================== #

def _seed_perf(rm, **raw_pcts):
    """Populate _perf_cache in the CURRENT 4-tuple shape: (score, ts, pf, raw_pct).

    Leverage is driven by _get_cross_symbol_score, which normalises this symbol's
    raw_pct against every symbol in symbol_weights — so every symbol must be
    seeded, not just the one under test. These tests previously seeded 3-tuples,
    which silently fell through to the no-data path.
    """
    import time
    now = time.monotonic()
    for sym, pct in raw_pcts.items():
        rm._perf_cache[sym] = (0.0, now, 9999999999.0, pct)


class TestRiskManager:
    # ── Tier selection ────────────────────────────────────────────────────────

    def test_tier_below_1000(self, tmp_path):
        rm = make_rm(tmp_path, balance=500.0)
        assert rm.get_allocation("BTCUSDT") == pytest.approx(500 * 0.40 * 0.5)

    def test_tier_above_1000(self, tmp_path):
        rm = make_rm(tmp_path, balance=2000.0)
        # tier max_deploy_pct=50, weights equal so 50% of deployable
        assert rm.get_allocation("BTCUSDT") == pytest.approx(2000 * 0.50 * 0.5)

    # ── Symbol weights ────────────────────────────────────────────────────────

    def test_unequal_weights(self, tmp_path):
        rm = make_rm(tmp_path, balance=1000.0,
                     symbol_weights={"BTCUSDT": 2, "ETHUSDT": 1})
        # deployable=500, BTC gets 2/3, ETH gets 1/3
        assert rm.get_allocation("BTCUSDT") == pytest.approx(500 * 2 / 3)
        assert rm.get_allocation("ETHUSDT") == pytest.approx(500 * 1 / 3)

    # ── can_open_sync — capital gate ──────────────────────────────────────────

    def test_unknown_symbol_is_allowed_but_at_base_leverage(self, tmp_path):
        """Policy (see can_open_sync): pf=0.0 means "no data yet", which is treated as
        unknown rather than as a loser — otherwise a new symbol could never trade and
        so could never accumulate data. The risk is contained on the leverage side
        instead: an unknown symbol sizes at base leverage, not mid-range.
        This test previously asserted the opposite and predated that decision."""
        rm = make_rm(tmp_path, balance=1000.0)
        allowed, reason = rm.can_open_sync("BTCUSDT")
        assert allowed is True
        assert reason == ""
        assert rm.get_leverage("BTCUSDT") == 2   # base — the containment for unknowns

    def test_symbol_with_poor_profit_factor_is_blocked(self, tmp_path):
        """A symbol that HAS data and performs badly is still blocked."""
        rm = make_rm(tmp_path, balance=1000.0)
        (tmp_path / "backtest_results_BTCUSDT.json").write_text(json.dumps({
            "presets": {"p": {
                "total_trades": 50, "total_profit_pct": -5.0,
                "trades": [{"profit_pct": 1.0}] * 10 + [{"profit_pct": -5.0}] * 40,
            }}
        }))
        allowed, reason = rm.can_open_sync("BTCUSDT")
        assert allowed is False
        assert "profit_factor" in reason

    def test_can_open_passes_when_pf_ok(self, tmp_path):
        rm = make_rm(tmp_path, balance=1000.0)
        # Inject a fake cached score with acceptable pf
        rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)  # (score, ts, pf)
        allowed, reason = rm.can_open_sync("BTCUSDT")
        assert allowed is True
        assert reason == ""

    def test_can_open_blocked_by_hard_stop(self, tmp_path):
        rm = make_rm(tmp_path, balance=1000.0)
        rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)
        rm._hard_stop_active = True
        allowed, reason = rm.can_open_sync("BTCUSDT")
        assert allowed is False
        assert "hard_stop" in reason

    # ── Drawdown guard ────────────────────────────────────────────────────────

    def test_warning_fires_at_threshold(self, tmp_path, capsys):
        rm = make_rm(tmp_path, balance=1000.0)
        rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)
        rm.seed_real_balance(1000.0)  # anchor peak before any update
        # Drop balance 11% from peak (warning=10%)
        rm.update_balance(890.0)
        captured = capsys.readouterr()
        assert "drawdown_warning" in captured.out
        assert rm._warning_active is True
        assert rm._hard_stop_active is False

    def test_hard_stop_latches(self, tmp_path, capsys):
        rm = make_rm(tmp_path, balance=1000.0)
        rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)
        rm.seed_real_balance(1000.0)  # anchor peak before any update
        # Drop 21% (hard stop=20%)
        rm.update_balance(790.0)
        captured = capsys.readouterr()
        assert "hard_stop" in captured.out
        assert rm._hard_stop_active is True
        # Recovery does NOT auto-reset
        rm.update_balance(1100.0)
        assert rm._hard_stop_active is True

    def test_reset_hard_stop(self, tmp_path):
        rm = make_rm(tmp_path, balance=1000.0)
        rm.seed_real_balance(1000.0)  # anchor peak before any update
        rm.update_balance(790.0)
        assert rm._hard_stop_active is True
        rm.reset_hard_stop()
        assert rm._hard_stop_active is False
        allowed, _ = rm.can_open_sync("BTCUSDT")
        # Still blocked by profit_factor (no results file) but not by hard stop
        assert "hard_stop" not in _

    def test_warning_auto_resets_on_recovery(self, tmp_path):
        rm = make_rm(tmp_path, balance=1000.0)
        rm.seed_real_balance(1000.0)  # anchor peak before any update
        rm.update_balance(890.0)   # triggers warning
        assert rm._warning_active is True
        rm.update_balance(960.0)   # recovers above warning level (dd < 10%)
        assert rm._warning_active is False
        assert rm._hard_stop_active is False

    # ── Leverage computation ──────────────────────────────────────────────────

    def test_leverage_base_when_worst_cross_symbol(self, tmp_path):
        rm = make_rm(tmp_path, balance=1000.0)
        _seed_perf(rm, BTCUSDT=0.0, ETHUSDT=10.0)   # BTC is the worst performer
        assert rm.get_leverage("BTCUSDT") == 2      # base_leverage

    def test_leverage_max_when_best_cross_symbol(self, tmp_path):
        rm = make_rm(tmp_path, balance=2000.0)      # tier ceiling = 10
        _seed_perf(rm, BTCUSDT=10.0, ETHUSDT=0.0)   # BTC is the best performer
        assert rm.get_leverage("BTCUSDT") == 10     # min(max_leverage=10, ceiling=10)

    def test_leverage_capped_by_tier_ceiling(self, tmp_path):
        rm = make_rm(tmp_path, balance=500.0)       # tier ceiling = 5
        _seed_perf(rm, BTCUSDT=10.0, ETHUSDT=0.0)
        assert rm.get_leverage("BTCUSDT") == 5      # ceiling from balance tier

    def test_leverage_midpoint(self, tmp_path):
        rm = make_rm(tmp_path, balance=2000.0,      # base=2, ceiling=10
                     symbol_weights={"BTCUSDT": 1, "ETHUSDT": 1, "XRPUSDT": 1})
        _seed_perf(rm, BTCUSDT=5.0, ETHUSDT=0.0, XRPUSDT=10.0)
        # BTC sits midway between worst (0.0) and best (10.0):
        # (5-0)/(10-0) = 0.5 -> base + floor(0.5 * (10-2)) = 6
        assert rm.get_leverage("BTCUSDT") == 6

    def test_perf_cache_ttl(self, tmp_path, monkeypatch):
        rm = make_rm(tmp_path, balance=1000.0)
        calls = []
        original = rm._compute_perf_score
        def patched(sym):
            calls.append(sym)
            return 0.5, 1.5, 3.0        # (intra_score, pf, raw_profit_pct)
        monkeypatch.setattr(rm, "_compute_perf_score", patched)

        rm.get_leverage("BTCUSDT")
        rm.get_leverage("BTCUSDT")  # second call — should use cache
        # _get_cross_symbol_score walks every symbol in symbol_weights, so count
        # only recomputes of the symbol under test.
        assert calls.count("BTCUSDT") == 1

        # Expire the cache
        rm._perf_cache["BTCUSDT"] = (0.5, 0.0, 1.5, 3.0)  # ts=0 → always expired
        rm.get_leverage("BTCUSDT")
        assert calls.count("BTCUSDT") == 2

    def test_get_balance_returns_current_balance(self, tmp_path):
        cfg_path = tmp_path / "risk_config.json"
        cfg = {
            "balance_tiers": [
                {"min_balance_usdt": 0, "max_deploy_pct": 40, "max_leverage_ceiling": 5},
            ],
            "base_leverage": 2,
            "max_leverage": 10,
            "min_profit_factor": 1.2,
            "drawdown_warning_pct": 10.0,
            "drawdown_hard_stop_pct": 20.0,
            "backtest_initial_balance_usdt": 1000.0,
            "symbol_weights": {"BTCUSDT": 1},
        }
        cfg_path.write_text(json.dumps(cfg))
        rm = RiskManager('test', initial_balance=500.0, config_path=cfg_path, state_path=tmp_path / 's.json')
        assert rm.get_balance() == pytest.approx(500.0)
        rm.update_balance(750.0)
        assert rm.get_balance() == pytest.approx(750.0)

    def test_set_scenario_info_appears_in_snapshot(self, tmp_path):
        cfg = {
            "balance_tiers": [
                {"min_balance_usdt": 0, "max_deploy_pct": 50, "max_leverage_ceiling": 10},
            ],
            "base_leverage": 2,
            "max_leverage": 10,
            "min_profit_factor": 1.2,
            "drawdown_warning_pct": 10.0,
            "drawdown_hard_stop_pct": 20.0,
            "backtest_initial_balance_usdt": 1000.0,
            "symbol_weights": {"BTCUSDT": 1, "ETHUSDT": 1},
            "min_balance_pct": 0,
        }
        cfg_path = tmp_path / "risk_config.json"
        cfg_path.write_text(json.dumps(cfg))
        rm = RiskManager(
            mode='test',
            initial_balance=1000.0,
            config_path=cfg_path,
            state_path=tmp_path / 'risk_state.json',
            backtest_results_dir=tmp_path,
        )
        rm.set_scenario_info(
            name='allocation',
            global_level=3,
            per_symbol={'BTCUSDT': 3, 'ETHUSDT': 2},
        )
        snap = rm.snapshot()
        assert snap['scenario'] == 'allocation'
        assert snap['leverage_level'] == 3
        assert snap['per_symbol']['BTCUSDT']['leverage_level'] == 3
        assert snap['per_symbol']['ETHUSDT']['leverage_level'] == 2

    def test_backtester_tracks_compound_balance(self, tmp_path):
        """PresetResult should include balance_start, balance_end, drawdown_triggered."""
        cfg_path = tmp_path / "risk_config.json"
        cfg_path.write_text(json.dumps({
            "balance_tiers": [{"min_balance_usdt": 0, "max_deploy_pct": 100, "max_leverage_ceiling": 1}],
            "base_leverage": 1, "max_leverage": 1, "min_profit_factor": 0.0,
            "drawdown_warning_pct": 50.0, "drawdown_hard_stop_pct": 90.0,
            "backtest_initial_balance_usdt": 500.0, "symbol_weights": {},
        }))
        settings = load_settings("BTCUSDT")
        bt = Backtester(base_settings=settings, initial_balance=500.0, risk_config_path=cfg_path)
        # Minimal klines — 5 flat candles — produces 0 trades so balance unchanged
        # Format: [open_time_ms, open, high, low, close, volume, close_time_ms]
        klines = [[0, "100", "101", "99", "100", "1000", 60000]] * 5
        results = bt.run(klines, {"default": {}})
        d = results["default"].to_dict()
        assert d["balance_start"] == 500.0
        assert d["balance_end"] == 500.0
        assert d["drawdown_triggered"] is False


# =========================================================================== #
# Unknown symbol gets base leverage (was test_risk_manager_unknown_symbol.py) #
# =========================================================================== #

class TestRiskManagerUnknownSymbol:
    """A symbol with no performance data must get BASE leverage, not mid-range.

    _get_cross_symbol_score returned 0.5 when a symbol had no backtest results file,
    which produced base + floor(0.5 * (max - base)) — 6x on a 2/10 config. An
    unknown symbol is the one case where the conservative default matters most:
    it is exactly the situation where we have no evidence it can carry leverage.
    """

    def test_symbol_with_no_backtest_results_gets_base_leverage(self, tmp_path):
        """No results file at all — the riskiest unknown."""
        rm = make_rm(tmp_path)
        assert rm.get_leverage("NEWCOINUSDT") == 2

    def test_unknown_symbol_does_not_get_midrange_leverage(self, tmp_path):
        """Regression: the 0.5 fallback produced 6x on a 2/10 config."""
        rm = make_rm(tmp_path)
        assert rm.get_leverage("NEWCOINUSDT") != 6

    def test_known_good_symbol_still_scales_above_base(self, tmp_path):
        """The fix must not flatten leverage for symbols that DO have data."""
        rm = make_rm(tmp_path)
        for sym, pct in (("BTCUSDT", 20.0), ("ETHUSDT", 0.0)):
            (tmp_path / f"backtest_results_{sym}.json").write_text(json.dumps({
                "presets": {"p": {
                    "total_trades": 50, "total_profit_pct": pct,
                    "trades": [{"profit_pct": 1.0}] * 30 + [{"profit_pct": -0.5}] * 20,
                }}
            }))
        assert rm.get_leverage("BTCUSDT") > 2      # best performer scales up
        assert rm.get_leverage("ETHUSDT") == 2     # worst performer sits at base

    def test_leverage_never_below_base_or_above_ceiling(self, tmp_path):
        rm = make_rm(tmp_path)
        lev = rm.get_leverage("ANYTHINGUSDT")
        assert 2 <= lev <= 10


# =========================================================================== #
# Peak-balance poisoning guards (was test_risk_manager_peak_guard.py)         #
# =========================================================================== #

def _peak_cfg(tmp_path, **extra):
    cfg = {
        "base_leverage": 2,
        "max_leverage": 10,
        "min_profit_factor": 0.0,
        "drawdown_warning_pct": 10,
        "drawdown_hard_stop_pct": 20,
        "symbol_weights": {},
        "balance_tiers": [
            {"min_balance_usdt": 0, "max_deploy_pct": 80, "max_leverage_ceiling": 10}
        ],
    }
    cfg.update(extra)
    path = tmp_path / "risk_config.json"
    path.write_text(json.dumps(cfg))
    return path


def _peak_rm(tmp_path, **extra):
    return RiskManager(
        "test",
        initial_balance=1000.0,
        config_path=_peak_cfg(tmp_path, **extra),
        state_path=tmp_path / "risk_state.json",
    )


class TestPeakBalanceGuard:
    """Peak-balance poisoning guards (2026-08-18 incident).

    A transient totalWalletBalance reading of exactly 5000.0 raised the peak from
    3724 to 5000 while the real USDT balance was ~3044. The next reading computed a
    39.12% drawdown against that phantom peak and latched the hard stop, freezing
    all real trading for 11+ hours. Actual drawdown against the true peak was 18.3%
    — below the 20% limit.
    """

    # ----------------------------------------------------------------------- #
    # Guard 1 — implausible upward jumps must not become the peak
    # ----------------------------------------------------------------------- #

    def test_transient_spike_does_not_latch_hard_stop(self, tmp_path):
        """The exact 2026-08-18 sequence must no longer freeze the bot."""
        rm = _peak_rm(tmp_path)
        rm.seed_real_balance(3724.46)

        # Three consecutive bogus 5000.0 readings, then the true balance returns.
        for _ in range(3):
            rm.update_balance(5000.0)
        rm.update_balance(3043.94)

        snap = rm.snapshot()
        assert snap["hard_stop_active"] is False, "phantom drawdown re-latched the hard stop"
        allowed, reason = rm.can_open_sync("EIGENUSDT")
        assert allowed is True, f"trading still blocked: {reason}"

    def test_implausible_reading_leaves_peak_untouched(self, tmp_path):
        rm = _peak_rm(tmp_path, max_peak_jump_pct=20.0)
        rm.seed_real_balance(1000.0)

        rm.update_balance(5000.0)  # +400% — implausible

        assert rm.snapshot()["peak_balance"] == pytest.approx(1000.0)

    def test_plausible_gain_still_raises_peak_fully(self, tmp_path):
        """A normal winning trade must ratchet the peak exactly as before."""
        rm = _peak_rm(tmp_path, max_peak_jump_pct=20.0)
        rm.seed_real_balance(1000.0)

        rm.update_balance(1150.0)  # +15%, within the guard

        assert rm.snapshot()["peak_balance"] == pytest.approx(1150.0)

    def test_repeated_bogus_reading_never_ratchets_peak(self, tmp_path):
        """The guard must hold even when the bad value repeats many times.

        An earlier per-step-clamp version of this guard failed here: the peak
        ratcheted 3724 -> 4469 -> 5000 across three readings and the stop still
        latched. Each comparison must be made against the same clean peak.
        """
        rm = _peak_rm(tmp_path, max_peak_jump_pct=20.0)
        rm.seed_real_balance(1000.0)

        for _ in range(10):
            rm.update_balance(5000.0)

        assert rm.snapshot()["peak_balance"] == pytest.approx(1000.0)

    def test_incremental_growth_ratchets_peak_normally(self, tmp_path):
        """Ordinary compounding gains still move the peak up step by step."""
        rm = _peak_rm(tmp_path, max_peak_jump_pct=20.0)
        rm.seed_real_balance(1000.0)

        for bal in (1100.0, 1250.0, 1400.0, 1600.0):
            rm.update_balance(bal)

        assert rm.snapshot()["peak_balance"] == pytest.approx(1600.0)

    def test_balance_itself_is_never_clamped(self, tmp_path):
        """Only the peak is guarded; the reported balance stays the live reading."""
        rm = _peak_rm(tmp_path)
        rm.seed_real_balance(1000.0)

        rm.update_balance(5000.0)

        assert rm.get_balance() == pytest.approx(5000.0)

    def test_genuine_drawdown_still_fires_hard_stop(self, tmp_path):
        """The guard must not weaken the real protection."""
        rm = _peak_rm(tmp_path)
        rm.seed_real_balance(1000.0)

        rm.update_balance(750.0)  # -25% against a legitimate peak

        assert rm.snapshot()["hard_stop_active"] is True
        allowed, reason = rm.can_open_sync("EIGENUSDT")
        assert allowed is False
        assert reason == "hard_stop_active"

    # ----------------------------------------------------------------------- #
    # Guard 2 — reset_hard_stop must re-anchor the peak
    # ----------------------------------------------------------------------- #

    def test_reset_hard_stop_reanchors_peak(self, tmp_path):
        rm = _peak_rm(tmp_path)
        rm.seed_real_balance(1000.0)
        rm.update_balance(750.0)
        assert rm.snapshot()["hard_stop_active"] is True

        rm.reset_hard_stop()

        snap = rm.snapshot()
        assert snap["hard_stop_active"] is False
        assert snap["peak_balance"] == pytest.approx(750.0), "stale peak survived the reset"

    def test_reset_hard_stop_does_not_relatch_on_next_update(self, tmp_path):
        """Regression: before the fix the stop re-latched within one candle."""
        rm = _peak_rm(tmp_path)
        rm.seed_real_balance(1000.0)
        rm.update_balance(750.0)
        rm.reset_hard_stop()

        rm.update_balance(750.0)  # next candle, same balance

        assert rm.snapshot()["hard_stop_active"] is False
        assert rm.can_open_sync("EIGENUSDT")[0] is True


# =========================================================================== #
# Leverage scenarios (was test_leverage_scenario.py)                          #
# =========================================================================== #

class TestLeverageScenario:
    @pytest.fixture
    def path(self, tmp_path):
        return tmp_path / 'lev.json'

    # ── DefaultScenario ──────────────────────────────────────────────────── #

    def test_default_starts_at_level_1(self, path):
        s = DefaultScenario('test', ['BTCUSDT'], path)
        assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 1

    def test_default_does_not_advance_until_all_symbols_close(self, path):
        s = DefaultScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
        s.record_closed('BTCUSDT', 1)
        assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 1

    def test_default_advances_when_all_close(self, path):
        s = DefaultScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
        s.record_closed('BTCUSDT', 1)
        s.record_closed('ETHUSDT', 1)
        assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 2

    def test_default_capped_by_max_policy(self, path):
        s = DefaultScenario('test', ['BTCUSDT'], path, max_level=5)
        s.record_closed('BTCUSDT', 1)  # advances to 2
        # max_policy=2 caps even though level is 2
        assert s.get_leverage('BTCUSDT', 0.0, 1, 2, 10) == 2

    def test_default_get_global_level(self, path):
        s = DefaultScenario('test', ['BTCUSDT'], path)
        s.record_closed('BTCUSDT', 1)
        assert s.get_global_level() == 2

    def test_default_get_symbol_level_equals_global(self, path):
        s = DefaultScenario('test', ['BTCUSDT'], path)
        s.record_closed('BTCUSDT', 1)
        assert s.get_symbol_level('BTCUSDT') == s.get_global_level()

    def test_default_persists_and_reloads(self, path):
        s1 = DefaultScenario('test', ['BTCUSDT'], path)
        s1.record_closed('BTCUSDT', 1)
        s2 = DefaultScenario('test', ['BTCUSDT'], path)
        assert s2.get_global_level() == 2

    # ── AllocationScenario ───────────────────────────────────────────────── #

    def test_allocation_each_symbol_tracks_independently(self, path):
        s = AllocationScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
        s.record_closed('BTCUSDT', 1)
        # BTCUSDT should advance; ETHUSDT stays at 1
        assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 2
        assert s.get_leverage('ETHUSDT', 0.0, 1, 5, 10) == 1

    def test_allocation_get_symbol_level(self, path):
        s = AllocationScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
        s.record_closed('BTCUSDT', 1)
        assert s.get_symbol_level('BTCUSDT') == 2
        assert s.get_symbol_level('ETHUSDT') == 1

    def test_allocation_get_global_level_is_min(self, path):
        s = AllocationScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
        s.record_closed('BTCUSDT', 1)
        assert s.get_global_level() == 1  # min of [2, 1]

    def test_allocation_persists_and_reloads(self, path):
        s1 = AllocationScenario('test', ['BTCUSDT'], path)
        s1.record_closed('BTCUSDT', 1)
        s2 = AllocationScenario('test', ['BTCUSDT'], path)
        assert s2.get_symbol_level('BTCUSDT') == 2

    def test_allocation_new_symbol_starts_at_1(self, path):
        s = AllocationScenario('test', ['BTCUSDT'], path)
        s.record_closed('BTCUSDT', 1)  # BTCUSDT at level 2
        s.add_symbol('ETHUSDT')
        assert s.get_symbol_level('ETHUSDT') == 1

    # ── FirstHasMostScenario ─────────────────────────────────────────────── #

    def test_first_has_most_score_0_gives_base(self, path):
        s = FirstHasMostScenario()
        assert s.get_leverage('BTCUSDT', 0.0, 2, 5, 10) == 2

    def test_first_has_most_score_1_gives_max_policy(self, path):
        s = FirstHasMostScenario()
        assert s.get_leverage('BTCUSDT', 1.0, 2, 5, 10) == 5

    def test_first_has_most_score_half(self, path):
        s = FirstHasMostScenario()
        # base=2, max_policy=6 → range=4, floor(0.5*4)=2 → 2+2=4
        assert s.get_leverage('BTCUSDT', 0.5, 2, 6, 10) == 4

    def test_first_has_most_capped_by_bracket_max(self, path):
        s = FirstHasMostScenario()
        # score=1.0, base=2, max_policy=10, bracket_max=3 → min(10, 3)=3
        assert s.get_leverage('BTCUSDT', 1.0, 2, 10, 3) == 3

    def test_first_has_most_record_closed_is_noop(self, path):
        s = FirstHasMostScenario()
        s.record_closed('BTCUSDT', 5)  # must not raise

    def test_first_has_most_get_global_level_returns_0(self, path):
        s = FirstHasMostScenario()
        assert s.get_global_level() == 0

    # ── Factory ──────────────────────────────────────────────────────────── #

    def test_create_scenario_default(self, path):
        s = create_scenario('default', 'test', ['BTCUSDT'], path, 5)
        assert isinstance(s, DefaultScenario)

    def test_create_scenario_allocation(self, path):
        s = create_scenario('allocation', 'test', ['BTCUSDT'], path, 5)
        assert isinstance(s, AllocationScenario)

    def test_create_scenario_first_has_most(self, path):
        s = create_scenario('first_has_most', 'test', [], path, 5)
        assert isinstance(s, FirstHasMostScenario)

    def test_create_scenario_unknown_falls_back_to_default(self, path):
        s = create_scenario('bogus', 'test', ['BTCUSDT'], path, 5)
        assert isinstance(s, DefaultScenario)


# =========================================================================== #
# Leverage tracker (was test_leverage_tracker.py)                             #
# =========================================================================== #

class TestLeverageTracker:
    @pytest.fixture
    def path(self, tmp_path):
        return tmp_path / 'leverage_state.json'

    def test_starts_at_level_1(self, path):
        lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
        assert lt.get_current_level() == 1

    def test_does_not_advance_with_partial_graduation(self, path):
        lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
        lt.record_closed('BTCUSDT', 1)
        assert lt.get_current_level() == 1  # ETHUSDT hasn't closed level 1 yet

    def test_advances_when_all_symbols_graduate(self, path):
        lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
        lt.record_closed('BTCUSDT', 1)
        lt.record_closed('ETHUSDT', 1)
        assert lt.get_current_level() == 2

    def test_new_symbol_blocks_next_advance(self, path):
        lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
        lt.record_closed('BTCUSDT', 1)   # advances to 2
        lt.add_symbol('ETHUSDT')          # ETHUSDT must now complete level 2
        lt.record_closed('BTCUSDT', 2)
        assert lt.get_current_level() == 2  # ETHUSDT has not closed level 2

    def test_new_symbol_unblocks_after_one_close_at_current_level(self, path):
        lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
        lt.record_closed('BTCUSDT', 1)   # advances to 2
        lt.add_symbol('ETHUSDT')
        lt.record_closed('BTCUSDT', 2)
        lt.record_closed('ETHUSDT', 2)   # both at level 2 → advance to 3
        assert lt.get_current_level() == 3

    def test_remove_symbol_may_unblock_advance(self, path):
        lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
        lt.record_closed('BTCUSDT', 1)
        assert lt.get_current_level() == 1  # blocked by ETHUSDT
        lt.remove_symbol('ETHUSDT')         # only BTCUSDT needed, already done
        assert lt.get_current_level() == 2

    def test_capped_at_max_level(self, path):
        lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path, max_level=2)
        lt.record_closed('BTCUSDT', 1)   # advances to 2
        lt.record_closed('BTCUSDT', 2)   # would advance to 3, capped at max=2
        assert lt.get_current_level() == 2

    def test_persists_state_and_loads(self, path):
        lt1 = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
        lt1.record_closed('BTCUSDT', 1)
        assert lt1.get_current_level() == 2
        # Second instance loads from disk
        lt2 = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
        assert lt2.get_current_level() == 2

    def test_no_advance_with_no_active_symbols(self, path):
        lt = LeverageTracker(mode='test', active_symbols=[], data_path=path)
        assert lt.get_current_level() == 1  # stays frozen

    def test_record_closed_returns_true_when_advanced(self, path):
        lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
        advanced = lt.record_closed('BTCUSDT', 1)
        assert advanced is True

    def test_record_closed_returns_false_when_not_advanced(self, path):
        lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
        advanced = lt.record_closed('BTCUSDT', 1)
        assert advanced is False


# =========================================================================== #
# Constants shared by the two sizing sections below                           #
# =========================================================================== #

MAIN = src('main.py')
DEPLOYABLE = 2107.27   # the live deployable budget both sizing measurements were taken on


# =========================================================================== #
# TATS sole-candidate sizing (was test_tats_sole_candidate_sizing.py)         #
# =========================================================================== #
# A funded symbol must never be sized to zero — and fixing that must not resize others.
#
# TATS decides candidacy from risk_config.symbol_weights, but the single-candidate sizing
# branch computed its fraction from symbol_registry's own, unrelated weights. The two
# disagree, and on 2026-09-09 the disagreement refused real orders:
#
#     REZUSDT   risk_config weight 13 (2nd largest allocation), registry weight 0
#               -> _sym_frac = 0  ->  sym_cap = 0.00
#               -> 'balance=0.00 < margin=1.00'  ->  skip_balance
#               Measured: 42 log events across 6 distinct candles (09-08 13:00, and five
#               consecutive candles 09-09 09:00-10:00), so ~2 lost signal episodes. The
#               09:00-10:00 gap sits between two winning REZUSDT trails (+19.82 closed
#               09:00, +20.86 opened 11:15) on a day it went 3-for-3 averaging +22.00.
#
#     ETHFIUSDT risk_config weight 9, registry weight 0  -> primed to do the same
#
# Together that is 22 of 54 configured weight — 41% of allocated capital — unable to trade
# whenever it was the sole candidate. REZUSDT still placed orders because the MULTI-candidate
# branch passes `remaining` rather than a weight fraction, which is why the bug hid.
#
# WHY THE FIRST FIX (aa5f9e7) WAS TOO BROAD
# -----------------------------------------
# It switched the fraction to risk_config for EVERY symbol, which silently resized the four
# that were working (deployable 2107.27):
#
#     symbol      cfg  reg   deployed   aa5f9e7
#     INJUSDT      14    1        421       546
#     REZUSDT      13    0          0       507   <- the bug
#     ETHFIUSDT     9    0          0       351   <- the bug
#     SOLUSDT       8    1        421       312
#     EIGENUSDT     6    1        421       234
#     TIAUSDT       4    1        421       156   <- 63% cut
#
# TIAUSDT is the most productive symbol under the current locked-preset config: 9 of the 14
# post-lock real orders, +92.09 USDT, 56% win, +2.87%/trade. Cutting it 63% as a side effect
# of fixing two other symbols trades away known income for no stated reason.
#
# So the fallback is narrowed to the broken case only: registry-proportional stays wherever
# it produces a usable fraction, and risk_config is consulted only when the registry has no
# weight for a symbol risk_config funds. That is missing data, not an instruction to size at
# zero.
#
# Deliberately NOT changed: the decision to take the fraction path still reads the registry.
# Reading risk_config there would put every weight above tats_min_weight (3) and hand each
# sole candidate the whole deployable budget — measured, ~421 -> ~2107 margin, about 5x the
# observed position size. That is a risk change, not a bug fix. Logged in TODO.md.

LINES = MAIN.splitlines()


def _branch() -> str:
    """The single-candidate TATS sizing branch."""
    i = next(i for i, l in enumerate(LINES) if 'tats_min_weight: low-weight symbols' in l)
    j = next(j for j in range(i, len(LINES)) if 'bypass_pct_cap=True)' in LINES[j])
    return '\n'.join(LINES[i:j + 1])


BRANCH = _branch()


def _code() -> str:
    """The branch with comment lines stripped.

    Source-scanning assertions must read the code, not the commentary — the comment
    below mentions `_sym_frac` and `symbol_registry.get_weight` while explaining the
    bug, which made an earlier version of this test match the prose instead.
    """
    return '\n'.join(l for l in BRANCH.splitlines() if not l.lstrip().startswith('#'))


CODE = _code()

# The live configuration this was measured against. _active_ws is every enabled,
# unpaused symbol — which includes the weight-0 ones, so they belong in the fixture:
# omitting AVAXUSDT put the registry denominator at 4 and the expected share at 1/4,
# when the observed cap of 421 on a 2107.27 budget is 1/5.
CFG_W = {'INJUSDT': 14, 'REZUSDT': 13, 'ETHFIUSDT': 9,
         'SOLUSDT': 8, 'EIGENUSDT': 6, 'TIAUSDT': 4, 'AVAXUSDT': 0, 'BTCUSDT': 0}
REG_W = {'INJUSDT': 1, 'REZUSDT': 0, 'ETHFIUSDT': 0, 'SOLUSDT': 1,
         'EIGENUSDT': 1, 'TIAUSDT': 1, 'AVAXUSDT': 1, 'BTCUSDT': 0}
FUNDED = ('INJUSDT', 'REZUSDT', 'ETHFIUSDT', 'SOLUSDT', 'EIGENUSDT', 'TIAUSDT')


def frac(sym, cfg_w=None, reg_w=None):
    """The fraction the branch must produce. Mirrors the implementation."""
    cfg_w = CFG_W if cfg_w is None else cfg_w
    reg_w = REG_W if reg_w is None else reg_w
    reg_total = sum(float(w) for w in reg_w.values())
    f = (float(reg_w.get(sym, 0.0)) / reg_total) if reg_total > 0 else 1.0
    if f > 0.0:
        return f
    cfg_total = sum(float(cfg_w.get(s, 0.0)) for s in reg_w)
    return (float(cfg_w.get(sym, 0.0)) / cfg_total) if cfg_total > 0 else 0.0


class TestTheBugIsFixed:
    def test_no_funded_symbol_is_sized_to_zero(self):
        for sym in FUNDED:
            assert frac(sym) > 0.0, f'{sym} sized to zero'

    def test_rezusdt_gets_its_configured_share(self):
        """13 of 54 configured weight."""
        assert abs(frac('REZUSDT') - 13 / 54) < 1e-9   # AVAX/BTC contribute 0
        assert abs(DEPLOYABLE * frac('REZUSDT') - 507.31) < 0.5

    def test_ethfiusdt_gets_its_configured_share(self):
        assert abs(frac('ETHFIUSDT') - 9 / 54) < 1e-9

    def test_the_fallback_is_wired_to_risk_config(self):
        assert '_cfg_ws' in CODE, 'no risk_config fallback present'


class TestTheWorkingSymbolsAreUntouched:
    """The half aa5f9e7 got wrong. Every symbol the registry can size must keep the
    exact cap it had before, or this is a risk change wearing a bug fix's clothes."""

    def test_registry_sized_symbols_keep_the_flat_share(self):
        for sym in ('INJUSDT', 'SOLUSDT', 'EIGENUSDT', 'TIAUSDT'):
            assert abs(frac(sym) - 1 / 5) < 1e-9, f'{sym} was resized'

    def test_tiausdt_is_not_cut(self):
        """It earns +2.87%/trade over 9 post-lock orders — the most productive symbol."""
        cap = DEPLOYABLE * frac('TIAUSDT')
        assert cap > 400, f'TIAUSDT cut to {cap:.0f}; it was 421'

    def test_the_registry_still_supplies_the_fraction_when_it_can(self):
        i = CODE.index('_sym_frac')
        assert 'symbol_registry.get_weight' in CODE[:i], \
            'the registry no longer sizes anything'

    def test_the_fallback_is_conditional_not_unconditional(self):
        """An unconditional risk_config fraction is exactly the too-broad fix."""
        assert 'if _sym_frac' in CODE or 'if not _sym_frac' in CODE, \
            'the risk_config share is applied unconditionally'


class TestTheRiskPostureIsUnchanged:
    def test_the_path_decision_still_reads_the_registry(self):
        assert 'symbol_registry.get_weight(sym) < _tats_min_w' in BRANCH

    def test_no_cap_exceeds_what_has_been_observed(self):
        """Real orders have used 340-400 margin. A cap far above that is a risk change."""
        caps = [DEPLOYABLE * frac(s) for s in FUNDED]
        assert max(caps) < 700, f'largest cap {max(caps):.0f}'

    def test_no_cap_falls_below_min_notional(self):
        caps = [DEPLOYABLE * frac(s) for s in FUNDED]
        assert min(caps) > 50, f'smallest cap {min(caps):.0f}'

    def test_the_reason_is_recorded_for_the_next_reader(self):
        assert 'risk change, not a bug fix' in BRANCH


class TestTatsEdgeCases:
    def test_an_unfunded_symbol_still_gets_nothing(self):
        """Weight 0 means "do not trade this". It is gated earlier by the score, but if
        it ever reaches here the fraction must not resurrect it."""
        cfg = dict(CFG_W, DOGEUSDT=0)
        reg = dict(REG_W, DOGEUSDT=0)
        assert frac('DOGEUSDT', cfg, reg) == 0.0

    def test_zero_in_both_sources_does_not_divide_by_zero(self):
        assert frac('NOPEUSDT', {}, {}) in (0.0, 1.0)

    def test_an_empty_registry_does_not_divide_by_zero(self):
        assert frac('REZUSDT', CFG_W, {}) == 1.0

    def test_a_registry_that_agrees_needs_no_fallback(self):
        """If someone fixes the registry data, the fallback must go quiet, not double up."""
        reg = dict(REG_W, REZUSDT=1, ETHFIUSDT=1)   # seven of eight now weighted 1
        for sym in FUNDED:
            assert abs(frac(sym, CFG_W, reg) - 1 / 7) < 1e-9

    def test_the_guard_against_a_zero_total_is_present(self):
        assert 'else 1.0' in BRANCH


# =========================================================================== #
# Unused allocation is re-offered (was test_unused_allocation_is_reoffered.py)#
# =========================================================================== #
# A candidate that does not trade must not hold its slice of the budget hostage.
#
# The multi-candidate branches size each symbol from a STATIC fraction of the total score:
#
#     total_score = sum(max(0.0, s) for _, _, _, s in candidates)
#     ...
#     sym_cap = deployable * max(0.0, score) / total_score
#
# `remaining` correctly tracks what is still unspent (`deployable - deployed`), but `sym_cap`
# ignores it. So when the biggest-scoring candidate is refused by a later gate, its share is
# simply never offered to anyone, and every symbol behind it is sized against a budget that
# was never actually claimed.
#
# Measured 2026-09-09 19:45 (deployable 2107.27, scores are efficiency x weight):
#
#     INJUSDT     eff 428.24 x 14 = 5995   92.4%  cap ~1947  -> SKIPPED, sl_dist 16.37% > 10%
#     SOLUSDT     eff  40.32 x  8 =  322    5.0%  cap  ~105  -> placed at 96
#     ETHFIUSDT   eff  19.07 x  9 =  172    2.7%  cap   ~56  -> placed at 51
#     REZUSDT     eff   0.00 x 13 =    0      --             -> no signal
#
# 1947 USDT of budget reserved and wasted; the two symbols that did trade got 8% of the
# budget between them. Across the decision log, 8 of 9 multi-candidate candles that produced
# a placement wasted budget this way, mean 56% — so orders on those candles were sized to
# about 44% of what was available, and as low as 4%.
#
# The pattern is not random: the highest-scoring symbol is often the one with the widest
# stop, so it wins the allocation and then fails the SL gate. `skip_max_sl_pct` did this on
# TIAUSDT (3 candles) and INJUSDT (2).
#
# The fix renormalises as the loop advances — each candidate's share is measured against the
# score still ahead of it and the budget still unspent. It cannot inflate a position beyond
# what the bot already does daily, because two ceilings still apply downstream:
# max_trade_pct (30% of deployable) and max_order_notional_usdt (2000, i.e. 400 margin at
# 5x, which is exactly the 400.0 seen on every full-size order).

# The 19:45 candle, in descending score order as the loop sees it.
CANDLE = [('INJUSDT', 428.24 * 14), ('SOLUSDT', 40.32 * 8), ('ETHFIUSDT', 19.07 * 9)]
MAX_TRADE_PCT = 30.0
MAX_NOTIONAL = 2000.0
LEV = 5


def _ceilings(cap):
    """The two caps that still apply after allocation."""
    return min(cap, DEPLOYABLE * MAX_TRADE_PCT / 100.0, MAX_NOTIONAL / LEV)


def old_caps(candle, placed):
    """Static fraction of total_score — the current behaviour."""
    total = sum(s for _, s in candle)
    out, deployed = {}, 0.0
    for sym, s in candle:
        remaining = max(0.0, DEPLOYABLE - deployed)
        if remaining <= 0:
            break
        cap = _ceilings(DEPLOYABLE * s / total)
        out[sym] = cap
        if sym in placed:
            deployed += cap
    return out


def new_caps(candle, placed):
    """Renormalised against the score still ahead and the budget still unspent."""
    remaining_score = sum(s for _, s in candle)
    out, deployed = {}, 0.0
    for sym, s in candle:
        remaining = max(0.0, DEPLOYABLE - deployed)
        if remaining <= 0:
            break
        cap = _ceilings(remaining * s / remaining_score) if remaining_score > 0 else 0.0
        remaining_score = max(0.0, remaining_score - s)
        out[sym] = cap
        if sym in placed:
            deployed += cap
    return out


class TestTheMeasuredCandle:
    def test_the_old_behaviour_starves_the_symbols_that_trade(self):
        old = old_caps(CANDLE, placed={'SOLUSDT', 'ETHFIUSDT'})
        assert old['SOLUSDT'] < 120, old
        assert old['ETHFIUSDT'] < 70, old

    def test_renormalising_restores_them_to_a_normal_size(self):
        new = new_caps(CANDLE, placed={'SOLUSDT', 'ETHFIUSDT'})
        assert new['SOLUSDT'] == 400.0, new
        assert new['ETHFIUSDT'] == 400.0, new

    def test_the_skipped_leader_still_gets_its_share_offered_first(self):
        """It is not penalised — it simply cannot reserve what it does not use."""
        new = new_caps(CANDLE, placed={'SOLUSDT', 'ETHFIUSDT'})
        assert new['INJUSDT'] == 400.0

    def test_nothing_exceeds_what_the_bot_already_places(self):
        new = new_caps(CANDLE, placed={'SOLUSDT', 'ETHFIUSDT'})
        for sym, cap in new.items():
            assert cap <= 400.0 + 1e-9, f'{sym} sized above the notional ceiling'

    def test_total_deployed_stays_inside_the_budget(self):
        new = new_caps(CANDLE, placed={'SOLUSDT', 'ETHFIUSDT'})
        assert new['SOLUSDT'] + new['ETHFIUSDT'] <= DEPLOYABLE


class TestItChangesNothingWhenEveryoneTrades:
    def test_all_candidates_trading_is_unaffected_in_total(self):
        """If nobody is refused there is no unused slice, so the budget spent is the
        same — only the order in which it is handed out differs."""
        old = old_caps(CANDLE, placed={s for s, _ in CANDLE})
        new = new_caps(CANDLE, placed={s for s, _ in CANDLE})
        assert sum(old.values()) <= DEPLOYABLE
        assert sum(new.values()) <= DEPLOYABLE

    def test_a_single_candidate_is_unaffected(self):
        one = [('REZUSDT', 13 * 34.29)]
        assert new_caps(one, placed={'REZUSDT'}) == old_caps(one, placed={'REZUSDT'})

    def test_a_zero_score_candidate_gets_nothing(self):
        candle = [('INJUSDT', 5995.0), ('REZUSDT', 0.0)]
        assert new_caps(candle, placed={'INJUSDT'})['REZUSDT'] == 0.0


class TestUnusedAllocationEdgeCases:
    def test_all_scores_zero_does_not_divide_by_zero(self):
        candle = [('A', 0.0), ('B', 0.0)]
        caps = new_caps(candle, placed=set())
        assert all(c == 0.0 for c in caps.values())

    def test_the_budget_running_out_stops_the_loop(self):
        candle = [('A', 100.0), ('B', 100.0), ('C', 100.0),
                  ('D', 100.0), ('E', 100.0), ('F', 100.0)]
        caps = new_caps(candle, placed={s for s, _ in candle})
        assert sum(caps.values()) <= DEPLOYABLE + 1e-9

    def test_the_last_candidate_cannot_take_more_than_the_ceiling(self):
        """Its renormalised share is 100% of what is left, so only the ceilings bound it."""
        candle = [('BIG', 9000.0), ('LAST', 1.0)]
        caps = new_caps(candle, placed={'LAST'})
        assert caps['LAST'] <= 400.0 + 1e-9


class TestBothBranchesAreFixed:
    """TATS n>1 is the live path; BGF shares the identical bug and is fixed with it
    rather than left as a latent one, since that scenario is not currently active."""

    def test_no_branch_still_divides_by_the_static_total(self):
        assert 'deployable * max(0.0, score) / total_score' not in MAIN, \
            'a static-fraction cap remains — a skipped candidate still reserves budget'

    def test_the_running_score_is_tracked(self):
        assert MAIN.count('_remaining_score') >= 2, \
            'both multi-candidate branches must renormalise'

    def test_the_reason_is_recorded_for_the_next_reader(self):
        assert 'hold its slice' in MAIN or 'hostage' in MAIN
