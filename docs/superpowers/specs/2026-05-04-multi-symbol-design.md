# Multi-Symbol Support — Design Spec
**Date:** 2026-05-04  
**Status:** Approved, pending implementation plan

---

## Goal

Extend the entire system (data, engine, backtester, paper trader, dashboard) to support 4–10 symbols simultaneously (e.g. BTCUSDT, XAUUSDT). Each symbol is fully isolated at the data and engine layers. A shared `RiskManager` gates live order placement across symbols.

---

## 1. Configuration

### `.env` additions

```
SYMBOLS=BTCUSDT,XAUUSDT          # comma-separated, no spaces; replaces SYMBOL
TIMEFRAME=15m                     # shared across all symbols

# Optional per-symbol overrides (format: {SYMBOL}_{SETTING_NAME})
XAUUSDT_PROXIMITY_ZONE_PCT=15.0
XAUUSDT_MIN_SL_PCT=0.1
```

- `SYMBOL` (singular) remains as fallback for backward compatibility with `main.py` and any direct single-symbol usage.
- Per-symbol overrides apply on top of the base settings. Any `Settings` field can be overridden.

### `config/settings.py` changes

- `load_symbols() -> list[str]` — reads `SYMBOLS` env var (comma-split, uppercased). Falls back to `[SYMBOL]` if `SYMBOLS` is not set.
- `load_settings(symbol: str) -> Settings` — loads base settings, sets `Settings.symbol = symbol`, then applies `{SYMBOL}_*` env var overrides.
- `load_settings()` (no args, existing signature) — still works via `SYMBOL` env var; used by `main.py`.

---

## 2. File Naming Convention

All per-symbol output files use `{SYMBOL}_` prefix. Kline cache naming is already correct.

| Artifact | Old path | New path |
|---|---|---|
| Kline cache | `data/BTCUSDT_15m_test.json` | unchanged |
| Strategy export | `dashboard/public/results.json` | `dashboard/public/results_{SYMBOL}.json` |
| Backtest results | `dashboard/public/backtest_results.json` | `dashboard/public/backtest_results_{SYMBOL}.json` |
| Paper results | `dashboard/public/paper_results.json` | `dashboard/public/paper_results_{SYMBOL}.json` |
| Paper state | `data/paper_state.json` | `data/paper_state_{SYMBOL}.json` |
| Symbol list (new) | — | `dashboard/public/symbols.json` |

Old files (`results.json`, `paper_results.json`, `backtest_results.json`) are not deleted — they simply stop being written to.

### `symbols.json` format

Written by the bot at startup (in `paper_trade.py` and `main.py`):

```json
{ "symbols": ["BTCUSDT", "XAUUSDT"] }
```

---

## 3. Python Backend — Concurrency Model

### Decision: pure asyncio (Approach A)

One event loop, multiple concurrent WebSocket streams via `asyncio.gather`. Startup kline loading is synchronous (before the event loop). No new dependencies required.

### `paper_trade.py` restructure

```python
async def run_symbol(symbol: str, risk_manager: RiskManager) -> None:
    settings = load_settings(symbol)
    feed = DataFeed(settings)
    klines = feed.load_klines(symbol, settings.timeframe, settings.kline_limit)
    trader = PaperTrader(
        base_settings=settings,
        presets=PAPER_PRESETS,
        state_path=Path(f'data/paper_state_{symbol}.json'),
        export_path=Path(f'dashboard/public/paper_results_{symbol}.json'),
        risk_manager=risk_manager,   # optional; only gates live orders
    )
    trader.build_from_klines(klines)
    await feed.stream_klines(
        symbol=symbol,
        timeframe=settings.timeframe,
        on_candle_close=trader.on_candle,
        on_price_update=trader.on_price_update,
    )

async def main() -> None:
    symbols = load_symbols()
    write_symbols_json(symbols)        # dashboard/public/symbols.json
    risk_manager = RiskManager(...)
    await asyncio.gather(*[run_symbol(s, risk_manager) for s in symbols])
```

### Per-symbol isolation

Each symbol owns:
- Its own `DataFeed` instance (independent WebSocket connection)
- Its own `Analyzer` instance (independent trend + swing point history)
- Its own `PaperTrader` instance with all presets and `FakeOrder` states
- Its own state file (`data/paper_state_{SYMBOL}.json`)
- Its own export file (`dashboard/public/paper_results_{SYMBOL}.json`)

**No state is shared between symbol workers** except the `RiskManager`.

### `bot/risk_manager.py` (new)

```python
class RiskManager:
    def __init__(self, max_total_pct: float, max_per_symbol_pct: float):
        self._allocated: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def can_open(self, symbol: str, size_usdt: float, total_capital: float) -> bool:
        async with self._lock:
            per_sym = self._allocated.get(symbol, 0.0) + size_usdt
            total = sum(self._allocated.values()) + size_usdt
            if per_sym / total_capital > self._max_per_symbol_pct / 100:
                return False
            if total / total_capital > self._max_total_pct / 100:
                return False
            return True

    async def open(self, symbol: str, size_usdt: float) -> None: ...
    async def close(self, symbol: str) -> None: ...
```

- `asyncio.Lock` is sufficient (all coroutines share one thread).
- For paper trading, `RiskManager` is passed in but optional — paper traders use unlimited virtual capital per symbol.
- For live order placement (Phase 4), `can_open()` is checked before any order.
- Config via `.env`: `MAX_TOTAL_EXPOSURE_PCT=80`, `MAX_PER_SYMBOL_PCT=40`.

