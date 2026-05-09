# Trades Page & Virtual Order Simulation Design

## Goal

Add a `/trades` dashboard page showing per-symbol real order history, preset efficiency, and a chart with trade overlays. Back this with a virtual order simulation engine that independently tracks all non-best presets against live prices, giving each preset a statistically valid efficiency score based on real market behaviour rather than backtest-only data.

## Architecture

Four components work together:

1. **`VirtualOrderSimulator`** (new `bot/virtual_order_simulator.py`) — manages open/closed virtual positions per symbol per mode; runs recommendation checks for all non-best presets on each candle close; checks TP/SL on every price update; persists to `virtual_orders_{symbol}_{mode}.json`; market-closes all on bot stop or mode switch.
2. **`VirtualTracker` fix** — `seed_from_backtest` becomes conditional; only seeds symbols not yet present in `preset_efficiency_{mode}.json`.
3. **Real order recording** — `OrderExecutor` writes one record to `real_orders_{symbol}_{mode}.json` on each real order close.
4. **`/trades` dashboard page** — per-symbol page with preset efficiency table, candlestick chart with real trade overlays, and real order history list.

---

## Data Architecture

Four files, strictly separated ownership:

| File | Owner | Written by | Notes |
|------|-------|-----------|-------|
| `backtest_results_{symbol}.json` | Backtest | `backtest.py` only | Runtime never touches this |
| `preset_efficiency_{mode}.json` | Runtime efficiency | Bot only | Seeded once per symbol+mode from backtest; updated by real + virtual closes |
| `real_orders_{symbol}_{mode}.json` | Real trade history | `OrderExecutor` on close | Individual records, all symbols separate |
| `virtual_orders_{symbol}_{mode}.json` | Virtual positions | `VirtualOrderSimulator` | Per symbol per mode; open + closed orders |

**Critical rule**: running `backtest.py` again must never overwrite accumulated runtime efficiency. The `preset_efficiency_{mode}.json` file is owned exclusively by the bot runtime after initial seeding.

### `real_orders_{symbol}_{mode}.json` schema

```json
[
  {
    "preset_name": "r6_arm15_full",
    "side": "BUY",
    "entry_price": 50000.0,
    "close_price": 55000.0,
    "tp": 55000.0,
    "sl": 48000.0,
    "quantity": 0.005,
    "leverage": 10,
    "open_time": "2026-05-09T10:00:00Z",
    "close_time": "2026-05-09T14:30:00Z",
    "pnl_usdt": 25.0,
    "result": "win"
  }
]
```

### `virtual_orders_{symbol}_{mode}.json` schema

```json
{
  "BTCUSDT": [
    {
      "preset_name": "sl_adjust_rr_trail",
      "side": "BUY",
      "entry_price": 50000.0,
      "tp": 55000.0,
      "sl": 48000.0,
      "quantity": 0.005,
      "leverage": 10,
      "open_time": "2026-05-09T10:00:00Z",
      "status": "open",
      "close_price": null,
      "close_time": null,
      "pnl_usdt": null,
      "result": null
    }
  ]
}
```

`status` is either `"open"` or `"closed"`. On bot stop or mode switch, all open virtual orders are market-closed (set `close_price` to current REST price, `result` to `"closed_early"`, `pnl_usdt` computed from close_price vs entry).

---

## Preset Cleanup

22 presets removed from `backtest.py` (threshold: total profit % < −10 across all 15 symbols):

`structure_sensitive`, `tp_90pct_high_rr`, `very_high_rr`, `rr_4x`, `high_rr`, `high_rr_tight`, `tight_entry`, `conservative`, `tp_90pct`, `sl_adjust_rr`, `medium_entry`, `sl_filter_medium`, `lh_sell_prox20`, `medium_rr_trail_30`, `lh_sell_prox15`, `lh_sell_prox10`, `tp_95pct`, `high_rr_trail_30`, `sl_filter_tight`, `max_profit_3pct`, `tp_85pct`, `trail_40_from_50`

**Remaining**: 100 presets in `PRESETS` + 4 in `LOCKED_PRESETS`. This is the set that participates in virtual order simulation.

---

## `seed_from_backtest` Fix

**Current bug**: `seed_from_backtest(symbol, bt_path)` unconditionally overwrites runtime-accumulated efficiency for `symbol` every time it's called (on startup and every mode switch).

