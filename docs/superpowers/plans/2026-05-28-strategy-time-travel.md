# Strategy Page Time Travel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a time-scrubber control to the Strategy page so the user can replay the bot's trend analysis state at any historical candle — swing points, trend levels, and signals update together to reflect "what the bot saw" at that moment.

**Architecture:** A new `replay_api.py` subprocess (same pattern as `backtest_api.py`) re-runs `Analyzer.build_from_klines(klines[:idx+1])` against the already-stored results JSON and returns historical trend state. A Next.js POST route at `/api/replay` spawns it. The frontend adds a `TimeScrubber` component and thin state-switching logic to `page.tsx` so all widgets source from replay data when the scrubber is not at the live (max) position.

**Tech Stack:** Python 3, Next.js 15 App Router, TypeScript, Tailwind CSS, existing `Analyzer` class from `bot/analyzer.py`.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `dashboard/lib/types.ts` | Modify | Add `ReplayResult` interface |
| `replay_api.py` | Create | Python replay script; takes `{symbol, candle_index}`, returns `{trend_levels, all_points, signals}` |
| `tests/test_replay_api.py` | Create | Pytest tests for `replay_api.replay()` |
| `dashboard/app/api/replay/route.ts` | Create | Next.js POST route; spawns `replay_api.py` subprocess |
| `dashboard/components/TimeScrubber.tsx` | Create | Slider + tick buttons + LIVE/datetime label |
| `dashboard/app/page.tsx` | Modify | Replay state, debounced fetch, data-source switch, render `<TimeScrubber>` |

---

## Task 1: Add ReplayResult type

**Files:**
- Modify: `dashboard/lib/types.ts`

- [ ] **Step 1: Add the interface after the `Kline` interface**

Open `dashboard/lib/types.ts` and add after the `Kline` interface (around line 37):

```typescript
// Returned by /api/replay — historical trend state at a given candle index.
export interface ReplayResult {
  trend_levels: TrendLevel[]
  all_points: SwingPoint[]
  signals: Signal[]
  candle_index: number  // echoed back so kline clip is always in sync with overlay data
}
```

- [ ] **Step 2: Verify no TypeScript errors**

```bash
cd dashboard && node_modules/.bin/tsc --noEmit
```

Expected: exits 0 with no output.

- [ ] **Step 3: Commit**

```bash
cd ..
git add dashboard/lib/types.ts
git commit -m "feat(types): add ReplayResult interface for strategy time travel"
```

---

## Task 2: Write replay_api.py

**Files:**
- Create: `replay_api.py`
- Create: `tests/test_replay_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_replay_api.py`:

```python
"""Tests for replay_api.replay()."""
import json
import pytest
from pathlib import Path


def _make_klines(n: int, base_price: float = 100.0) -> list:
    """n synthetic 15-minute klines in dashboard JSON format."""
    base_ts = 1_700_000_000  # Unix seconds
    klines = []
    p = base_price
    for i in range(n):
        klines.append({
            'time': base_ts + i * 900,
            'open': round(p, 4),
            'high': round(p + 1.0, 4),
            'low':  round(p - 1.0, 4),
            'close': round(p + 0.5, 4),
        })
        p += 0.1
    return klines


def _write_results(path: Path, symbol: str, klines: list) -> None:
    data = {
        'symbol': symbol,
        'timeframe': '15m',
        'mode': 'testnet',
        'generated_at': '2026-01-01T00:00:00+00:00',
        'current_price': klines[-1]['close'] if klines else 0.0,
        'trend_levels': [],
        'all_points': [],
        'klines': klines,
        'signals': [],
        'best_signal': None,
    }
    (path / f'results_{symbol}.json').write_text(json.dumps(data))


# ── tests ─────────────────────────────────────────────────────────────────────

def test_replay_returns_correct_shape(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
    _write_results(tmp_path, 'TESTUSDT', _make_klines(60))

    result = replay_api.replay('TESTUSDT', 59)

    assert 'trend_levels' in result
    assert 'all_points' in result
    assert 'signals' in result
    assert isinstance(result['trend_levels'], list)
    assert isinstance(result['all_points'], list)
    assert isinstance(result['signals'], list)


def test_replay_respects_candle_index(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
    _write_results(tmp_path, 'TESTUSDT', _make_klines(200))

    result_small = replay_api.replay('TESTUSDT', 5)
    result_large = replay_api.replay('TESTUSDT', 199)

    # More candles → at least as many (usually more) swing points detected
    assert len(result_large['all_points']) >= len(result_small['all_points'])


def test_replay_candle_index_beyond_length_uses_all(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
    klines = _make_klines(50)
    _write_results(tmp_path, 'TESTUSDT', klines)

    # Index beyond array length — Python slice handles this gracefully
    result = replay_api.replay('TESTUSDT', 9999)
    assert 'trend_levels' in result


def test_replay_zero_candles_returns_empty(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
    _write_results(tmp_path, 'TESTUSDT', _make_klines(50))

    # candle_index=-1 → slice [:0] → empty → early return
    result = replay_api.replay('TESTUSDT', -1)
    assert result == {'trend_levels': [], 'all_points': [], 'signals': []}


def test_replay_missing_file_raises(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)

    with pytest.raises(FileNotFoundError):
        replay_api.replay('NONEXISTENT', 10)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_replay_api.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'replay_api'` (or similar ImportError).

