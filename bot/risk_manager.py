# bot/risk_manager.py
from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from bot.notifier import Notifier

logger = logging.getLogger(__name__)

_PERF_CACHE_TTL = 60.0   # seconds before re-reading backtest results from disk
_MIN_PRESET_TRADES = 4   # presets with fewer trades are excluded from scoring

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "risk_config.json"
_DEFAULT_STATE_PATH = _PROJECT_ROOT / "dashboard" / "public" / "risk_state.json"
_DEFAULT_RESULTS_DIR = _PROJECT_ROOT / "dashboard" / "public"


class RiskManager:
    """
    Single source of truth for capital and leverage decisions.

    Uses threading.RLock — safe in both sync and async contexts and
    re-entrant so internal helpers can call each other freely.
    """

    def __init__(
        self,
        mode: Literal["backtest", "test", "live"],
        initial_balance: float = 1000.0,
        config_path: Path = _DEFAULT_CONFIG_PATH,
        state_path: Path = _DEFAULT_STATE_PATH,
        backtest_results_dir: Path = _DEFAULT_RESULTS_DIR,
        notifier: Notifier | None = None,
    ) -> None:
        self._mode = mode
        self._config_path = config_path
        self._state_path = state_path
        self._results_dir = backtest_results_dir
        self._notifier = notifier

        self._lock = threading.RLock()

        self._balance: float = initial_balance
        self._peak_balance: float = initial_balance
        self._hard_stop_active: bool = False
        self._warning_active: bool = False
        self._last_drawdown_pct: float = 0.0

        self._last_notify_event: str = ""
        self._last_notify_time: str = ""

        # {symbol: (score: float, timestamp: float, true_pf: float)}
        self._perf_cache: dict[str, tuple[float, float, float]] = {}

        logger.info(
            f"RiskManager({mode}) — balance={initial_balance:.2f} USDT "
            f"config={config_path}"
        )

    # ------------------------------------------------------------------ #
    # Public sync interface                                                #
    # ------------------------------------------------------------------ #

    def update_balance(self, balance: float) -> None:
        """Record new balance and check drawdown thresholds."""
        pending: tuple | None = None
        min_balance_notify: tuple | None = None
        with self._lock:
            self._balance = balance
            if balance > self._peak_balance:
                self._peak_balance = balance
            pending = self._check_drawdown()

            # Check minimum balance floor
            cfg = self._load_config()
            min_balance = cfg.get("min_balance_usdt", 0.0)
            if min_balance > 0 and self._balance < min_balance:
                min_balance_notify = (
                    "emergency",
                    f"Balance below floor: {self._balance:.2f} USDT (floor: {min_balance:.2f})",
                    "risk_manager",
                )

            self._write_snapshot()
        if pending:
            self.notify(*pending)
        if min_balance_notify and self._notifier:
            level, title, source = min_balance_notify
            self._notifier.notify(level, title, "", source)

    def can_open_sync(
        self, symbol: str, estimated_size_usdt: float = 0.0
    ) -> tuple[bool, str]:
        """
        Gate for all order placement. Returns (allowed, reason).
        reason is '' when allowed=True.
        """
        with self._lock:
            cfg = self._load_config()

            if self._hard_stop_active:
                return False, "hard_stop_active"

            _, pf = self._get_perf_score(symbol, cfg)
            if pf < cfg["min_profit_factor"]:
                return (
                    False,
                    f"profit_factor={pf:.2f} below threshold={cfg['min_profit_factor']}",
                )

            if estimated_size_usdt > 0:
                tier = self._get_tier(cfg)
                max_deploy = self._balance * tier["max_deploy_pct"] / 100.0
                sym_alloc = self._calc_allocation(symbol, cfg)

                if estimated_size_usdt > max_deploy:
                    return (
                        False,
                        f"deployment cap reached "
                        f"({estimated_size_usdt:.0f} > {max_deploy:.0f} USDT, "
                        f"{tier['max_deploy_pct']}% of {self._balance:.0f})",
                    )
                if estimated_size_usdt > sym_alloc:
                    return (
                        False,
                        f"symbol allocation cap: {estimated_size_usdt:.0f} > {sym_alloc:.0f} USDT",
                    )

            return True, ""

    def get_leverage(self, symbol: str) -> int:
        """Dynamic leverage for symbol based on performance score and balance tier."""
        with self._lock:
            cfg = self._load_config()
            return self._calc_leverage(symbol, cfg)

    def get_allocation(self, symbol: str) -> float:
        """USDT amount available for this symbol at current balance."""
        with self._lock:
            cfg = self._load_config()
            return self._calc_allocation(symbol, cfg)

    def get_balance(self) -> float:
        """Current wallet balance in USDT."""
        with self._lock:
            return self._balance

    def notify(self, event: str, payload: dict) -> None:
        with self._lock:
            self._last_notify_event = event
            self._last_notify_time = datetime.now(timezone.utc).isoformat()

        msg = f"[RISK] {event}: {payload}"
        logger.warning(msg)
        print(msg, flush=True)

    def reset_hard_stop(self) -> None:
        """Clear the hard stop latch. Requires user action via the dashboard."""
        was_active = False
        with self._lock:
            was_active = self._hard_stop_active
            self._hard_stop_active = False
            self._warning_active = False
            self._write_snapshot()
        if was_active:
            logger.info("RiskManager: hard stop reset by user")
            self.notify("hard_stop_reset", {"balance": self._balance})

    def reset_for_mode_switch(self, new_mode: str) -> None:
        """Update mode after a live/test switch and refresh the state snapshot."""
        with self._lock:
            self._mode = new_mode
            self._write_snapshot()
        logger.info(f"RiskManager mode updated to {new_mode}")

    def snapshot(self) -> dict:
        """Full state dump written to risk_state.json."""
        with self._lock:
            return self._build_snapshot()

    # ------------------------------------------------------------------ #
    # Async thin wrapper                                                   #
    # ------------------------------------------------------------------ #

    async def can_open(
        self, symbol: str, estimated_size_usdt: float = 0.0
    ) -> tuple[bool, str]:
        return self.can_open_sync(symbol, estimated_size_usdt)

    # ------------------------------------------------------------------ #
    # Internal helpers (all called inside self._lock)                     #
    # ------------------------------------------------------------------ #

    def _load_config(self) -> dict:
        from config.risk_config import load_risk_config
        return load_risk_config(self._config_path)

    def _get_tier(self, cfg: dict) -> dict:
        tiers = sorted(cfg["balance_tiers"], key=lambda t: t["min_balance_usdt"])
        active = tiers[0]
        for t in tiers:
            if self._balance >= t["min_balance_usdt"]:
                active = t
        return active

    def _calc_allocation(self, symbol: str, cfg: dict) -> float:
        weights: dict = cfg.get("symbol_weights", {})
        w_sym = float(weights.get(symbol, 1))
        total_w = float(sum(weights.values())) if weights else 1.0
        if total_w == 0:
            total_w = 1.0
        tier = self._get_tier(cfg)
        deployable = self._balance * tier["max_deploy_pct"] / 100.0
        return deployable * (w_sym / total_w)

    def _calc_leverage(self, symbol: str, cfg: dict) -> int:
        tier = self._get_tier(cfg)
        ceiling = tier["max_leverage_ceiling"]
        base = cfg["base_leverage"]
        effective_max = min(cfg["max_leverage"], ceiling)
        score, _ = self._get_perf_score(symbol, cfg)
        raw = base + math.floor(score * (effective_max - base))
        return max(base, min(effective_max, raw))

    def _get_perf_score(self, symbol: str, cfg: dict) -> tuple[float, float]:
        """Returns (score, true_pf). Updates cache if stale."""
        now = time.monotonic()
        cached = self._perf_cache.get(symbol)
        if cached is not None:
            score, ts, pf = cached
            if now - ts < _PERF_CACHE_TTL:
                return score, pf
        score, pf = self._compute_perf_score(symbol)
        self._perf_cache[symbol] = (score, now, pf)
        return score, pf

    def _compute_perf_score(self, symbol: str) -> tuple[float, float]:
        """Read backtest_results_{symbol}.json and compute normalised score."""
        path = self._results_dir / f"backtest_results_{symbol}.json"
        try:
            data = json.loads(path.read_text())
        except Exception:
            return 0.0, 0.0

        presets = [
            p for p in data.get("presets", {}).values()
            if p.get("total_trades", 0) >= _MIN_PRESET_TRADES
        ]
        if not presets:
            return 0.0, 0.0

        def true_pf(p: dict) -> float:
            gp = sum((t.get("profit_pct") or 0) for t in p.get("trades", [])
                     if (t.get("profit_pct") or 0) > 0)
            gl = sum(abs(t.get("profit_pct") or 0) for t in p.get("trades", [])
                     if (t.get("profit_pct") or 0) < 0)
            return gp / gl if gl else 0.0

        best = max(presets, key=lambda p: p["total_profit_pct"])
        best_pf = true_pf(best)

        all_pcts = [p["total_profit_pct"] for p in presets]
        all_pfs = [true_pf(p) for p in presets]

        def norm(val: float, vals: list[float]) -> float:
            lo, hi = min(vals), max(vals)
            return (val - lo) / (hi - lo) if hi > lo else 1.0

        score = (norm(best["total_profit_pct"], all_pcts) + norm(best_pf, all_pfs)) / 2.0
        return max(0.0, min(1.0, score)), best_pf

    def _check_drawdown(self) -> tuple | None:
        """
        Called inside lock. Returns (event, payload) if notification should
        fire, else None. Caller must call notify() AFTER releasing the lock.
        """
        if self._peak_balance <= 0:
            return None
        dd = (self._peak_balance - self._balance) / self._peak_balance * 100.0
        self._last_drawdown_pct = dd
        cfg = self._load_config()

        if dd >= cfg["drawdown_hard_stop_pct"] and not self._hard_stop_active:
            self._hard_stop_active = True
            self._warning_active = True
            return ("hard_stop", {
                "drawdown_pct": round(dd, 2),
                "balance": self._balance,
                "peak_balance": self._peak_balance,
            })

        if dd >= cfg["drawdown_warning_pct"] and not self._warning_active:
            self._warning_active = True
            return ("drawdown_warning", {
                "drawdown_pct": round(dd, 2),
                "balance": self._balance,
                "peak_balance": self._peak_balance,
            })

        # Recover from warning when balance climbs back above warning level
        if dd < cfg["drawdown_warning_pct"] and self._warning_active and not self._hard_stop_active:
            self._warning_active = False

        return None

    def _build_snapshot(self) -> dict:
        cfg = self._load_config()
        symbols = list(cfg.get("symbol_weights", {}).keys())
        per_symbol: dict = {}
        for sym in symbols:
            cached = self._perf_cache.get(sym)
            score = cached[0] if cached else 0.0
            per_symbol[sym] = {
                "allocation_usdt": round(self._calc_allocation(sym, cfg), 2),
                "leverage": self._calc_leverage(sym, cfg),
                "performance_score": round(score, 3),
            }
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": self._mode,
            "balance": round(self._balance, 2),
            "peak_balance": round(self._peak_balance, 2),
            "drawdown_pct": round(self._last_drawdown_pct, 2),
            "warning_active": self._warning_active,
            "hard_stop_active": self._hard_stop_active,
            "active_tier": self._get_tier(cfg),
            "last_event": self._last_notify_event,
            "last_event_time": self._last_notify_time,
            "per_symbol": per_symbol,
        }

    def _write_snapshot(self) -> None:
        snap = self._build_snapshot()
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snap, indent=2))
            tmp.replace(self._state_path)
        except Exception as e:
            logger.error(f"RiskManager: snapshot write failed: {e}")
