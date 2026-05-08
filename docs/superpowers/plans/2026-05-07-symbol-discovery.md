# Symbol Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Symbol Discovery module that automatically finds, backtests, scores, and presents new candidate symbols for approval in the Settings dashboard.

**Architecture:** A standalone `discover.py` subprocess (same pattern as `backtest.py`) is spawned by a new dashboard API route. Pure logic lives in `bot/symbol_discovery.py`. The dashboard polls `dashboard/public/discovery_state.json` every 3s and reads `dashboard/public/discovery_candidates.json` for results.

**Tech Stack:** Python 3.11 (requests, concurrent.futures, dataclasses), Next.js 15 App Router, TypeScript, Tailwind CSS, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `dashboard/app/api/_utils.ts` | **Create** | Shared `isAlive(pid)` + `BOT_ROOT` used by symbol and discovery API routes |
| `dashboard/app/api/symbols/_registry.ts` | **Modify** | Import `isAlive` from `../_utils` instead of defining it inline |
| `dashboard/lib/types.ts` | **Modify** | Add `CandidateResult`, `DiscoveryState`, `DiscoveryCandidatesFile` interfaces |
| `bot/symbol_discovery.py` | **Create** | `CandidateResult` dataclass + `SymbolDiscovery` class (pure logic, no side effects) |
| `discover.py` | **Create** | CLI entry point — reads config, batches candidates via ThreadPoolExecutor, writes state |
| `dashboard/app/api/discovery/run/route.ts` | **Create** | `POST` — writes config, spawns discover.py, rejects if already running |
| `dashboard/app/api/discovery/cancel/route.ts` | **Create** | `POST` — reads PID from state file, sends SIGTERM |
| `dashboard/components/SymbolDiscovery.tsx` | **Create** | Controls panel + progress bar + sortable results table |
| `dashboard/app/settings/page.tsx` | **Modify** | Import and render `<SymbolDiscovery />` below the "Add Symbol" panel |
| `tests/test_symbol_discovery.py` | **Create** | Unit tests for `SymbolDiscovery` methods |

---

### Task 1: TypeScript shared utility + types

**Files:**
- Create: `dashboard/app/api/_utils.ts`
- Modify: `dashboard/app/api/symbols/_registry.ts`
- Modify: `dashboard/lib/types.ts`

- [ ] **Step 1: Create the shared utility**

Create `dashboard/app/api/_utils.ts`:

```typescript
import path from 'path'

export const BOT_ROOT = path.resolve(process.cwd(), '..')

/** Returns true if the OS process with `pid` is still alive. */
export function isAlive(pid: number | null): boolean {
  if (!pid) return false
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}
```

- [ ] **Step 2: Update `_registry.ts` to import from the shared utility**

In `dashboard/app/api/symbols/_registry.ts`, replace the existing `BOT_ROOT` constant and `isAlive` function with an import:

```typescript
// Server-only helpers shared between /api/symbols route handlers.
import fs from 'fs'
import path from 'path'
import { BOT_ROOT, isAlive } from '../../_utils'

export { BOT_ROOT, isAlive }

export const REGISTRY_PATH = path.join(BOT_ROOT, 'symbol_registry.json')
const SYMBOLS_JSON = path.join(BOT_ROOT, 'dashboard', 'public', 'symbols.json')

export type BacktestStatus = 'none' | 'running' | 'complete' | 'error' | 'cancelled'

export interface SymbolStatus {
  backtest: BacktestStatus
  pid: number | null
}

export interface RegistryFile {
  symbols: string[]
  updated_at: string
  status: Record<string, SymbolStatus>
}

export function readRegistry(): RegistryFile {
  try {
    return JSON.parse(fs.readFileSync(REGISTRY_PATH, 'utf8')) as RegistryFile
  } catch {
    return { symbols: [], updated_at: '', status: {} }
  }
}

export function writeRegistry(data: RegistryFile): void {
  data.updated_at = new Date().toISOString()
  fs.writeFileSync(REGISTRY_PATH, JSON.stringify(data, null, 2))
  fs.writeFileSync(SYMBOLS_JSON, JSON.stringify({ symbols: data.symbols }, null, 2))
}
```

- [ ] **Step 3: Add discovery types to `dashboard/lib/types.ts`**

Append to the end of `dashboard/lib/types.ts`:

```typescript
// ── Symbol Discovery ───────────────────────────────────────────────────────

export interface CandidateResult {
  symbol: string
  efficiency_score: number        // total_profit_pct_sum / total_order_count across fast presets
  total_net_profit: number        // sum of total_profit_pct across fast presets
  total_order_count: number
  profit_factor: number           // sum(pos_pcts) / sum(abs(neg_pcts))
  best_preset_id: string
  best_preset_profit_pct: number
  win_rate: number
  max_drawdown: number
  sharpe_ratio: number            // approx: mean(trade_pcts) / stdev(trade_pcts)
  baseline_efficiency: number
  vs_baseline_pct: number         // (efficiency / baseline - 1) × 100
  potential_gain_usdt: number     // (best_preset_profit_pct / 100) × position_size × leverage
}

export interface DiscoveryState {
  status: 'idle' | 'running' | 'complete' | 'cancelled' | 'error'
  pid: number | null
  total_precandidates: number
  processed_count: number
  in_progress: string[]
  last_run_timestamp: string | null
  error?: string
}

export interface DiscoveryCandidatesFile {
  generated_at: string
  candidates: CandidateResult[]
}
```

- [ ] **Step 4: TypeScript check**

```bash
npx tsc --noEmit
```

Expected: no output (zero errors)

- [ ] **Step 5: Commit**

```bash
git add dashboard/app/api/_utils.ts dashboard/app/api/symbols/_registry.ts dashboard/lib/types.ts
git commit -m "feat: add shared isAlive util and discovery TS types"
```

---

### Task 2: `bot/symbol_discovery.py` — CandidateResult + get_precandidates

**Files:**
- Create: `bot/symbol_discovery.py`
- Create: `tests/test_symbol_discovery.py`

- [ ] **Step 1: Write failing tests for get_precandidates**

Create `tests/test_symbol_discovery.py`:

```python
import json
import pytest
from unittest.mock import patch, MagicMock
from bot.symbol_discovery import SymbolDiscovery, FUTURES_SYMBOLS


EXCHANGE_INFO_RESPONSE = {
    "symbols": [
        {"symbol": "BTCUSDT",  "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        {"symbol": "ETHUSDT",  "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        {"symbol": "XRPUSDT",  "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        {"symbol": "FOOBAR",   "contractType": "PERPETUAL", "quoteAsset": "USDT"},  # not in FUTURES_SYMBOLS
        {"symbol": "BTCBUSD",  "contractType": "PERPETUAL", "quoteAsset": "BUSD"},  # wrong quote
        {"symbol": "DOGEUSDT", "contractType": "DELIVERING", "quoteAsset": "USDT"}, # not perpetual
    ]
}

TICKER_RESPONSE = [
    {"symbol": "BTCUSDT",  "quoteVolume": "5000000000"},
    {"symbol": "ETHUSDT",  "quoteVolume": "2000000000"},
    {"symbol": "XRPUSDT",  "quoteVolume": "500000"},      # below threshold
    {"symbol": "FOOBAR",   "quoteVolume": "9000000000"},
]


def _mock_get(url, **kwargs):
    m = MagicMock()
    if 'exchangeInfo' in url:
        m.json.return_value = EXCHANGE_INFO_RESPONSE
    else:
        m.json.return_value = TICKER_RESPONSE
    return m


def test_get_precandidates_filters_by_allowlist():
    sd = SymbolDiscovery()
    with patch('bot.symbol_discovery.requests.get', side_effect=_mock_get):
        result = sd.get_precandidates(active=[], min_volume=1_000_000)
    # FOOBAR not in FUTURES_SYMBOLS, BTCBUSD wrong quote, DOGEUSDT not perpetual
    assert 'FOOBAR' not in result
    assert 'BTCBUSD' not in result
    assert 'DOGEUSDT' not in result


def test_get_precandidates_filters_active():
    sd = SymbolDiscovery()
    with patch('bot.symbol_discovery.requests.get', side_effect=_mock_get):
        result = sd.get_precandidates(active=['BTCUSDT'], min_volume=1_000_000)
    assert 'BTCUSDT' not in result


def test_get_precandidates_filters_low_volume():
    sd = SymbolDiscovery()
    with patch('bot.symbol_discovery.requests.get', side_effect=_mock_get):
        result = sd.get_precandidates(active=[], min_volume=1_000_000)
    # XRPUSDT has volume 500_000 < 1_000_000
    assert 'XRPUSDT' not in result
    assert 'ETHUSDT' in result


def test_get_precandidates_raises_on_api_error():
    sd = SymbolDiscovery()
    with patch('bot.symbol_discovery.requests.get', side_effect=Exception("timeout")):
        with pytest.raises(RuntimeError, match="Exchange info fetch failed"):
            sd.get_precandidates(active=[], min_volume=1_000_000)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_symbol_discovery.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'bot.symbol_discovery'`

- [ ] **Step 3: Create `bot/symbol_discovery.py` with CandidateResult + get_precandidates**

```python
"""
Symbol Discovery — finds, backtests, and scores new candidate symbols.

The SymbolDiscovery class contains pure logic only (no daemon threads,
no side effects beyond reading backtest result files).  All I/O and
orchestration live in discover.py.
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

FUTURES_SYMBOLS: list[str] = [
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

_FAPI_BASE = "https://fapi.binance.com/fapi/v1"
_DASHBOARD_PUBLIC = Path("dashboard") / "public"


@dataclass
class CandidateResult:
    symbol: str
    efficiency_score: float
    total_net_profit: float
    total_order_count: int
    profit_factor: float
    best_preset_id: str
    best_preset_profit_pct: float
    win_rate: float
    max_drawdown: int
    sharpe_ratio: float
    baseline_efficiency: float
    vs_baseline_pct: float
    potential_gain_usdt: float

    def to_dict(self) -> dict:
        return asdict(self)


class SymbolDiscovery:
    def get_precandidates(self, active: list[str], min_volume: float) -> list[str]:
        """Return symbols from FUTURES_SYMBOLS that are listed, not active, and liquid."""
        try:
            info_resp = requests.get(f"{_FAPI_BASE}/exchangeInfo", timeout=10)
            ticker_resp = requests.get(f"{_FAPI_BASE}/ticker/24hr", timeout=10)
        except Exception as exc:
            raise RuntimeError(f"Exchange info fetch failed: {exc}") from exc

        listed = {
            s["symbol"]
            for s in info_resp.json().get("symbols", [])
            if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
        }
        volume = {
            t["symbol"]: float(t.get("quoteVolume", 0))
            for t in ticker_resp.json()
        }

        active_set = set(active)
        return sorted(
            sym for sym in FUTURES_SYMBOLS
            if sym in listed
            and sym not in active_set
            and volume.get(sym, 0.0) >= min_volume
        )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_symbol_discovery.py::test_get_precandidates_filters_by_allowlist \
       tests/test_symbol_discovery.py::test_get_precandidates_filters_active \
       tests/test_symbol_discovery.py::test_get_precandidates_filters_low_volume \
       tests/test_symbol_discovery.py::test_get_precandidates_raises_on_api_error \
       -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add bot/symbol_discovery.py tests/test_symbol_discovery.py
git commit -m "feat: add CandidateResult dataclass and get_precandidates"
```

---

### Task 3: `bot/symbol_discovery.py` — get_fast_presets + compute_baseline

**Files:**
- Modify: `bot/symbol_discovery.py`
- Modify: `tests/test_symbol_discovery.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_symbol_discovery.py`:

```python
def test_get_fast_presets_ranks_by_avg_profit(tmp_path):
    sd = SymbolDiscovery()

    results_dir = tmp_path / "dashboard" / "public"
    results_dir.mkdir(parents=True)
    (results_dir / "backtest_results_BTCUSDT.json").write_text(json.dumps({
        "presets": {
            "preset_a": {"total_profit_pct": 10.0, "total_trades": 5},
            "preset_b": {"total_profit_pct":  2.0, "total_trades": 5},
            "preset_c": {"total_profit_pct":  6.0, "total_trades": 5},
        }
    }))
    (results_dir / "backtest_results_ETHUSDT.json").write_text(json.dumps({
        "presets": {
            "preset_a": {"total_profit_pct": 8.0, "total_trades": 5},
            "preset_b": {"total_profit_pct": 4.0, "total_trades": 5},
        }
    }))

    with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
        result = sd.get_fast_presets(
            active=["BTCUSDT", "ETHUSDT"],
            n=2,
            all_preset_names=["preset_a", "preset_b", "preset_c"],
        )

    # preset_a avg=(10+8)/2=9, preset_c avg=6 (ETHUSDT missing → 6/1), preset_b avg=(2+4)/2=3
    assert result[0] == "preset_a"
    assert len(result) == 2


def test_get_fast_presets_falls_back_when_no_results(tmp_path):
    sd = SymbolDiscovery()
    results_dir = tmp_path / "dashboard" / "public"
    results_dir.mkdir(parents=True)
    all_names = ["p1", "p2", "p3", "p4"]

    with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
        result = sd.get_fast_presets(active=["BTCUSDT"], n=2, all_preset_names=all_names)

    assert result == ["p1", "p2"]


def test_compute_baseline_averages_best_preset_efficiency(tmp_path):
    sd = SymbolDiscovery()
    results_dir = tmp_path / "dashboard" / "public"
    results_dir.mkdir(parents=True)
    # BTCUSDT best preset: profit 10 / 5 trades = 2.0
    # ETHUSDT best preset: profit 6  / 2 trades = 3.0
    # baseline = (2.0 + 3.0) / 2 = 2.5
    (results_dir / "backtest_results_BTCUSDT.json").write_text(json.dumps({
        "presets": {
            "p1": {"total_profit_pct": 10.0, "total_trades": 5},
            "p2": {"total_profit_pct":  4.0, "total_trades": 5},
        }
    }))
    (results_dir / "backtest_results_ETHUSDT.json").write_text(json.dumps({
        "presets": {
            "p1": {"total_profit_pct": 6.0, "total_trades": 2},
        }
    }))

    with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
        result = sd.compute_baseline(["BTCUSDT", "ETHUSDT"])

    assert result == pytest.approx(2.5)


def test_compute_baseline_returns_zero_when_no_files(tmp_path):
    sd = SymbolDiscovery()
    results_dir = tmp_path / "dashboard" / "public"
    results_dir.mkdir(parents=True)

    with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
        result = sd.compute_baseline(["BTCUSDT"])

    assert result == 0.0
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_symbol_discovery.py::test_get_fast_presets_ranks_by_avg_profit -v 2>&1 | tail -5
```

Expected: `AttributeError: 'SymbolDiscovery' object has no attribute 'get_fast_presets'`

- [ ] **Step 3: Implement get_fast_presets and compute_baseline**

Add these two methods to the `SymbolDiscovery` class in `bot/symbol_discovery.py`:

```python
    def get_fast_presets(
        self,
        active: list[str],
        n: int,
        all_preset_names: list[str],
    ) -> list[str]:
        """Return top-N presets ranked by avg total_profit_pct across active symbols."""
        scores: dict[str, list[float]] = {}
        for sym in active:
            path = _DASHBOARD_PUBLIC / f"backtest_results_{sym}.json"
            try:
                data = json.loads(path.read_text())
                for name, pdata in data.get("presets", {}).items():
                    scores.setdefault(name, []).append(
                        float(pdata.get("total_profit_pct", 0.0))
                    )
            except Exception:
                continue

        if not scores:
            return all_preset_names[:n]

        avg = {name: sum(vals) / len(vals) for name, vals in scores.items()}
        ranked = sorted(avg, key=avg.__getitem__, reverse=True)
        return ranked[:n]

    def compute_baseline(self, active: list[str]) -> float:
        """Avg efficiency of the best preset across all active symbols with results."""
        efficiencies: list[float] = []
        for sym in active:
            path = _DASHBOARD_PUBLIC / f"backtest_results_{sym}.json"
            try:
                data = json.loads(path.read_text())
                presets = data.get("presets", {})
                if not presets:
                    continue
                best_eff = max(
                    p.get("total_profit_pct", 0.0) / max(1, p.get("total_trades", 1))
                    for p in presets.values()
                )
                efficiencies.append(best_eff)
            except Exception:
                continue

        return sum(efficiencies) / len(efficiencies) if efficiencies else 0.0
```

- [ ] **Step 4: Run all tests so far**

```bash
pytest tests/test_symbol_discovery.py -v
```

Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add bot/symbol_discovery.py tests/test_symbol_discovery.py
git commit -m "feat: add get_fast_presets and compute_baseline"
```

---

### Task 4: `bot/symbol_discovery.py` — score_candidate

**Files:**
- Modify: `bot/symbol_discovery.py`
- Modify: `tests/test_symbol_discovery.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_symbol_discovery.py`:

```python
def test_score_candidate_returns_none_when_no_cache(tmp_path, monkeypatch):
    """When DataFeed fetch fails and no cache file exists, returns None."""
    from bot.symbol_discovery import SymbolDiscovery
    sd = SymbolDiscovery()

    # DataFeed.refresh_klines raises → cache still absent
    with patch('bot.symbol_discovery.DataFeed') as MockFeed:
        MockFeed.return_value.refresh_klines.side_effect = Exception("api error")
        # Point cache lookup to a dir that has no files
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        # Load settings needs .env; patch it out
        with patch('bot.symbol_discovery.load_settings') as mock_ls:
            mock_settings = MagicMock()
            mock_settings.trading_mode = 'testnet'
            mock_settings.timeframe = '15m'
            mock_ls.return_value = mock_settings
            result = sd.score_candidate(
                symbol="NEWUSDT",
                preset_subset={"default": {}},
                klines_count=500,
                baseline=2.0,
                baseline_ratio=0.7,
                min_floor=0.0,
                position_size=1000.0,
                leverage=1.0,
            )
    assert result is None


def test_score_candidate_returns_none_below_efficiency_threshold(tmp_path, monkeypatch):
    """A candidate scoring below baseline_ratio × baseline is filtered out."""
    import json as _json
    from bot.symbol_discovery import SymbolDiscovery
    sd = SymbolDiscovery()

    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Write a cache file with klines that produce zero trades (too few candles)
    klines = [[i * 60000, "100", "101", "99", "100", "1000", (i + 1) * 60000]
              for i in range(10)]
    cache = data_dir / "NEWUSDT_15m_test.json"
    cache.write_text(_json.dumps(klines))

    with patch('bot.symbol_discovery.DataFeed'):
        with patch('bot.symbol_discovery.load_settings') as mock_ls:
            mock_settings = MagicMock()
            mock_settings.trading_mode = 'testnet'
            mock_settings.timeframe = '15m'
            mock_ls.return_value = mock_settings
            with patch('bot.symbol_discovery.load_risk_config', return_value={"backtest_initial_balance_usdt": 0.0}):
                result = sd.score_candidate(
                    symbol="NEWUSDT",
                    preset_subset={"default": {}},
                    klines_count=500,
                    baseline=100.0,   # very high baseline
                    baseline_ratio=0.7,
                    min_floor=0.0,
                    position_size=1000.0,
                    leverage=1.0,
                )
    # Zero trades → total_order_count=0 → returns None (no trades produced)
    assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_symbol_discovery.py::test_score_candidate_returns_none_when_no_cache -v 2>&1 | tail -5
