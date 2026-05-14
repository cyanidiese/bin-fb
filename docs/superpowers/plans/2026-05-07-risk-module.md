# Risk Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shared RiskManager that controls capital allocation, dynamic leverage, and drawdown protection across backtesting, paper trading, and future live trading — with a full dashboard Risk page to configure and monitor it.

**Architecture:** A single `RiskManager` class backed by `risk_config.json` (editable via dashboard) writes its runtime state to `dashboard/public/risk_state.json` (polled by the React page). The backtester receives `initial_balance` and tracks compound balance per preset; the paper trader calls `can_open_sync()` before every entry. The leverage formula combines normalised profit-pct and true profit-factor scores from the best backtest preset (≥ 4 trades, 60 s TTL cache).

**Tech Stack:** Python 3.11 (`threading.RLock`, `pathlib`, `json`), Next.js 15 App Router (TypeScript), Tailwind CSS, pytest.

**Branch:** `feature/risk-module`

**Read before starting:** `docs/superpowers/specs/2026-05-07-risk-module-design.md`

---

## Task 1: `config/risk_config.py` + `risk_config.json`

**Files:**
- Create: `config/risk_config.py`
- Create: `risk_config.json` (project root)
- Create: `tests/test_risk_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_risk_config.py
import json
import pytest
from pathlib import Path
from config.risk_config import load_risk_config, save_risk_config, DEFAULT_CONFIG


def test_load_creates_file_when_missing(tmp_path):
    p = tmp_path / "risk_config.json"
    cfg = load_risk_config(p)
    assert p.exists()
    assert cfg["base_leverage"] == 2
    assert cfg["min_profit_factor"] == 1.2
    assert len(cfg["balance_tiers"]) == 3


def test_load_merges_missing_keys(tmp_path):
    p = tmp_path / "risk_config.json"
    p.write_text(json.dumps({"base_leverage": 5}))
    cfg = load_risk_config(p)
    # New key from defaults appears
    assert "min_profit_factor" in cfg
    # Existing key preserved
    assert cfg["base_leverage"] == 5


def test_save_and_reload(tmp_path):
    p = tmp_path / "risk_config.json"
    cfg = load_risk_config(p)
    cfg["base_leverage"] = 7
    save_risk_config(cfg, p)
    cfg2 = load_risk_config(p)
    assert cfg2["base_leverage"] == 7


def test_corrupt_file_returns_defaults(tmp_path):
    p = tmp_path / "risk_config.json"
    p.write_text("not json{{{")
    cfg = load_risk_config(p)
    assert cfg["base_leverage"] == 2
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd /path/to/project && python -m pytest tests/test_risk_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'config.risk_config'`

- [ ] **Step 3: Create `config/risk_config.py`**

```python
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
```

- [ ] **Step 4: Create `risk_config.json`**

```bash
python -c "from config.risk_config import load_risk_config; load_risk_config()"
```

Expected: file `risk_config.json` appears in project root with default content.

- [ ] **Step 5: Run tests — confirm they pass**

```bash
python -m pytest tests/test_risk_config.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add config/risk_config.py risk_config.json tests/test_risk_config.py
git commit -m "feat: add risk_config module and default risk_config.json"
```

---

## Task 2: `bot/risk_manager.py` — balance, allocation, and capital gate

**Files:**
- Create: `bot/risk_manager.py` (replaces stub)
- Create: `tests/test_risk_manager.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
python -m pytest tests/test_risk_manager.py -v
```

Expected: `ImportError` or attribute errors.

- [ ] **Step 3: Write `bot/risk_manager.py`**

```python
# bot/risk_manager.py
from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

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
        mode: Literal["backtest", "paper", "live"],
        initial_balance: float = 1000.0,
        config_path: Path = _DEFAULT_CONFIG_PATH,
        state_path: Path = _DEFAULT_STATE_PATH,
        backtest_results_dir: Path = _DEFAULT_RESULTS_DIR,
    ) -> None:
        self._mode = mode
        self._config_path = config_path
        self._state_path = state_path
        self._results_dir = backtest_results_dir

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
        with self._lock:
            self._balance = balance
            if balance > self._peak_balance:
                self._peak_balance = balance
            pending = self._check_drawdown()
            self._write_snapshot()
        if pending:
            self.notify(*pending)

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

    def notify(self, event: str, payload: dict) -> None:
        """
        Log a risk event to file and stdout.

        # TODO: Telegram stub — replace print below with:
        # from bot.notifier import send_telegram
        # send_telegram(event, payload)
        """
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
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
python -m pytest tests/test_risk_manager.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/risk_manager.py tests/test_risk_manager.py
git commit -m "feat: implement RiskManager — balance, allocation, capital gate"
```

---

## Task 3: `bot/risk_manager.py` — drawdown guard tests + leverage tests

**Files:**
- Modify: `tests/test_risk_manager.py` (add drawdown and leverage tests)

- [ ] **Step 1: Add failing tests**

Append to `tests/test_risk_manager.py`:

```python
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
```

- [ ] **Step 2: Run tests — confirm new ones fail, existing pass**