**Fix**: before writing, check if `symbol` already has an entry in `preset_efficiency_{mode}.json`. If yes, skip entirely.

```python
def seed_from_backtest(self, symbol: str, bt_path: Path) -> None:
    existing = self._read_efficiency()
    if symbol in existing:
        return  # already seeded — don't overwrite runtime data
    # ... original seeding logic
```

**Seeding trigger**: called after each obligatory backtest run (startup + mode switch). With the fix, the first call seeds; subsequent calls are no-ops for that symbol.

---

## Virtual Order Simulator

### File: `bot/virtual_order_simulator.py`

**Class**: `VirtualOrderSimulator(mode: str, all_presets: dict, project_root: Path)`

**State**: `_open: dict[str, list[dict]]` — open virtual orders keyed by symbol.

### Opening virtual orders (on candle close)

Called from `on_candle_close` in `main.py` after the real order logic runs.

For each symbol, for each preset that is NOT the current best preset:
1. If there is already an open virtual order for this preset+symbol → skip.
2. Run `RecommendationEngine(preset_settings).generate(analyzer.get_trend(), analyzer.get_current_price())`.
3. If a recommendation is returned and it passes basic validity (entry > 0, tp > 0, sl > 0) → open a virtual order using the recommendation's entry, tp, sl, side.
4. Quantity: use the same allocation logic as real orders (from `RiskManager`), but virtual — no exchange call, no capital deduction.
5. Append to `_open[symbol]` and persist to file.

**Preset settings construction**:
```python
import dataclasses
preset_settings = dataclasses.replace(base_settings, **overrides)
rec = RecommendationEngine(preset_settings).generate(analyzer.get_trend(), price)
```

`base_settings` is the symbol's base `Settings` object (same one used for real orders). `overrides` is `all_presets[preset_name]`.

### Checking virtual orders (on price update)

Called from `on_price_update` in `main.py` alongside the real order check.

For each open virtual order for `symbol`:
- **BUY**: if `price >= tp` → win; if `price <= sl` → loss.
- **SELL**: if `price <= tp` → win; if `price >= sl` → loss.
- Trailing stop: if `trailing_stop_pct > 0`, apply the same trailing logic as `OrderExecutor`.
- On close: update record with `close_price`, `close_time`, `pnl_usdt`, `result`; move from `_open` to the closed list in the file; update `preset_efficiency_{mode}.json`.

### Closing all virtual orders (on bot stop / mode switch)

For each open virtual order for all symbols:
1. Fetch current price from REST API (`futures_symbol_ticker`).
2. Set `close_price = current_price`, `result = "closed_early"`, compute `pnl_usdt`.
3. Persist to file. Do NOT update `preset_efficiency_{mode}.json` (early close is not a valid efficiency data point).

### Persistence

File path: `data/virtual_orders_{symbol}_{mode}.json`

On each open/close event the file is rewritten atomically (tmp → rename). The file stores only the last 500 closed orders per symbol to prevent unbounded growth; open orders are always preserved in full.

---

## Analyzer Helper

Add to `bot/analyzer.py`:

```python
def get_recommendation_for_preset(self, overrides: dict) -> Optional[Recommendation]:
    """Run the recommendation engine with preset-overridden settings."""
    if self._trend is None or self._engine is None:
        return None
    import dataclasses
    s = dataclasses.replace(self._engine._s, **overrides)
    return RecommendationEngine(s).generate(self._trend, self._current_price)
```

---

## Real Order Recording

Add to `OrderExecutor`:

```python
def _record_real_order_close(self, symbol: str, order: OpenOrder, close_price: float,
                              result: str, pnl_usdt: float) -> None:
    path = self._project_root / 'data' / f'real_orders_{symbol}_{self._mode}.json'
    records = []
    if path.exists():
        try:
            records = json.loads(path.read_text())
        except Exception:
            records = []
    records.append({
        'preset_name': order.preset_name,
        'side': order.side,
        'entry_price': order.entry,
        'close_price': close_price,
        'tp': order.tp,
        'sl': order.sl,
        'quantity': order.quantity,
        'leverage': order.leverage,
        'open_time': order.open_time,
        'close_time': datetime.now(timezone.utc).isoformat(),
        'pnl_usdt': pnl_usdt,
        'result': result,
    })
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(records))
    tmp.replace(path)
```

