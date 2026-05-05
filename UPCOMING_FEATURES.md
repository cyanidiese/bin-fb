# Upcoming Features

This file captures approved designs for features not yet implemented. Maintained to survive token-limit session resets. Reference before starting any new session on these topics.

---

## Multi-Symbol Support

**Status:** Design approved 2026-05-04. Implementation plan pending.  
**Full spec:** `docs/superpowers/specs/2026-05-04-multi-symbol-design.md`

### What it does

Runs the full pipeline (klines, trend engine, backtester, paper trader, dashboard) for multiple symbols simultaneously (e.g. BTCUSDT, XAUUSDT). Each symbol is fully isolated at the data and engine layers.

### Key decisions locked in

- **Config:** `SYMBOLS=BTCUSDT,XAUUSDT` in `.env` (comma-separated). Per-symbol overrides via `{SYMBOL}_{SETTING}` env vars (e.g. `XAUUSDT_PROXIMITY_ZONE_PCT=15.0`). `SYMBOL` (singular) kept for backward compat.
- **Concurrency:** Pure asyncio — `asyncio.gather` over per-symbol coroutines. One event loop, one thread. Klines loaded synchronously at startup before the loop starts.
- **File naming:** All output files prefixed with symbol. `results_{SYMBOL}.json`, `backtest_results_{SYMBOL}.json`, `paper_results_{SYMBOL}.json`, `paper_state_{SYMBOL}.json`. Kline cache already correct.
- **Symbol discovery:** Bot writes `dashboard/public/symbols.json` at startup. Dashboard reads it to know which symbols exist.
- **RiskManager (`bot/risk_manager.py`):** New module. `asyncio.Lock`-protected. Gates live order placement by per-symbol cap and total cap. Optional for paper trading (paper uses unlimited virtual capital per symbol).
- **Backtest:** Serial loop over symbols; writes `backtest_results_{SYMBOL}.json` per symbol.
- **`main.py`:** Stays single-symbol for now (display UI is not multi-symbol-ready). Uses `SYMBOL` fallback.

### Dashboard changes

- `SymbolSwitcher.tsx` + `useSymbol.ts` already exist and are compatible — just need wiring.
- All three pages (`/`, `/backtest`, `/paper`) wire in symbol switcher top-right.
- localStorage keys become symbol-scoped: `db:strategy:{symbol}:selectedLevel` etc.
- New component: `CrossSymbolComparison.tsx` on `/backtest` page with 3 tabs:
  - **Side-by-side:** Same preset across symbols, profit% per column
  - **Best per symbol:** Each symbol's single best preset in one row
  - **Combined score (default):** Average profit% across all symbols, sorted desc

### Files to create/modify

**New Python:** `bot/risk_manager.py`  
**Modified Python:** `config/settings.py`, `bot/exporter.py`, `bot/paper_trader.py`, `paper_trade.py`, `backtest.py`, `backtest_api.py`, `main.py`  
**New Dashboard:** `components/CrossSymbolComparison.tsx`  
**Modified Dashboard:** `app/page.tsx`, `app/backtest/page.tsx`, `app/paper/page.tsx`, `lib/types.ts`  
**Unchanged:** All bot engine files (`analyzer.py`, `trend.py`, etc.), `SymbolSwitcher.tsx`, `useSymbol.ts`, all other dashboard components

---

_Add new upcoming features below this line as sections._