```bash
python -m pytest tests/test_risk_manager.py -v
```

Expected: 7 previously passing still pass; new 8 fail.

- [ ] **Step 3: Verify the code already handles these cases**

The `_check_drawdown`, `_calc_leverage`, and `_get_perf_score` implementations from Task 2 cover all these paths. Run the tests:

```bash
python -m pytest tests/test_risk_manager.py -v
```

Expected: all 15 pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_risk_manager.py
git commit -m "test: add drawdown guard and leverage tests for RiskManager"
```

---

## Task 4: `bot/backtester.py` + `backtest.py` — backtest integration

**Files:**
- Modify: `bot/backtester.py` — add `initial_balance` + `risk_config_path` params, track compound balance per preset, apply drawdown hard-stop gate, add balance fields to output
- Modify: `backtest.py` — read `backtest_initial_balance_usdt` from risk config, pass to `Backtester`
- Modify: `tests/test_risk_manager.py` — add integration test for backtester compound balance

- [ ] **Step 1: Add failing test**

Append to `tests/test_risk_manager.py`:

```python
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
    klines = [[0, "100", "101", "99", "100", "1000"]] * 5
    results = bt.run(klines, {"default": {}})
    d = results["default"].to_dict()
    assert d["balance_start"] == 500.0
    assert d["balance_end"] == 500.0
    assert d["drawdown_triggered"] is False
```

- [ ] **Step 2: Run test — confirm it fails**

```bash
python -m pytest tests/test_risk_manager.py::test_backtester_tracks_compound_balance -v
```

Expected: `TypeError` — `Backtester` doesn't accept `initial_balance`.

- [ ] **Step 3: Modify `bot/backtester.py`**

Add parameters to `Backtester.__init__` and tracking to `_run_preset`. Change only the constructor signature and `_run_preset`; leave `PresetResult` and `Backtester.run()` mostly intact.

In `Backtester.__init__` — replace:
```python
def __init__(self, base_settings: Settings):
    self._base = base_settings
```

With:
```python
def __init__(
    self,
    base_settings: Settings,
    initial_balance: float = 0.0,
    risk_config_path: Path | None = None,
):
    self._base = base_settings
    self._initial_balance = initial_balance
    self._risk_config_path = risk_config_path
```

Add `from pathlib import Path` to imports if not already present.

In `PresetResult.to_dict()` — add three new fields at the end of the returned dict:

```python
'balance_start': round(self.balance_start, 2),
'balance_end': round(self.balance_end, 2),
'drawdown_triggered': self.drawdown_triggered,
```

Add corresponding attributes to `PresetResult.__init__`:

```python
def __init__(self, name: str):
    self.name = name
    self.trades: List[FakeOrder] = []
    self.balance_start: float = 0.0
    self.balance_end: float = 0.0
    self.drawdown_triggered: bool = False
```

In `Backtester._run_preset()` — add balance tracking. After the line `result = PresetResult(name)`, add:

```python
# Balance tracking for drawdown simulation
balance = self._initial_balance
peak_balance = balance
hard_stop_pct = 20.0  # default; overridden from config below
if self._initial_balance > 0 and self._risk_config_path is not None:
    from config.risk_config import load_risk_config
    _cfg = load_risk_config(self._risk_config_path)
    hard_stop_pct = _cfg.get("drawdown_hard_stop_pct", 20.0)
result.balance_start = balance
drawdown_triggered = False
```

After the block `result.add(open_order)` and before `open_order = None`, add:

```python
if self._initial_balance > 0:
    pct = open_order.profit_pct() or 0.0
    balance *= (1 + pct / 100.0)
    if balance > peak_balance:
        peak_balance = balance
    if peak_balance > 0:
        dd = (peak_balance - balance) / peak_balance * 100.0
        if dd >= hard_stop_pct and not drawdown_triggered:
            drawdown_triggered = True
            logger.info(
                f"  {name}: drawdown hard stop triggered "
                f"({dd:.1f}% >= {hard_stop_pct}%) at candle {i}"
            )
```

Just before `return result`, add:

```python
result.balance_end = balance
result.drawdown_triggered = drawdown_triggered
```

Add hard-stop gate before the entry block (inside `if open_order is None:`). Before the line `if i + 1 >= len(klines): continue`, add:

```python
if drawdown_triggered:
    continue
