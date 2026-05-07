# Risk Module Design

## Goal

A single `RiskManager` class shared across backtesting, paper trading, and future real trading that controls capital allocation, leverage sizing, and drawdown protection. All risk decisions flow through one place so no context requires special-casing.

---

## Architecture

### Files

| File | Action | Purpose |
|------|--------|---------|
| `config/risk_config.py` | Create | Read/write `risk_config.json`; merges missing keys with defaults on load |
| `bot/risk_manager.py` | Replace stub | Core class — all capital/leverage/drawdown logic |
| `bot/backtester.py` | Modify | Accept `initial_balance`; track compound balance per preset; apply hard-stop gate |
| `backtest.py` | Modify | Load risk config; pass `initial_balance` to Backtester; instantiate RiskManager for leverage/snapshot |
| `bot/paper_trader.py` | Modify | Call `risk_manager.can_open_sync()` before every entry attempt |
| `paper_trade.py` | Modify | Instantiate `RiskManager(mode="paper")`; wire `update_balance()` callback |
| `dashboard/app/api/risk/route.ts` | Create | GET returns config+state JSON; POST saves config to disk |
| `dashboard/app/risk/page.tsx` | Create | Risk page — Sections A–E |
| `dashboard/components/NavBar.tsx` | Modify | Add Risk link |

### New JSON files

| File | Written by | Read by |
|------|-----------|--------|
| `risk_config.json` (project root) | dashboard POST /api/risk | Python (risk_config.py) |
| `dashboard/public/risk_state.json` | Python (RiskManager.snapshot) | dashboard fetch('/risk_state.json') |

---

## Config schema — `risk_config.json`

```json
{
  "balance_tiers": [
    { "min_balance_usdt": 0,    "max_deploy_pct": 40, "max_leverage_ceiling": 5  },
    { "min_balance_usdt": 1000, "max_deploy_pct": 50, "max_leverage_ceiling": 10 },
    { "min_balance_usdt": 5000, "max_deploy_pct": 60, "max_leverage_ceiling": 15 }
  ],
  "base_leverage": 2,
  "max_leverage": 10,
  "min_profit_factor": 1.2,
  "drawdown_warning_pct": 10.0,
  "drawdown_hard_stop_pct": 20.0,
  "backtest_initial_balance_usdt": 1000.0,
  "symbol_weights": {
    "BTCUSDT": 1,
    "XAUUSDT": 1,
    "ETHUSDT": 1,
    "SOLUSDT": 1,
    "BNBUSDT": 1
  }
}
```

The file is created with these defaults on first Python run if absent. When the dashboard saves changes, it writes the full object back. New keys added in future versions are auto-merged from defaults on read.

---

## `RiskManager` public interface

```python
class RiskManager:
    def __init__(
        self,
        mode: Literal["backtest", "paper", "live"],
        initial_balance: float = 1000.0,
        config_path: Path = ...,      # project_root/risk_config.json
        state_path: Path = ...,       # dashboard/public/risk_state.json
        backtest_results_dir: Path = ...,  # dashboard/public/
    )

    # Sync (used by backtester + paper_trader directly)
    def update_balance(self, balance: float) -> None
    def can_open_sync(self, symbol: str, estimated_size_usdt: float = 0.0) -> tuple[bool, str]
    def get_leverage(self, symbol: str) -> int
    def get_allocation(self, symbol: str) -> float
    def notify(self, event: str, payload: dict) -> None
    def reset_hard_stop(self) -> None
    def snapshot(self) -> dict

    # Async thin wrapper (paper/live)
    async def can_open(self, symbol: str, estimated_size_usdt: float = 0.0) -> tuple[bool, str]
```

### Locking

`threading.RLock()` throughout — safe in both sync and async contexts, re-entrant so internal helpers can call each other without deadlock.

---

## Capital allocation logic

```
tier          = largest tier where balance >= min_balance_usdt
deployable    = balance × tier.max_deploy_pct / 100
symbol_alloc  = deployable × (weight[symbol] / sum(all weights))
```

`can_open_sync()` checks in order:
1. Hard stop active → False
2. `true_profit_factor` of best preset < `min_profit_factor` → False
3. `total_already_allocated + estimated_size > deployable` → False
4. `estimated_size > symbol_alloc` → False

