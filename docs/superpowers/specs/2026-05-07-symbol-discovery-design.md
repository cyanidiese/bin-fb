# Symbol Discovery Implementation Design

## Goal

Automatically find, backtest, score, and present new candidate symbols for approval — integrated into the existing Symbols section of the Settings dashboard page.

## Architecture

Discovery runs as a standalone `discover.py` subprocess spawned by the dashboard API, identical in pattern to how `backtest.py` is spawned today. No changes to `main.py` or bot startup. Pure logic lives in `bot/symbol_discovery.py`; the CLI entry point and orchestration live in `discover.py`.

```
Dashboard "Discover Symbols" button
  → POST /api/discovery/run   (writes config, spawns discover.py, stores PID)
  → dashboard polls dashboard/public/discovery_state.json every 3s

discover.py
  → imports SymbolDiscovery from bot/symbol_discovery.py
  → fetches Binance exchange info + 24h ticker
  → filters pre-candidates
  → selects fast preset subset
  → ThreadPoolExecutor(max_workers=batch_size) — Backtester in-process per candidate
  → writes discovery_state.json after each batch (progress)
  → scores passing candidates → writes discovery_candidates.json
  → SIGTERM → threading.Event → graceful shutdown, status=cancelled

POST /api/discovery/cancel
  → reads PID from discovery_state.json → sends SIGTERM
```

"Add Symbol" button on a candidate row calls `POST /api/symbols` — the existing endpoint, unchanged.

## Files

### New

| File | Responsibility |
|------|---------------|
| `bot/symbol_discovery.py` | `SymbolDiscovery` class — pure logic: exchange info fetch, pre-candidate filtering, fast preset selection, scoring, baseline computation |
| `discover.py` | CLI entry point — orchestration, ThreadPoolExecutor batching, state file writes, SIGTERM handling |
| `dashboard/app/api/discovery/run/route.ts` | `POST` — validate config, write `data/discovery_config.json`, spawn `discover.py`, reject if already running |
| `dashboard/app/api/discovery/cancel/route.ts` | `POST` — read PID from state file, send SIGTERM |
| `dashboard/app/api/_utils.ts` | Shared `isAlive(pid)` helper used by both symbols and discovery routes |
| `dashboard/components/SymbolDiscovery.tsx` | Discovery UI subsection rendered inside Settings page |

### Modified

| File | Change |
|------|--------|
| `dashboard/app/settings/page.tsx` | Import and render `<SymbolDiscovery />` below the "Add Symbol" panel |
| `dashboard/lib/types.ts` | Add `CandidateResult` and `DiscoveryState` interfaces |
| `dashboard/app/api/symbols/_registry.ts` | Extract `isAlive()` call to `_utils.ts`; import from there |

## Python Backend

### `bot/symbol_discovery.py`

```python
FUTURES_SYMBOLS = [
    # Major / Large Cap
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
    "LTCUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT",
    # Mid Cap / High Volume
    "AAVEUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
    "INJUSDT", "SUIUSDT", "SEIUSDT", "TIAUSDT", "WLDUSDT",
    "JUPUSDT", "RENDERUSDT", "FETUSDT", "AGIXUSDT", "OCEANUSDT",
    # Meme / High Volatility
    "PEPEUSDT", "SHIBUSDT", "FLOKIUSDT", "BONKUSDT", "WIFUSDT",
    "MEMEUSDT", "TRUMPUSDT", "1000SHIBUSDT", "1000PEPEUSDT",
    # DeFi
    "ENAUSDT", "EIGENUSDT", "ETHFIUSDT", "REZUSDT",
    # Layer 1 / Layer 2
    "STXUSDT", "RUNEUSDT", "THETAUSDT", "ALGOUSDT", "FLOWUSDT",
    "ICPUSDT", "FTMUSDT", "HBARUSDT", "EGLDUSDT", "XTZUSDT",
]

class SymbolDiscovery:
    def get_precandidates(active: list[str], min_volume: float) -> list[str]
    # GET /fapi/v1/exchangeInfo → all PERPETUAL USD-M contracts
    # GET /fapi/v1/ticker/24hr → quoteVolume per symbol
    # Filter: symbol in FUTURES_SYMBOLS, symbol not in active, quoteVolume >= min_volume
    # Returns sorted list

    def get_fast_presets(active: list[str], n: int, all_preset_names: list[str]) -> list[str]
    # Read dashboard/public/backtest_results_{sym}.json for each active symbol
    # Rank presets by avg(total_profit_pct) across all symbols with results
    # Return top N names. Fallback: first N from all_preset_names if no results available.

    def score_candidate(
        symbol: str,
        preset_names: list[str],
        klines_count: int,
        baseline: float,
        baseline_ratio: float,
        min_floor: float,
        position_size: float,
        leverage: float,
    ) -> CandidateResult | None
    # Calls DataFeed.refresh_klines(symbol, timeframe, klines_count) to fetch and cache klines.
    # Falls back to existing cache file if API call fails.
    # If no cache file exists after fetch attempt, skips candidate (returns None).
    # Runs Backtester with only preset_names subset.
    # Returns None if candidate fails filters or produces no trades.

    def compute_baseline(active: list[str]) -> float
    # For each active symbol, read best preset's efficiency_score from backtest results
    # Returns avg. Returns 0.0 if no results (disables baseline filter).
```

