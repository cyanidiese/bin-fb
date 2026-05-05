# Multi-Symbol Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the entire stack — config, data pipeline, backtester, paper trader, and dashboard — to support multiple trading symbols simultaneously (e.g. BTCUSDT + XAUUSDT).

**Architecture:** Pure asyncio concurrency — `asyncio.gather` over per-symbol coroutines sharing one event loop. Each symbol owns an independent `DataFeed`, `Analyzer`, and `PaperTrader`. A shared `RiskManager` (async-safe) gates live order placement. All output files are prefixed `{SYMBOL}_`; the bot writes `symbols.json` at startup so the dashboard can discover active symbols.

**Tech Stack:** Python asyncio, existing `bot/` modules unchanged, Next.js 15 App Router, Tailwind v4, TypeScript.

---

## File Map

### New files
| Path | Purpose |
|---|---|
| `bot/risk_manager.py` | Async-safe capital budget tracker |
| `dashboard/lib/useSymbols.ts` | Hook that fetches `symbols.json` |
| `dashboard/components/CrossSymbolComparison.tsx` | 3-tab cross-symbol preset comparison |

### Modified files
| Path | Change |
|---|---|
| `config/settings.py` | Add `load_symbols()` + `load_settings(symbol)` with per-symbol env overrides |
| `bot/exporter.py` | Write to `results_{SYMBOL}.json`; add `write_symbols_json()` |
| `paper_trade.py` | Rewrite: `asyncio.gather` over symbols, symbol-prefixed paths |
| `backtest.py` | Add `--symbols` arg; loop `run_for_symbol()` over each symbol |
| `backtest_api.py` | Accept `symbol` key in overrides JSON; fix `find_klines(symbol)` |
| `main.py` | Call `write_symbols_json([settings.symbol])` at startup |
| `dashboard/app/api/run-backtest/route.ts` | Pass `--symbols {symbol}` to `backtest.py` |
| `dashboard/lib/types.ts` | Add `SymbolConfig` interface |
| `dashboard/app/page.tsx` | Symbol wiring, dynamic fetch URL, scoped localStorage keys |
| `dashboard/app/backtest/page.tsx` | Symbol wiring, cross-symbol comparison, fix fetch URLs |
| `dashboard/app/paper/page.tsx` | Symbol wiring, fix fetch URL |

### Unchanged
`bot/analyzer.py`, `bot/trend.py`, `bot/kline_processor.py`, `bot/recommendation_engine.py`, `bot/fake_order.py`, `bot/backtester.py`, `bot/paper_trader.py`, `bot/data_feed.py`, `dashboard/components/SymbolSwitcher.tsx`, `dashboard/lib/useSymbol.ts`, all other dashboard components.

---

## Task 1: Config — `load_symbols()` and per-symbol `load_settings(symbol)`

**Files:**
- Modify: `config/settings.py`

- [ ] **Step 1: Add imports and `load_symbols()` at the bottom of `config/settings.py`**

Add after the existing `load_settings()` function:

```python
import dataclasses   # add to top-of-file imports

# --- add at bottom of file ---

def load_symbols() -> list[str]:
    """
    Returns the list of symbols from SYMBOLS env var (comma-separated).
    Falls back to SYMBOL if SYMBOLS is not set. Raises if neither is set.
    """
    raw = os.getenv('SYMBOLS', '').strip()
    if raw:
        return [s.strip().upper() for s in raw.split(',') if s.strip()]
    fallback = os.getenv('SYMBOL', '').strip()
    if fallback:
        return [fallback.upper()]
    raise RuntimeError("Neither SYMBOLS nor SYMBOL is set in .env")


def _apply_symbol_overrides(settings: Settings, symbol: str) -> Settings:
    """
    Reads {SYMBOL}_{FIELD_NAME} env vars and applies them on top of settings.
    E.g. XAUUSDT_PROXIMITY_ZONE_PCT=15.0 overrides proximity_zone_pct for XAUUSDT.
    Uses the existing value's runtime type to coerce the string.
    """
    prefix = symbol.upper() + '_'
    field_names = {f.name for f in dataclasses.fields(Settings)}
    overrides: dict = {}
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        field_name = env_key[len(prefix):].lower()
        if field_name not in field_names:
            continue
        current = getattr(settings, field_name)
        try:
            if isinstance(current, bool):
                overrides[field_name] = env_val.lower() in ('1', 'true', 'yes')
            elif isinstance(current, int):
                overrides[field_name] = int(env_val)
            elif isinstance(current, float):
                overrides[field_name] = float(env_val)
            else:
                overrides[field_name] = env_val
        except (ValueError, TypeError):
            pass
    return dataclasses.replace(settings, **overrides) if overrides else settings
```

- [ ] **Step 2: Update the signature of `load_settings()` to accept optional `symbol`**

Change the function signature and the symbol-resolution block inside `load_settings()`. The full updated function (replace the existing `load_settings`):