Called inside `_market_close` and `check_symbol_price` after a real order closes.

---

## Real Order Opening Guard

Before opening a new real order for a symbol, verify there is no open position via the exchange API if:
- Internal state shows `OrderState.IDLE` but the best preset has changed since the last order.
- Or on first order after startup/mode switch.

Implementation:
1. `OrderExecutor` stores `_last_opened_preset: dict[str, str]` — maps symbol to the preset name of the last opened order.
2. If `virtual_tracker.best_preset(symbol) != _last_opened_preset.get(symbol)` and state is IDLE → call `check_symbols_on_exchange([symbol])` to confirm no open position before placing.
3. This is a lightweight REST call (`futures_get_open_orders`) already used in `reconcile_with_exchange`.

---

## `main.py` Changes

### `on_candle_close`
After existing real order logic:
```python
await virtual_order_simulator.on_candle_close(
    symbol=symbol,
    analyzer=analyzer,
    all_presets=all_presets,
    best_preset_name=virtual_tracker.best_preset(symbol),
    base_settings=settings,
)
```

### `on_price_update`
After existing `check_symbol_price` call:
```python
virtual_closed = await virtual_order_simulator.check_prices(symbol, price)
for vc in virtual_closed:
    virtual_tracker.record_closed_trade(vc['symbol'], vc['preset_name'], vc['pnl_usdt'])
```

### Bot stop / mode switch
Before closing real orders:
```python
await virtual_order_simulator.close_all_open(symbols, feed)
```

---

## `/trades` Dashboard Page

### Route

`/trades` — symbol selected via the existing `useSymbol` context (same SymbolSwitcher as all other pages).

### Sections

**1. Preset Efficiency Table**

Columns: Preset Name | Type (Real / Virtual) | Trades | Win Rate | Total PnL % | Avg PnL/trade | Status (Best / Active / —)

- Row marked **Best** = current `best_preset` for this symbol (uses real order stats)
- All other rows = virtual order stats
- Sorted by Total PnL % descending by default
- If a preset has 0 trades (never fired) it is shown with `—` in metric columns
- Efficiency comes from `preset_efficiency_{mode}.json`; trade count and win rate computed from `real_orders` / `virtual_orders` files

**2. Candlestick Chart**

- OHLC candlestick chart (Chart.js financial plugin or CandlestickController)
- Reuses kline data from `results_{symbol}.json` (already available, no new endpoint needed)
- Real trade overlays: entry markers (▲ BUY / ▼ SELL) at entry candle timestamp; exit markers (✕) at close candle timestamp; a connecting line between entry and exit colored green (win) or red (loss)
- Toggle: show/hide virtual order overlays per preset (too many to show by default)

**3. Real Orders Table**

Columns: Open Time | Close Time | Preset | Side | Entry | Close | PnL USDT | PnL % | Result

- Color-coded rows: green (win), red (loss), amber (closed_early)
- Most recent first
- Served from `real_orders_{symbol}_{mode}.json`

### API Routes

**`GET /api/trades?symbol=BTCUSDT`**

Returns:
```json
{
  "real_orders": [...],
  "virtual_summary": {
    "preset_name": { "total_winning_usdt": 0.0, "trade_count": 0 }
  },
  "best_preset": "r6_arm15_full"
}
```

Reads `real_orders_{symbol}_{mode}.json`, `preset_efficiency_{mode}.json`, and `data/bot_mode.json` to determine current mode.

---

## Integration with `on_price_update`

The price update path now handles three things for each symbol:
1. `analyzer.update_price(price)` — existing
2. `order_executor.check_symbol_price(symbol, price)` — real orders
3. `virtual_order_simulator.check_prices(symbol, price)` — virtual orders

All three run on every price tick. Virtual order checking is in-memory (no I/O on every tick); file write only happens on close events.

---

## Testing

- `tests/test_virtual_order_simulator.py`: open/close lifecycle, dedup guard (no double-open per preset), TP/SL triggers, early-close on stop, persistence round-trip, `seed_from_backtest` conditional seeding.
- `tests/test_real_order_recording.py`: record written on close, append behaviour, file created on first close.
- Dashboard E2E: manual test — run bot with ≥2 symbols, let a real order close, verify `/trades` page shows it with correct PnL.

---

## Open Decisions

None — all design questions resolved during brainstorming.