- [ ] **Step 3: Create replay_api.py**

Create `replay_api.py` in the project root:

```python
#!/usr/bin/env python3
"""Replay the trend analyzer state at a historical candle index.

Usage:
    python replay_api.py '{"symbol": "SOLUSDT", "candle_index": 450}'

Prints JSON to stdout on success.
Prints {"error": "..."} and exits with code 1 on failure.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bot.analyzer import Analyzer

RESULTS_DIR = Path('dashboard/public')


def _ts(unix_seconds) -> str | None:
    if unix_seconds is None:
        return None
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat()


def _format_trend_levels(trend) -> list:
    levels = []
    current = trend
    while current is not None:
        last_high = current.getLastHigh()
        last_low  = current.getLastLow()
        levels.append({
            'level':     current.getLevel(),
            'direction': ('ASC' if current.isAscending() else 'DESC' if current.isDescending() else 'NONE'),
            'bos':       current.getBreakOfStructure(),
            'bos_since': _ts(current.getBreakOfStructureTime()),
            'last_high': {'price': last_high.getHighValue(), 'time': _ts(last_high.getTime())} if last_high else None,
            'last_low':  {'price': last_low.getLowValue(),  'time': _ts(last_low.getTime())}  if last_low  else None,
        })
        current = current.getBiggerTrend() if current.hasBiggerTrend() else None
    return levels


def _format_rec(rec) -> dict:
    return {
        'level':       rec.getLevel(),
        'side':        rec.getSide(),
        'signal_type': rec.getType().value,
        'is_reversal': rec.isReversal(),
        'entry':       rec.getEntryPrice(),
        'target':      rec.getTarget(),
        'stop':        rec.getStop(),
        'rr':          rec.getRR(),
        'precision':   rec.getPrecision(),
    }


def replay(symbol: str, candle_index: int) -> dict:
    """Build and return historical trend state at candle_index.

    Raises FileNotFoundError if the results JSON for symbol is missing.
    """
    path = RESULTS_DIR / f'results_{symbol}.json'
    if not path.exists():
        raise FileNotFoundError(f'results_{symbol}.json not found')

    data = json.loads(path.read_text())

    # Convert dashboard kline format {time(s), open, high, low, close}
    # to analyzer format [timestamp_ms, open, high, low, close, volume].
    analyzer_klines = [
        [int(k['time']) * 1000, float(k['open']), float(k['high']), float(k['low']), float(k['close']), 0]
        for k in data['klines']
    ]

    sliced = analyzer_klines[:candle_index + 1]
    if not sliced:
        return {'trend_levels': [], 'all_points': [], 'signals': []}

    analyzer = Analyzer(swing_neighbours=2)
    analyzer.build_from_klines(sliced)

    trend = analyzer.get_trend()
    if trend is None:
        return {'trend_levels': [], 'all_points': [], 'signals': []}

    replay_price = sliced[-1][4]  # close of last sliced candle
    analyzer.update_price(replay_price)

    trend_levels = _format_trend_levels(trend)

    all_points = sorted(
        [
            {
                'time':   _ts(p['time']),
                'level':  p['level'],
                'type':   p['type'],
                'price':  p['price'],
                'active': p['active'],
            }
            for p in analyzer.get_all_points()
        ],
        key=lambda p: p['time'],
        reverse=True,
    )

    recs = trend.getRecommendations(entry_price=replay_price, proximity_zone_pct=10.0)
    signals = [_format_rec(r) for r in recs]

    return {'trend_levels': trend_levels, 'all_points': all_points, 'signals': signals}


if __name__ == '__main__':
    try:
        args   = json.loads(sys.argv[1])
        symbol = str(args['symbol']).strip().upper()
        idx    = int(args['candle_index'])
        result = replay(symbol, idx)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
python -m pytest tests/test_replay_api.py -v
```