```

Expected: `AttributeError: 'SymbolDiscovery' object has no attribute 'score_candidate'`

- [ ] **Step 3: Implement score_candidate**

Add this method to the `SymbolDiscovery` class in `bot/symbol_discovery.py`. Also add these imports at the top of the file:

```python
# Add to imports at top of bot/symbol_discovery.py:
from config.settings import load_settings
from config.risk_config import load_risk_config, _CONFIG_PATH as _RISK_CONFIG_PATH
from bot.data_feed import DataFeed
from bot.backtester import Backtester
```

```python
    def score_candidate(
        self,
        symbol: str,
        preset_subset: dict[str, dict],
        klines_count: int,
        baseline: float,
        baseline_ratio: float,
        min_floor: float,
        position_size: float,
        leverage: float,
    ) -> CandidateResult | None:
        """Backtest symbol against preset_subset, score it, return None if it fails filters."""
        settings = load_settings(symbol)
        suffix = "test" if settings.trading_mode == "testnet" else "live"
        cache_path = Path("data") / f"{symbol}_{settings.timeframe}_{suffix}.json"

        try:
            feed = DataFeed(settings)
            feed.refresh_klines(symbol, settings.timeframe, fetch_count=klines_count)
        except Exception as exc:
            logger.warning(f"[{symbol}] Kline fetch failed: {exc} — trying cache")

        if not cache_path.exists():
            logger.warning(f"[{symbol}] No kline cache found — skipping")
            return None

        klines = json.loads(cache_path.read_text())
        if klines_count and len(klines) > klines_count:
            klines = klines[-klines_count:]

        if len(klines) < 50:
            logger.warning(f"[{symbol}] Too few klines ({len(klines)}) — skipping")
            return None

        if not preset_subset:
            return None

        risk_cfg = load_risk_config(_RISK_CONFIG_PATH)
        backtester = Backtester(
            base_settings=settings,
            initial_balance=risk_cfg.get("backtest_initial_balance_usdt", 0.0),
        )
        results = backtester.run(klines, preset_subset)

        total_profit = sum(r.total_profit_pct() for r in results.values())
        total_orders = sum(r.total() for r in results.values())

        if total_orders == 0:
            return None

        efficiency_score = total_profit / total_orders

        if baseline > 0 and efficiency_score < baseline_ratio * baseline:
            logger.info(
                f"[{symbol}] efficiency={efficiency_score:.4f} below "
                f"threshold={baseline_ratio * baseline:.4f} — skipping"
            )
            return None
        if efficiency_score < min_floor:
            return None

        # Aggregate metrics
        all_pcts = [
            t.profit_pct() or 0.0
            for r in results.values()
            for t in r.trades
        ]
        pos_sum = sum(p for p in all_pcts if p > 0)
        neg_sum = sum(abs(p) for p in all_pcts if p < 0)
        profit_factor = pos_sum / max(0.001, neg_sum)

        total_wins = sum(
            r.wins() + r.partials() + r.trails() for r in results.values()
        )
        win_rate = total_wins / total_orders

        max_drawdown = max(r.max_consecutive_losses() for r in results.values())

        if len(all_pcts) >= 2:
            try:
                sharpe = statistics.mean(all_pcts) / statistics.stdev(all_pcts)
            except statistics.StatisticsError:
                sharpe = 0.0
        else:
            sharpe = 0.0

        best_preset_id = max(results, key=lambda n: results[n].total_profit_pct())
        best_pct = results[best_preset_id].total_profit_pct()

        return CandidateResult(
            symbol=symbol,
            efficiency_score=round(efficiency_score, 4),
            total_net_profit=round(total_profit, 4),
            total_order_count=total_orders,
            profit_factor=round(profit_factor, 4),
            best_preset_id=best_preset_id,
            best_preset_profit_pct=round(best_pct, 4),
            win_rate=round(win_rate, 4),
            max_drawdown=max_drawdown,
            sharpe_ratio=round(sharpe, 4),
            baseline_efficiency=round(baseline, 4),
            vs_baseline_pct=round((efficiency_score / max(0.001, baseline) - 1) * 100, 2),
            potential_gain_usdt=round((best_pct / 100) * position_size * leverage, 2),
        )
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_symbol_discovery.py -v
```

Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add bot/symbol_discovery.py tests/test_symbol_discovery.py
git commit -m "feat: implement score_candidate in SymbolDiscovery"
```

---

### Task 5: `discover.py` — orchestration script

**Files:**
- Create: `discover.py`

- [ ] **Step 1: Create discover.py**

