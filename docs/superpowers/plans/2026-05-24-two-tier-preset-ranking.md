# Two-Tier Preset Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hard `_MIN_TRADES = 8` binary cutoff with a two-tier ranking system where any live-proven preset (≥ N real+virtual trades) always outranks any seed-only preset, with N configurable per symbol from the Risk dashboard.

**Architecture:** Module-level `_score(stats, min_trades) -> tuple[int, float]` replaces the nested numeric scorer; tuple comparison ensures Tier 1 always beats Tier 2 without any explicit conditional. `risk_config.py` gains a `get_min_trades_for_ranking(cfg, symbol)` helper; `VirtualTracker.__init__` accepts `get_min_trades: Callable[[str], int]` so main.py can inject the live config lookup.

**Tech Stack:** Python 3.12, Next.js 15 App Router, TypeScript, Tailwind v4.

---

## File Map

| File | Change |
|---|---|
| `config/risk_config.py` | Add 2 defaults + `get_min_trades_for_ranking()` helper |
| `bot/virtual_tracker.py` | Remove `_MIN_TRADES`; add `get_min_trades` param; tuple scoring in 3 methods |
| `main.py` | Pass `get_min_trades` lambda to both `VirtualTracker` constructions |
| `dashboard/lib/risk-types.ts` | Add 2 fields to `RiskConfig` |
| `dashboard/app/api/risk/route.ts` | Add 2 defaults to `DEFAULT_CONFIG` |
| `dashboard/components/risk/PresetRankingSection.tsx` | New component (create) |
| `dashboard/app/risk/page.tsx` | Import + render `PresetRankingSection` |
| `tests/test_virtual_tracker.py` | Update `_make_tracker`; update 2 comments; add 4 new tests |

---

### Task 1: Add config defaults and helper to risk_config.py

**Files:**
- Modify: `config/risk_config.py`

- [ ] **Step 1: Add two keys to DEFAULT_CONFIG**

In `config/risk_config.py`, add after the `"weight_rebalancer"` block (line 53, before the closing `}`):

```python
    "min_trades_for_ranking": 3,
    "min_trades_for_ranking_per_symbol": {},
```

The full block after edit — `DEFAULT_CONFIG` ends with:
```python
    "weight_rebalancer": {
        "enabled": False,
        "rebalance_candles": 96,
        "backtest_window_candles": 96,
        "real_pnl_alpha": 0.5,
        "blend_rate": 0.15,
        "weight_floor_ratio": 0.3,
    },
    "min_trades_for_ranking": 3,
    "min_trades_for_ranking_per_symbol": {},
}
```

- [ ] **Step 2: Add the helper function after `_atomic_write`**

Append to the bottom of `config/risk_config.py`:

```python
def get_min_trades_for_ranking(cfg: dict, symbol: str) -> int:
    """Return min trades threshold for symbol, falling back to global default."""
    per_sym = cfg.get("min_trades_for_ranking_per_symbol", {})
    return int(per_sym.get(symbol, cfg.get("min_trades_for_ranking", 3)))
```

- [ ] **Step 3: Verify no import errors**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python3 -c "from config.risk_config import load_risk_config, get_min_trades_for_ranking; cfg = load_risk_config(); print(get_min_trades_for_ranking(cfg, 'BTCUSDT'), get_min_trades_for_ranking(cfg, 'TIAUSDT'))"
```

Expected output: `3 3`

- [ ] **Step 4: Commit**

```bash
git add config/risk_config.py
git commit -m "feat: add min_trades_for_ranking config fields and helper"
```

---

### Task 2: Rewrite VirtualTracker scoring to two-tier tuples

**Files:**
- Modify: `bot/virtual_tracker.py`

- [ ] **Step 1: Replace `_MIN_TRADES` constant with module-level `_score` function**

Remove line 11:
```python
_MIN_TRADES = 8  # combined real + virtual trades before live score overrides the backtest seed
```

Add in its place (after the imports, before `_SENTINEL`):

```python
def _score(stats: dict, min_trades: int) -> tuple[int, float]:
    """Two-tier score: Tier 1 (live-proven) always beats Tier 2 (seed-only).

    Tier 1: trade_count >= min_trades → ranked by live total_winning_usdt.
    Tier 2: trade_count <  min_trades → ranked by seeded_winning_usdt (backtest).
    Python tuple comparison ensures any (1, x) > any (0, y).
    """
    count = stats.get("trade_count", 0)
    if count >= min_trades:
        return (1, stats.get("total_winning_usdt", 0.0))
    return (0, stats.get("seeded_winning_usdt", 0.0))
