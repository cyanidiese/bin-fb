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

def test_can_open_passes_with_zero_size(tmp_path):
    # No backtest results file → profit_factor=0 < threshold → blocked
    rm = make_rm(tmp_path, balance=1000.0)
    allowed, reason = rm.can_open_sync("BTCUSDT", 0.0)
    # Blocked because no results file → performance score 0 → pf below threshold
    assert allowed is False
    assert "profit_factor" in reason


def test_can_open_passes_when_pf_ok(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0)
    # Inject a fake cached score with acceptable pf
    rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)  # (score, ts, pf)
    allowed, reason = rm.can_open_sync("BTCUSDT", 0.0)
    assert allowed is True
    assert reason == ""


def test_can_open_blocked_by_hard_stop(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0)
    rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)
    rm._hard_stop_active = True
    allowed, reason = rm.can_open_sync("BTCUSDT", 0.0)
    assert allowed is False
    assert "hard_stop" in reason


def test_can_open_blocked_by_deployment_cap(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0)
    rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)
    # Deployable = 1000*50% = 500; request 600 USDT
    allowed, reason = rm.can_open_sync("BTCUSDT", 600.0)
    assert allowed is False
    assert "deployment cap" in reason


import time as _time

# ── Drawdown guard ────────────────────────────────────────────────────────────

def test_warning_fires_at_threshold(tmp_path, capsys):
    rm = make_rm(tmp_path, balance=1000.0)
    rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)
    # Drop balance 11% from peak (warning=10%)
    rm.update_balance(890.0)
    captured = capsys.readouterr()
    assert "drawdown_warning" in captured.out
    assert rm._warning_active is True
    assert rm._hard_stop_active is False


def test_hard_stop_latches(tmp_path, capsys):
    rm = make_rm(tmp_path, balance=1000.0)
    rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)
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
    rm.update_balance(790.0)
    assert rm._hard_stop_active is True
    rm.reset_hard_stop()
    assert rm._hard_stop_active is False
    allowed, _ = rm.can_open_sync("BTCUSDT", 0.0)
    # Still blocked by profit_factor (no results file) but not by hard stop
    assert "hard_stop" not in _


def test_warning_auto_resets_on_recovery(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0)
    rm.update_balance(890.0)   # triggers warning
    assert rm._warning_active is True
    rm.update_balance(960.0)   # recovers above warning level (dd < 10%)
    assert rm._warning_active is False
    assert rm._hard_stop_active is False


# ── Leverage computation ──────────────────────────────────────────────────────

def test_leverage_base_when_score_zero(tmp_path):
    rm = make_rm(tmp_path, balance=1000.0)
    rm._perf_cache["BTCUSDT"] = (0.0, 9999999999.0, 2.0)
    lev = rm.get_leverage("BTCUSDT")
    assert lev == 2  # base_leverage


def test_leverage_max_when_score_one(tmp_path):
    rm = make_rm(tmp_path, balance=2000.0)  # tier ceiling=10
    rm._perf_cache["BTCUSDT"] = (1.0, 9999999999.0, 5.0)
    lev = rm.get_leverage("BTCUSDT")
    assert lev == 10  # min(max_leverage=10, ceiling=10)


def test_leverage_capped_by_tier_ceiling(tmp_path):
    rm = make_rm(tmp_path, balance=500.0)   # tier ceiling=5
    rm._perf_cache["BTCUSDT"] = (1.0, 9999999999.0, 5.0)
    lev = rm.get_leverage("BTCUSDT")
    assert lev == 5  # ceiling from balance tier


def test_leverage_midpoint(tmp_path):
    rm = make_rm(tmp_path, balance=2000.0)  # tier: base=2, ceiling=10
    rm._perf_cache["BTCUSDT"] = (0.5, 9999999999.0, 2.0)
    lev = rm.get_leverage("BTCUSDT")
    # base + floor(0.5 * (10 - 2)) = 2 + floor(4.0) = 6
    assert lev == 6


def test_perf_cache_ttl(tmp_path, monkeypatch):
    rm = make_rm(tmp_path, balance=1000.0)
    calls = []
    original = rm._compute_perf_score
    def patched(sym):
        calls.append(sym)
        return 0.5, 1.5
    monkeypatch.setattr(rm, "_compute_perf_score", patched)

    rm.get_leverage("BTCUSDT")
    rm.get_leverage("BTCUSDT")  # second call — should use cache
    assert len(calls) == 1

    # Expire the cache
    rm._perf_cache["BTCUSDT"] = (0.5, 0.0, 1.5)  # ts=0 → always expired
    rm.get_leverage("BTCUSDT")
    assert len(calls) == 2


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
