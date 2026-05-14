# Balance & Leverage Progression Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current weighted-allocation + performance-leverage system with a conservative, graduated approach: all symbols start at leverage 1, advance globally only when every active symbol has proven at least one closed real order at the current level. Position size is always the minimum viable margin for the exchange (`min_notional / current_leverage`). Allocation weighting is archived and placed behind a disabled-by-default Settings checkbox.

**Architecture:** New `LeverageTracker` class owns the global leverage level and graduation state. `main.py` uses it for every real order. `VirtualOrderSimulator` gets its own shared virtual balance and uses the same sizing formula. Balance history is logged for future analysis.

**Tech Stack:** Python (asyncio, json, pathlib), Next.js 15 App Router, existing `risk_config.json` / `DEFAULT_CONFIG` pattern.

---

## Decisions Made

| Topic | Decision |
|---|---|
| Position size | Option A: `margin = min_notional / current_leverage` (minimum viable) |
| Leverage progression | Global level, advances when ALL active symbols have ≥1 closed order at current level |
| Allocation weighting | Archived — `use_allocation_weighting: false` by default, Settings checkbox to re-enable |
| Order loop | Efficiency-ranked (most efficient first), balance re-fetched per symbol (5s TTL per candle) |
| Virtual balance | Separate shared pool for all virtual presets/symbols, Option A sizing, persisted to disk |

---

## Component Overview

### New: `bot/leverage_tracker.py`

Single source of truth for the current global leverage level.

```
LeverageTracker(mode, active_symbols, data_path, max_level=5)
  _current_level: int          # starts at 1, persisted
  _completed: {symbol: {1,2,3}}  # levels with ≥1 closed order per symbol
  _active_symbols: list[str]   # updated on symbol add/remove

  get_current_level() -> int
  record_closed(symbol, leverage) -> None   # may advance level
  add_symbol(symbol) -> None               # new symbol must graduate from level 1
  remove_symbol(symbol) -> None            # removed symbol no longer blocks graduation
  _check_advance() -> None                 # called after every record_closed/add/remove
  _save() / _load()                        # persist to data/leverage_state_{mode}.json
```

Advancement rule: if every symbol in `_active_symbols` has `current_level` in its `_completed` set, increment `current_level` (capped at `max_level`). Log the advancement with a system_log `info` entry.

**Persistence schema** (`data/leverage_state_{mode}.json`):
```json
{
  "current_level": 1,
  "completed": { "BTCUSDT": [1], "ETHUSDT": [] }
}
```

**Edge cases:**
- New symbol added mid-run: the new symbol only needs **level 1** in its completed set before it stops blocking the next advancement at `current_level`. It does not need to catch up through all intermediate levels — only the current global level matters at advancement time. Reasoning: holding back proven efficient symbols from higher leverage because a new symbol was added contradicts the max-profit goal. Other existing symbols do not regress.
- Symbol removed: `_check_advance()` is called immediately — removal may unblock an advancement that was waiting on that symbol.
- All symbols removed: level stays frozen.

### Modified: `main.py` — order loop

Replace the current per-symbol `_try_place_order` call with an efficiency-ranked loop.

**Current flow** (one symbol, per candle):
```python
if best is not None and order_executor.get_state(symbol) == OrderState.IDLE:
    await _try_place_order(symbol, best, settings)
```

**New flow** (all symbols, ranked, inside `on_candle_close`):
```python
# Collect signals from all symbols this candle (called once after all analyzers updated)
# This requires on_candle_close to be aware of all symbols, not just the one that fired.
# See "Candle Loop Coordination" section below.
```

Because the current `on_candle_close` is called per-symbol independently by the WebSocket, the ranked loop runs at the end of every `on_candle_close` call across all active symbols. This is idempotent: already-OPEN symbols are skipped, the balance TTL cache (5s) prevents redundant REST calls, and placing the loop in every callback means the most recently fired candle triggers placement for all pending signals — no coordinator task needed.

