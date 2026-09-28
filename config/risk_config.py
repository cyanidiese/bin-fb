# config/risk_config.py
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from config.safe_write import write_json

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
    # A practice (virtual) position older than this is closed with result 'max_age'.
    # Positions no longer die on a rank reshuffle, so without a ceiling one stuck trade
    # blocks its slot indefinitely — the longest observed ran 11 days. 96 x 15m = 24h.
    "virtual_max_age_candles": 96,
    "backtest_klines": 1500,
    # Run a full backtest on bot start. OFF by default: it is a blocking subprocess
    # that measured 256-567s (worst 9m11s) across 15 symbols, during which there is no
    # WebSocket, no candle processing and no position monitoring. Deploys are the main
    # source of restarts, so the cost is paid constantly for data that persists in
    # dashboard/public/backtest_results_*.json anyway. Turn it on from the dashboard
    # Settings page when a fresh backtest is actually wanted, or run one on demand from
    # the Backtest page.
    "startup_backtest": False,
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
    # Execution slippage charged against VIRTUAL PnL so it is comparable to real PnL.
    # Virtual orders open at the signalled price; real orders are MARKET orders. Measured
    # 2026-09-13 over 137 real fills: mean +0.098% adverse, never once favourable, and
    # varying ~13x by symbol (REZUSDT +0.013% to TIAUSDT +0.178%). At 5x that is ~0.49%
    # of margin per trade against a virtual edge of -0.052%/trade, so symbol-selection
    # decisions taken off unadjusted virtual stats are unsafe.
    # Charged to money only, never to TP/SL geometry — see
    # docs/specs/2026-09-13-slippage-modelling.md.
    "slippage_model_enabled": True,
    # Used until a symbol has slippage_min_samples real fills of its own. The symbols we
    # most want to judge are exactly those with no real fills, so it must not flatter them.
    "slippage_default_pct": 0.10,
    "slippage_min_samples": 5,
    # Manual per-symbol overrides, e.g. {"TIAUSDT": 0.18}. Beats the measured mean.
    "slippage_per_symbol": {},
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
    # Per trading mode: {"test": {symbol: preset}, "live": {...}}. The mirror instance
    # mounts this same file read-only, so a single shared dict forced the testnet locks
    # onto the live market — where the ranking may well differ, which is the whole
    # reason the mirror exists. A legacy flat dict is still read as the test set.
    "locked_presets": {"test": {}, "live": {}},
    # Backtest realism: scale seeded USD scores to match live leverage so Tier-0 rankings
    # are comparable with live PnL. Set to actual mean leverage once code is deployed.
    "backtest_seed_leverage_factor": 1.0,
    # Backtest realism: adverse fill slippage % applied to entry price in backtester.
    # 0.0 = exact fill (current behaviour). Set to 0.05 for typical liquid-pair fill cost.
    "backtest_entry_slippage_pct": 0.0,
    # TATS scenario — profitability gate config.
    # tats_min_profit_usdt: min recent-window sum a Tier-1 symbol must have to place real orders.
    # tats_degradation_max_drop_pct: max % decline from first-half to second-half of recent window
    #   before the symbol is treated as degrading. 0 disables the degradation check.
    "tats_min_profit_usdt": 0.0,
    "tats_degradation_max_drop_pct": 50.0,
}

_ROOT = Path(__file__).resolve().parent.parent
# The legacy single config. Nothing reads it for trading any more — it is the seed for
# risk_config_test.json (scripts/split_risk_config.py) and the rollback path. Kept under
# this name because tests and explicit-path callers import it.
_CONFIG_PATH = _ROOT / "risk_config.json"

MODES = ("test", "live")

# Keys that are the same for both modes, kept in risk_config_shared.json.
# The rule: shared = anything that shapes signals, preset ranking or virtual/backtest
# accounting, plus process settings — two instances with different values would be
# measuring different strategies. Per mode = everything that decides real orders and money.
# Spec: docs/specs/2026-09-26-shared-settings-and-per-mode-registry.md
# Must match SHARED_KEYS in dashboard/app/api/_risk-config.ts.
SHARED_KEYS = (
    # process
    "telegram", "telegram_notify_interval_s", "emergency_repeat_interval_s",
    "warning_repeat_interval_s", "analysis_log_enabled", "analysis_log_max_mb",
    "analysis_log_backups",
    # backtest method
    "startup_backtest", "backtest_klines", "backtest_initial_balance_usdt",
    "backtest_seed_leverage_factor", "backtest_entry_slippage_pct",
    # virtual accounting
    "virtual_max_age_candles", "slippage_model_enabled", "slippage_default_pct",
    "slippage_min_samples", "slippage_per_symbol",
    # signal filters
    "global_min_rr", "global_max_rr", "global_min_sl_pct", "entry_zone_max_pct",
    "global_trend_regime_filter", "global_trend_regime_lookback",
    "global_blocked_signal_types", "global_max_level", "global_correction_weight",
    "global_enforce_parent_alignment", "per_symbol_settings",
    # preset ranking
    "preset_blocklist", "ranking_window_size", "min_trades_for_ranking",
    "min_trades_for_ranking_per_symbol", "preset_hysteresis_pct", "preset_cooldown_trades",
    # trend bootstrap depth
    "analyzer_history_candles",
)