```python
def load_settings(symbol: str | None = None) -> Settings:
    trading_mode = os.getenv('TRADING_MODE', 'testnet').lower()

    if trading_mode not in ('testnet', 'live'):
        raise RuntimeError(f"TRADING_MODE must be 'testnet' or 'live', got: '{trading_mode}'")

    if trading_mode == 'testnet':
        api_key = os.getenv('TESTNET_API_KEY', '')
        api_secret = os.getenv('TESTNET_API_SECRET', '')
        key_names = ('TESTNET_API_KEY', 'TESTNET_API_SECRET')
    else:
        api_key = os.getenv('API_KEY', '')
        api_secret = os.getenv('API_SECRET', '')
        key_names = ('API_KEY', 'API_SECRET')

    missing = []
    if not api_key:
        missing.append(key_names[0])
    if not api_secret:
        missing.append(key_names[1])

    # Resolve symbol: caller-provided takes precedence over SYMBOL env var
    resolved_symbol = symbol.upper() if symbol else os.getenv('SYMBOL', '').upper()
    if not resolved_symbol:
        missing.append('SYMBOL')

    if missing:
        raise RuntimeError(f"Missing required .env variables: {', '.join(missing)}")

    if trading_mode == 'live':
        confirmed = os.getenv('LIVE_MODE_CONFIRMED', '').strip().lower()
        if confirmed != 'yes':
            raise RuntimeError(
                "TRADING_MODE=live requires LIVE_MODE_CONFIRMED=yes in .env. "
                "Set this only after reviewing all risk parameters."
            )

    base = Settings(
        trading_mode=trading_mode,
        api_key=api_key,
        api_secret=api_secret,
        symbol=resolved_symbol,
        timeframe=os.getenv('TIMEFRAME', '15m'),
        kline_limit=int(os.getenv('KLINE_LIMIT', '1000')),
        kline_cache_limit=int(os.getenv('KLINE_CACHE_LIMIT', '5000')),
        swing_neighbours=int(os.getenv('SWING_NEIGHBOURS', '2')),
        timezone=os.getenv('TIMEZONE', 'UTC'),
        min_swing_points=int(os.getenv('MIN_SWING_POINTS', '3')),
        min_profit_pct=float(os.getenv('MIN_PROFIT_PCT', '0.5')),
        min_profit_loss_ratio=float(os.getenv('MIN_PROFIT_LOSS_RATIO', '1.5')),
        precision_similarity_threshold=float(os.getenv('PRECISION_SIMILARITY_THRESHOLD', '0.10')),
        projection_lookback=int(os.getenv('PROJECTION_LOOKBACK', '4')),
        proximity_zone_pct=float(os.getenv('PROXIMITY_ZONE_PCT', '10.0')),
        partial_take_pct=float(os.getenv('PARTIAL_TAKE_PCT', '0.0')),
        trailing_stop_pct=float(os.getenv('TRAILING_STOP_PCT', '0.0')),
        tp_multiplier=float(os.getenv('TP_MULTIPLIER', '1.0')),
        min_sl_pct=float(os.getenv('MIN_SL_PCT', '0.0')),
        max_sl_pct=float(os.getenv('MAX_SL_PCT', '0.0')),
        sl_adjust_to_rr=os.getenv('SL_ADJUST_TO_RR', 'false').lower() in ('1', 'true', 'yes'),
        max_profit_pct=float(os.getenv('MAX_PROFIT_PCT', '0.0')),
        correction_weight=float(os.getenv('CORRECTION_WEIGHT', '0.0')),
        loss_streak_max=int(os.getenv('LOSS_STREAK_MAX', '0')),
        loss_streak_cooldown_candles=int(os.getenv('LOSS_STREAK_COOLDOWN_CANDLES', '5')),
        global_pause_trigger_candles=int(os.getenv('GLOBAL_PAUSE_TRIGGER_CANDLES', '0')),
        global_pause_candles=int(os.getenv('GLOBAL_PAUSE_CANDLES', '10')),
        lower_high_sell=os.getenv('LOWER_HIGH_SELL', 'false').lower() in ('1', 'true', 'yes'),
        higher_low_buy=os.getenv('HIGHER_LOW_BUY', 'false').lower() in ('1', 'true', 'yes'),
    )

    # Apply per-symbol env overrides when a specific symbol is requested
    if symbol is not None:
        base = _apply_symbol_overrides(base, symbol)

    return base
```

- [ ] **Step 3: Smoke-test manually**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -c "
from config.settings import load_symbols, load_settings
print(load_symbols())
s = load_settings('BTCUSDT')
print(s.symbol, s.proximity_zone_pct)
"
```

Expected: prints `['BTCUSDT']` (or your SYMBOLS list) then `BTCUSDT 10.0`.

---

## Task 2: Exporter — symbol-prefixed output + `write_symbols_json()`

**Files:**
- Modify: `bot/exporter.py`

- [ ] **Step 1: Remove the module-level `_OUTPUT_PATH` constant and update `export()`**

Replace:
```python
_OUTPUT_PATH = Path('dashboard/public/results.json')
```
with nothing (delete it).

Inside `export()`, replace the two lines that write the file:
```python
    try:
        _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OUTPUT_PATH.write_text(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Failed to write results.json: {e}")
```
with:
```python
    output_path = Path(f'dashboard/public/results_{symbol}.json')
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Failed to write results_{symbol}.json: {e}")
```

- [ ] **Step 2: Add `write_symbols_json()` at the bottom of `bot/exporter.py`**

```python
def write_symbols_json(symbols: list[str]) -> None:
    """Writes dashboard/public/symbols.json so the dashboard knows which symbols are active."""
    path = Path('dashboard/public/symbols.json')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'symbols': symbols}, indent=2))
    except Exception as e:
        logger.error(f"Failed to write symbols.json: {e}")