**Implementation (Approach A — efficiency-ranked across all symbols):**
```python
async def on_candle_close(symbol: str, kline: list) -> None:
    # 1. Update this symbol's analyzer as before
    # 2. Collect signals from ALL symbols (not just this one)
    candidates = []
    for sym in symbol_registry.get_symbols():
        if order_executor.get_state(sym) != OrderState.IDLE:
            continue
        best = analyzers[sym].get_best_recommendation()
        if best is None:
            continue
        score = virtual_tracker.get_efficiency_score(sym)  # new helper
        candidates.append((sym, best, sym_settings[sym], score))

    # 3. Sort most-efficient first — capital goes to best performers
    candidates.sort(key=lambda x: x[3], reverse=True)

    # 4. Place orders in efficiency order
    for sym, best, settings, _ in candidates:
        await _try_place_order(sym, best, settings)
```

Reasoning: the bot's purpose is max profit. Without efficiency ranking, capital goes to whichever symbol's WebSocket event happens to fire first — which is random. The sort costs negligible CPU and ensures the best-performing symbol always gets first access to available balance when capital is limited.

### `_try_place_order` — new sizing logic

```python
async def _try_place_order(symbol: str, best, settings) -> None:
    preset_name = virtual_tracker.best_preset(symbol)
    overrides   = all_presets.get(preset_name or 'default', {})
    preset_settings = dataclasses.replace(settings, **overrides)

    current_lev   = leverage_tracker.get_current_level()
    min_notional  = await order_executor.get_min_notional(symbol)
    bracket_max   = order_executor.get_bracket_max(symbol)
    max_policy_lev = risk_cfg.get('max_leverage_level', 5)

    # Clamp leverage to exchange and policy limits
    actual_lev = min(current_lev, bracket_max, max_policy_lev)

    # Re-fetch balance (5s TTL shared within the candle batch)
    balance = await _get_fresh_balance()
    risk_manager.update_balance(balance)

    # Option A: minimum viable margin
    margin = min_notional / actual_lev
    if balance < margin:
        logger.info(f"[{symbol}] Insufficient balance for margin={margin:.2f} at lev={actual_lev}")
        return

    entry    = best.getEntryPrice()
    quantity = (margin * actual_lev) / entry   # notional / entry = min_notional / entry

    allowed, reason = risk_manager.can_open_sync(symbol)  # drawdown / hard_stop gates only
    if not allowed:
        logger.info(f"[{symbol}] Order skipped: {reason}")
        return

    await order_executor.place_order(
        symbol=symbol, preset_name=preset_name or 'default',
        side=best.getSide(), entry=entry,
        tp=best.getTarget(), sl=best.getStop() or 0.0,
        quantity=quantity, leverage=actual_lev,
        partial_take_pct=preset_settings.partial_take_pct,
        trailing_stop_pct=preset_settings.trailing_stop_pct,
        level=best.getLevel(), signal_type=best.getType().value,
        balance_at_open=balance,   # new param, stored in order record
    )
```

**`can_open_sync` change:** remove the `estimated_size_usdt` / allocation checks. Keep both remaining gates:
- `hard_stop_active` — prevents trading during a drawdown emergency
- `min_profit_factor` gate — prevents trading symbols with poor historical performance; keeping this serves max profit by not wasting capital on underperforming symbols

The sizing affordability check (`balance >= margin`) is done explicitly in `_try_place_order` before calling `can_open_sync`.

**Balance TTL helper** (in `main.py`):
```python
_balance_cache: tuple[float, float] = (0.0, 0.0)  # (value, monotonic_time)
_BALANCE_TTL = 5.0  # seconds

async def _get_fresh_balance() -> float:
    global _balance_cache
    now = time.monotonic()
    if now - _balance_cache[1] < _BALANCE_TTL:
        return _balance_cache[0]
    bal = await order_executor.fetch_account_balance()
    if bal > 0:
        _balance_cache = (bal, now)
    return bal or _balance_cache[0]
```