# The trading mode this process runs. main.py sets it first thing; backtest.py from
# --mode. Unset → TRADING_MODE env → test.
_active_mode: str | None = None


def set_active_mode(mode: str) -> None:
    """Pin which risk_config_{mode}.json this process reads and writes by default."""
    global _active_mode
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    _active_mode = mode


def active_mode() -> str:
    if _active_mode is not None:
        return _active_mode
    env = os.getenv("TRADING_MODE", "").strip().lower()
    return env if env in MODES else "test"


def config_path(mode: str | None = None) -> Path:
    """risk_config_{mode}.json — per trading mode, NOT per instance, so a bot_mode switch
    brings each market's own settings with it. Spec: docs/specs/2026-09-26-per-mode-risk-config.md"""
    return _ROOT / f"risk_config_{mode or active_mode()}.json"


def shared_config_path() -> Path:
    """risk_config_shared.json — the SHARED_KEYS, one value for both modes."""
    return _ROOT / "risk_config_shared.json"


def _shared_overlay() -> dict:
    """The shared file's SHARED_KEYS. Missing file → {} so the mode files' copies apply."""
    shared = _read(shared_config_path())
    return {k: shared[k] for k in SHARED_KEYS if k in shared}


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_risk_config(path: Path | None = None, mode: str | None = None) -> dict:
    """The risk config for a trading mode (default: this process's).

    Live falls back key by key to the test file: a key live lacks — one added to test
    later — reads test's value, which is the user's rule that missing live config comes
    from test. locked_presets is nested per mode ({"test": {...}} in the test file), so
    that fallback can never hand testnet's locks to live.

    SHARED_KEYS come from risk_config_shared.json and win over any copy in a mode file.

    Never creates a file: the mirror mounts its config read-only. With an explicit `path`
    the old single-file behaviour applies (tests, tools), including creating defaults.
    """
    if path is not None:
        # An explicit per-mode path is still a per-mode read: same fallback, no create.
        for m_ in MODES:
            try:
                if path.resolve() == config_path(m_).resolve():
                    return load_risk_config(mode=m_)
            except OSError:
                pass
        with _LOCK:
            if not path.exists():
                _atomic_write(path, DEFAULT_CONFIG)
                return dict(DEFAULT_CONFIG)
            try:
                return {**DEFAULT_CONFIG, **json.loads(path.read_text())}
            except Exception:
                return dict(DEFAULT_CONFIG)
    m = mode or active_mode()
    with _LOCK:
        base = _read(config_path("test")) if m == "live" else {}
        own = _read(config_path(m))
        shared = _shared_overlay()
    return {**DEFAULT_CONFIG, **base, **own, **shared}


def save_risk_config(config: dict, path: Path | None = None, mode: str | None = None) -> None:
    """Persist config to `path`, or to this mode's risk_config_{mode}.json."""
    with _LOCK:
        _atomic_write(path if path is not None else config_path(mode), config)


def _atomic_write(path: Path, data: dict) -> None:
    """Persist config, atomically where the filesystem allows it.

    This used to be tmp+rename only, which cannot work on risk_config.json inside the
    container: it is bind-mounted as a single file, so the path is itself a mount point
    and rename fails with EBUSY (verified on the server 2026-09-07). The only
    in-container caller is weight_rebalancer, which is disabled — so this would have
    started raising the day rebalancing was switched on.
    """
    write_json(path, data)


def locked_presets_for(cfg: dict, mode: str) -> dict:
    """The {symbol: preset} locks that apply to `mode`.

    Accepts both shapes. The current one is nested per mode; a legacy flat dict is read
    as the TEST set, so existing server config keeps working unchanged and the live
    instance starts with none rather than inheriting testnet's.

    Returns a copy, and never raises — this runs on the candle path, so a malformed
    config must degrade to "no locks", not take the bot down.
    """
    raw = cfg.get("locked_presets")
    if not isinstance(raw, dict):
        return {}
    if "test" in raw or "live" in raw:
        got = raw.get(mode)
        return dict(got) if isinstance(got, dict) else {}
    # legacy flat {symbol: preset} — testnet's locks, and only testnet's
    return dict(raw) if mode == "test" else {}


def get_min_trades_for_ranking(cfg: dict, symbol: str) -> int:
    """Return min trades threshold for symbol, falling back to global default."""
    per_sym = cfg.get("min_trades_for_ranking_per_symbol", {})
    return int(per_sym.get(symbol, cfg.get("min_trades_for_ranking", 3)))