```

- [ ] **Step 3: Verify `main.py` still calls `export()` correctly**

Open `main.py` and confirm the call signature is `export(symbol=..., ...)` or `export(settings.symbol, ...)`. If `symbol` was always the first positional arg, no change needed. If it was a keyword arg, verify nothing broke.

Run:
```bash
python -c "from bot.exporter import export, write_symbols_json; print('OK')"
```
Expected: `OK`

---

## Task 3: New module — `bot/risk_manager.py`

**Files:**
- Create: `bot/risk_manager.py`

- [ ] **Step 1: Create `bot/risk_manager.py`**

```python
"""
Shared capital budget tracker for multi-symbol trading.
Gates order placement by per-symbol and total exposure caps.
asyncio.Lock is sufficient — all symbol workers share one event loop thread.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, max_total_pct: float = 80.0, max_per_symbol_pct: float = 40.0):
        self._max_total_pct = max_total_pct
        self._max_per_symbol_pct = max_per_symbol_pct
        self._allocated: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def can_open(self, symbol: str, size_usdt: float, total_capital: float) -> bool:
        """Returns True if opening a position of `size_usdt` for `symbol` stays within caps."""
        async with self._lock:
            if total_capital <= 0:
                return True
            per_sym = self._allocated.get(symbol, 0.0) + size_usdt
            total = sum(self._allocated.values()) + size_usdt
            if per_sym / total_capital * 100 > self._max_per_symbol_pct:
                logger.info(
                    f"RiskManager: {symbol} would exceed per-symbol cap "
                    f"({per_sym:.0f}/{total_capital:.0f} USDT, limit {self._max_per_symbol_pct}%)"
                )
                return False
            if total / total_capital * 100 > self._max_total_pct:
                logger.info(
                    f"RiskManager: total exposure would exceed cap "
                    f"({total:.0f}/{total_capital:.0f} USDT, limit {self._max_total_pct}%)"
                )
                return False
            return True

    async def open(self, symbol: str, size_usdt: float) -> None:
        """Record that `size_usdt` has been allocated to `symbol`."""
        async with self._lock:
            self._allocated[symbol] = self._allocated.get(symbol, 0.0) + size_usdt
            logger.info(
                f"RiskManager.open: {symbol} +{size_usdt:.0f} USDT, "
                f"total={sum(self._allocated.values()):.0f}"
            )

    async def close(self, symbol: str) -> None:
        """Release all allocation for `symbol`."""
        async with self._lock:
            removed = self._allocated.pop(symbol, 0.0)
            logger.info(
                f"RiskManager.close: {symbol} -{removed:.0f} USDT, "
                f"total={sum(self._allocated.values()):.0f}"
            )

    def get_allocated(self) -> dict[str, float]:
        """Snapshot of current allocations (not lock-protected, for logging only)."""
        return dict(self._allocated)
```

- [ ] **Step 2: Smoke-test**

```bash
python -c "from bot.risk_manager import RiskManager; print('OK')"
```
Expected: `OK`

---

## Task 4: Rewrite `paper_trade.py` for multi-symbol

**Files:**
- Modify: `paper_trade.py`

- [ ] **Step 1: Replace `paper_trade.py` with the multi-symbol version**

Keep `PAPER_PRESETS` dict exactly as-is. Replace everything else:

```python
"""
Paper trading runner — streams live Binance klines for multiple symbols
simultaneously and runs curated preset configurations with FakeOrder simulation.
No real orders are placed.

Usage:
  python paper_trade.py
  python paper_trade.py --symbols BTCUSDT XAUUSDT

State is persisted per symbol to data/paper_state_{SYMBOL}.json.
Results are exported to dashboard/public/paper_results_{SYMBOL}.json.
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

from bot.data_feed import DataFeed
from bot.exporter import write_symbols_json
from bot.paper_trader import PaperTrader
from bot.risk_manager import RiskManager
from config.settings import load_settings, load_symbols

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger('paper_trade')

# ── Presets to run in paper trading ────────────────────────────────────────
# Keep PAPER_PRESETS exactly as it was — paste the existing dict here unchanged.
PAPER_PRESETS: dict = { ... }  # <- paste existing dict, do not change it


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
    )
    trader.build_from_klines(klines)

    logger.info(f"[{symbol}] Starting live stream. Press Ctrl+C to stop.")
    await feed.stream_klines(
        symbol=symbol,
        timeframe=settings.timeframe,
        on_candle_close=trader.on_candle,
        on_price_update=trader.on_price_update,
    )


async def _run(symbols: list[str]) -> None:
    write_symbols_json(symbols)
    risk_manager = RiskManager()
    logger.info(
        f"Paper trading {len(symbols)} symbol(s): {', '.join(symbols)} "
        f"— {len(PAPER_PRESETS)} presets each"
    )
    await asyncio.gather(*[run_symbol(s, risk_manager) for s in symbols])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Paper trade one or more symbols.')
    parser.add_argument(
        '--symbols', nargs='+', metavar='SYMBOL',
        help='Override SYMBOLS from .env (e.g. --symbols BTCUSDT XAUUSDT)',
    )
    args = parser.parse_args()

    symbols = [s.upper() for s in args.symbols] if args.symbols else load_symbols()
    try:
        asyncio.run(_run(symbols))
    except KeyboardInterrupt:
        logger.info("Paper trader stopped.")
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
python -c "import paper_trade; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Dry-run with `--help`**

```bash
python paper_trade.py --help
```
Expected: shows `--symbols` option.

---

## Task 5: Update `backtest.py` to loop over symbols

**Files:**
- Modify: `backtest.py`

- [ ] **Step 1: Add `--symbols` arg and import `load_symbols`**

In the `import` block at the top, add:
```python
from config.settings import load_settings, load_symbols
```
(Replace the existing `from config.settings import load_settings` line.)

In `main()`, after the existing `argparse` block, add the new arg before `args = parser.parse_args()`:
```python
    parser.add_argument(
        '--symbols',
        nargs='+',
        metavar='SYMBOL',
        help='Symbols to backtest. Overrides SYMBOLS from .env.',
    )
```

- [ ] **Step 2: Extract per-symbol logic into `run_for_symbol()`**

Add this function above `main()`. It contains the same logic as the current `main()` body, parameterised by symbol:

```python
def run_for_symbol(symbol: str, args) -> None:
    settings = load_settings(symbol)

    # Resolve klines path
    if args.klines:
        klines_path = Path(args.klines)
    else:
        suffix = 'test' if settings.trading_mode == 'testnet' else 'live'
        klines_path = Path('data') / f'{symbol}_{settings.timeframe}_{suffix}.json'

    # Refresh klines cache (skip if --no-fetch or explicit --klines path provided)
    if not args.no_fetch and not args.klines:
        try:
            feed = DataFeed(settings)
            feed.refresh_klines(symbol, settings.timeframe, fetch_count=args.klines_count)
            logger.info(f"[{symbol}] Kline cache refreshed from API")
        except Exception as e:
            logger.warning(f"[{symbol}] Could not refresh klines: {e} — using existing cache")

    if not klines_path.exists():
        logger.error(f"[{symbol}] Klines file not found: {klines_path}")
        return

    with open(klines_path) as f:
        klines = json.load(f)

    if args.klines_count and len(klines) > args.klines_count:
        klines = klines[-args.klines_count:]
    logger.info(f"[{symbol}] Loaded {len(klines)} klines from {klines_path}")

    all_presets = {**LOCKED_PRESETS, **PRESETS}
    backtester = Backtester(settings)
    results = backtester.run(klines, all_presets)

    # Build output payload — preserve any dashboard-added locks
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    preset_dicts = {
        name: {**r.to_dict(), 'settings': all_presets[name]}
        for name, r in results.items()
    }

    code_locked = set(LOCKED_PRESETS.keys())
    extra_locked: list[str] = []
    dashboard_path = Path('dashboard') / 'public' / f'backtest_results_{symbol}.json'
    if dashboard_path.exists():
        try:
            with open(dashboard_path) as f:
                old = json.load(f)
            extra_locked = [
                n for n in old.get('locked_presets', [])
                if n not in code_locked and n in preset_dicts
            ]
        except Exception:
            pass

    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'symbol': symbol,
        'timeframe': settings.timeframe,
        'klines_file': str(klines_path),
        'total_klines': len(klines),
        'presets': preset_dicts,
        'locked_presets': list(code_locked) + extra_locked,
    }

    # Archive copy
    archive_path = Path('data') / f'backtest_{symbol}_{ts}.json'
    archive_path.parent.mkdir(exist_ok=True)
    with open(archive_path, 'w') as f:
        json.dump(output, f, indent=2)
    logger.info(f"[{symbol}] Archive saved to {archive_path}")

    # Dashboard live feed
    if dashboard_path.parent.exists():
        with open(dashboard_path, 'w') as f:
            json.dump(output, f, indent=2)
        logger.info(f"[{symbol}] Dashboard feed updated at {dashboard_path}")

    # Print summary table
    print(f"\n{'='*20} {symbol} {'='*20}")
    header = (
        f"{'Preset':<25} {'Trades':>6} {'Wins':>5} {'Part':>5} "
        f"{'Trail':>6} {'Loss':>5} {'Win%':>6} {'Profit%':>8} "
        f"{'Pts':>8} {'MaxDD':>6} {'AvgTP%':>7}"
    )
    print(header)
    print('─' * len(header))
    for name, r in results.items():
        print(
            f"{name:<25} {r.total():>6} {r.wins():>5} {r.partials():>5} "
            f"{r.trails():>6} {r.losses():>5} {r.win_rate():>5.1%} "
            f"{r.total_profit_pct():>+8.2f} {r.total_profit_pts():>+8.1f} "
            f"{r.max_consecutive_losses():>6} {r.avg_max_tp_reach_pct():>6.1f}%"
        )
```

- [ ] **Step 3: Replace the body of `main()` with a symbol loop**

Replace the existing `main()` body (everything after `args = parser.parse_args()`) with:

```python
    symbols = [s.upper() for s in args.symbols] if args.symbols else load_symbols()
    for symbol in symbols:
        run_for_symbol(symbol, args)
```

Remove the old `settings = load_settings()` line and everything that follows in the old `main()` — all that logic is now in `run_for_symbol()`.

- [ ] **Step 4: Smoke-test**

```bash
python backtest.py --help
```
Expected: shows `--symbols` option alongside existing args.

```bash
python backtest.py --no-fetch --symbols BTCUSDT 2>&1 | head -5
```
Expected: starts logging `[BTCUSDT] Loaded N klines...` or an error about missing cache (not an import error).

---

## Task 6: Update `backtest_api.py` to accept `symbol`

**Files:**
- Modify: `backtest_api.py`

- [ ] **Step 1: Update `find_klines()` to accept a `symbol` parameter**

Replace the existing `find_klines()`:

```python
def find_klines(symbol: str) -> Path:
    results_path = Path(f'dashboard/public/backtest_results_{symbol}.json')
    if results_path.exists():
        try:
            with open(results_path) as f:
                klines_file = Path(json.load(f).get('klines_file', ''))
            if klines_file.exists():
                return klines_file
        except Exception:
            pass

    timeframe = os.getenv('TIMEFRAME', '15m')
    for name in (f'{symbol}_{timeframe}_test.json', f'{symbol}_{timeframe}.json'):
        p = Path('data') / name
        if p.exists():
            return p
    raise FileNotFoundError(
        f'No klines file found for {symbol} in data/. '
        'Run backtest.py first to populate the cache.'
    )
```

- [ ] **Step 2: Update `build_settings()` to accept `symbol`**

Replace:
```python
def build_settings(overrides: dict) -> Settings:
    p = {**DEFAULTS, **overrides}
    symbol = os.getenv('SYMBOL', 'BTCUSDT').upper()
```
with:
```python
def build_settings(overrides: dict, symbol: str | None = None) -> Settings:
    p = {**DEFAULTS, **overrides}
    if symbol is None:
        symbol = os.getenv('SYMBOL', 'BTCUSDT').upper()
```