**Scoring formulas:**
- `efficiency_score = sum(total_profit_pct across fast presets) / max(1, sum(total_order_count across fast presets))`
- Pass condition: `efficiency_score >= baseline_ratio × baseline_efficiency AND efficiency_score >= min_floor`
- `potential_gain_usdt = (best_preset_total_profit_pct / 100) × position_size × leverage`
- `profit_factor = sum(positive profit_pcts) / max(0.001, sum(abs(negative profit_pcts)))` across all fast presets
- `sharpe_ratio`: approximated as `mean(per_trade_profit_pcts) / std(per_trade_profit_pcts)`, 0.0 if fewer than 2 trades
- `best_preset_id`: preset name with highest `total_profit_pct` among the fast subset
- `vs_baseline_pct = (efficiency_score / max(0.001, baseline_efficiency) - 1) × 100`

### `discover.py`

Config is always read from `data/discovery_config.json` (written by the API route before spawning — no CLI args needed).

Orchestration flow:

1. Read `data/discovery_config.json`
2. Clean up `data/discovery/` (delete stale files from any previous crashed run)
3. Call `SymbolDiscovery.get_precandidates()` → total count; write partial state update
4. Call `SymbolDiscovery.get_fast_presets()` → preset subset
5. Call `SymbolDiscovery.compute_baseline()` → baseline value
6. Submit candidates to `ThreadPoolExecutor(max_workers=batch_size)`
7. After each future completes: update `processed_count`, `in_progress` list, write state atomically
8. Check `stop_event` between submissions — if set, cancel pending futures
9. Collect passing candidates, write `dashboard/public/discovery_candidates.json`
10. Write final state: `status=complete|cancelled`, clear `in_progress`

The API route writes the initial state `{ status: 'running', pid: child.pid, total_precandidates: 0, processed_count: 0, in_progress: [] }` immediately after spawning. `discover.py` never writes pid — it only updates progress fields and final status. This eliminates any race between the two writers.

SIGTERM handler: `stop_event.set()` — executor drains in-flight work, skips remaining submissions.

State file (`dashboard/public/discovery_state.json`) written atomically (tmp + rename) after every batch completion.

## API Routes

### `POST /api/discovery/run`

Request body:
```json
{
  "min_volume": 1000000,
  "preset_count": 12,
  "batch_size": 3,
  "baseline_ratio": 0.7,
  "min_floor": 0.0,
  "position_size": 1000,
  "leverage": 1,
  "klines_count": 500
}
```

- Reads `discovery_state.json`; returns 409 if `status === 'running'`
- Writes `data/discovery_config.json`
- Spawns `discover.py` with `{ detached: false, stdio: 'ignore' }`
- Stores PID by re-reading and updating state file
- Returns `{ ok: true, pid }`

### `POST /api/discovery/cancel`

- Reads PID from `dashboard/public/discovery_state.json`
- Returns 409 if `status !== 'running'`
- Sends SIGTERM to PID
- Returns `{ ok: true }`

No GET route — dashboard reads public JSON files directly.

## Data Schemas

### `CandidateResult`