Expected output (all pass):
```
tests/test_replay_api.py::test_replay_returns_correct_shape PASSED
tests/test_replay_api.py::test_replay_respects_candle_index PASSED
tests/test_replay_api.py::test_replay_candle_index_beyond_length_uses_all PASSED
tests/test_replay_api.py::test_replay_zero_candles_returns_empty PASSED
tests/test_replay_api.py::test_replay_missing_file_raises PASSED
```

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all pre-existing tests still pass; only the new 5 tests are added.

- [ ] **Step 6: Smoke-test the CLI interface**

```bash
python replay_api.py '{"symbol": "SOLUSDT", "candle_index": 100}' | python -m json.tool | head -20
```

Expected: well-formed JSON with `trend_levels`, `all_points`, `signals` keys.

- [ ] **Step 7: Commit**

```bash
git add replay_api.py tests/test_replay_api.py
git commit -m "feat: add replay_api.py for strategy time travel"
```

---

## Task 3: Add /api/replay Next.js route

**Files:**
- Create: `dashboard/app/api/replay/route.ts`

- [ ] **Step 1: Create the route file**

Create `dashboard/app/api/replay/route.ts`:

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
  let symbol: string
  let candle_index: number
  try {
    const body = await req.json()
    if (typeof body.symbol !== 'string' || !body.symbol.trim()) {
      return NextResponse.json({ error: 'symbol required' }, { status: 400 })
    }
    if (
      typeof body.candle_index !== 'number' ||
      !Number.isInteger(body.candle_index) ||
      body.candle_index < 0
    ) {
      return NextResponse.json(
        { error: 'candle_index must be a non-negative integer' },
        { status: 400 },
      )
    }
    symbol = body.symbol.trim().toUpperCase()
    candle_index = body.candle_index
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
  }

  const python = getPython()
  const payload = JSON.stringify({ symbol, candle_index })

  return new Promise<NextResponse>(resolve => {
    let stdout = ''
    let stderr = ''

    const child = spawn(python, ['replay_api.py', payload], { cwd: BOT_ROOT })

    child.stdout.on('data', (chunk: Buffer) => { stdout += chunk })
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk })

    child.on('error', (err: Error) => {
      resolve(NextResponse.json({ error: `Failed to start Python: ${err.message}` }, { status: 500 }))
    })

    child.on('close', (code: number) => {
      if (code !== 0) {
        resolve(NextResponse.json(
          { error: stderr.trim() || `Python exited with code ${code}` },
          { status: 500 },
        ))
        return
      }
      try {
        const data = JSON.parse(stdout)
        if (data.error) {
          resolve(NextResponse.json({ error: data.error }, { status: 500 }))
          return
        }
        resolve(NextResponse.json(data))
      } catch {
        resolve(NextResponse.json(
          { error: 'Failed to parse Python output', raw: stdout.slice(0, 500) },
          { status: 500 },
        ))
      }
    })
  })
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd dashboard && node_modules/.bin/tsc --noEmit
```

Expected: exits 0, no output.

- [ ] **Step 3: Smoke-test via curl (requires dev server running)**

In one terminal: `cd dashboard && node_modules/.bin/next dev`

In another:
```bash
curl -s -X POST http://localhost:3000/api/replay \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"SOLUSDT","candle_index":100}' | python -m json.tool | head -10
```

Expected: JSON object with `trend_levels`, `all_points`, `signals`.

```bash
curl -s -X POST http://localhost:3000/api/replay \
  -H 'Content-Type: application/json' \
  -d '{"symbol":""}' | python -m json.tool