```

- [ ] **Step 4: Modify `backtest.py`**

Near the top of the file, after `from config.settings import load_settings, load_symbols`, add:

```python
from config.risk_config import load_risk_config, _CONFIG_PATH as RISK_CONFIG_PATH
```

In the section where `Backtester` is instantiated (search for `Backtester(`), change:

```python
backtester = Backtester(base_settings=settings)
```

to:

```python
risk_cfg = load_risk_config(RISK_CONFIG_PATH)
backtester = Backtester(
    base_settings=settings,
    initial_balance=risk_cfg.get("backtest_initial_balance_usdt", 0.0),
    risk_config_path=RISK_CONFIG_PATH,
)
```

- [ ] **Step 5: Run the test**

```bash
python -m pytest tests/test_risk_manager.py::test_backtester_tracks_compound_balance -v
```

Expected: PASS.

- [ ] **Step 6: Smoke-test full backtest**

```bash
python backtest.py --symbols BTCUSDT --klines-count 100 2>&1 | tail -5
```

Expected: completes without error; `dashboard/public/backtest_results_BTCUSDT.json` updated.

- [ ] **Step 7: Commit**

```bash
git add bot/backtester.py backtest.py tests/test_risk_manager.py
git commit -m "feat: integrate RiskManager into backtester — compound balance and drawdown gate"
```

---

## Task 5: `bot/paper_trader.py` + `paper_trade.py` — paper trade integration

**Files:**
- Modify: `bot/paper_trader.py` — accept `risk_manager` param; call `can_open_sync()` in `_try_open()`
- Modify: `paper_trade.py` — instantiate `RiskManager(mode="paper")`; pass to `PaperTrader`; call `update_balance()` each symbol loop

- [ ] **Step 1: Modify `bot/paper_trader.py`**

In `PaperTrader.__init__` — add `risk_manager` parameter:

```python
from bot.risk_manager import RiskManager   # add at top of file

def __init__(
    self,
    base_settings: Settings,
    presets: Dict[str, dict],
    state_path: Path,
    export_path: Path,
    risk_manager: RiskManager | None = None,   # ← new
):
    ...
    self._risk_manager = risk_manager
```

In `PaperTrader._try_open()` — add gate at the very top of the method, before all other checks:

```python
def _try_open(self, name, overrides, trend, entry_price, candle_index):
    if self._risk_manager is not None:
        allowed, reason = self._risk_manager.can_open_sync(self._base.symbol)
        if not allowed:
            logger.debug(f"[{name}] blocked by risk manager: {reason}")
            return
    # ... existing code continues unchanged ...
```

- [ ] **Step 2: Modify `paper_trade.py`**

Change `run_symbol` signature to receive `risk_manager` and pass it to `PaperTrader`:

```python
async def run_symbol(symbol: str, risk_manager: RiskManager) -> None:
    settings = load_settings(symbol)
    feed = DataFeed(settings)
    logger.info(f"[{symbol}] Loading historical klines...")
    klines = feed.load_klines(symbol, settings.timeframe, settings.kline_limit)

    trader = PaperTrader(
        base_settings=settings,
        presets=PAPER_PRESETS,
        state_path=Path(f'data/paper_state_{symbol}.json'),
        export_path=Path(f'dashboard/public/paper_results_{symbol}.json'),
        risk_manager=risk_manager,   # ← new
    )
    trader.build_from_klines(klines)
    logger.info(f"[{symbol}] Starting live stream. Press Ctrl+C to stop.")
    await feed.stream_klines(
        symbol=symbol,
        timeframe=settings.timeframe,
        on_candle_close=trader.on_candle,
        on_price_update=trader.on_price_update,
    )
```

Change `_run` to instantiate `RiskManager(mode="paper")`:

```python
from bot.risk_manager import RiskManager
from config.risk_config import load_risk_config, _CONFIG_PATH as RISK_CONFIG_PATH

async def _run(symbols: list[str]) -> None:
    write_symbols_json(symbols)
    risk_cfg = load_risk_config(RISK_CONFIG_PATH)
    risk_manager = RiskManager(mode="paper")
    logger.info(
        f"Paper trading {len(symbols)} symbol(s): {', '.join(symbols)} "
        f"— {len(PAPER_PRESETS)} presets each"
    )
    await asyncio.gather(*[run_symbol(s, risk_manager) for s in symbols])
```

- [ ] **Step 3: Verify no import errors**

```bash
python -c "import paper_trade" && echo "OK"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add bot/paper_trader.py paper_trade.py
git commit -m "feat: wire RiskManager into paper trader — can_open gate on every entry"
```

---

## Task 6: `dashboard/app/api/risk/route.ts`

**Files:**
- Create: `dashboard/app/api/risk/route.ts`

The route serves two concerns: GET returns the current config (for the page to render inputs) and the latest state snapshot (for the live panel). POST saves a new config object to disk.

- [ ] **Step 1: Create the file**

```typescript
// dashboard/app/api/risk/route.ts
import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const BOT_ROOT = path.resolve(process.cwd(), '..')
const CONFIG_PATH = path.join(BOT_ROOT, 'risk_config.json')
const STATE_PATH = path.join(BOT_ROOT, 'dashboard', 'public', 'risk_state.json')

const DEFAULT_CONFIG = {
  balance_tiers: [
    { min_balance_usdt: 0,    max_deploy_pct: 40, max_leverage_ceiling: 5  },
    { min_balance_usdt: 1000, max_deploy_pct: 50, max_leverage_ceiling: 10 },
    { min_balance_usdt: 5000, max_deploy_pct: 60, max_leverage_ceiling: 15 },
  ],
  base_leverage: 2,
  max_leverage: 10,
  min_profit_factor: 1.2,
  drawdown_warning_pct: 10.0,
  drawdown_hard_stop_pct: 20.0,
  backtest_initial_balance_usdt: 1000.0,
  symbol_weights: {} as Record<string, number>,
}

function readJson(filePath: string, fallback: unknown) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return fallback
  }
}