```typescript
interface CandidateResult {
  symbol: string
  efficiency_score: number        // profit_pct_sum / order_count across fast presets
  total_net_profit: number        // sum of total_profit_pct across fast presets
  total_order_count: number       // sum of trades across fast presets
  profit_factor: number           // sum(pos_pcts) / sum(abs(neg_pcts))
  best_preset_id: string          // preset with highest profit_pct in fast subset
  best_preset_profit_pct: number  // that preset's total_profit_pct
  win_rate: number                // weighted avg across fast presets
  max_drawdown: number            // max max_consecutive_losses across fast presets
  sharpe_ratio: number            // approx: mean(trade_pcts) / std(trade_pcts)
  baseline_efficiency: number     // active-symbol baseline at time of evaluation
  vs_baseline_pct: number         // (efficiency / baseline - 1) × 100
  potential_gain_usdt: number     // (best_preset_profit_pct / 100) × position_size × leverage
}
```

### `DiscoveryState`

```typescript
interface DiscoveryState {
  status: 'idle' | 'running' | 'complete' | 'cancelled' | 'error'
  pid: number | null
  total_precandidates: number
  processed_count: number
  in_progress: string[]           // symbols currently being backtested
  last_run_timestamp: string | null
  error?: string                  // set on status=error
}
```

`discovery_candidates.json` is a separate file: `{ generated_at, candidates: CandidateResult[] }`.

## Dashboard UI — `SymbolDiscovery` component

Rendered below the "Add Symbol" panel in `dashboard/app/settings/page.tsx`. Polls `discovery_state.json` every 3s (same interval as Settings page's registry poll). Reads `discovery_candidates.json` once on mount and after each completed run.

### Controls panel (collapsible, collapsed by default)

All labels use `title=` tooltips matching the Settings page pattern:

| Control | Default | Tooltip |
|---------|---------|---------|
| Min 24h volume (USDT) | 1,000,000 | "Symbols below this volume are considered illiquid and skipped" |
| Fast preset count (N) | 12 | "Number of top-performing presets used to evaluate each candidate" |
| Batch size | 3 | "How many candidate symbols are backtested simultaneously" |
| Baseline ratio (0–1) | 0.7 | "Candidate must score at least this fraction of the average active symbol's efficiency" |
| Min efficiency floor | 0.0 | "Hard minimum profit-per-order regardless of baseline (0 = disabled)" |
| Margin (USDT) | 1000 | "Position size used to project potential gains" |
| Leverage | 1× | "Leverage multiplier used to project potential gains" |
| Klines count | 500 | "Number of recent candles used to backtest each candidate — fewer = faster" |

"Discover Symbols" button: disabled when `status === 'running'`.

### Progress bar (visible during `running` or just after `cancelled`)

```
Processed: 14 / 31 pre-candidates  ████████░░░░░░░░  45%
In progress: ETHUSDT, OPUSDT, ARBUSDT
[Cancel]
```

Cancel button calls `POST /api/discovery/cancel`.

### Results table (visible when `status === 'complete'` and candidates array non-empty)

Header summary row (styled like Cross-Symbol Comparison):
```
12 candidates found · Best single: +$143.20 · Total potential: +$892.40
```

Sortable columns (default: Efficiency Score descending):
Symbol | Efficiency | Profit% | Orders | Profit Factor | Win% | MaxDD | Sharpe | vs Baseline% | Potential $ | Best Preset | Add

"Add" button per row:
- Calls `POST /api/symbols` with the symbol
- Shows spinner while in-flight
- On success: row is removed from candidates table; symbol appears in Active Symbols panel

Table is sourced from `discovery_candidates.json`, persists across page navigations. Cleared (file overwritten) when a new run starts.

Empty state when `status === 'complete'` but no candidates: "No candidates passed the filters. Try lowering the baseline ratio or volume threshold."

## Defaults and Configuration

All numeric defaults are held in the React component state and written to `data/discovery_config.json` only when a run is triggered. No persistent user config file is maintained between sessions — the controls always reset to defaults on page load. (This keeps the implementation simple and avoids stale config drift.)

## Error Handling

- Binance API unreachable: `discover.py` writes `status=error`, `error="Exchange info fetch failed: <msg>"`, exits non-zero
- No pre-candidates after filtering: writes `status=complete`, empty candidates, logs reason
- Candidate backtest fails (no kline file, API error): that candidate is skipped, processing continues
- `discover.py` crashes unexpectedly: next page load detects stale `status=running` with dead PID (same `isAlive()` check used in registry) and updates status to `error`

## Stale Run Detection

The discovery run route checks on each `POST /api/discovery/run` call: if existing state has `status === 'running'` and PID is not alive → treat as stale, allow a new run. This uses the same `isAlive(pid)` helper extracted to `dashboard/app/api/_utils.ts` (shared between the symbols registry and the discovery routes).
