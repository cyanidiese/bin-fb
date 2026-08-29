# tests/test_risk_manager.py
import json
import pytest
from pathlib import Path
from bot.risk_manager import RiskManager


def make_rm(tmp_path, balance=1000.0, symbol_weights=None) -> RiskManager:
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


# ── Tier selection ────────────────────────────────────────────────────────────

def test_tier_below_1000(tmp_path):
    rm = make_rm(tmp_path, balance=500.0)
    assert rm.get_allocation("BTCUSDT") == pytest.approx(500 * 0.40 * 0.5)


def test_tier_above_1000(tmp_path):
    rm = make_rm(tmp_path, balance=2000.0)
    # tier max_deploy_pct=50, weights equal so 50% of deployable
    assert rm.get_allocation("BTCUSDT") == pytest.approx(2000 * 0.50 * 0.5)


# ── Symbol weights ────────────────────────────────────────────────────────────

def test_unequal_weights(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0,
                 symbol_weights={"BTCUSDT": 2, "ETHUSDT": 1})
    # deployable=500, BTC gets 2/3, ETH gets 1/3
    assert rm.get_allocation("BTCUSDT") == pytest.approx(500 * 2 / 3)
    assert rm.get_allocation("ETHUSDT") == pytest.approx(500 * 1 / 3)


# ── can_open_sync — capital gate ──────────────────────────────────────────────

def test_unknown_symbol_is_allowed_but_at_base_leverage(tmp_path):
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


def test_symbol_with_poor_profit_factor_is_blocked(tmp_path):
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


def test_can_open_passes_when_pf_ok(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0)
    # Inject a fake cached score with acceptable pf
    rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)  # (score, ts, pf)
    allowed, reason = rm.can_open_sync("BTCUSDT")
    assert allowed is True
    assert reason == ""


def test_can_open_blocked_by_hard_stop(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0)
    rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)
    rm._hard_stop_active = True
    allowed, reason = rm.can_open_sync("BTCUSDT")
    assert allowed is False
    assert "hard_stop" in reason


import time as _time

# ── Drawdown guard ────────────────────────────────────────────────────────────

def test_warning_fires_at_threshold(tmp_path, capsys):
    rm = make_rm(tmp_path, balance=1000.0)
    rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)
    rm.seed_real_balance(1000.0)  # anchor peak before any update
    # Drop balance 11% from peak (warning=10%)
    rm.update_balance(890.0)
    captured = capsys.readouterr()
    assert "drawdown_warning" in captured.out
    assert rm._warning_active is True
    assert rm._hard_stop_active is False


def test_hard_stop_latches(tmp_path, capsys):
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


def test_reset_hard_stop(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0)
    rm.seed_real_balance(1000.0)  # anchor peak before any update
    rm.update_balance(790.0)
    assert rm._hard_stop_active is True
    rm.reset_hard_stop()
    assert rm._hard_stop_active is False
    allowed, _ = rm.can_open_sync("BTCUSDT")
    # Still blocked by profit_factor (no results file) but not by hard stop
    assert "hard_stop" not in _


def test_warning_auto_resets_on_recovery(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0)
    rm.seed_real_balance(1000.0)  # anchor peak before any update
    rm.update_balance(890.0)   # triggers warning
    assert rm._warning_active is True
    rm.update_balance(960.0)   # recovers above warning level (dd < 10%)
    assert rm._warning_active is False
    assert rm._hard_stop_active is False


# ── Leverage computation ──────────────────────────────────────────────────────

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


def test_leverage_base_when_worst_cross_symbol(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0)
    _seed_perf(rm, BTCUSDT=0.0, ETHUSDT=10.0)   # BTC is the worst performer
    assert rm.get_leverage("BTCUSDT") == 2      # base_leverage


def test_leverage_max_when_best_cross_symbol(tmp_path):
    rm = make_rm(tmp_path, balance=2000.0)      # tier ceiling = 10
    _seed_perf(rm, BTCUSDT=10.0, ETHUSDT=0.0)   # BTC is the best performer
    assert rm.get_leverage("BTCUSDT") == 10     # min(max_leverage=10, ceiling=10)


def test_leverage_capped_by_tier_ceiling(tmp_path):
    rm = make_rm(tmp_path, balance=500.0)       # tier ceiling = 5
    _seed_perf(rm, BTCUSDT=10.0, ETHUSDT=0.0)
    assert rm.get_leverage("BTCUSDT") == 5      # ceiling from balance tier


def test_leverage_midpoint(tmp_path):
    rm = make_rm(tmp_path, balance=2000.0,      # base=2, ceiling=10
                 symbol_weights={"BTCUSDT": 1, "ETHUSDT": 1, "XRPUSDT": 1})
    _seed_perf(rm, BTCUSDT=5.0, ETHUSDT=0.0, XRPUSDT=10.0)
    # BTC sits midway between worst (0.0) and best (10.0):
    # (5-0)/(10-0) = 0.5 -> base + floor(0.5 * (10-2)) = 6
    assert rm.get_leverage("BTCUSDT") == 6


def test_perf_cache_ttl(tmp_path, monkeypatch):
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


def test_get_balance_returns_current_balance(tmp_path):
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


def test_set_scenario_info_appears_in_snapshot(tmp_path):
    from bot.risk_manager import RiskManager
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


from bot.backtester import Backtester
from config.settings import load_settings

def test_backtester_tracks_compound_balance(tmp_path):
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