```

- [ ] **Step 2: Add `get_min_trades` parameter to `__init__`**

Replace the `__init__` signature and body storage:

```python
def __init__(
    self,
    mode: Literal["test", "live"],
    orders_path: Path,
    efficiency_path: Path,
    get_min_trades: Callable[[str], int] = lambda _: 3,
) -> None:
    self._mode = mode
    self._orders_path = orders_path
    self._efficiency_path = efficiency_path
    self._get_min_trades = get_min_trades
    self._efficiency: dict = self._load_efficiency()
    self._last_best: dict[str, str | None] = {}
```

Also add `Callable` to the imports at the top of the file:

```python
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal
```

- [ ] **Step 3: Update `best_preset` to use tuple scoring**

Replace the entire `best_preset` method:

```python
def best_preset(self, symbol: str) -> str | None:
    symbol_data = self._efficiency.get(symbol, {})
    if not symbol_data:
        return None

    min_t = self._get_min_trades(symbol)
    best = max(symbol_data, key=lambda n: _score(symbol_data[n], min_t))
    result = best if _score(symbol_data[best], min_t)[1] >= 0 else None

    prev = self._last_best.get(symbol, _SENTINEL)
    if prev is not _SENTINEL and prev != result:
        prev_stats = symbol_data.get(prev or '', {})
        new_stats = symbol_data.get(result or '', {})
        logger.info(
            f"[{symbol}] Best preset changed: {prev!r} -> {result!r} | "
            f"prev(cnt={prev_stats.get('trade_count', 0)}, "
            f"seeded={prev_stats.get('seeded_winning_usdt', 0.0):.2f}, "
            f"live={prev_stats.get('total_winning_usdt', 0.0):.2f}, "
            f"score={_score(prev_stats, min_t)}) | "
            f"new(cnt={new_stats.get('trade_count', 0)}, "
            f"seeded={new_stats.get('seeded_winning_usdt', 0.0):.2f}, "
            f"live={new_stats.get('total_winning_usdt', 0.0):.2f}, "
            f"score={_score(new_stats, min_t)})"
        )
    self._last_best[symbol] = result
    return result
```

- [ ] **Step 4: Update `get_efficiency_score` to use tuple scoring**

Replace `get_efficiency_score`:

```python
def get_efficiency_score(self, symbol: str) -> float:
    symbol_data = self._efficiency.get(symbol, {})
    if not symbol_data:
        return 0.0
    min_t = self._get_min_trades(symbol)
    best_tuple = max(_score(stats, min_t) for stats in symbol_data.values())
    return best_tuple[1]
```

- [ ] **Step 5: Update `get_preset_efficiency` to use tuple scoring**

Replace `get_preset_efficiency`:

```python
def get_preset_efficiency(self, symbol: str, preset_name: str) -> float:
    stats = self._efficiency.get(symbol, {}).get(preset_name, {})
    return _score(stats, self._get_min_trades(symbol))[1]
```

- [ ] **Step 6: Verify import and basic instantiation**

```bash
python3 -c "
from bot.virtual_tracker import VirtualTracker
from pathlib import Path
import tempfile, os
tmp = Path(tempfile.mkdtemp())
t = VirtualTracker('test', tmp/'v.json', tmp/'e.json', get_min_trades=lambda s: 3)
print('ok, min_trades for BTCUSDT:', t._get_min_trades('BTCUSDT'))
"
```

Expected output: `ok, min_trades for BTCUSDT: 3`

- [ ] **Step 7: Commit**

```bash
git add bot/virtual_tracker.py
git commit -m "feat: two-tier preset ranking — live-proven presets always beat seed-only"
```

---

### Task 3: Update tests for VirtualTracker

**Files:**
- Modify: `tests/test_virtual_tracker.py`

- [ ] **Step 1: Update `_make_tracker` helper to accept and pass `get_min_trades`**

Replace the `_make_tracker` function:

```python
def _make_tracker(tmp_path, mode='test', min_trades=3):
    return VirtualTracker(
        mode=mode,
        orders_path=tmp_path / f"virtual_orders_{mode}.json",
        efficiency_path=tmp_path / f"preset_efficiency_{mode}.json",
        get_min_trades=lambda _: min_trades,
    )