Remove `_should_poll_balance()` and the global 30s timer. The `_get_fresh_balance()` call in `_try_place_order` replaces it. Dashboard balance freshness is not affected — it reads from `risk_state.json`.

### Modified: `bot/order_executor.py`

**`place_order` signature** — add `balance_at_open: float = 0.0` param, forward to `_record_real_order_close`.

**`OpenOrder` dataclass** — add `balance_at_open: float = 0.0`.

**`_record_real_order_close`** — extended record with signal context and balance:
```json
{
  "preset_name": "r5_arm15_cooldown",
  "side": "BUY",
  "entry_price": 50000.0,
  "close_price": 51000.0,
  "quantity": 0.001,
  "leverage": 1,
  "open_time": "...",
  "close_time": "...",
  "pnl_usdt": 1.0,
  "result": "win",
  "balance_at_open": 100.0,
  "signal_type": "ASCENDING_NEAR_HIGHER_LOW",
  "signal_level": 2,
  "precision_score": 0.74
}
```

`signal_type` is already passed to `place_order`. Add `signal_level` and `precision_score` as new params — obtained from the `Recommendation` object in `_try_place_order` before calling `place_order`. These fields let post-run analysis correlate signal quality with trade outcomes.

**`can_open_sync`** — remove the `estimated_size_usdt` / deployment-cap / allocation-cap logic. Signature becomes `can_open_sync(symbol) -> tuple[bool, str]`. Only checks: `hard_stop_active`, `profit_factor < min_profit_factor`.

### Modified: `config/risk_config.py` — new DEFAULT_CONFIG keys

```python
"use_allocation_weighting": False,  # archived; re-enable via Settings checkbox
"max_leverage_level": 5,            # LeverageTracker ceiling
```

Remove from `DEFAULT_CONFIG` (or keep but ignore when `use_allocation_weighting=False`):
- `balance_tiers` — still used for drawdown guard tiers, keep but don't use for sizing
- `base_leverage`, `max_leverage` — replaced by `max_leverage_level` for placement; keep for backward compat

### Modified: `bot/risk_manager.py`

**`get_allocation(symbol)`** — unchanged (still computes weighted allocation), but `main.py` only calls it when `use_allocation_weighting=True`.

**`can_open_sync`** — remove `estimated_size_usdt` parameter and all allocation/deployment-cap checks. Keep:
- `hard_stop_active` gate
- `min_profit_factor` gate (profit factor from backtest)

### Modified: `bot/virtual_order_simulator.py`

```python
class VirtualOrderSimulator:
    def __init__(
        self,
        mode: str,
        all_presets: dict,
        project_root: Path,
        leverage_tracker: LeverageTracker,
        initial_balance: float,
    ) -> None:
        self._leverage_tracker = leverage_tracker
        self._virtual_balance = initial_balance
        self._virtual_committed = 0.0
        self._virtual_balance_path = project_root / 'data' / f'virtual_balance_{mode}.json'
        self._load_virtual_balance()
        # risk_manager dependency removed
```

**Sizing formula** (same as real orders, Option A):
```python
current_lev   = self._leverage_tracker.get_current_level()
margin        = min_notional / current_lev   # ← but how do we get min_notional here?
```

**`min_notional` source:** `main.py` passes a `min_notionals: dict[str, float]` reference that is populated after `_ensure_lot_size` calls at startup and updated lazily when new symbols are added. The simulator reads `min_notionals.get(symbol, 5.0)` (5.0 USDT Binance default as fallback). This avoids coupling the simulator to `order_executor` while keeping data accurate.

**Virtual preset ordering:** when iterating `_all_presets` to decide which presets to open virtual orders for, sort presets by their efficiency score (from `VirtualTracker.get_efficiency_score(symbol, preset_name)`) descending before the loop. When virtual balance is limited, the most efficient presets get capital first. This mirrors the real-order efficiency ranking and serves the max-profit goal.

