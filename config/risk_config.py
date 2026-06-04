# config/risk_config.py
from __future__ import annotations

import json
import threading
from pathlib import Path

_LOCK = threading.Lock()

DEFAULT_CONFIG: dict = {
    "balance_tiers": [
        {"min_balance_usdt": 0,    "max_deploy_pct": 40, "max_leverage_ceiling": 5},
        {"min_balance_usdt": 1000, "max_deploy_pct": 50, "max_leverage_ceiling": 10},
        {"min_balance_usdt": 5000, "max_deploy_pct": 60, "max_leverage_ceiling": 15},
    ],
    "base_leverage": 2,
    "max_leverage": 20,
    "min_profit_factor": 1.2,
    "drawdown_warning_pct": 10.0,
    "drawdown_hard_stop_pct": 20.0,
    "backtest_initial_balance_usdt": 1000.0,
    "backtest_klines": 1500,
    # BGF scenario: cap allocation to top-N symbols by score (0 = no cap, use all)
    "bgf_top_n": 0,
    "symbol_weights": {},
    # Telegram alerting
    "telegram": {"token": "", "chat_id": ""},
    # Emergency thresholds — keep 15% of balance untouched
    "min_balance_pct": 15.0,
    "consecutive_failure_threshold": 3,
    # Test mode
    "test_starting_balance_usdt": 10000.0,
    # Order execution
    "price_stale_threshold_s": 15,
    "max_order_notional_usdt": 500.0,
    # Leverage progression
    "max_leverage_level": 5,
    # Allocation weighting (archived — disabled by default)
    "use_allocation_weighting": False,
    # Telegram rate limiting
    "telegram_notify_interval_s": 120,
    # Telegram content-dedup cooldowns (suppress re-sending identical message)
    "emergency_repeat_interval_s": 1800,   # 30 min
    "warning_repeat_interval_s": 14400,    # 4 hours
    "scenario": "default",
    "weight_rebalancer": {
        "enabled": False,
        "rebalance_candles": 96,
        "backtest_window_candles": 96,
        "real_pnl_alpha": 0.5,
        "blend_rate": 0.15,
        "weight_floor_ratio": 0.3,
    },
    "ranking_window_size": 10,
    "virtual_only_floor": -5.0,
    "min_trades_for_ranking": 3,
    "min_trades_for_ranking_per_symbol": {},
    # Hard floor on SL distance (% of entry). Any signal whose SL is tighter than this
    # is rejected at order-placement time, regardless of preset. 0 = disabled.
    # Prevents micro-SL orders (< noise level) from reaching real execution.
    "global_min_sl_pct": 0.3,
    # Global per-order loss cap: close any order whose unrealized loss exceeds this USDT amount.
    # 0 = disabled. Applied in both live/test trading and backtest simulation.
    "max_loss_usdt": 25.0,
    # Per-symbol USDT overrides — replace the global cap for specific symbols.
    "max_loss_usdt_per_symbol": {},
    # TP-ratio cap: also cap loss at (ratio × tp_distance_usdt) per order.
    # Takes the tighter of this and the USDT cap. 0 = disabled.
    # Example: 1.5 means "never lose more than 1.5× the potential profit on this trade."
    "max_loss_tp_ratio": 0.0,
    # Symbol → preset name. When set, bypasses virtual tracker scoring for that symbol.
    "locked_presets": {},
    # Backtest realism: scale seeded USD scores to match live leverage so Tier-0 rankings
    # are comparable with live PnL. Set to actual mean leverage once code is deployed.
    "backtest_seed_leverage_factor": 1.0,
    # Backtest realism: adverse fill slippage % applied to entry price in backtester.
    # 0.0 = exact fill (current behaviour). Set to 0.05 for typical liquid-pair fill cost.
    "backtest_entry_slippage_pct": 0.0,
}

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "risk_config.json"


def load_risk_config(path: Path = _CONFIG_PATH) -> dict:
    """Read risk_config.json, creating it with defaults if missing or corrupt."""
    with _LOCK:
        if not path.exists():
            _atomic_write(path, DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)
        try:
            data = json.loads(path.read_text())
            # Forward-compatible: new keys from DEFAULT_CONFIG appear automatically
            return {**DEFAULT_CONFIG, **data}
        except Exception:
            return dict(DEFAULT_CONFIG)


def save_risk_config(config: dict, path: Path = _CONFIG_PATH) -> None:
    """Persist config atomically (write tmp → rename)."""
    with _LOCK:
        _atomic_write(path, config)


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def get_min_trades_for_ranking(cfg: dict, symbol: str) -> int:
    """Return min trades threshold for symbol, falling back to global default."""
    per_sym = cfg.get("min_trades_for_ranking_per_symbol", {})
    return int(per_sym.get(symbol, cfg.get("min_trades_for_ranking", 3)))