```

- [ ] **Step 2: Update stale comment in `test_best_preset_selection`**

The existing test passes with `min_trades=3` (counts 8 and 9 are both Tier 1, count 2 is Tier 2). Update the inline comment on `"too_few"` line from the old threshold reference:

```python
def test_best_preset_selection(tmp_path):
    tracker = _make_tracker(tmp_path)
    # count=8,9 >= min_trades(3) → Tier 1, ranked by live. count=2 < 3 → Tier 2 (seed=0).
    tracker._set_efficiency("BTCUSDT", "slow", total_winning=100.0, count=8)
    tracker._set_efficiency("BTCUSDT", "fast", total_winning=250.0, count=9)
    tracker._set_efficiency("BTCUSDT", "too_few", total_winning=999.0, count=2)
    best = tracker.best_preset("BTCUSDT")
    assert best == "fast"
```

- [ ] **Step 3: Update stale comment in `test_best_preset_returned_when_score_is_zero`**

```python
def test_best_preset_returned_when_score_is_zero(tmp_path):
    # count=2 < min_trades(3) → Tier 2, score uses seeded_winning_usdt which defaults to 0.
    # score value 0 >= 0, so best preset is returned (not None).
    tracker = _make_tracker(tmp_path)
    tracker._set_efficiency("BTCUSDT", "p1", total_winning=999.0, count=2)
    assert tracker.best_preset("BTCUSDT") == "p1"
```

- [ ] **Step 4: Add four new tests covering two-tier behaviour**

Append to `tests/test_virtual_tracker.py`:

```python
def test_tier1_always_beats_tier2_regardless_of_seed(tmp_path):
    # A live-proven preset with $1 live P&L must beat a seed-only preset with $1000 seed.
    tracker = _make_tracker(tmp_path, min_trades=3)
    # Tier 2: huge seed, no live trades
    tracker._set_efficiency("BTCUSDT", "seed_giant", total_winning=0.0, count=0)
    tracker._efficiency["BTCUSDT"]["seed_giant"]["seeded_winning_usdt"] = 1000.0
    # Tier 1: modest live profit, enough trades
    tracker._set_efficiency("BTCUSDT", "live_small", total_winning=1.0, count=3)
    assert tracker.best_preset("BTCUSDT") == "live_small"


def test_losing_champion_dethroned_by_better_live_challenger(tmp_path):
    # Mirrors the TIAUSDT scenario: champion has 4 real trades at -$14,
    # challenger has 5 virtual trades at +$66. Challenger must win.
    tracker = _make_tracker(tmp_path, min_trades=3)
    tracker._set_efficiency("BTCUSDT", "champion", total_winning=-14.0, count=4)
    tracker._set_efficiency("BTCUSDT", "challenger", total_winning=66.0, count=5)
    assert tracker.best_preset("BTCUSDT") == "challenger"


def test_seed_determines_rank_when_no_preset_has_enough_trades(tmp_path):
    # With min_trades=3, if all presets have count < 3, seeded_winning_usdt decides.
    tracker = _make_tracker(tmp_path, min_trades=3)
    tracker._set_efficiency("BTCUSDT", "low_seed", total_winning=500.0, count=2)
    tracker._efficiency["BTCUSDT"]["low_seed"]["seeded_winning_usdt"] = 10.0
    tracker._set_efficiency("BTCUSDT", "high_seed", total_winning=0.0, count=1)
    tracker._efficiency["BTCUSDT"]["high_seed"]["seeded_winning_usdt"] = 200.0
    assert tracker.best_preset("BTCUSDT") == "high_seed"