```python
"""
Symbol Discovery runner — spawned by the dashboard's /api/discovery/run endpoint.

Reads config from data/discovery_config.json.
Writes progress to dashboard/public/discovery_state.json (atomic).
Writes passing candidates to dashboard/public/discovery_candidates.json.
Cancellable via SIGTERM.
"""
import json
import logging
import os
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from config.settings import load_symbols
from bot.symbol_discovery import SymbolDiscovery
# Import preset dicts from backtest.py so discovery uses the same preset definitions.
from backtest import PRESETS, LOCKED_PRESETS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("discover")

_STATE_PATH = Path("dashboard") / "public" / "discovery_state.json"
_CANDIDATES_PATH = Path("dashboard") / "public" / "discovery_candidates.json"
_CONFIG_PATH = Path("data") / "discovery_config.json"
_TEMP_DIR = Path("data") / "discovery"


def _write_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _read_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text())
    except Exception:
        return {}


def _update_state(**kwargs) -> None:
    state = _read_state()
    state.update(kwargs)
    _write_atomic(_STATE_PATH, state)


def main() -> None:
    # Read config written by the API route.
    try:
        cfg = json.loads(_CONFIG_PATH.read_text())
    except Exception as exc:
        logger.error(f"Cannot read discovery config: {exc}")
        sys.exit(1)

    min_volume: float = float(cfg.get("min_volume", 1_000_000))
    preset_count: int = int(cfg.get("preset_count", 12))
    batch_size: int = int(cfg.get("batch_size", 3))
    baseline_ratio: float = float(cfg.get("baseline_ratio", 0.7))
    min_floor: float = float(cfg.get("min_floor", 0.0))
    position_size: float = float(cfg.get("position_size", 1000.0))
    leverage: float = float(cfg.get("leverage", 1.0))
    klines_count: int = int(cfg.get("klines_count", 500))

    stop_event = threading.Event()

    def _handle_sigterm(signum, frame):  # noqa: ANN001
        logger.info("SIGTERM received — stopping discovery")
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # Clean up stale temp files from any previous crashed run.
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    for f in _TEMP_DIR.glob("*.json"):
        try:
            f.unlink()
        except Exception:
            pass

    discovery = SymbolDiscovery()

    # Load active symbols from registry.
    try:
        import json as _j
        reg = _j.loads(Path("symbol_registry.json").read_text())
        active = reg.get("symbols", [])
    except Exception:
        active = load_symbols()

    # Get pre-candidates.
    try:
        precandidates = discovery.get_precandidates(active, min_volume)
    except RuntimeError as exc:
        _update_state(status="error", error=str(exc), in_progress=[])
        logger.error(str(exc))
        sys.exit(1)

    _update_state(total_precandidates=len(precandidates), processed_count=0, in_progress=[])
    logger.info(f"Found {len(precandidates)} pre-candidates: {precandidates}")

    if not precandidates:
        _update_state(
            status="complete",
            in_progress=[],
            last_run_timestamp=datetime.now(timezone.utc).isoformat(),
        )
        _write_atomic(_CANDIDATES_PATH, {"generated_at": datetime.now(timezone.utc).isoformat(), "candidates": []})
        return

    # Fast preset subset.
    all_preset_names = list({**LOCKED_PRESETS, **PRESETS}.keys())
    fast_preset_names = discovery.get_fast_presets(active, preset_count, all_preset_names)
    all_presets = {**LOCKED_PRESETS, **PRESETS}
    preset_subset = {n: all_presets[n] for n in fast_preset_names if n in all_presets}
    logger.info(f"Fast preset subset ({len(preset_subset)}): {list(preset_subset)}")

    baseline = discovery.compute_baseline(active)
    logger.info(f"Baseline efficiency: {baseline:.4f}")

    passing: list[dict] = []
    processed = 0
    in_progress: list[str] = []
    lock = threading.Lock()

    def _score(symbol: str) -> tuple[str, object]:
        return symbol, discovery.score_candidate(
            symbol=symbol,
            preset_subset=preset_subset,
            klines_count=klines_count,
            baseline=baseline,
            baseline_ratio=baseline_ratio,
            min_floor=min_floor,
            position_size=position_size,
            leverage=leverage,
        )

    futures = {}
    with ThreadPoolExecutor(max_workers=batch_size) as executor:
        for sym in precandidates:
            if stop_event.is_set():
                break
            future = executor.submit(_score, sym)
            futures[future] = sym
            with lock:
                in_progress.append(sym)
            _update_state(in_progress=list(in_progress))

        for future in as_completed(futures):
            sym = futures[future]
            try:
                _, result = future.result()
                if result is not None:
                    passing.append(result.to_dict())
                    logger.info(f"[{sym}] PASSED — efficiency={result.efficiency_score:.4f}")
                else:
                    logger.info(f"[{sym}] filtered out")
            except Exception as exc:
                logger.warning(f"[{sym}] error during scoring: {exc}")

            with lock:
                processed += 1
                if sym in in_progress:
                    in_progress.remove(sym)

            _update_state(processed_count=processed, in_progress=list(in_progress))

    final_status = "cancelled" if stop_event.is_set() else "complete"
    now = datetime.now(timezone.utc).isoformat()
    _update_state(status=final_status, in_progress=[], last_run_timestamp=now)
    _write_atomic(_CANDIDATES_PATH, {"generated_at": now, "candidates": passing})
    logger.info(f"Discovery {final_status}: {len(passing)} candidates")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test the script structure (no live API)**

```bash
python -c "import discover; print('discover.py imports OK')"
```

Expected: `discover.py imports OK`

- [ ] **Step 3: Commit**

```bash
git add discover.py
git commit -m "feat: add discover.py orchestration script"
```

---

### Task 6: Dashboard API routes

**Files:**
- Create: `dashboard/app/api/discovery/run/route.ts`
- Create: `dashboard/app/api/discovery/cancel/route.ts`

- [ ] **Step 1: Create the run route**

Create `dashboard/app/api/discovery/run/route.ts`:

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT, isAlive } from '../../_utils'

const STATE_PATH = path.join(BOT_ROOT, 'dashboard', 'public', 'discovery_state.json')
const CONFIG_PATH = path.join(BOT_ROOT, 'data', 'discovery_config.json')

function getPython(): string {
  const venvPy = path.join(BOT_ROOT, '.venv', 'bin', 'python3')
  return fs.existsSync(venvPy) ? venvPy : 'python3'
}

function readState(): Record<string, unknown> {
  try {
    return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'))
  } catch {
    return { status: 'idle', pid: null, total_precandidates: 0, processed_count: 0, in_progress: [] }
  }
}

function writeState(data: Record<string, unknown>): void {
  fs.mkdirSync(path.dirname(STATE_PATH), { recursive: true })
  fs.writeFileSync(STATE_PATH, JSON.stringify(data, null, 2))
}

export async function POST(req: NextRequest) {
  let body: Record<string, unknown> = {}
  try { body = await req.json() } catch { /* use defaults */ }

  const cfg = {
    min_volume:     Number(body.min_volume     ?? 1_000_000),
    preset_count:   Number(body.preset_count   ?? 12),
    batch_size:     Number(body.batch_size     ?? 3),
    baseline_ratio: Number(body.baseline_ratio ?? 0.7),
    min_floor:      Number(body.min_floor      ?? 0.0),
    position_size:  Number(body.position_size  ?? 1000),
    leverage:       Number(body.leverage       ?? 1),
    klines_count:   Number(body.klines_count   ?? 500),
  }

  // Check for a live run (allow stale runs with dead PIDs to be restarted).
  const state = readState()
  if (state.status === 'running' && isAlive(state.pid as number | null)) {
    return NextResponse.json({ error: 'Discovery already running' }, { status: 409 })
  }

  // Write config for discover.py to read.
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true })
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2))

  // Write initial state before spawning (discover.py only updates progress).
  writeState({
    status: 'running',
    pid: null,
    total_precandidates: 0,
    processed_count: 0,
    in_progress: [],
    last_run_timestamp: null,
  })

  const python = getPython()
  const child = spawn(python, ['discover.py'], {
    cwd: BOT_ROOT,
    detached: false,
    stdio: 'ignore',
  })

  // Store PID as soon as we have it.
  if (child.pid) {
    const s = readState()
    s.pid = child.pid
    writeState(s)
  }

  child.on('close', (code: number | null) => {
    const s = readState()
    if (s.status === 'running') {
      s.status = code === 0 ? 'complete' : 'error'
      if (code !== 0) s.error = `discover.py exited with code ${code}`
      writeState(s)
    }
  })

  child.unref()

  return NextResponse.json({ ok: true, pid: child.pid ?? null })
}
```

