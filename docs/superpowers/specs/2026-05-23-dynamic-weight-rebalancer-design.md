# Dynamic Weight Rebalancer — Design Spec

**Date**: 2026-05-23  
**Status**: Approved  
**Feature**: Periodically recompute symbol allocation weights based on recent live performance

---

## Overview

The bot currently allocates capital between symbols using `symbol_weights` in `risk_config.json`. These weights are static — set manually or via the dashboard and never updated at runtime.

This feature adds a `WeightRebalancer` that runs every N closed candles, scores each active symbol on two signals (a mini-backtest on recent klines + real closed P&L from the same window), and soft-blends the current weights toward the new scores. Better-performing symbols gradually accumulate more allocation; underperformers drift down but are protected by a configurable floor.

This is additive to the existing BGF ordering (which controls *who gets capital first in the queue*). The rebalancer controls *how much* each symbol's slice is.

---

## Scoring Formula

Every `rebalance_candles` closed candles, for each active symbol:

### Signal 1 — Mini-backtest profit %

Slice `klines[-backtest_window_candles:]` from the already-loaded cache (no REST fetch needed). Run `Backtester` in-process on that slice across all active presets. Take the **best preset's `total_profit_pct`** for that window.

### Signal 2 — Real closed P&L (USDT)

Sum `profit_usdt` across all entries in `data/real_orders_{symbol}_{mode}.json` where `close_time` falls within the same N-candle window (using the candle timestamps to bound the window).

### Combination

Both signals are **rank-normalized** across active symbols independently:

```
rank_score = (n_symbols - rank) / (n_symbols - 1)   # rank 1 (best) → 1.0, last → 0.0
```

Rank normalization is used (not min-max) to be robust against outliers — one symbol with an extreme result doesn't compress all others to near-zero.

```
raw_score[symbol] = real_pnl_alpha × rank_score_real[symbol]
                  + (1 - real_pnl_alpha) × rank_score_backtest[symbol]
```

Default `real_pnl_alpha = 0.5` (equal blend of both signals).

**Edge cases:**
- Symbol with 0 real orders in window: real P&L = 0 USDT, treated as tied-last in real P&L ranking.
- All symbols have the same real P&L (e.g. all 0): real P&L ranks are tied; all receive score 0.5 for that signal — backtest signal dominates.
- Only one active symbol: no rebalance (weights are irrelevant with a single symbol).

### Soft blend

```
new_weight[symbol] = (1 - blend_rate) × old_weight[symbol]
                   + blend_rate × raw_score[symbol]
```

Default `blend_rate = 0.15` — weights drift at most 15% of the gap per cycle.

### Floor clamp

```
floor = weight_floor_ratio / n_active_symbols
new_weight[symbol] = max(floor, new_weight[symbol])
```

Default `weight_floor_ratio = 0.3` → floor = 0.3 × equal_share. No symbol drops below 30% of its equal-share allocation.

### Renormalize

After clamping, renormalize all weights to sum to 1.0:

```
new_weight[symbol] /= sum(new_weight.values())
```

Persist to `risk_config.json` under `symbol_weights`. `RiskManager._calc_allocation()` already reads from there on every call — no other changes needed.

---

## New Module: `bot/weight_rebalancer.py`

### Class: `WeightRebalancer`

```python
WeightRebalancer(
    symbol_registry: SymbolRegistry,
    risk_manager: RiskManager,
    get_klines_fn: Callable[[str], list],  # lambda sym: analyzers[sym].get_klines()
    candle_duration_ms: int,               # from _tf_to_ms(timeframe) in main.py
    mode: str,                             # "test" or "live"
    risk_config_path: str,
    cfg: dict,                             # "weight_rebalancer" section from risk_config
)
```

### Methods

**`on_candle_close(candle_ts: int) -> None`**

Called from `main.py` once per closed candle (after all per-symbol loops). Increments internal counter. When `counter % rebalance_candles == 0`, checks if a rebalance is already running (atomic `threading.Event` flag). If not, spawns `threading.Thread(daemon=True, target=self._run, args=(candle_ts,))`.

**`_run(trigger_ts: int) -> None`**

Runs in background thread. Steps:
1. Collect active symbols from `symbol_registry` (non-disabled, weight > 0).
2. If fewer than 2 active symbols, log and return.
3. For each symbol: call `_score(symbol, trigger_ts)` → `(backtest_pct, real_pnl_usdt)`.
4. Call `_blend_and_save(scores, trigger_ts)`.
5. Clear the running flag.

**`_score(symbol: str, trigger_ts: int) -> tuple[float, float]`**