def test_custom_min_trades_per_symbol(tmp_path):
    # With min_trades=5, a preset with count=4 is still Tier 2 (seed-only).
    tracker = _make_tracker(tmp_path, min_trades=5)
    tracker._set_efficiency("BTCUSDT", "almost_live", total_winning=100.0, count=4)
    tracker._efficiency["BTCUSDT"]["almost_live"]["seeded_winning_usdt"] = 5.0
    tracker._set_efficiency("BTCUSDT", "seed_winner", total_winning=0.0, count=0)
    tracker._efficiency["BTCUSDT"]["seed_winner"]["seeded_winning_usdt"] = 50.0
    # count=4 < min_trades=5 → Tier 2 with seed=5; seed_winner has seed=50 → wins
    assert tracker.best_preset("BTCUSDT") == "seed_winner"
```

- [ ] **Step 5: Run the full virtual tracker test suite**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python3 -m pytest tests/test_virtual_tracker.py tests/test_virtual_tracker_helpers.py -v
```

Expected: all tests pass (7 existing + 4 new = 11 total).

- [ ] **Step 6: Commit**

```bash
git add tests/test_virtual_tracker.py
git commit -m "test: update virtual_tracker tests for two-tier scoring"
```

---

### Task 4: Wire `get_min_trades` into main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Import the helper**

Find the existing import from risk_config near the top of `main.py`:

```python
from config.risk_config import load_risk_config, save_risk_config
```

Add `get_min_trades_for_ranking` to it:

```python
from config.risk_config import load_risk_config, save_risk_config, get_min_trades_for_ranking
```

- [ ] **Step 2: Build the lambda after `risk_cfg` is loaded**

Find where `risk_cfg` is first set (search for `risk_cfg = load_risk_config`). After that line, add:

```python
_get_min_trades = lambda sym: get_min_trades_for_ranking(risk_cfg, sym)
```

- [ ] **Step 3: Pass `get_min_trades` to the first VirtualTracker construction (startup)**

Find (around line 160):
```python
virtual_tracker = VirtualTracker(
    mode=current_mode,
    orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{current_mode}.json",
    efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{current_mode}.json",
)
```

Replace with:
```python
virtual_tracker = VirtualTracker(
    mode=current_mode,
    orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{current_mode}.json",
    efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{current_mode}.json",
    get_min_trades=_get_min_trades,
)
```

- [ ] **Step 4: Pass `get_min_trades` to the second VirtualTracker construction (mode switch)**

Find (around line 1059):
```python
virtual_tracker = VirtualTracker(
    mode=target_mode,
    orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{target_mode}.json",
    efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{target_mode}.json",
)
```

Replace with:
```python
virtual_tracker = VirtualTracker(
    mode=target_mode,
    orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{target_mode}.json",
    efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{target_mode}.json",
    get_min_trades=_get_min_trades,
)
```

- [ ] **Step 5: Verify the bot starts without errors**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python3 -c "import main" 2>&1 | head -5
```

Expected: no output (clean import).

- [ ] **Step 6: Run full test suite to confirm no regressions**

```bash
python3 -m pytest tests/ -x -q 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "feat: pass get_min_trades callable to VirtualTracker from risk config"
```

---

### Task 5: Update dashboard types and API route

**Files:**
- Modify: `dashboard/lib/risk-types.ts`
- Modify: `dashboard/app/api/risk/route.ts`

- [ ] **Step 1: Add two fields to `RiskConfig` in risk-types.ts**

In `dashboard/lib/risk-types.ts`, add to `RiskConfig` after `symbol_leverage?`:

```typescript
  min_trades_for_ranking?: number
  min_trades_for_ranking_per_symbol?: Record<string, number>
```

- [ ] **Step 2: Add two defaults to `DEFAULT_CONFIG` in route.ts**

In `dashboard/app/api/risk/route.ts`, add to `DEFAULT_CONFIG` after the `weight_rebalancer` block:

```typescript
  min_trades_for_ranking: 3,
  min_trades_for_ranking_per_symbol: {} as Record<string, number>,
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add dashboard/lib/risk-types.ts dashboard/app/api/risk/route.ts
git commit -m "feat: add min_trades_for_ranking fields to Risk dashboard types and API"
```

---

### Task 6: Create PresetRankingSection dashboard component

**Files:**
- Create: `dashboard/components/risk/PresetRankingSection.tsx`

- [ ] **Step 1: Create the component file**

Create `dashboard/components/risk/PresetRankingSection.tsx` with the full content:

```tsx
'use client'

import { RiskConfig } from '@/lib/risk-types'

interface Props {
  config: RiskConfig
  availableSymbols: string[]
  patchConfig: (patch: Partial<RiskConfig>) => void
}

