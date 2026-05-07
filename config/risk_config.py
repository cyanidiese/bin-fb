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
    "max_leverage": 10,
    "min_profit_factor": 1.2,
    "drawdown_warning_pct": 10.0,
    "drawdown_hard_stop_pct": 20.0,
    "backtest_initial_balance_usdt": 1000.0,
    "symbol_weights": {},
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