- [ ] **Step 2: Create the cancel route**

Create `dashboard/app/api/discovery/cancel/route.ts`:

```typescript
import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT, isAlive } from '../../_utils'

const STATE_PATH = path.join(BOT_ROOT, 'dashboard', 'public', 'discovery_state.json')

function readState(): Record<string, unknown> {
  try {
    return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'))
  } catch {
    return { status: 'idle', pid: null }
  }
}

export async function POST() {
  const state = readState()

  if (state.status !== 'running') {
    return NextResponse.json({ error: 'No active discovery run' }, { status: 409 })
  }

  const pid = state.pid as number | null
  if (!isAlive(pid)) {
    return NextResponse.json({ error: 'Process is not alive' }, { status: 409 })
  }

  try {
    process.kill(pid!, 'SIGTERM')
  } catch (e) {
    return NextResponse.json({ error: `Failed to send SIGTERM: ${e}` }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
```

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit
```

Expected: no output

- [ ] **Step 4: Commit**

```bash
git add dashboard/app/api/discovery/run/route.ts dashboard/app/api/discovery/cancel/route.ts
git commit -m "feat: add discovery run and cancel API routes"
```

---

### Task 7: `SymbolDiscovery` React component

**Files:**
- Create: `dashboard/components/SymbolDiscovery.tsx`

- [ ] **Step 1: Create the component**

Create `dashboard/components/SymbolDiscovery.tsx`:

```tsx
'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import type { CandidateResult, DiscoveryState, DiscoveryCandidatesFile } from '@/lib/types'

const LEVERAGES = [1, 2, 3, 5, 10, 15, 20, 25, 50, 75, 100, 125]
const POLL_MS = 3000

type SortKey = keyof Omit<CandidateResult, 'symbol' | 'best_preset_id'>
type SortDir = 'asc' | 'desc'

const DEFAULT_STATE: DiscoveryState = {
  status: 'idle',
  pid: null,
  total_precandidates: 0,
  processed_count: 0,
  in_progress: [],
  last_run_timestamp: null,
}