```python
sorted_presets = sorted(
    self._all_presets.items(),
    key=lambda kv: virtual_tracker.get_preset_efficiency(symbol, kv[0]),
    reverse=True,
)
for preset_name, overrides in sorted_presets:
    ...
```

This requires `VirtualOrderSimulator` to accept a `virtual_tracker` reference (or a callable score function). Pass it at construction from `main.py`.

**Virtual order open guard:**
```python
available = self._virtual_balance - self._virtual_committed
if available < margin:
    continue  # virtual account can't afford this order
self._virtual_committed += margin
record['virtual_margin'] = margin
record['virtual_balance_at_open'] = self._virtual_balance
```

**Virtual order close:**
```python
self._virtual_committed -= record.get('virtual_margin', 0.0)
self._virtual_balance += pnl
record['virtual_balance_after_close'] = self._virtual_balance  # stored in closed record
self._save_virtual_balance()
```

**Persistence** (`data/virtual_balance_{mode}.json`):
```json
{"virtual_balance": 432.50, "virtual_committed": 22.50}
```

**On mode switch** (`main.py`'s `on_switch_mode`): after closing virtual orders, re-create `VirtualOrderSimulator` with `initial_balance` = current real balance from exchange.

### New: `bot/decision_log.py`

Records every placement decision — both placed and skipped — so post-run analysis can answer "why didn't we trade that signal?"

```python
def record(path, candle_ts, symbol, decision, reason,
           balance, leverage, efficiency_score,
           preset_name=None, signal_type=None,
           precision_score=None, level=None):
    """Append one decision entry. Caps at MAX_ENTRIES=5000."""
```

**`decision` values:** `"placed"` | `"skip_balance"` | `"skip_profit_factor"` | `"skip_hard_stop"` | `"skip_already_open"` | `"skip_no_signal"`

**Entry shape:**
```json
{
  "timestamp": "2026-05-10T12:00:00Z",
  "candle_ts": 1746878400000,
  "symbol": "ETHUSDT",
  "decision": "skip_balance",
  "reason": "balance=18.50 < margin=22.50",
  "balance": 18.50,
  "leverage": 2,
  "efficiency_score": 0.83,
  "preset_name": "r5_arm15_cooldown",
  "signal_type": "ASCENDING_NEAR_HIGHER_LOW",
  "precision_score": 0.71,
  "level": 2
}
```

Called from `_try_place_order` in `main.py` — one entry per symbol per candle that had a signal, regardless of outcome. Persisted to `data/decision_log_{mode}.json`.

This is the primary tool for answering after the first run: "Which signals were valid but skipped due to capital limits? Were they winners?"

### New: `bot/balance_history.py`

Thin append-only logger, same pattern as `system_log.py`.

```python
def record(path, balance, trigger, symbol=None, leverage=None, pnl_usdt=None):
    """Append one event. Caps at MAX_ENTRIES=10000."""
```

**Triggers to record:**
- `"startup"` — bot start, initial balance
- `"order_open"` — before placement, with symbol + leverage
- `"order_close"` — after close, with symbol + pnl_usdt
- `"balance_refresh"` — if re-fetched value differs from cached by > 0.5%

**Entry shape:**
```json
{
  "timestamp": "2026-05-10T12:00:00Z",
  "balance": 432.50,
  "trigger": "order_close",
  "symbol": "BTCUSDT",
  "leverage": 1,
  "pnl_usdt": 2.15
}
```

Called from `main.py` — not from inside `order_executor` (keeps the executor clean of history concerns).

### Dashboard changes

**`dashboard/app/api/risk/route.ts` (POST)**
- Accept + persist new fields: `use_allocation_weighting`, `max_leverage_level`

**`dashboard/app/risk/page.tsx`**
- Section B (or new section): add "Allocation weighting" checkbox — when unchecked, allocation fields are greyed out
- Add "Max leverage level" number input (1–20)
- Add read-only "Current leverage level" indicator (from `risk_state.json` or new `leverage_state.json`)

**New: `dashboard/app/api/balance-history/route.ts`**
- `GET /api/balance-history?mode=test&limit=500`
- Read `data/balance_history_{mode}.json`, return last `limit` entries (reversed, newest first)

**Risk page: balance history chart** (optional, can be a follow-up)
- Chart.js line chart: x=timestamp, y=balance
- Markers for order_open (green dot) and order_close (red/green dot per pnl sign)

---

## Data Flow Summary

```
Candle close (all symbols, ranked by efficiency_score descending)
  → _get_fresh_balance() [5s TTL]
  → risk_manager.update_balance(balance)
  → balance_history.record("balance_refresh", ...)  [if changed > 0.5%]

  For each symbol with a signal (efficiency-ranked):
    → leverage_tracker.get_current_level()  → lev
    → min_notional / lev  → margin
    → if balance < margin:
        decision_log.record(symbol, "skip_balance", balance, lev, ...)
        continue
    → if can_open_sync fails:
        decision_log.record(symbol, "skip_profit_factor"|"skip_hard_stop", ...)
        continue
    → place_order(leverage=lev, balance_at_open=balance,
                  signal_level=rec.level, precision_score=rec.precision)
        → decision_log.record(symbol, "placed", ...)
        → balance_history.record("order_open", symbol, lev)

  → order closes (TP/SL/manual)
      → leverage_tracker.record_closed(symbol, lev)  [may advance global level]
      → balance_history.record("order_close", symbol, lev, pnl)

Virtual candle close (same candle, after real loop)
  → presets sorted by get_preset_efficiency(symbol, preset) descending
  → leverage_tracker.get_current_level()  → lev
  → min_notionals[symbol] / lev  → virtual_margin
  → virtual_balance - virtual_committed >= virtual_margin?  → open virtual order
  → virtual order closes
      → virtual_balance += pnl
      → record['virtual_balance_after_close'] = virtual_balance
```

---

## Config Changes Summary

`risk_config.json` additions:
```json
{
  "use_allocation_weighting": false,
  "max_leverage_level": 5
}
```

Existing keys kept for backward compatibility. `balance_tiers` is still used for drawdown guard percentage tier thresholds (separate from order sizing).

---

## Files Affected

| File | Change |
|---|---|
| `bot/leverage_tracker.py` | **New** |
| `bot/balance_history.py` | **New** |
| `bot/decision_log.py` | **New** |
| `bot/virtual_order_simulator.py` | Add virtual balance, remove risk_manager dep, use leverage_tracker |
| `bot/order_executor.py` | Add `balance_at_open` to records; simplify `can_open_sync` |
| `bot/risk_manager.py` | `can_open_sync` removes sizing checks |
| `main.py` | New `_get_fresh_balance()`, wire leverage_tracker, balance_history calls, pass min_notionals to simulator |
| `config/risk_config.py` | Add `use_allocation_weighting`, `max_leverage_level` defaults |
| `dashboard/app/api/risk/route.ts` | Accept new config fields |
| `dashboard/app/risk/page.tsx` | Allocation checkbox, max leverage level input, current level display |
| `dashboard/app/api/balance-history/route.ts` | **New** |
| `tests/test_leverage_tracker.py` | **New** |
| `tests/test_virtual_order_simulator.py` | Update for new constructor signature |

---

## Required additions to existing classes

**`VirtualTracker`** — add two helpers used by the new components:
```python
def get_efficiency_score(self, symbol: str) -> float:
    """Returns a float score for the symbol's best preset (for real-order ranking)."""

def get_preset_efficiency(self, symbol: str, preset_name: str) -> float:
    """Returns a float score for a specific preset (for virtual-order ranking)."""
```
Both return 0.0 when no data exists (new symbol/preset sorts to the back).

---

## Out of Scope (deferred)

- Balance history chart on Risk page — the API route is enough for now; chart is a follow-up
- Allocation weighting re-enabled path — checkbox and config field exist but the weighted code path is not re-tested in this cycle
- Cross-symbol leverage normalization — solved naturally by the global level tracker; no extra code needed
