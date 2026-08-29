"""A symbol with no performance data must get BASE leverage, not mid-range.

_get_cross_symbol_score returned 0.5 when a symbol had no backtest results file,
which produced base + floor(0.5 * (max - base)) — 6x on a 2/10 config. An
unknown symbol is the one case where the conservative default matters most:
it is exactly the situation where we have no evidence it can carry leverage.
"""
import json
import pytest

from bot.risk_manager import RiskManager


def _rm(tmp_path, balance=1000.0):
    cfg_path = tmp_path / "risk_config.json"
    cfg = {
        "balance_tiers": [
            {"min_balance_usdt": 0, "max_deploy_pct": 40, "max_leverage_ceiling": 5},
            {"min_balance_usdt": 1000, "max_deploy_pct": 50, "max_leverage_ceiling": 10},
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
    cfg_path.write_text(json.dumps(cfg))
    return RiskManager(
        mode="backtest", initial_balance=balance, config_path=cfg_path,
        state_path=tmp_path / "risk_state.json", backtest_results_dir=tmp_path,
    )


def test_symbol_with_no_backtest_results_gets_base_leverage(tmp_path):
    """No results file at all — the riskiest unknown."""
    rm = _rm(tmp_path)
    assert rm.get_leverage("NEWCOINUSDT") == 2


def test_unknown_symbol_does_not_get_midrange_leverage(tmp_path):
    """Regression: the 0.5 fallback produced 6x on a 2/10 config."""
    rm = _rm(tmp_path)
    assert rm.get_leverage("NEWCOINUSDT") != 6


def test_known_good_symbol_still_scales_above_base(tmp_path):
    """The fix must not flatten leverage for symbols that DO have data."""
    rm = _rm(tmp_path)
    for sym, pct in (("BTCUSDT", 20.0), ("ETHUSDT", 0.0)):
        (tmp_path / f"backtest_results_{sym}.json").write_text(json.dumps({
            "presets": {"p": {
                "total_trades": 50, "total_profit_pct": pct,
                "trades": [{"profit_pct": 1.0}] * 30 + [{"profit_pct": -0.5}] * 20,
            }}
        }))
    assert rm.get_leverage("BTCUSDT") > 2      # best performer scales up
    assert rm.get_leverage("ETHUSDT") == 2     # worst performer sits at base


def test_leverage_never_below_base_or_above_ceiling(tmp_path):
    rm = _rm(tmp_path)
    lev = rm.get_leverage("ANYTHINGUSDT")
    assert 2 <= lev <= 10