/** GET /api/risk — returns { config, state } */
export async function GET() {
  const config = { ...DEFAULT_CONFIG, ...readJson(CONFIG_PATH, {}) }
  const state = readJson(STATE_PATH, null)
  return NextResponse.json({ config, state })
}

/** POST /api/risk — save full config object to disk */
export async function POST(req: NextRequest) {
  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  // Validate required top-level keys exist
  const required = ['balance_tiers', 'base_leverage', 'max_leverage',
                    'min_profit_factor', 'drawdown_warning_pct',
                    'drawdown_hard_stop_pct', 'symbol_weights']
  for (const key of required) {
    if (!(key in body)) {
      return NextResponse.json({ error: `Missing field: ${key}` }, { status: 400 })
    }
  }

  try {
    const tmp = CONFIG_PATH + '.tmp'
    fs.writeFileSync(tmp, JSON.stringify(body, null, 2))
    fs.renameSync(tmp, CONFIG_PATH)
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}

/** POST /api/risk/reset-hard-stop — writes a sentinel file the bot watches */
// Note: actual hard stop reset is triggered by restarting the bot or via
// a future WebSocket command. For now the dashboard calls this and shows
// a banner to restart the paper trader.
```

- [ ] **Step 2: TypeScript check**

```bash
cd dashboard && npx tsc --noEmit 2>&1
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/api/risk/route.ts
git commit -m "feat: add /api/risk GET+POST route for config read/write"
```

---

## Task 7: `dashboard/app/risk/page.tsx` — Risk page

**Files:**
- Create: `dashboard/app/risk/page.tsx`

The page has five sections (A–E). It loads config once from `GET /api/risk`, polls `risk_state.json` every 5 s for live state, and saves config via `POST /api/risk`. Every label and input carries a `title=` tooltip.

- [ ] **Step 1: Create `dashboard/app/risk/page.tsx`**

```tsx
'use client'

import { useState, useEffect, useCallback } from 'react'
import { useSymbolContext } from '@/lib/SymbolContext'

// ── Types ─────────────────────────────────────────────────────────────────────

interface BalanceTier {
  min_balance_usdt: number
  max_deploy_pct: number
  max_leverage_ceiling: number
}

interface RiskConfig {
  balance_tiers: BalanceTier[]
  base_leverage: number
  max_leverage: number
  min_profit_factor: number
  drawdown_warning_pct: number
  drawdown_hard_stop_pct: number
  backtest_initial_balance_usdt: number
  symbol_weights: Record<string, number>
}

interface PerSymbol {
  allocation_usdt: number
  leverage: number
  performance_score: number
}

interface RiskState {
  generated_at: string
  mode: string
  balance: number
  peak_balance: number
  drawdown_pct: number
  warning_active: boolean
  hard_stop_active: boolean
  active_tier: BalanceTier
  last_event: string
  last_event_time: string
  per_symbol: Record<string, PerSymbol>
}

// ── Constants ─────────────────────────────────────────────────────────────────

const POLL_MS = 5000

const INPUT_CLS =
  'bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 text-xs font-mono ' +
  'focus:outline-none focus:border-indigo-500 disabled:opacity-40 w-24'

const SECTION_CLS = 'rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden'
const SECTION_HEADER_CLS =
  'px-4 py-2 border-b border-gray-800 text-xs font-semibold text-gray-400 uppercase tracking-wide'
const SECTION_BODY_CLS = 'px-4 py-4 space-y-4'

const SAVE_BTN_CLS =
  'px-4 py-1.5 rounded border border-indigo-700 bg-indigo-900/60 text-indigo-300 text-xs ' +
  'font-semibold hover:bg-indigo-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors'

// ── Helpers ───────────────────────────────────────────────────────────────────

function LabeledInput({
  label, tooltip, value, onChange, type = 'number', min, max, step, disabled,
}: {
  label: string; tooltip: string; value: number | string
  onChange: (v: string) => void; type?: string
  min?: number; max?: number; step?: number; disabled?: boolean
}) {
  return (
    <div className="flex items-center gap-3">
      <label
        className="text-xs text-gray-500 w-52 shrink-0"
        title={tooltip}
      >
        {label}
      </label>
      <input
        type={type}
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        title={tooltip}
        onChange={e => onChange(e.target.value)}
        className={INPUT_CLS}
      />
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RiskPage() {
  const { availableSymbols } = useSymbolContext()
  const [config, setConfig] = useState<RiskConfig | null>(null)
  const [state, setState] = useState<RiskState | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveOk, setSaveOk] = useState(false)

  // ── Load config once ────────────────────────────────────────────────────────
  useEffect(() => {
    fetch('/api/risk')
      .then(r => r.json())
      .then(({ config: cfg }) => setConfig(cfg))
      .catch(() => {})
  }, [])

  // ── Poll risk_state.json ────────────────────────────────────────────────────
  const pollState = useCallback(() => {
    fetch(`/risk_state.json?t=${Date.now()}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setState(data) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    pollState()
    const id = setInterval(pollState, POLL_MS)
    return () => clearInterval(id)
  }, [pollState])

  // ── Ensure every active symbol has a weight entry ───────────────────────────
  useEffect(() => {
    if (!config || availableSymbols.length === 0) return
    const w = { ...config.symbol_weights }
    let changed = false
    for (const sym of availableSymbols) {
      if (!(sym in w)) { w[sym] = 1; changed = true }
    }
    if (changed) setConfig(c => c ? { ...c, symbol_weights: w } : c)
  }, [availableSymbols, config?.symbol_weights])

  async function handleSave() {
    if (!config) return
    setSaving(true)
    setSaveError(null)
    setSaveOk(false)
    try {
      const res = await fetch('/api/risk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      const data = await res.json()
      if (!res.ok) { setSaveError(data.error ?? `HTTP ${res.status}`); return }
      setSaveOk(true)
      setTimeout(() => setSaveOk(false), 3000)
    } catch (e) {
      setSaveError(String(e))
    } finally {
      setSaving(false)
    }
  }

  function patchConfig(patch: Partial<RiskConfig>) {
    setConfig(c => c ? { ...c, ...patch } : c)
  }

  function patchTier(idx: number, patch: Partial<BalanceTier>) {
    if (!config) return
    const tiers = config.balance_tiers.map((t, i) => i === idx ? { ...t, ...patch } : t)
    patchConfig({ balance_tiers: tiers })
  }

  function addTier() {
    if (!config) return
    patchConfig({
      balance_tiers: [
        ...config.balance_tiers,
        { min_balance_usdt: 0, max_deploy_pct: 40, max_leverage_ceiling: 5 },
      ],
    })
  }

  function removeTier(idx: number) {
    if (!config || config.balance_tiers.length <= 1) return
    patchConfig({ balance_tiers: config.balance_tiers.filter((_, i) => i !== idx) })
  }

  const totalWeight = config
    ? Object.values(config.symbol_weights).reduce((a, b) => a + b, 0) || 1
    : 1

  if (!config) {
    return <main className="p-6 text-gray-500 text-sm">Loading risk config…</main>
  }

  return (
    <main className="p-4 space-y-6 max-w-3xl">
      {/* Title row */}
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-lg font-bold text-white">Risk Manager</h1>
        <div className="ml-auto flex items-center gap-3">
          {saveError && <span className="text-xs text-red-400 font-mono">{saveError}</span>}
          {saveOk && <span className="text-xs text-emerald-400 font-mono">Saved ✓</span>}
          <button
            onClick={handleSave}
            disabled={saving}
            title="Save all risk settings to disk. The bot picks up changes within 60 seconds."
            className={SAVE_BTN_CLS}
          >
            {saving ? 'Saving…' : 'Save All'}
          </button>
        </div>
      </div>

      {/* ── Section A — Global Capital Rules ─────────────────────────────── */}
      <section className={SECTION_CLS}>
        <p className={SECTION_HEADER_CLS} title="Controls how much of your balance is deployed across all symbols combined.">
          A — Global Capital Rules
        </p>
        <div className={SECTION_BODY_CLS}>

          {/* Active tier display */}
          {state && (
            <div className="text-xs font-mono text-gray-500 bg-gray-800/60 rounded px-3 py-2">
              <span title="The balance tier currently active, based on your live balance.">
                Active tier:
              </span>{' '}
              balance ≥ ${state.active_tier.min_balance_usdt.toLocaleString()} →{' '}
              deploy up to{' '}
              <span className="text-indigo-300">{state.active_tier.max_deploy_pct}%</span>,{' '}
              max leverage{' '}
              <span className="text-indigo-300">{state.active_tier.max_leverage_ceiling}×</span>
            </div>
          )}

          {/* Balance tiers editor */}
          <div>
            <p
              className="text-xs text-gray-500 mb-2"
              title="Define balance thresholds that unlock higher deployment caps and leverage ceilings. The highest tier whose min_balance ≤ current balance is active."
            >
              Balance tiers
            </p>
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="text-gray-600 border-b border-gray-800">
                  <th className="text-left py-1 pr-4 font-normal" title="Minimum balance in USDT to activate this tier.">Min balance (USDT)</th>
                  <th className="text-left py-1 pr-4 font-normal" title="Maximum % of available balance that may be deployed across all symbols when this tier is active.">Max deploy %</th>
                  <th className="text-left py-1 pr-4 font-normal" title="Maximum leverage ceiling for any symbol when this tier is active. Further limited by max_leverage.">Leverage ceiling</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {config.balance_tiers.map((tier, idx) => (
                  <tr key={idx} className="border-b border-gray-900">
                    <td className="py-1 pr-4">
                      <input
                        type="number" min={0} step={100}
                        value={tier.min_balance_usdt}
                        title="Minimum account balance (USDT) to activate this tier."
                        onChange={e => patchTier(idx, { min_balance_usdt: Number(e.target.value) })}
                        className={INPUT_CLS}
                      />
                    </td>
                    <td className="py-1 pr-4">
                      <input
                        type="number" min={1} max={100} step={1}
                        value={tier.max_deploy_pct}
                        title="Maximum % of available balance to deploy across all symbols when this tier is active."
                        onChange={e => patchTier(idx, { max_deploy_pct: Number(e.target.value) })}
                        className={INPUT_CLS}
                      />
                    </td>
                    <td className="py-1 pr-4">
                      <input
                        type="number" min={1} max={125} step={1}
                        value={tier.max_leverage_ceiling}
                        title="Hard cap on leverage for any symbol in this tier. Overrides max_leverage if lower."
                        onChange={e => patchTier(idx, { max_leverage_ceiling: Number(e.target.value) })}
                        className={INPUT_CLS}
                      />
                    </td>
                    <td className="py-1 text-right">
                      <button
                        onClick={() => removeTier(idx)}
                        disabled={config.balance_tiers.length <= 1}
                        title="Remove this tier. At least one tier must remain."
                        className="text-[10px] text-red-500 hover:text-red-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button
              onClick={addTier}
              title="Add a new balance tier row."
              className="mt-2 text-[10px] text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              + Add tier
            </button>
          </div>

          <LabeledInput
            label="Backtest initial balance (USDT)"
            tooltip="Starting balance used when simulating capital deployment during backtests. Higher values produce more stable drawdown percentages."
            value={config.backtest_initial_balance_usdt}
            onChange={v => patchConfig({ backtest_initial_balance_usdt: Number(v) })}
            min={10} step={100}
          />
        </div>
      </section>

      {/* ── Section B — Per-Symbol Allocation ────────────────────────────── */}
      <section className={SECTION_CLS}>
        <p className={SECTION_HEADER_CLS} title="Relative weights determine how the deployable budget is split across active symbols.">
          B — Per-Symbol Allocation
        </p>
        <div className={SECTION_BODY_CLS}>
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-600 border-b border-gray-800">
                <th className="text-left py-1 pr-4 font-normal" title="Binance USD-M Futures symbol.">Symbol</th>
                <th className="text-left py-1 pr-4 font-normal" title="Relative weight. Higher weight = larger share of deployable capital.">Weight</th>
                <th className="text-left py-1 pr-4 font-normal" title="Computed share of the deployable budget this symbol receives (weight ÷ total weights).">Alloc %</th>
                <th className="text-left py-1 pr-4 font-normal" title="Current USDT allocation for this symbol, based on live balance and active tier.">Alloc USDT</th>
                <th className="text-left py-1 pr-4 font-normal" title="Dynamic leverage currently assigned to this symbol by the risk manager.">Leverage</th>
                <th className="text-left py-1 font-normal" title="Normalised performance score (0–1) from the best backtest preset for this symbol. Drives the leverage formula.">Perf score</th>
              </tr>
            </thead>
            <tbody>
              {availableSymbols.map(sym => {
                const w = config.symbol_weights[sym] ?? 1
                const allocPct = (w / totalWeight * 100).toFixed(1)
                const live = state?.per_symbol[sym]
                return (
                  <tr key={sym} className="border-b border-gray-900 hover:bg-gray-900/40">
                    <td className="py-1.5 pr-4 text-indigo-300 font-semibold">{sym}</td>
                    <td className="py-1.5 pr-4">
                      <input
                        type="number" min={1} step={1}
                        value={w}
                        title={`Relative capital weight for ${sym}. Increase to allocate more USDT to this symbol.`}
                        onChange={e => patchConfig({
                          symbol_weights: { ...config.symbol_weights, [sym]: Number(e.target.value) },
                        })}
                        className={INPUT_CLS + ' w-16'}
                      />
                    </td>
                    <td className="py-1.5 pr-4 text-gray-400">{allocPct}%</td>
                    <td className="py-1.5 pr-4 text-gray-400">
                      {live ? `$${live.allocation_usdt.toFixed(0)}` : '—'}
                    </td>
                    <td className="py-1.5 pr-4 text-gray-400">
                      {live ? `${live.leverage}×` : '—'}
                    </td>
                    <td className="py-1.5 text-gray-400">
                      {live ? live.performance_score.toFixed(3) : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Section C — Leverage Controls ────────────────────────────────── */}
      <section className={SECTION_CLS}>
        <p className={SECTION_HEADER_CLS} title="Global leverage bounds. Actual leverage is computed per symbol from the performance score formula, then capped by the active balance tier.">
          C — Leverage Controls
        </p>
        <div className={SECTION_BODY_CLS}>
          <LabeledInput
            label="Base leverage"
            tooltip="Minimum leverage assigned to any symbol, regardless of performance score. Applied when score = 0."
            value={config.base_leverage}
            onChange={v => patchConfig({ base_leverage: Number(v) })}
            min={1} max={20} step={1}
          />
          <LabeledInput
            label="Max leverage"
            tooltip="Upper bound for leverage before the balance-tier ceiling is applied. Applied when performance score = 1."
            value={config.max_leverage}
            onChange={v => patchConfig({ max_leverage: Number(v) })}
            min={1} max={125} step={1}
          />
          <LabeledInput
            label="Min profit factor"
            tooltip="Minimum true profit factor (realized gains ÷ realized losses from best backtest preset) required to allow trading a symbol. Below this, can_open() returns False regardless of capital availability."
            value={config.min_profit_factor}
            onChange={v => patchConfig({ min_profit_factor: Number(v) })}
            min={0.1} max={10} step={0.1}
          />
          <p className="text-[10px] text-gray-600 font-mono">
            Formula: leverage = base + floor(score × (min(max, tier_ceiling) − base))
          </p>
        </div>
      </section>

      {/* ── Section D — Drawdown Guard ───────────────────────────────────── */}
      <section className={SECTION_CLS}>
        <p className={SECTION_HEADER_CLS} title="Protects capital from large losing streaks. The hard stop is latched and requires a manual reset.">
          D — Drawdown Guard
        </p>
        <div className={SECTION_BODY_CLS}>
          <LabeledInput
            label="Warning threshold %"
            tooltip="When drawdown from peak balance exceeds this %, a warning banner appears and a risk event is logged. Resets automatically when balance recovers."
            value={config.drawdown_warning_pct}
            onChange={v => patchConfig({ drawdown_warning_pct: Number(v) })}
            min={1} max={50} step={0.5}
          />
          <LabeledInput
            label="Hard stop threshold %"
            tooltip="When drawdown from peak exceeds this %, all new entries are blocked. Existing orders close naturally. Requires manual reset — does not auto-reset."
            value={config.drawdown_hard_stop_pct}
            onChange={v => patchConfig({ drawdown_hard_stop_pct: Number(v) })}
            min={1} max={100} step={0.5}
          />

          {/* Live drawdown display */}
          <div className="flex items-center gap-6 text-xs font-mono">
            <div title="Current drawdown from peak balance, updated every 5 seconds.">
              <span className="text-gray-600">Current drawdown: </span>
              <span className={
                state
                  ? state.drawdown_pct >= config.drawdown_hard_stop_pct
                    ? 'text-red-400'
                    : state.drawdown_pct >= config.drawdown_warning_pct
                    ? 'text-amber-400'
                    : 'text-emerald-400'
                  : 'text-gray-600'
              }>
                {state ? `${state.drawdown_pct.toFixed(2)}%` : '—'}
              </span>
            </div>

            {/* Hard stop indicator */}
            <div className="flex items-center gap-2" title="Hard stop status. Red = active, all new entries blocked.">
              <span className="text-gray-600">Hard stop:</span>
              <span className={`font-semibold ${state?.hard_stop_active ? 'text-red-400' : 'text-emerald-400'}`}>
                {state ? (state.hard_stop_active ? '● ACTIVE' : '● OK') : '—'}
              </span>
            </div>

            {/* Reset button — only shown when hard stop is active */}
            {state?.hard_stop_active && (
              <button
                onClick={() => {
                  if (window.confirm(
                    'Reset the hard stop latch?\n\n' +
                    'This allows new entries again. Only do this after reviewing ' +
                    'your drawdown and confirming you are ready to resume trading.'
                  )) {
                    // Write a flag file for the bot to pick up on next balance update
                    fetch('/api/risk', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ ...config, _reset_hard_stop: true }),
                    })
                  }
                }}
                title="Reset the drawdown hard stop latch. Requires confirmation. The bot must be restarted to take effect."
                className="px-3 py-1 rounded border border-red-700 bg-red-950/40 text-red-400 text-[10px] font-semibold hover:bg-red-900/40 transition-colors"
              >
                Reset drawdown guard
              </button>
            )}
          </div>

          {/* Warning banner */}
          {state?.warning_active && !state.hard_stop_active && (
            <div
              className="rounded border border-amber-700/60 bg-amber-900/20 px-3 py-2 text-xs text-amber-300 font-mono"
              title="Drawdown has crossed the warning threshold. Review your positions."
            >
              ⚠ Drawdown warning — {state.drawdown_pct.toFixed(2)}% from peak ${state.peak_balance.toFixed(2)}
            </div>
          )}

          {state?.hard_stop_active && (
            <div
              className="rounded border border-red-700/60 bg-red-900/20 px-3 py-2 text-xs text-red-300 font-mono"
              title="Hard stop is active. No new entries will be opened until it is manually reset."
            >
              ✗ HARD STOP ACTIVE — all new entries blocked. Reset to resume.
            </div>
          )}
        </div>
      </section>

      {/* ── Section E — Live Risk State ──────────────────────────────────── */}
      <section className={SECTION_CLS}>
        <p
          className={SECTION_HEADER_CLS}
          title="Read-only snapshot from risk_state.json, updated by the bot after each balance change. Polling every 5 seconds."
        >
          E — Live Risk State
          {state && (
            <span className="ml-2 text-[10px] text-gray-600 normal-case tracking-normal font-normal">
              updated {new Date(state.generated_at).toLocaleTimeString()}
            </span>
          )}
        </p>
        <div className="px-4 py-4">
          {!state ? (
            <p className="text-xs text-gray-600 italic">
              No risk_state.json yet — start the bot to generate it.
            </p>
          ) : (
            <div className="space-y-3">
              {/* Key stats */}
              <div className="flex flex-wrap gap-6 text-xs font-mono">
                {[
                  { label: 'Mode',    value: state.mode,                              color: 'text-gray-300' },
                  { label: 'Balance', value: `$${state.balance.toFixed(2)}`,          color: 'text-gray-300' },
                  { label: 'Peak',    value: `$${state.peak_balance.toFixed(2)}`,     color: 'text-gray-300' },
                  { label: 'Drawdown',value: `${state.drawdown_pct.toFixed(2)}%`,
                    color: state.drawdown_pct >= config.drawdown_hard_stop_pct
                      ? 'text-red-400'
                      : state.drawdown_pct >= config.drawdown_warning_pct
                      ? 'text-amber-400'
                      : 'text-emerald-400' },
                  { label: 'Last event', value: state.last_event || 'none',           color: 'text-gray-500' },
                ].map(s => (
                  <div key={s.label}>
                    <span className="text-gray-600">{s.label}: </span>
                    <span className={s.color}>{s.value}</span>
                  </div>
                ))}
              </div>

              {/* Raw JSON */}
              <details>
                <summary
                  className="text-[10px] text-gray-600 cursor-pointer hover:text-gray-400 transition-colors"
                  title="Expand to see the full raw risk_state.json snapshot."
                >
                  Raw snapshot
                </summary>
                <pre className="mt-2 text-[10px] text-gray-500 font-mono bg-gray-900 rounded p-3 overflow-x-auto max-h-64">
                  {JSON.stringify(state, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </div>
      </section>
    </main>
  )
}
```

- [ ] **Step 2: TypeScript check**

```bash
cd dashboard && npx tsc --noEmit 2>&1
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/risk/page.tsx
git commit -m "feat: add Risk page — sections A-E with live state polling"
```

---

## Task 8: Add Risk link to NavBar

**Files:**
- Modify: `dashboard/components/NavBar.tsx`

- [ ] **Step 1: Add the link**

In `dashboard/components/NavBar.tsx`, find the `NAV_LINKS` array:

```typescript
const NAV_LINKS = [
  { href: '/',          label: 'Strategy' },
  { href: '/backtest',  label: 'Backtest' },
  { href: '/paper',     label: 'Paper'    },
  { href: '/create',    label: 'Create'   },
  { href: '/settings',  label: 'Settings' },
]
```

Replace with:

```typescript
const NAV_LINKS = [
  { href: '/',          label: 'Strategy' },
  { href: '/backtest',  label: 'Backtest' },
  { href: '/paper',     label: 'Paper'    },
  { href: '/create',    label: 'Create'   },
  { href: '/risk',      label: 'Risk'     },
  { href: '/settings',  label: 'Settings' },
]
```

- [ ] **Step 2: TypeScript check**

```bash
cd dashboard && npx tsc --noEmit 2>&1
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/components/NavBar.tsx
git commit -m "feat: add Risk nav link"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|----------------|------|
| `RiskManager` class with async lock | Task 2 |
| `can_open_sync()` / `can_open()` | Task 2 |
| `get_leverage()` | Task 2 |
| `get_allocation()` | Task 2 |
| `notify()` with TODO Telegram stub | Task 2 |
| `snapshot()` + write `risk_state.json` | Task 2 |
| Weight-normalised allocation | Task 2 |
| Balance tiers + auto-tier selection | Task 2 |
| Leverage formula (score × range) | Task 2/3 |
| Performance score: true_pf + norm_pct | Task 2 |
| 60s TTL cache | Task 2 |
| ≥ 4 trade minimum | Task 2 |
| Drawdown warning (soft) | Task 3 |
| Hard stop (latched) | Task 3 |
| Hard stop does not force-close positions | Task 3 (can_open only) |
| `reset_hard_stop()` | Task 3 |
| Backtest: initial_balance from config | Task 4 |
| Backtest: compound balance per preset | Task 4 |
| Backtest: drawdown gate per preset | Task 4 |
| `mode` param (backtest/paper/live) | Task 2 |
| Paper trader integration | Task 5 |
| `/api/risk` GET + POST | Task 6 |
| Risk page Section A (tiers, global cap) | Task 7 |
| Risk page Section B (per-symbol) | Task 7 |
| Risk page Section C (leverage controls) | Task 7 |
| Risk page Section D (drawdown + reset) | Task 7 |
| Risk page Section E (live state) | Task 7 |
| Tooltip pattern on all controls | Task 7 |
| NavBar Risk link | Task 8 |

### Type consistency

- `RiskManager._perf_cache` stores `tuple[float, float, float]` — (score, ts, pf). `_get_perf_score` returns `tuple[float, float]` — (score, pf). Consistent.
- `can_open_sync()` returns `tuple[bool, str]`. `can_open()` wraps it. Consistent.
- `BalanceTier` dict keys match between Python (`max_deploy_pct`) and TypeScript (`max_deploy_pct`). Consistent.

### No placeholder scan

All steps contain complete code. No "TBD", "add appropriate handling", or "similar to above" patterns present.