export default function PresetRankingSection({ config, availableSymbols, patchConfig }: Props) {
  const minTrades = config.min_trades_for_ranking ?? 3
  const perSymbol = config.min_trades_for_ranking_per_symbol ?? {}
  const unusedSymbols = availableSymbols.filter(s => !(s in perSymbol))

  function setGlobal(val: number) {
    patchConfig({ min_trades_for_ranking: Math.max(1, val) })
  }

  function setSymbolOverride(sym: string, val: number) {
    patchConfig({
      min_trades_for_ranking_per_symbol: { ...perSymbol, [sym]: Math.max(1, val) },
    })
  }

  function removeOverride(sym: string) {
    const next = { ...perSymbol }
    delete next[sym]
    patchConfig({ min_trades_for_ranking_per_symbol: next })
  }

  function addSymbol(sym: string) {
    if (!sym) return
    patchConfig({
      min_trades_for_ranking_per_symbol: { ...perSymbol, [sym]: minTrades },
    })
  }

  return (
    <section className="border border-neutral-700 rounded p-4">
      <h2 className="font-semibold text-sm mb-3 text-white">Preset Ranking</h2>

      <div className="flex items-center gap-3 mb-4">
        <label className="text-xs text-gray-400 w-52">Min trades before live ranking</label>
        <input
          type="number"
          min={1}
          value={minTrades}
          onChange={e => setGlobal(parseInt(e.target.value) || 1)}
          className="w-16 bg-neutral-800 border border-neutral-600 rounded px-2 py-1 text-xs text-white"
        />
      </div>

      {Object.keys(perSymbol).length > 0 && (
        <table className="w-full text-xs mb-3">
          <thead>
            <tr className="text-gray-400 text-left border-b border-neutral-700">
              <th className="pb-1 font-normal">Symbol</th>
              <th className="pb-1 font-normal">Min trades</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {Object.entries(perSymbol).map(([sym, val]) => (
              <tr key={sym} className="border-b border-neutral-800">
                <td className="py-1 text-gray-300">{sym}</td>
                <td className="py-1">
                  <input
                    type="number"
                    min={1}
                    value={val}
                    onChange={e => setSymbolOverride(sym, parseInt(e.target.value) || 1)}
                    className="w-16 bg-neutral-800 border border-neutral-600 rounded px-2 py-0.5 text-xs text-white"
                  />
                </td>
                <td className="py-1 text-right">
                  <button
                    onClick={() => removeOverride(sym)}
                    className="text-red-400 hover:text-red-300 text-xs px-1"
                    title={`Remove override for ${sym}`}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {unusedSymbols.length > 0 && (
        <select
          value=""
          onChange={e => { addSymbol(e.target.value); e.target.value = '' }}
          className="text-xs bg-neutral-800 border border-neutral-600 rounded px-2 py-1 text-gray-400 mb-3"
        >
          <option value="">+ Add per-symbol override</option>
          {unusedSymbols.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      )}

      <p className="text-xs text-gray-500 mt-1">
        After N real+virtual trades a preset uses live P&L for ranking. Below N, backtest seed applies. Bot restart required for changes to take effect.
      </p>
    </section>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/components/risk/PresetRankingSection.tsx
git commit -m "feat: add PresetRankingSection dashboard component"
```

---

### Task 7: Wire PresetRankingSection into the Risk page

**Files:**
- Modify: `dashboard/app/risk/page.tsx`

- [ ] **Step 1: Import the component**

Add to the import block at the top of `dashboard/app/risk/page.tsx`:

```tsx
import PresetRankingSection from '@/components/risk/PresetRankingSection'
```

- [ ] **Step 2: Render the section**

Find the existing `<WeightRebalancerSection ... />` block in the JSX and insert the following immediately after its closing tag:

```tsx
<PresetRankingSection
  config={config}
  availableSymbols={availableSymbols}
  patchConfig={patchConfig}
/>
```

- [ ] **Step 3: Verify TypeScript compiles cleanly**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 4: Run full Python test suite one final time**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python3 -m pytest tests/ -x -q 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 5: Final commit**

```bash
git add dashboard/app/risk/page.tsx
git commit -m "feat: render PresetRankingSection on Risk page"
```