### `backtest.py` restructure

Serial loop over symbols (no parallelism needed — batch operation):

```python
for symbol in load_symbols():
    settings = load_settings(symbol)
    klines = DataFeed(settings).load_klines(symbol, ...)
    results = Backtester().run_all(klines, all_presets, settings)
    write(f'dashboard/public/backtest_results_{symbol}.json', results)
    write(f'data/backtest_{symbol}_{timestamp}.json', results)   # archive
```

### `bot/exporter.py` change

`export()` already accepts `symbol` as a parameter. Change the output path from the hardcoded `dashboard/public/results.json` to `dashboard/public/results_{symbol}.json`.

---

## 4. Dashboard

### Symbol discovery

All three pages (`/`, `/backtest`, `/paper`) fetch `/symbols.json` on mount. The result populates `useSymbol()`. Fallback: `["BTCUSDT"]` if `symbols.json` is missing or fetch fails.

### Symbol switcher

`SymbolSwitcher.tsx` already exists and is compatible. Wire it into the top-right of each page's header. Switching symbol triggers a re-fetch of the relevant JSON — no page reload.

### localStorage key namespacing

Level filter and date range keys become symbol-scoped:
- `db:strategy:{symbol}:selectedLevel`
- `db:strategy:{symbol}:fromDate`
- `db:strategy:{symbol}:toDate`

Paper and backtest page selections are already symbol-agnostic (no filtering state saved) — no change needed there.

### Data fetching per page

| Page | Fetches |
|---|---|
| `/` (strategy) | `results_{symbol}.json` |
| `/backtest` | `backtest_results_{symbol}.json` for active symbol; all symbols' files for cross-symbol panel |
| `/paper` | `paper_results_{symbol}.json` |

### Cross-symbol preset comparison — `CrossSymbolComparison.tsx`

New component on the `/backtest` page, below the existing per-symbol summary table.

**Three tabs:**

**Tab 1 — Side-by-side**  
One row per preset. Columns: Preset name | {SYMBOL1} profit% | {SYMBOL2} profit% | …  
Sorted by active symbol's profit% descending. Missing = "—".

**Tab 2 — Best per symbol**  
One row per symbol. Shows: Symbol | Best preset name | profit% | win% | trades | MaxDD.

**Tab 3 — Combined score (default)**  
One row per preset. Columns: Preset name | avg profit% across all symbols | per-symbol profit% columns.  
Default sort: avg profit% descending.  
Only presets that appear in at least one symbol's results are shown.

All data computed client-side from already-loaded JSON files. No new API route needed.

### `useSymbol.ts` — no changes needed

Already correct. Takes `availableSymbols: string[]` from the `symbols.json` fetch result.

---

## 5. Modified files summary

### Python

| File | Change |
|---|---|
| `config/settings.py` | Add `load_symbols()`, update `load_settings(symbol)` with per-symbol overrides |
| `bot/exporter.py` | Write to `results_{symbol}.json` instead of `results.json` |
| `bot/paper_trader.py` | Accept optional `risk_manager` param; no logic change for paper trading |
| `bot/risk_manager.py` | **New** — async-safe capital budget tracker |
| `paper_trade.py` | Rewrite: loop over symbols, `asyncio.gather`, write `symbols.json` |
| `backtest.py` | Loop over symbols, write `backtest_results_{symbol}.json` |
| `backtest_api.py` | Accept optional `symbol` param; use `load_settings(symbol)` |
| `main.py` | Use `load_symbols()`, write `symbols.json` |

### Dashboard

| File | Change |
|---|---|
| `app/page.tsx` | Fetch `symbols.json`, wire `useSymbol`, fetch `results_{symbol}.json`, scope localStorage keys |
| `app/backtest/page.tsx` | Same symbol wiring; fetch all symbols' backtest files for cross-symbol panel |
| `app/paper/page.tsx` | Same symbol wiring |
| `components/CrossSymbolComparison.tsx` | **New** — 3-tab cross-symbol preset comparison |
| `lib/types.ts` | Add `SymbolConfig` type for `symbols.json`; no breaking changes to existing types |

### Unchanged

`SymbolSwitcher.tsx`, `useSymbol.ts`, `bot/analyzer.py`, `bot/trend.py`, `bot/kline_processor.py`, `bot/recommendation_engine.py`, `bot/fake_order.py`, `bot/backtester.py`, `bot/data_feed.py`, all other dashboard components.

---

## 6. Migration notes

- Existing `data/paper_state.json` → rename to `data/paper_state_BTCUSDT.json` on first run (or just let it start fresh — paper state is not critical).
- Existing `dashboard/public/results.json`, `backtest_results.json`, `paper_results.json` → left in place, stop being written. Dashboard stops reading them once pages are updated.
- The `backtest_api.py` Run Backtest button in the dashboard currently hardcodes a single symbol. After migration it passes the active symbol as a query param: `POST /api/run-backtest?symbol=BTCUSDT`.

## 7. Open decisions deferred to implementation

- Whether `main.py` (the live display bot) also runs multi-symbol. Currently single-symbol with `display.py` terminal UI; multi-symbol live display is complex. Defer — `main.py` stays single-symbol for now, uses `SYMBOL` fallback.
- `backtest_api.py` (used by the dashboard Run Backtest button) — needs a `symbol` query param; default to active symbol from frontend.