```

Expected: `{"error": "symbol required"}` with HTTP 400.

- [ ] **Step 4: Commit**

```bash
cd ..
git add dashboard/app/api/replay/route.ts
git commit -m "feat(api): add /api/replay route for strategy time travel"
```

---

## Task 4: Create TimeScrubber component

**Files:**
- Create: `dashboard/components/TimeScrubber.tsx`

- [ ] **Step 1: Create the component**

Create `dashboard/components/TimeScrubber.tsx`:

```tsx
'use client'

const TICK = 10  // klines per step button press

interface Props {
  klines: { time: number }[]
  scrubberIdx: number | null   // null means live (at the most recent candle)
  isLoading: boolean
  onScrub: (idx: number | null) => void
}

function fmtTime(unixSec: number): string {
  const d = new Date(unixSec * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export default function TimeScrubber({ klines, scrubberIdx, isLoading, onScrub }: Props) {
  if (klines.length === 0) return null

  const maxIdx    = klines.length - 1
  const isLive    = scrubberIdx === null
  const displayIdx = scrubberIdx ?? maxIdx

  function handleSlider(e: React.ChangeEvent<HTMLInputElement>) {
    const v = Number(e.target.value)
    onScrub(v >= maxIdx ? null : v)
  }

  function stepBack() {
    const current = scrubberIdx ?? maxIdx
    const next = Math.max(0, current - TICK)
    onScrub(next >= maxIdx ? null : next)
  }

  function stepForward() {
    if (isLive) return
    const next = (scrubberIdx ?? maxIdx) + TICK
    onScrub(next >= maxIdx ? null : next)
  }

  const btnCls =
    'px-2 py-1 text-xs rounded border border-gray-700 bg-gray-900 text-gray-400 ' +
    'hover:text-white hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors'

  return (
    <div className="flex items-center gap-2">
      <button onClick={stepBack} disabled={isLoading || displayIdx <= 0} className={btnCls}>
        ◀
      </button>

      <input
        type="range"
        min={0}
        max={maxIdx}
        value={displayIdx}
        onChange={handleSlider}
        disabled={isLoading}
        className="w-48 accent-indigo-500 disabled:opacity-40 cursor-pointer"
      />

      <button onClick={stepForward} disabled={isLoading || isLive} className={btnCls}>
        ▶
      </button>

      <span className="text-xs font-mono min-w-[108px]">
        {isLive ? (
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400 inline-block animate-pulse" />
            <span className="text-emerald-400 font-semibold">LIVE</span>
          </span>
        ) : (
          <span className="text-gray-300">{fmtTime(klines[displayIdx].time)}</span>
        )}
      </span>

      {isLoading && (
        <span className="text-xs text-gray-600">updating…</span>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd dashboard && node_modules/.bin/tsc --noEmit
```

Expected: exits 0, no output.

- [ ] **Step 3: Commit**

```bash
cd ..
git add dashboard/components/TimeScrubber.tsx
git commit -m "feat(ui): add TimeScrubber component for strategy time travel"
```

---

## Task 5: Wire up page.tsx

**Files:**
- Modify: `dashboard/app/page.tsx`

This task adds three independent concerns: replay state, debounced fetch effect, and data-source switching.

- [ ] **Step 1: Add imports and state**

At the top of `dashboard/app/page.tsx`, add to the existing import block:

```typescript
import TimeScrubber from '@/components/TimeScrubber'
import type { ReplayResult } from '@/lib/types'
```

Inside `PageContent`, after the existing `useState` declarations (around line 43), add:

```typescript
const [scrubberIdx, setScrubberIdx] = useState<number | null>(null)
const [replayData,  setReplayData]  = useState<ReplayResult | null>(null)
const [isReplaying, setIsReplaying] = useState(false)
```

- [ ] **Step 2: Add the debounced fetch effect**

After the existing polling `useEffect` (around line 82), add:

```typescript
useEffect(() => {
  if (scrubberIdx === null) {
    setReplayData(null)
    setIsReplaying(false)
    return
  }
  setIsReplaying(true)
  const timer = setTimeout(() => {
    fetch('/api/replay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, candle_index: scrubberIdx }),
    })
      .then(r => r.json())
      .then((d: Omit<ReplayResult, 'candle_index'>) => {
        setReplayData({ ...d, candle_index: scrubberIdx })
        setIsReplaying(false)
      })
      .catch(() => setIsReplaying(false))
  }, 300)
  return () => clearTimeout(timer)
}, [scrubberIdx, symbol])
```

- [ ] **Step 3: Update the useMemo to switch data sources**

The existing `useMemo` (around line 85) begins:

```typescript
const { filteredPoints, filteredKlines, filteredLevels, availableLevels } = useMemo(() => {
  if (!data || selectedLevel === null) {
    return { filteredPoints: [], filteredKlines: [], filteredLevels: [], availableLevels: [] }
  }

  const availableLevels = data.trend_levels.map(t => t.level).sort((a, b) => a - b)
  const filteredLevels  = data.trend_levels.filter(t => t.level <= selectedLevel)
  ...
  const filteredPoints = levelPoints.filter(...)
  ...
  const filteredKlines = data.klines.filter(...)
```

Replace the **entire `useMemo` block** with this version that adds source-switching at the top:

```typescript
const { filteredPoints, filteredKlines, filteredLevels, availableLevels } = useMemo(() => {
  if (!data || selectedLevel === null) {
    return { filteredPoints: [], filteredKlines: [], filteredLevels: [], availableLevels: [] }
  }

  // When replay is active and data has arrived, use historical sources.
  // While loading (replayData not yet updated), fall back to live data so
  // klines and overlays remain in sync (both stale-live, not mismatched).
  const isReplay = scrubberIdx !== null && replayData !== null
  const srcLevels = isReplay ? replayData.trend_levels : data.trend_levels
  const srcPoints = isReplay ? replayData.all_points   : data.all_points
  const srcKlines = isReplay
    ? data.klines.slice(0, replayData.candle_index + 1)
    : data.klines

  const availableLevels = srcLevels.map(t => t.level).sort((a, b) => a - b)
  const filteredLevels  = srcLevels.filter(t => t.level <= selectedLevel)

  const fromMs = fromDate ? new Date(fromDate).getTime() : 0
  const toMs   = toDate   ? new Date(toDate).getTime()   : Infinity

  const levelPoints = srcPoints.filter(p => {
    if (p.level > selectedLevel) return false
    const ms = new Date(p.time).getTime()
    return ms >= fromMs && ms <= toMs
  })

  const activeMs = levelPoints.filter(p => p.active).map(p => new Date(p.time).getTime())
  const oldestActiveMs = activeMs.length > 0 ? Math.min(...activeMs) : 0
  const filteredPoints = levelPoints.filter(p => p.active || new Date(p.time).getTime() >= oldestActiveMs)

  const effectiveFromMs = !fromDate && oldestActiveMs > 0 ? oldestActiveMs : fromMs

  const filteredKlines = srcKlines.filter(k => {
    const ms = k.time * 1000
    return ms >= effectiveFromMs && ms <= toMs
  })

  return { filteredPoints, filteredKlines, filteredLevels, availableLevels }
}, [data, selectedLevel, fromDate, toDate, scrubberIdx, replayData])
```

- [ ] **Step 4: Update the signals prop and render TimeScrubber**

In the render section, find:

```tsx
const srcSignals = isReplay ? replayData.signals : (data?.signals ?? [])
```

Wait — `isReplay` is only defined inside the `useMemo`. Instead, derive it at the render level:

After the `useMemo` call (and before the `if (error)` check), add:

```typescript
const srcSignals = (scrubberIdx !== null && replayData !== null)
  ? replayData.signals
  : (data?.signals ?? [])
```

Find the `<SignalsPanel signals={data.signals} />` line and change it to:

```tsx
<SignalsPanel signals={srcSignals} />
```

Now add `<TimeScrubber>` to the toolbar. Find the toolbar `<div>` (the one containing the level filter and date pickers — around line 158):

```tsx
<div className="flex flex-wrap items-center gap-3 justify-end">
  <LevelFilter ...
```

Replace with:

```tsx
<div className="flex flex-wrap items-center gap-3 justify-between">
  <TimeScrubber
    klines={data.klines}
    scrubberIdx={scrubberIdx}
    isLoading={isReplaying}
    onScrub={setScrubberIdx}
  />
  <div className="flex items-center gap-3 flex-wrap justify-end">
    <LevelFilter
      levels={availableLevels}
      selected={selectedLevel}
      onChange={setSelectedLevel}
    />

    <div className="flex items-center gap-2 text-xs text-gray-500">
      <span className="uppercase tracking-wider">From</span>
      <input
        type="datetime-local"
        step={900}
        value={fromDate}
        min={klineMinDate}
        max={klineMaxDate}
        onChange={e => setFromDate(snapTo15Min(e.target.value))}
        className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-gray-300 text-xs focus:outline-none focus:border-indigo-500"
      />
      <span className="uppercase tracking-wider">To</span>
      <input
        type="datetime-local"
        step={900}
        value={toDate}
        min={klineMinDate}
        max={klineMaxDate}
        onChange={e => setToDate(snapTo15Min(e.target.value))}
        className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-gray-300 text-xs focus:outline-none focus:border-indigo-500"
      />
    </div>

    <button
      onClick={() => { setFromDate(''); setToDate('') }}
      className="px-3 py-1.5 text-xs font-semibold rounded border border-gray-700 bg-gray-900 text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
    >
      Clear
    </button>
  </div>
</div>
```

- [ ] **Step 5: Verify TypeScript compiles**

```bash
cd dashboard && node_modules/.bin/tsc --noEmit
```

Expected: exits 0, no output.

- [ ] **Step 6: Manual smoke test in the browser**

Start the dev server: `cd dashboard && node_modules/.bin/next dev`

Open `http://localhost:3000`. With a symbol that has data:

1. The toolbar shows the scrubber on the left (◀ slider ▶ LIVE badge).
2. Slider is at the rightmost position and LIVE badge is green and pulsing.
3. Click ◀ once — slider moves 10 positions left, label shows a datetime, "updating…" appears briefly, then swing points, trend levels, and signals update to the historical state.
4. Drag the slider to various positions — each stop triggers a fresh replay (debounced).
5. Drag slider fully right OR click ▶ to live — LIVE badge returns, all widgets revert to the latest polled snapshot.
6. While "updating…" is visible, the ◀ ▶ buttons and slider are disabled.
7. Switch symbol — scrubber resets to LIVE (component remounts via `key={symbol}`).

- [ ] **Step 7: Commit**

```bash
cd ..
git add dashboard/app/page.tsx
git commit -m "feat(strategy): wire up time-travel scrubber to strategy page"
```

---

## Self-Review

**Spec coverage:**
- ✅ Range control that maps to candle positions — `TimeScrubber` with `input[type=range]`
- ✅ Tick-by-tick back/forward buttons — ◀ ▶ step ±TICK (10) klines
- ✅ Default is current time — `scrubberIdx` starts `null` (live)
- ✅ All widgets update for the selected time — `useMemo` sources from `replayData` when active
- ✅ Send selected time to server with debounce — 300ms debounce in `useEffect`
- ✅ Disable controls while sending — `isReplaying` disables buttons and slider
- ✅ Get data from server based on selected time — `/api/replay` POST route spawning `replay_api.py`
- ✅ No need to retrieve klines — klines come from the already-loaded `data.klines`; only trend/points/signals come from server

**Placeholder scan:** None found.

**Type consistency:**
- `ReplayResult` defined in Task 1, used in Task 5 (`useState<ReplayResult | null>`)
- `replayData.trend_levels` / `replayData.all_points` / `replayData.signals` / `replayData.candle_index` — all fields defined in `ReplayResult`
- `TimeScrubber` props: `klines: {time: number}[]`, `scrubberIdx: number | null`, `isLoading: boolean`, `onScrub: (idx: number | null) => void` — all consistent with usage in `page.tsx`
- `replay_api.replay()` return shape (`trend_levels`, `all_points`, `signals`) matches what the route serves and what `ReplayResult` expects (minus `candle_index` which is added client-side)