- Calls `get_klines_fn(symbol)` (backed by `analyzers[symbol].get_klines()` — already in memory, no I/O).
- Slices `klines[-backtest_window_candles:]`.
- Instantiates `Backtester(symbol, klines_slice, ALL_PRESETS)` and calls `.run()`.
- Takes `best_total_profit_pct = max(r["total_profit_pct"] for r in results)`.
- Loads `data/real_orders_{symbol}_{mode}.json` (reads file once, skips if missing).
- Computes window start timestamp: `trigger_ts - rebalance_candles × self._candle_duration_ms`.
- Sums `profit_usdt` for orders where `close_time >= window_start`.
- Returns `(best_total_profit_pct, real_pnl_usdt)`.

**`_blend_and_save(scores: dict, trigger_ts: int) -> None`**

- Rank-normalizes backtest scores and real P&L scores separately.
- Combines into `raw_score` per symbol.
- Reads current weights from `risk_manager` (via `risk_config.json`).
- Applies soft blend, floor clamp, renormalize.
- Writes updated `symbol_weights` back to `risk_config.json` via `risk_config.save()` (atomic write).
- Appends entry to `data/weight_rebalance_log_{mode}.json` (capped at 50 entries):
  ```json
  {
    "ts": 1234567890000,
    "symbols": {
      "BTCUSDT": {
        "backtest_pct": 1.42,
        "real_pnl_usdt": 3.21,
        "raw_score": 0.87,
        "old_weight": 0.071,
        "new_weight": 0.079
      }
    }
  }
  ```
- Logs one `INFO` line per symbol showing old → new weight and the two input scores.

---

## Config Schema

Added to `DEFAULT_CONFIG` in `config/risk_config.py` under key `"weight_rebalancer"`:

```python
"weight_rebalancer": {
    "enabled": False,
    "rebalance_candles": 96,        # trigger every N closed candles (~1 day at 15m)
    "backtest_window_candles": 96,  # klines slice size for mini-backtest
    "real_pnl_alpha": 0.5,          # weight of real P&L signal vs backtest signal
    "blend_rate": 0.15,             # how fast weights drift toward new scores (0–1)
    "weight_floor_ratio": 0.3,      # floor = ratio × equal_share per symbol
}
```

Disabled by default. No existing behavior changes when `enabled = false`.

---

## Integration: `main.py`

**Setup** (alongside existing object creation):

```python
from bot.weight_rebalancer import WeightRebalancer

weight_rebalancer = WeightRebalancer(
    symbol_registry=symbol_registry,
    risk_manager=risk_manager,
    get_klines_fn=lambda sym: analyzers[sym].get_klines(),
    candle_duration_ms=_tf_to_ms(timeframe),
    mode=mode,
    risk_config_path=RISK_CONFIG_PATH,
    cfg=risk_cfg.get("weight_rebalancer", {}),
)
```

**In `on_candle_close`** (after all per-symbol loops, before sleeping):

```python
if weight_rebalancer.enabled:
    weight_rebalancer.on_candle_close(candle_ts)
```

No other changes to existing bot logic.

---

## Dashboard: Risk Page

New collapsible section appended after the existing per-symbol allocation table.

**Controls (read/write via `POST /api/risk`):**
- Enable/disable toggle (`weight_rebalancer.enabled`)
- `rebalance_candles` number input
- `backtest_window_candles` number input
- `real_pnl_alpha` slider (0.0 – 1.0, step 0.05)
- `blend_rate` slider (0.05 – 0.5, step 0.05)
- `weight_floor_ratio` slider (0.1 – 0.9, step 0.05)

**Status display (read-only, from `weight_rebalance_log_{mode}.json`):**
- "Last rebalance: X minutes ago" (or "Never")
- Per-symbol table: symbol | backtest % | real P&L | raw score | old weight → new weight

Data served via existing `/api/public-file?f=weight_rebalance_log_{mode}.json` route.

---

## Thread Safety

- `WeightRebalancer` uses a `threading.Event` (`_running`) to ensure only one rebalance runs at a time. If a rebalance is still in progress when the next trigger fires, it is skipped and logged as a warning.
- `risk_config.save()` (in `config/risk_config.py`) already uses atomic writes (`tempfile` + `os.replace`). No additional locking needed for file writes.
- `data_feed.get_klines(symbol)` returns a list reference; the rebalancer operates on a slice copy (`klines[-N:]`) so the main loop's kline mutations don't affect the background computation.

---

## What This Does NOT Change

- BGF ordering (which symbol gets capital first) — unchanged, still driven by `raw_profit_pct` from static backtest results.
- `symbol_registry` weights (`symbol_registry.json`) — unchanged. The rebalancer only touches `symbol_weights` in `risk_config.json`, which feeds `RiskManager._calc_allocation()`.
- Any preset, order execution, or risk management logic.
- Behavior when `enabled = false` (default).