- [ ] **Step 3: Update `main()` to pop `symbol` from overrides**

Replace:
```python
def main() -> None:
    overrides = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}

    klines_path = find_klines()
    with open(klines_path) as f:
        klines = json.load(f)

    settings = build_settings(overrides)
```
with:
```python
def main() -> None:
    overrides = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    symbol = (overrides.pop('symbol', None) or os.getenv('SYMBOL', 'BTCUSDT')).upper()

    klines_path = find_klines(symbol)
    with open(klines_path) as f:
        klines = json.load(f)

    settings = build_settings(overrides, symbol=symbol)
```

- [ ] **Step 4: Smoke-test**

```bash
python backtest_api.py '{"symbol": "BTCUSDT"}' 2>/dev/null | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('preset','ok'))"
```
Expected: prints `custom` or valid JSON output (not a stack trace).

---

## Task 7: Update `main.py` to write `symbols.json`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Read `main.py` to find the startup section**

```bash
head -60 main.py
```

- [ ] **Step 2: Add `write_symbols_json` call at startup**

After the existing `settings = load_settings()` call at the top of `main()` (or wherever startup initialisation happens), add:

```python
from bot.exporter import write_symbols_json   # add to imports at top of file

# In the main() startup section, after settings = load_settings():
write_symbols_json([settings.symbol])
```

- [ ] **Step 3: Verify import works**

```bash
python -c "import main; print('OK')"
```
Expected: `OK` (no import errors — the module doesn't run on import since it's guarded by `if __name__ == '__main__'`).

---

## Task 8: Update `dashboard/app/api/run-backtest/route.ts` to pass `--symbols`

**Files:**
- Modify: `dashboard/app/api/run-backtest/route.ts`

- [ ] **Step 1: Update the route to accept and forward `symbol`**

Replace the entire file content:

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'
import fs from 'fs'

const BOT_ROOT = path.resolve(process.cwd(), '..')

function getPython(): string {
  const venvPy = path.join(BOT_ROOT, '.venv', 'bin', 'python3')
  return fs.existsSync(venvPy) ? venvPy : 'python3'
}

export async function POST(req: NextRequest) {
  let klinesCount = 1500
  let symbol = ''
  try {
    const body = await req.json()
    if (typeof body.klines_count === 'number' && body.klines_count > 0) {
      klinesCount = body.klines_count
    }
    if (typeof body.symbol === 'string' && body.symbol.trim()) {
      symbol = body.symbol.trim().toUpperCase()
    }
  } catch {
    // use defaults
  }

  const python = getPython()
  const args = [
    'backtest.py',
    '--klines-count', String(klinesCount),
    ...(symbol ? ['--symbols', symbol] : []),
  ]

  return new Promise<NextResponse>(resolve => {
    let stderr = ''
    const child = spawn(python, args, { cwd: BOT_ROOT })
    child.stdout.on('data', () => {})
    child.stderr.on('data', chunk => { stderr += chunk })
    child.on('error', err => {
      resolve(NextResponse.json({ error: `Failed to start Python: ${err.message}` }, { status: 500 }))
    })
    child.on('close', code => {
      if (code !== 0) {
        resolve(NextResponse.json(
          { error: stderr.trim() || `backtest.py exited with code ${code}` },
          { status: 500 },
        ))
        return
      }
      resolve(NextResponse.json({ ok: true, klines_count: klinesCount, symbol }))
    })
  })
}
```

---

## Task 9: Dashboard — `lib/types.ts` + `lib/useSymbols.ts`

**Files:**
- Modify: `dashboard/lib/types.ts`
- Create: `dashboard/lib/useSymbols.ts`

- [ ] **Step 1: Add `SymbolConfig` to `dashboard/lib/types.ts`**

At the bottom of the file, add:

```typescript
// ── Symbol config (written by bot at startup) ─────────────────────────────

export interface SymbolConfig {
  symbols: string[]
}
```

- [ ] **Step 2: Create `dashboard/lib/useSymbols.ts`**

```typescript
'use client'
import { useEffect, useState } from 'react'

/**
 * Fetches /symbols.json (written by the bot at startup) and returns the list
 * of active symbols. Falls back to ['BTCUSDT'] if the file is missing.
 */
export function useSymbols(): string[] {
  const [symbols, setSymbols] = useState<string[]>(['BTCUSDT'])

  useEffect(() => {
    fetch('/symbols.json')
      .then(r => r.json())
      .then(d => {
        if (Array.isArray(d.symbols) && d.symbols.length > 0) {
          setSymbols(d.symbols as string[])
        }
      })
      .catch(() => { /* keep default */ })
  }, [])

  return symbols
}
```

---

## Task 10: Update `dashboard/app/page.tsx` — symbol wiring

**Files:**
- Modify: `dashboard/app/page.tsx`

- [ ] **Step 1: Add new imports at the top**

Add to the import block:

```typescript
import SymbolSwitcher from '@/components/SymbolSwitcher'
import { useSymbols } from '@/lib/useSymbols'
import { useSymbol } from '@/lib/useSymbol'
```

- [ ] **Step 2: Split into outer `Page` + inner `PageContent` components**

The reason: `useLocalStorage` keys need to include the symbol. When the symbol changes, we want all per-symbol state to re-initialise. The cleanest React way is to give the inner component `key={symbol}`, which forces React to fully remount it.

Replace the `export default function Page()` with two components:

```typescript
// Outer shell: manages symbol selection, passes it into PageContent
export default function Page() {
  const availableSymbols = useSymbols()
  const [symbol, setSymbol] = useSymbol(availableSymbols)

  return (
    <>
      {/* Symbol switcher — fixed top-right, outside remounting content */}
      <div className="fixed top-3 right-4 z-50">
        <SymbolSwitcher symbols={availableSymbols} selected={symbol} onSelect={setSymbol} />
      </div>
      {/* key={symbol} forces full remount when symbol changes, resetting all state */}
      <PageContent key={symbol} symbol={symbol} />
    </>
  )
}
```

- [ ] **Step 3: Rename the existing `Page` body to `PageContent`**

Rename `export default function Page()` → `function PageContent({ symbol }: { symbol: string })`.

Remove the `const [selectedLevel, setSelectedLevel] = useLocalStorage<number | null>('db:strategy:selectedLevel', null)` line and replace with symbol-scoped key:

```typescript
const [selectedLevel, setSelectedLevel] = useLocalStorage<number | null>(
  `db:strategy:${symbol}:selectedLevel`, null
)
const [fromDate, setFromDate] = useLocalStorage<string>(`db:strategy:${symbol}:fromDate`, '')
const [toDate,   setToDate]   = useLocalStorage<string>(`db:strategy:${symbol}:toDate`, '')
```

- [ ] **Step 4: Update the fetch URL inside the `useEffect`**

Replace:
```typescript
      fetch(`/results.json?_=${Date.now()}`)
