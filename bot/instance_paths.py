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

Market data that a trading decision is derived from is NOT named here — it is keyed by
mode, because after a mode switch the primary must read the new market's copy:
backtest_results_name() below, and everything under data/ (orders, efficiency, klines).

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


def backtest_results_name(symbol: str, mode: str) -> str:
    """Filename for a symbol's backtest results: keyed by MARKET (mode), not instance.

    `RiskManager._compute_perf_score()` derives real-order leverage and cross-symbol
    capital allocation from this file, and the virtual tracker seeds from it. Keyed by
    instance it meant "whoever is primary": after a mode switch the primary sized orders
    from the previous market's backtest, and the mirror's copy could never be refreshed
    from the dashboard. Spec: docs/specs/2026-09-26-mode-switch-restart-and-per-mode-backtests.md
    """
    return f'backtest_results_{symbol}_{mode}.json'


def backtest_results_path(directory: Path, symbol: str, mode: str) -> Path:
    """Where to READ a symbol's backtest for `mode`. Falls back to the legacy unsuffixed
    file for test only — it was always the testnet primary's. Never across markets."""
    if mode not in ('test', 'live'):
        # Not a market (RiskManager(mode='backtest') in tools/tests): the unsuffixed file.
        return directory / f'backtest_results_{symbol}.json'
    p = directory / backtest_results_name(symbol, mode)
    if not p.exists() and mode == 'test':
        legacy = directory / f'backtest_results_{symbol}.json'
        if legacy.exists():
            return legacy
    return p
