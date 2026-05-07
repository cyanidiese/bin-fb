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