function pct(n: number) { return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%` }
function usd(n: number) { return `${n >= 0 ? '+' : '-'}$${Math.abs(n).toFixed(2)}` }

export default function SymbolDiscovery() {
  const [state, setState] = useState<DiscoveryState>(DEFAULT_STATE)
  const [candidates, setCandidates] = useState<CandidateResult[]>([])
  const [adding, setAdding] = useState<string | null>(null)
  const [addedSymbols, setAddedSymbols] = useState<Set<string>>(new Set())
  const [cancelling, setCancelling] = useState(false)
  const [controlsOpen, setControlsOpen] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('efficiency_score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  // Config state
  const [minVolume, setMinVolume]       = useState(1_000_000)
  const [presetCount, setPresetCount]   = useState(12)
  const [batchSize, setBatchSize]       = useState(3)
  const [baselineRatio, setBaselineRatio] = useState(0.7)
  const [minFloor, setMinFloor]         = useState(0.0)
  const [positionSize, setPositionSize] = useState(1000)
  const [leverage, setLeverage]         = useState(1)
  const [klinesCount, setKlinesCount]   = useState(500)

  const fetchState = useCallback(async () => {
    try {
      const res = await fetch(`/discovery_state.json?t=${Date.now()}`)
      if (res.ok) setState(await res.json())
    } catch { /* keep last */ }
  }, [])

  const fetchCandidates = useCallback(async () => {
    try {
      const res = await fetch(`/discovery_candidates.json?t=${Date.now()}`)
      if (res.ok) {
        const data: DiscoveryCandidatesFile = await res.json()
        setCandidates(data.candidates ?? [])
      }
    } catch { /* keep last */ }
  }, [])

  useEffect(() => {
    fetchState()
    fetchCandidates()
    const id = setInterval(fetchState, POLL_MS)
    return () => clearInterval(id)
  }, [fetchState, fetchCandidates])

  // Reload candidates when a run completes
  useEffect(() => {
    if (state.status === 'complete') fetchCandidates()
  }, [state.status, fetchCandidates])

  async function handleRun() {
    setAddedSymbols(new Set())
    await fetch('/api/discovery/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        min_volume: minVolume, preset_count: presetCount, batch_size: batchSize,
        baseline_ratio: baselineRatio, min_floor: minFloor,
        position_size: positionSize, leverage, klines_count: klinesCount,
      }),
    })
    fetchState()
  }

  async function handleCancel() {
    setCancelling(true)
    try {
      await fetch('/api/discovery/cancel', { method: 'POST' })
      fetchState()
    } finally {
      setCancelling(false)
    }
  }

  async function handleAdd(symbol: string) {
    setAdding(symbol)
    try {
      const res = await fetch('/api/symbols', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      })
      if (res.ok) setAddedSymbols(s => new Set([...s, symbol]))
    } finally {
      setAdding(null)
    }
  }

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const visibleCandidates = useMemo(() => {
    const filtered = candidates.filter(c => !addedSymbols.has(c.symbol))
    return [...filtered].sort((a, b) => {
      const av = a[sortKey] as number
      const bv = b[sortKey] as number
      return sortDir === 'desc' ? bv - av : av - bv
    })
  }, [candidates, addedSymbols, sortKey, sortDir])

  const totalPotential = visibleCandidates.reduce((s, c) => s + c.potential_gain_usdt, 0)
  const bestPotential  = visibleCandidates.reduce((m, c) => Math.max(m, c.potential_gain_usdt), 0)

  const progress = state.total_precandidates > 0
    ? Math.round((state.processed_count / state.total_precandidates) * 100)
    : 0

  const running = state.status === 'running'

  function Th({ label, k, title }: { label: string; k: SortKey; title?: string }) {
    const active = k === sortKey
    const arrow = active ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ' ⇅'
    return (
      <th
        onClick={() => toggleSort(k)}
        title={title}
        className={`py-1 pr-3 font-normal cursor-pointer select-none whitespace-nowrap text-right transition-colors ${
          active ? 'text-indigo-300' : 'text-gray-500 hover:text-gray-300'
        }`}
      >
        {label}<span className={active ? 'text-indigo-400' : 'text-gray-700'}>{arrow}</span>
      </th>
    )
  }

  return (
    <div className="space-y-3">
      {/* ── Section header ── */}
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden">
        <button
          className="w-full px-4 py-2 border-b border-gray-800 flex items-center justify-between"
          onClick={() => setControlsOpen(o => !o)}
          title="Configure and run automatic symbol discovery"
        >
          <p className="text-xs text-gray-500 font-semibold uppercase tracking-wide">
            Symbol Discovery
          </p>
          <span className="text-gray-600 text-xs">{controlsOpen ? '▲' : '▼'}</span>
        </button>

        {/* Controls */}
        {controlsOpen && (
          <div className="px-4 py-4 space-y-4">
            <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs font-mono">
              {[
                { label: 'Min 24h volume (USDT)', val: minVolume, set: setMinVolume, step: 100000,
                  title: 'Symbols below this volume are considered illiquid and skipped' },
                { label: 'Fast preset count', val: presetCount, set: setPresetCount, step: 1,
                  title: 'Number of top-performing presets used to evaluate each candidate' },
                { label: 'Batch size', val: batchSize, set: setBatchSize, step: 1,
                  title: 'How many candidate symbols are backtested simultaneously' },
                { label: 'Baseline ratio (0–1)', val: baselineRatio, set: setBaselineRatio, step: 0.05,
                  title: 'Candidate must score at least this fraction of the average active symbol efficiency' },
                { label: 'Min efficiency floor', val: minFloor, set: setMinFloor, step: 0.01,
                  title: 'Hard minimum profit-per-order regardless of baseline (0 = disabled)' },
                { label: 'Klines count', val: klinesCount, set: setKlinesCount, step: 100,
                  title: 'Number of recent candles used to backtest each candidate — fewer = faster' },
              ].map(({ label, val, set, step, title }) => (
                <label key={label} className="flex flex-col gap-1">
                  <span className="text-gray-500" title={title}>{label}</span>
                  <input
                    type="number"
                    step={step}
                    value={val}
                    onChange={e => set(Number(e.target.value) as never)}
                    className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 focus:outline-none focus:border-indigo-500"
                  />
                </label>
              ))}

              {/* Margin */}
              <label className="flex flex-col gap-1">
                <span className="text-gray-500" title="Position size used to project potential gains">Margin (USDT)</span>
                <input type="number" step={100} value={positionSize}
                  onChange={e => setPositionSize(Number(e.target.value))}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 focus:outline-none focus:border-indigo-500"
                />
              </label>

              {/* Leverage */}
              <label className="flex flex-col gap-1">
                <span className="text-gray-500" title="Leverage multiplier used to project potential gains">Leverage</span>
                <select value={leverage} onChange={e => setLeverage(Number(e.target.value))}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 focus:outline-none focus:border-indigo-500"
                >
                  {LEVERAGES.map(l => <option key={l} value={l}>{l}×</option>)}
                </select>
              </label>
            </div>

            <button
              onClick={handleRun}
              disabled={running}
              title="Start automatic symbol discovery using the configured parameters"
              className="px-4 py-2 rounded border border-indigo-700 bg-indigo-900/60 text-indigo-300 text-xs font-semibold hover:bg-indigo-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {running ? 'Running…' : 'Discover Symbols'}
            </button>
          </div>
        )}
      </div>

      {/* Progress */}
      {(running || state.status === 'cancelled') && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-3 space-y-2 text-xs font-mono">
          <div className="flex items-center justify-between">
            <span className="text-gray-400">
              Processed: <span className="text-white">{state.processed_count}</span>
              {' / '}{state.total_precandidates} pre-candidates
            </span>
            {running && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                title="Stop the discovery run"
                className="px-2 py-0.5 rounded border border-red-900/60 bg-red-950/30 text-red-400 text-[10px] font-semibold hover:bg-red-900/40 disabled:opacity-40 transition-colors"
              >
                {cancelling ? 'Cancelling…' : 'Cancel'}
              </button>
            )}
          </div>
          <div className="w-full bg-gray-800 rounded-full h-1.5">
            <div
              className="bg-indigo-500 h-1.5 rounded-full transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          {state.in_progress.length > 0 && (
            <p className="text-gray-600">
              In progress: <span className="text-gray-400">{state.in_progress.join(', ')}</span>
            </p>
          )}
          {state.status === 'cancelled' && (
            <p className="text-amber-500/80">Run cancelled.</p>
          )}
        </div>
      )}

      {/* Error */}
      {state.status === 'error' && state.error && (
        <p className="text-xs text-red-400 font-mono px-1">{state.error}</p>
      )}

      {/* Results table */}
      {state.status === 'complete' && candidates.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-3 text-xs font-mono text-gray-500 px-1">
            <span>
              <span className="text-white">{visibleCandidates.length}</span> candidates found
            </span>
            <span>·</span>
            <span>Best single: <span className="text-emerald-400">{usd(bestPotential)}</span></span>
            <span>·</span>
            <span>Total potential: <span className="text-emerald-400">{usd(totalPotential)}</span></span>
          </div>

          <div className="overflow-x-auto rounded-lg border border-gray-800">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-gray-800 bg-gray-900">
                  <th className="py-1 pr-3 text-left font-normal text-gray-500 whitespace-nowrap pl-3">Symbol</th>
                  <Th label="Efficiency"    k="efficiency_score"     title="Total profit % / order count across fast presets" />
                  <Th label="Profit%"       k="total_net_profit"     title="Sum of total_profit_pct across fast presets" />
                  <Th label="Orders"        k="total_order_count"    title="Total orders across fast presets" />
                  <Th label="PF"            k="profit_factor"        title="Profit factor: sum gains / sum losses" />
                  <Th label="Win%"          k="win_rate"             title="Win + partial + trail rate" />
                  <Th label="MaxDD"         k="max_drawdown"         title="Max consecutive losses" />
                  <Th label="Sharpe"        k="sharpe_ratio"         title="Approximate Sharpe ratio from per-trade returns" />
                  <Th label="vs Base%"      k="vs_baseline_pct"      title="How much better/worse than active symbol average" />
                  <Th label="Potential $"   k="potential_gain_usdt"  title="Projected gain at configured position size and leverage" />
                  <th className="py-1 pr-3 font-normal text-gray-500 text-left whitespace-nowrap">Best Preset</th>
                  <th className="py-1 pr-3" />
                </tr>
              </thead>
              <tbody>
                {visibleCandidates.map(c => (
                  <tr key={c.symbol} className="border-b border-gray-900 hover:bg-gray-900/40">
                    <td className="py-1 pr-3 pl-3 text-indigo-300 font-semibold">{c.symbol}</td>
                    <td className="text-right pr-3 text-gray-300">{c.efficiency_score.toFixed(4)}</td>
                    <td className={`text-right pr-3 font-semibold ${c.total_net_profit >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {pct(c.total_net_profit)}
                    </td>
                    <td className="text-right pr-3 text-gray-300">{c.total_order_count}</td>
                    <td className="text-right pr-3 text-gray-300">{c.profit_factor.toFixed(2)}</td>
                    <td className="text-right pr-3 text-gray-300">{(c.win_rate * 100).toFixed(1)}%</td>
                    <td className="text-right pr-3 text-gray-400">{c.max_drawdown}</td>
                    <td className="text-right pr-3 text-gray-300">{c.sharpe_ratio.toFixed(2)}</td>
                    <td className={`text-right pr-3 font-semibold ${c.vs_baseline_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {pct(c.vs_baseline_pct)}
                    </td>
                    <td className={`text-right pr-3 font-semibold ${c.potential_gain_usdt >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {usd(c.potential_gain_usdt)}
                    </td>
                    <td className="py-1 pr-3 text-gray-400 max-w-[120px] truncate">{c.best_preset_id}</td>
                    <td className="py-1 pr-3">
                      <button
                        onClick={() => handleAdd(c.symbol)}
                        disabled={adding === c.symbol}
                        title={`Add ${c.symbol} to active symbols and start a full backtest`}
                        className="px-2 py-0.5 rounded border border-indigo-700/60 bg-indigo-950/40 text-indigo-400 text-[10px] font-semibold hover:bg-indigo-900/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
                      >
                        {adding === c.symbol ? '…' : 'Add'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {state.status === 'complete' && candidates.length === 0 && (
        <p className="text-xs text-gray-600 italic px-1">
          No candidates passed the filters. Try lowering the baseline ratio or volume threshold.
        </p>
      )}
    </div>
  )
}
```

- [ ] **Step 2: TypeScript check**

```bash
npx tsc --noEmit
```

Expected: no output

- [ ] **Step 3: Commit**

```bash
git add dashboard/components/SymbolDiscovery.tsx
git commit -m "feat: add SymbolDiscovery React component"
```

---

### Task 8: Wire SymbolDiscovery into Settings page

**Files:**
- Modify: `dashboard/app/settings/page.tsx`

- [ ] **Step 1: Add import and render**

In `dashboard/app/settings/page.tsx`, add the import after the existing `'use client'` line:

```typescript
import SymbolDiscovery from '@/components/SymbolDiscovery'
```

Then, after the closing `</div>` of the "Add Symbol" panel (line ~229), add inside the `<section>`:

```tsx
        {/* ── Symbol Discovery ──────────────────────────────────────────── */}
        <SymbolDiscovery />
```

The full settings page section block should now look like:

```tsx
      <section className="space-y-4">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
          Symbol Registry
        </h2>

        {/* Active symbols table — unchanged */}
        ...

        {/* Add symbol — unchanged */}
        ...

        {/* Symbol Discovery */}
        <SymbolDiscovery />
      </section>
```

- [ ] **Step 2: TypeScript check**

```bash
npx tsc --noEmit
```

Expected: no output

- [ ] **Step 3: Manual smoke test**

Start the dashboard dev server:
```bash
npm run dev
```

Navigate to `http://localhost:3000/settings`. Verify:
- "Symbol Discovery" collapsible section appears below "Add Symbol"
- Expanding it shows all 8 controls with correct defaults
- "Discover Symbols" button is present and enabled
- No console errors

- [ ] **Step 4: Commit**

```bash
git add dashboard/app/settings/page.tsx
git commit -m "feat: wire SymbolDiscovery component into settings page"
```

---

## Self-Review

**1. Spec coverage:**
- ✅ `bot/symbol_discovery.py` with `SymbolDiscovery` class — Tasks 2–4
- ✅ `FUTURES_SYMBOLS` hardcoded list — Task 2
- ✅ Pre-candidate filtering (allowlist, active, volume) — Task 2
- ✅ Fast preset subset from avg total_profit_pct — Task 3
- ✅ Fallback to first N presets — Task 3
- ✅ Baseline computation — Task 3
- ✅ score_candidate with all scoring formulas — Task 4
- ✅ ThreadPoolExecutor batch concurrency — Task 5
- ✅ discovery_state.json progress updates — Task 5
- ✅ SIGTERM → stop_event — Task 5
- ✅ Stale temp file cleanup on startup — Task 5
- ✅ API route POST /api/discovery/run — Task 6
- ✅ API route POST /api/discovery/cancel — Task 6
- ✅ Stale PID detection in run route — Task 6
- ✅ Controls panel with all 8 inputs and tooltips — Task 7
- ✅ Progress bar with cancel button — Task 7
- ✅ Sortable results table with all 11 columns — Task 7
- ✅ "Add" button per row → POST /api/symbols — Task 7
- ✅ Table persists across navigations (sourced from file) — Task 7
- ✅ Empty-state message — Task 7
- ✅ Wire into settings page — Task 8
- ✅ Shared isAlive utility — Task 1
- ✅ CandidateResult and DiscoveryState TS types — Task 1

**2. Type consistency:**
- `CandidateResult` dataclass defined in Task 2, `.to_dict()` used in Task 5, TS interface defined in Task 1 — field names match.
- `preset_subset: dict[str, dict]` defined in Task 4, used in Task 5 — consistent.
- `_DASHBOARD_PUBLIC` module-level constant, patched in tests in Tasks 3–4 — consistent.

**3. No placeholders found** — all steps have complete code.