In backtest mode `estimated_size_usdt=0` is always passed, so only checks 1 and 2 apply (the deployment cap is always satisfied).

---

## Leverage formula

```
score         = (norm_profit_pct + norm_true_pf) / 2       # both normalised 0–1 across all presets ≥ 4 trades
ceiling       = tier.max_leverage_ceiling
effective_max = min(config.max_leverage, ceiling)
leverage      = base_leverage + floor(score × (effective_max − base_leverage))
leverage      = clamp(leverage, base_leverage, effective_max)
```

**Best preset** selected by highest `total_profit_pct` among presets with ≥ 4 trades.

**True profit factor** = `sum(positive profit_pct trades) / sum(abs(negative profit_pct trades))` computed from `result.trades` array in `backtest_results_{SYMBOL}.json`.

Performance score is **cached 60 s per symbol** — re-read from disk on cache miss so new backtests are picked up automatically within one minute.

---

## Drawdown guard

```
drawdown_pct = (peak_balance − balance) / peak_balance × 100
```

| Threshold | Action | Reset behaviour |
|-----------|--------|----------------|
| `drawdown_warning_pct` | `notify("drawdown_warning", ...)`, set `warning_active=True` | Auto-resets if balance recovers above warning level |
| `drawdown_hard_stop_pct` | `notify("hard_stop", ...)`, set `hard_stop_active=True`, `can_open_sync()` → False | **Latched** — requires explicit `reset_hard_stop()` call |

Hard stop does NOT force-close open orders. Existing positions run to their natural TP/SL.

---

## Backtester integration

`Backtester.__init__` gains `initial_balance: float = 0.0`. When `> 0`, each `_run_preset()` call:
- Tracks `running_balance` compounding per trade: `balance *= (1 + trade.profit_pct() / 100)`
- Gates new entries when `running_balance` drawdown from peak ≥ `hard_stop_pct` (read from risk config)
- Adds `balance_start`, `balance_end`, `drawdown_triggered` to `PresetResult.to_dict()`

No cross-symbol capital gates in backtester — each preset is evaluated independently.

---

## Paper trader integration

In `PaperTrader._try_open()`, before opening a `FakeOrder`:
```python
allowed, reason = self._risk_manager.can_open_sync(self._base.symbol)
if not allowed:
    logger.info(f"[{name}] entry blocked by risk manager: {reason}")
    return
```

`paper_trade.py` calls `risk_manager.update_balance(balance)` after fetching the testnet account balance (added to the per-symbol coroutine).

---

## Dashboard — Risk page

Route: `/risk`  
Data: polls `fetch('/risk_state.json?t=…')` every 5 s; config loaded once from `GET /api/risk`.

### Sections

**A — Global Capital Rules**: deployment cap display (shows active tier), balance tiers editor (table, add/remove rows), backtest initial balance.

**B — Per-Symbol Allocation**: active symbols table — weight (editable), computed allocation % and USDT, current leverage (read-only, from state), performance score (read-only).

**C — Leverage Controls**: base leverage, max leverage, min profit factor threshold.

**D — Drawdown Guard**: warning %, hard stop %, current drawdown (live), hard stop indicator, Reset button with confirmation.

**E — Live Risk State**: read-only JSON-style panel from `risk_state.json` — balance, peak, drawdown, last event.

All inputs and labels carry `title=` tooltip attributes matching the existing dashboard pattern.

---

## `risk_state.json` shape

```json
{
  "generated_at": "2026-05-07T12:00:00Z",
  "mode": "paper",
  "balance": 1234.56,
  "peak_balance": 1300.00,
  "drawdown_pct": 5.03,
  "warning_active": false,
  "hard_stop_active": false,
  "active_tier": { "min_balance_usdt": 1000, "max_deploy_pct": 50, "max_leverage_ceiling": 10 },
  "last_event": "drawdown_warning",
  "last_event_time": "2026-05-07T11:45:00Z",
  "per_symbol": {
    "BTCUSDT": { "allocation_usdt": 123.46, "leverage": 4, "performance_score": 0.52 }
  }
}
```