```
with:
```typescript
      fetch(`/results_${symbol}.json?_=${Date.now()}`)
```

Also reset `data` to `null` whenever `symbol` changes, by adding `symbol` to the effect's dependency array. Replace:
```typescript
  useEffect(() => {
    let cancelled = false

    function load() {
```
with:
```typescript
  useEffect(() => {
    let cancelled = false
    setData(null)   // clear stale data while new symbol loads

    function load() {
```

And change the effect dependency from `[]` to `[symbol]`:
```typescript
  }, [symbol])
```

- [ ] **Step 5: Verify TypeScript compiles**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -30
```
Expected: no errors (or pre-existing errors only, none introduced by this task).

---

## Task 11: Update `dashboard/app/backtest/page.tsx` — symbol wiring + cross-symbol comparison

**Files:**
- Modify: `dashboard/app/backtest/page.tsx`

- [ ] **Step 1: Add new imports**

```typescript
import SymbolSwitcher from '@/components/SymbolSwitcher'
import CrossSymbolComparison from '@/components/CrossSymbolComparison'
import { useSymbols } from '@/lib/useSymbols'
import { useSymbol } from '@/lib/useSymbol'
```

- [ ] **Step 2: Add symbol state at the top of `BacktestPage()`**

```typescript
  const availableSymbols = useSymbols()
  const [symbol, setSymbol] = useSymbol(availableSymbols)
  const [allSymbolData, setAllSymbolData] = useState<Record<string, BacktestResults | null>>({})
```

- [ ] **Step 3: Update the initial data fetch to use `symbol`**

Replace:
```typescript
    fetch(`/backtest_results.json?t=${Date.now()}`)
```
with:
```typescript
    fetch(`/backtest_results_${symbol}.json?t=${Date.now()}`)
```

Add `symbol` to the effect dependency array:
```typescript
  }, [symbol])
```

Also reset `data` to `null` and `selectedPreset` at the top of the effect when symbol changes:
```typescript
  useEffect(() => {
    setData(null)
    // ... existing fetch logic ...
  }, [symbol])
```

- [ ] **Step 4: Add a second `useEffect` to load all symbols' data for cross-symbol comparison**

Add after the existing data-fetch effect:

```typescript
  useEffect(() => {
    if (availableSymbols.length < 2) return
    Promise.all(
      availableSymbols.map(s =>
        fetch(`/backtest_results_${s}.json?t=${Date.now()}`)
          .then(r => (r.ok ? r.json() : null))
          .catch(() => null)
      )
    ).then(results => {
      const map: Record<string, BacktestResults | null> = {}
      availableSymbols.forEach((s, i) => { map[s] = results[i] as BacktestResults | null })
      setAllSymbolData(map)
    })
  }, [availableSymbols, symbol])
```

- [ ] **Step 5: Update `handleToggleLock` and `handleRunBacktest` to use symbol-specific URLs**

In `handleToggleLock`, replace:
```typescript
      const r = await fetch(`/backtest_results.json?t=${Date.now()}`)
```
with:
```typescript
      const r = await fetch(`/backtest_results_${symbol}.json?t=${Date.now()}`)
```

In `handleRunBacktest`, replace:
```typescript
        body: JSON.stringify({ klines_count: klinesCount }),
```
with:
```typescript
        body: JSON.stringify({ klines_count: klinesCount, symbol }),
```

And replace:
```typescript
      const r = await fetch(`/backtest_results.json?t=${Date.now()}`)
```
with:
```typescript
      const r = await fetch(`/backtest_results_${symbol}.json?t=${Date.now()}`)
```

- [ ] **Step 6: Add `SymbolSwitcher` to the header and `CrossSymbolComparison` at the bottom**

In the header `<div>`, add `SymbolSwitcher` at the end of the existing `ml-auto` div row:

```typescript
        <div className="ml-auto flex items-center gap-3">
          <SymbolSwitcher symbols={availableSymbols} selected={symbol} onSelect={setSymbol} />
          {/* existing run controls follow */}
          ...
        </div>
```

At the very bottom of the returned JSX (after the closing `</div>` of the dimmed content wrapper), add:

```typescript
      {availableSymbols.length > 1 && (
        <CollapsibleSection title="Cross-Symbol Comparison" storageKey="db:backtest:s:crosssymbol">
          <CrossSymbolComparison symbols={availableSymbols} dataBySymbol={allSymbolData} />
        </CollapsibleSection>
      )}
```

- [ ] **Step 7: Verify TypeScript compiles**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -30
```
Expected: no new errors.

---

## Task 12: Update `dashboard/app/paper/page.tsx` — symbol wiring

**Files:**
- Modify: `dashboard/app/paper/page.tsx`

- [ ] **Step 1: Add new imports**

```typescript
import SymbolSwitcher from '@/components/SymbolSwitcher'
import { useSymbols } from '@/lib/useSymbols'
import { useSymbol } from '@/lib/useSymbol'
```

- [ ] **Step 2: Add symbol state at the top of `PaperPage()`**

```typescript
  const availableSymbols = useSymbols()
  const [symbol, setSymbol] = useSymbol(availableSymbols)
```

- [ ] **Step 3: Update `fetchData()` to use `symbol`**

Inside `fetchData()`, replace:
```typescript
    fetch(`/paper_results.json?t=${Date.now()}`)
```
with:
```typescript
    fetch(`/paper_results_${symbol}.json?t=${Date.now()}`)
```

- [ ] **Step 4: Add `symbol` to the `useEffect` dependency array and reset on change**

Replace:
```typescript
  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
```
with:
```typescript
  useEffect(() => {
    setData(null)
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol])
```

- [ ] **Step 5: Add `SymbolSwitcher` to the header**

In the header `<div className="flex flex-wrap items-baseline gap-4">`, add after the existing spans:

```typescript
        <div className="ml-auto">
          <SymbolSwitcher symbols={availableSymbols} selected={symbol} onSelect={setSymbol} />
        </div>
```

- [ ] **Step 6: Verify TypeScript compiles**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -30
```
Expected: no new errors.

---

## Task 13: New component — `dashboard/components/CrossSymbolComparison.tsx`

**Files:**
- Create: `dashboard/components/CrossSymbolComparison.tsx`

- [ ] **Step 1: Create the component**

```typescript
'use client'

import { useMemo, useState } from 'react'
import type { BacktestResults } from '@/lib/types'

type TabId = 'combined' | 'side-by-side' | 'best-per-symbol'

interface Props {
  symbols: string[]
  dataBySymbol: Record<string, BacktestResults | null>
}

export default function CrossSymbolComparison({ symbols, dataBySymbol }: Props) {
  const [tab, setTab] = useState<TabId>('combined')

  const loadedSymbols = symbols.filter(s => dataBySymbol[s] != null)

  // Union of all preset names that appear in at least one symbol's results
  const allPresetNames = useMemo(() => {
    const names = new Set<string>()
    for (const sym of loadedSymbols) {
      const d = dataBySymbol[sym]
      if (d) Object.keys(d.presets).forEach(n => names.add(n))
    }
    return Array.from(names)
  }, [loadedSymbols, dataBySymbol])

  // Per-row data: preset name → avg profit% + per-symbol profit%
  const rows = useMemo(() => {
    return allPresetNames.map(name => {
      const perSymbol: Record<string, number | null> = {}
      for (const sym of loadedSymbols) {
        perSymbol[sym] = dataBySymbol[sym]?.presets[name]?.total_profit_pct ?? null
      }
      const values = Object.values(perSymbol).filter((v): v is number => v !== null)
      const avg = values.length > 0 ? values.reduce((a, b) => a + b, 0) / values.length : null
      return { name, avg, perSymbol }
    }).filter(r => r.avg !== null)
  }, [allPresetNames, loadedSymbols, dataBySymbol])

  const combinedRows = useMemo(
    () => [...rows].sort((a, b) => (b.avg ?? 0) - (a.avg ?? 0)),
    [rows]
  )

  const sideBySideRows = useMemo(() => {
    const firstSym = loadedSymbols[0]
    return [...rows].sort((a, b) => {
      const av = a.perSymbol[firstSym] ?? -Infinity
      const bv = b.perSymbol[firstSym] ?? -Infinity
      return bv - av
    })
  }, [rows, loadedSymbols])

  const bestPerSymbol = useMemo(() => {
    return loadedSymbols.map(sym => {
      const d = dataBySymbol[sym]
      if (!d) return null
      const presets = Object.values(d.presets)
      if (presets.length === 0) return null
      const best = presets.reduce((a, b) => b.total_profit_pct > a.total_profit_pct ? b : a)
      return {
        symbol: sym,
        preset: best.preset,
        profit: best.total_profit_pct,
        winRate: best.win_rate,
        trades: best.total_trades,
        maxdd: best.max_consecutive_losses,
      }
    }).filter(Boolean) as Array<{ symbol: string; preset: string; profit: number; winRate: number; trades: number; maxdd: number }>
  }, [loadedSymbols, dataBySymbol])

  if (loadedSymbols.length < 2) {
    return (
      <p className="text-sm text-gray-600 italic">
        Cross-symbol comparison available when 2+ symbols have backtest results.
      </p>
    )
  }

  function pctCell(v: number | null) {
    if (v === null) return <span className="text-gray-600">—</span>
    return (
      <span className={v >= 0 ? 'text-emerald-400' : 'text-red-400'}>
        {v >= 0 ? '+' : ''}{v.toFixed(2)}%
      </span>
    )
  }

  const tabs: { id: TabId; label: string }[] = [
    { id: 'combined',        label: 'Combined score' },
    { id: 'side-by-side',    label: 'Side-by-side' },
    { id: 'best-per-symbol', label: 'Best per symbol' },
  ]

  return (
    <div className="space-y-3">
      {/* Tab bar */}
      <div className="flex gap-1">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-1 rounded text-xs font-semibold transition-colors ${
              tab === t.id
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Combined — sorted by avg profit% desc */}
      {tab === 'combined' && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left py-1 pr-4 font-normal">Preset</th>
                <th className="text-right py-1 pr-4 text-indigo-400 font-normal">Avg profit%</th>
                {loadedSymbols.map(s => (
                  <th key={s} className="text-right py-1 pr-4 font-normal">{s}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {combinedRows.map(row => (
                <tr key={row.name} className="border-b border-gray-900 hover:bg-gray-900/40">
                  <td className="py-1 pr-4 text-gray-300">{row.name}</td>
                  <td className="text-right pr-4 font-semibold">{pctCell(row.avg)}</td>
                  {loadedSymbols.map(s => (
                    <td key={s} className="text-right pr-4">{pctCell(row.perSymbol[s])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Side-by-side — sorted by first loaded symbol's profit% */}
      {tab === 'side-by-side' && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left py-1 pr-4 font-normal">Preset</th>
                {loadedSymbols.map(s => (
                  <th key={s} className="text-right py-1 pr-4 font-normal">{s}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sideBySideRows.map(row => (
                <tr key={row.name} className="border-b border-gray-900 hover:bg-gray-900/40">
                  <td className="py-1 pr-4 text-gray-300">{row.name}</td>
                  {loadedSymbols.map(s => (
                    <td key={s} className="text-right pr-4">{pctCell(row.perSymbol[s])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Best per symbol */}
      {tab === 'best-per-symbol' && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left py-1 pr-4 font-normal">Symbol</th>
                <th className="text-left py-1 pr-4 font-normal">Best preset</th>
                <th className="text-right py-1 pr-4 font-normal">Profit%</th>
                <th className="text-right py-1 pr-4 font-normal">Win%</th>
                <th className="text-right py-1 pr-4 font-normal">Trades</th>
                <th className="text-right py-1 pr-4 font-normal">MaxDD</th>
              </tr>
            </thead>
            <tbody>
              {bestPerSymbol.map(row => (
                <tr key={row.symbol} className="border-b border-gray-900 hover:bg-gray-900/40">
                  <td className="py-1 pr-4 text-indigo-300 font-semibold">{row.symbol}</td>
                  <td className="py-1 pr-4 text-gray-300">{row.preset}</td>
                  <td className="text-right pr-4">{pctCell(row.profit)}</td>
                  <td className="text-right pr-4 text-gray-300">{(row.winRate * 100).toFixed(1)}%</td>
                  <td className="text-right pr-4 text-gray-300">{row.trades}</td>
                  <td className="text-right pr-4 text-gray-300">{row.maxdd}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -30
```
Expected: no errors introduced by this component.

---

## Task 14: End-to-end smoke test

- [ ] **Step 1: Add XAUUSDT kline data if not already present**

The testnet XAUUSDT cache should already exist at `data/XAUUSDT_15m_test.json`. Check:

```bash
ls -lh data/XAUUSDT_15m_test.json
```

If missing, run:
```bash
SYMBOLS=XAUUSDT python -c "
from config.settings import load_settings
from bot.data_feed import DataFeed
s = load_settings('XAUUSDT')
feed = DataFeed(s)
feed.load_klines('XAUUSDT', s.timeframe, 500)
print('Done')
"
```

- [ ] **Step 2: Add `SYMBOLS=BTCUSDT,XAUUSDT` to `.env`**

Open `.env` and add (or replace `SYMBOL=BTCUSDT`):
```
SYMBOLS=BTCUSDT,XAUUSDT
```

- [ ] **Step 3: Run backtest for both symbols**

```bash
python backtest.py --no-fetch 2>&1 | tail -20
```
Expected: prints two summary tables, one headed `===BTCUSDT===` and one `===XAUUSDT===`. Produces `dashboard/public/backtest_results_BTCUSDT.json` and `dashboard/public/backtest_results_XAUUSDT.json`.

```bash
ls -lh dashboard/public/backtest_results_*.json
```

- [ ] **Step 4: Write `symbols.json` manually for dashboard testing**

```bash
python -c "
from bot.exporter import write_symbols_json
write_symbols_json(['BTCUSDT', 'XAUUSDT'])
"
cat dashboard/public/symbols.json
```
Expected: `{"symbols": ["BTCUSDT", "XAUUSDT"]}`.

- [ ] **Step 5: Start the dashboard dev server and verify symbol switcher**

```bash
cd dashboard && npm run dev
```

Open `http://localhost:3000/backtest` in a browser. Verify:
- Symbol switcher appears in the header
- Switching between BTCUSDT and XAUUSDT loads the respective preset table
- "Cross-Symbol Comparison" collapsible section appears with three tabs
- "Combined score" tab shows avg profit% column sorted descending
- "Best per symbol" tab shows one row per symbol

- [ ] **Step 6: Verify strategy page (`/`) and paper page (`/paper`)**

Open `http://localhost:3000` and `http://localhost:3000/paper`:
- Symbol switcher present in both
- Switching symbol changes the data displayed

---

## Self-Review Findings (fixed inline above)

- **`backtest_api.py` `find_klines`**: old version read from `backtest_results.json` (non-prefixed); updated to `backtest_results_{symbol}.json` in Task 6. ✓
- **`paper_trade.py` old `async def main()` naming**: resolved — renamed to `_run()` to avoid shadowing the module-level name pattern. ✓
- **`CrossSymbolComparison` receiving `null` entries**: guarded with `dataBySymbol[s] != null` filter before any `.presets` access. ✓
- **`useEffect` deps warning in paper page**: documented with existing eslint-disable comment pattern already used in that file. ✓
- **`run_for_symbol` printing**: uses `[symbol]` prefix in log lines and a header separator in the print table so multi-symbol runs are clearly segmented. ✓
