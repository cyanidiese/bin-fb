"""Filenames that must differ between the two bot instances.

Two processes run at once: the **primary**, which places real orders in whatever mode
the dashboard recorded, and the **mirror**, which runs the opposite mode with virtual
orders only. They share `data/`, `logs/` and `dashboard/public/` through the same Docker
volumes, so any file both would write needs a per-instance name.

The suffix is keyed on **which instance writes**, not on the mode. That distinction is
the whole point of this module:

  primary, test mode  ->  risk_state.json
  primary, live mode  ->  risk_state.json        <- still the historical name
  mirror,  live mode  ->  risk_state_live.json
  mirror,  test mode  ->  risk_state_test.json

Keying on the mode instead would look equivalent today and break the moment the primary
goes live: the mirror would then be the test-mode process and would claim the unsuffixed
names — including `dashboard/public/risk_state.json`, which it writes with a zero
balance, blanking the trading bot's risk page. Keying on the instance means the primary
owns the historical name unconditionally, so every existing reader (the dashboard,
logrotate, our own diagnostics) is untouched by a mode switch.

The mirror's suffix is its own market, so a mode flip starts a fresh file rather than
appending live-market data to a test-market one.

Lives in `bot/` rather than `main.py` because `backtest.py` and `bot/risk_manager.py`
both need it and neither can import `main`.
"""
from __future__ import annotations

from pathlib import Path


def instance_path(base: Path, name: str, mode: str, mirror: bool) -> Path:
    """Per-instance path for `name` under `base`.

    The primary gets `name` unchanged. The mirror gets the mode inserted before the
    final extension, so `risk_state.json` becomes `risk_state_live.json` and an
    extensionless `notes` becomes `notes_live`.
    """
    if not mirror:
        return base / name
    stem, dot, ext = name.rpartition('.')
    if not dot:
        return base / f'{name}_{mode}'
    return base / f'{stem}_{mode}.{ext}'


def backtest_results_name(symbol: str, mode: str, mirror: bool) -> str:
    """Filename for a symbol's backtest results.

    Split out from `instance_path` because this one file is read from three different
    places that each build the directory themselves, and getting it wrong is expensive:
    `RiskManager._compute_perf_score()` derives leverage and cross-symbol capital
    allocation from it, so a mirror writing the primary's copy would resize real orders
    from a backtest of a different market.
    """
    return instance_path(Path('.'), f'backtest_results_{symbol}.json', mode, mirror).name
