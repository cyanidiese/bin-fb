# Strategy Page Time Travel — Design Spec

**Date:** 2026-05-28  
**Status:** Approved, pending implementation plan

---

## Overview

Add a time-scrubber control to the Strategy page that lets the user "travel" to any historical candle and see the bot's trend analysis state — swing points, trend levels, and signals — exactly as they would have appeared at that moment. All widgets update together. Default is the live (latest) position.

---

## Architecture

### Server-side

**New file: `replay_api.py`**

Entry point for the replay subprocess. Called by the Next.js API route.

- Reads `{symbol, candle_index}` from `sys.argv[1]` (JSON string)
- Loads `dashboard/public/results_{symbol}.json`; extracts the `klines` array
- Converts dashboard kline format `{time(s), open, high, low, close}` → analyzer format `[time_ms, open, high, low, close, 0]`
- Slices to `klines[:candle_index + 1]`
- Instantiates `Analyzer(swing_neighbours=2)` and calls `build_from_klines(sliced)`
- Extracts `trend_levels` (same serialisation as `exporter.py`) and `all_points` with active flags
- Runs `Analyzer.get_recommendations()` for signals using the replay price (last close of sliced klines)
- Prints JSON to stdout: `{trend_levels, all_points, signals}`
- On any error: prints `{error: "..."}` and exits with code 1

Note: `swing_neighbours=2` matches the live analyzer default. No preset overrides are applied — this is a raw trend replay.

**New file: `dashboard/app/api/replay/route.ts`**

POST route. Structure identical to `dashboard/app/api/backtest/route.ts`.

- Parses `{symbol: string, candle_index: number}` from request body
- Validates: symbol must be non-empty string, candle_index must be integer ≥ 0
- Spawns `replay_api.py` with JSON payload using `.venv/bin/python3` (falls back to `python3`)
- Returns parsed stdout on success, `{error}` on failure
- No timeout is set; the subprocess runs to completion (typical: 100–300 ms for 1000 klines)

---

### Frontend

**New type in `dashboard/lib/types.ts`**

```typescript
export interface ReplayResult {
  trend_levels: TrendLevel[]
  all_points: SwingPoint[]
  signals: Signal[]
  candle_index: number   // echoed back so the kline clip index is always in sync
}
```

**New component: `dashboard/components/TimeScrubber.tsx`**

Props:
```typescript
interface Props {
  klines: Kline[]           // full kline array from live data
  scrubberIdx: number | null  // null = live
  isLoading: boolean
  onScrub: (idx: number | null) => void
}
```

Renders (single row, left to right):
1. ◀ button — steps `scrubberIdx` back by `TICK` (10) klines; disabled when loading or at index 0
2. `input[type=range]` — `min=0`, `max=klines.length-1`, `value=scrubberIdx ?? klines.length-1`; changing to `klines.length-1` fires `onScrub(null)` (live); any other value fires `onScrub(index)`
3. ▶ button — steps `scrubberIdx` forward by `TICK`; when result ≥ `klines.length-1` fires `onScrub(null)` (live); disabled when loading or already live
4. Label — shows `MM/DD HH:mm` of the selected kline's timestamp, or a **LIVE** badge (green dot + "LIVE") when `scrubberIdx === null`
5. "updating…" text — shown only when `isLoading`; sits after the label, muted grey

All interactive elements get `disabled={isLoading}`.

**Changes to `dashboard/app/page.tsx`**

New state:
```typescript
const [scrubberIdx,  setScrubberIdx]  = useState<number | null>(null)
const [replayData,   setReplayData]   = useState<ReplayResult | null>(null)
const [isReplaying,  setIsReplaying]  = useState(false)
```

Debounced fetch effect (runs when `scrubberIdx` or `symbol` changes):
```
if scrubberIdx === null:
  clear replayData, clear isReplaying, return

setIsReplaying(true)
start 300ms timer:
  POST /api/replay {symbol, candle_index: scrubberIdx}
  on success: setReplayData({...result, candle_index: scrubberIdx}), setIsReplaying(false)
  on error:   setIsReplaying(false)

cleanup: clearTimeout (isReplaying stays true until next response or live reset)
```

Data source switching — inserted above the existing `useMemo`:
```typescript
const isLive    = scrubberIdx === null
const srcLevels = (!isLive && replayData) ? replayData.trend_levels : (data?.trend_levels ?? [])
const srcPoints = (!isLive && replayData) ? replayData.all_points   : (data?.all_points   ?? [])
const srcKlines = (!isLive && replayData)
  ? (data?.klines ?? []).slice(0, replayData.candle_index + 1)
  : (data?.klines ?? [])
const srcSignals = (!isLive && replayData) ? replayData.signals : (data?.signals ?? [])
```

The existing `useMemo` replaces references to `data.trend_levels`, `data.all_points`, `data.klines` with `srcLevels`, `srcPoints`, `srcKlines`. `data.signals` passed to `<SignalsPanel>` is replaced with `srcSignals`.

During a loading window (scrubberIdx changed but replayData not yet updated): `srcLevels/srcPoints/srcKlines` continue to serve the previous `replayData` position, keeping klines and overlays in sync. The "updating…" label signals to the user that a refresh is pending.

Placement of `<TimeScrubber>` — inside the toolbar row, before the level filter, occupying the left side:
```tsx
<div className="flex flex-wrap items-center gap-3 justify-between">
  <TimeScrubber
    klines={data.klines}
    scrubberIdx={scrubberIdx}
    isLoading={isReplaying}
    onScrub={setScrubberIdx}
  />
  <div className="flex items-center gap-3 justify-end">
    {/* existing LevelFilter + date pickers + Clear */}
  </div>
</div>
```

---

## Data flow summary

```
User drags slider
  → setScrubberIdx(idx)           // immediate
  → isReplaying = true            // controls disable immediately
  → 300ms debounce fires
  → POST /api/replay {symbol, candle_index}
  → replay_api.py runs Analyzer.build_from_klines(klines[:idx+1])
  → setReplayData({trend_levels, all_points, signals, candle_index})
  → isReplaying = false
  → srcKlines / srcPoints / srcLevels / srcSignals update together
  → SwingPointsChart, TrendLevelsTable, AllPointsTable, SignalsPanel re-render

User moves slider to end (max)
  → setScrubberIdx(null)
  → replayData cleared immediately
  → live polling resumes (was never stopped — polling continues in background)
```

---

## Files changed

| File | Change |
|------|--------|
| `replay_api.py` | New Python replay script |
| `dashboard/app/api/replay/route.ts` | New Next.js API route |
| `dashboard/components/TimeScrubber.tsx` | New scrubber UI component |
| `dashboard/app/page.tsx` | Scrubber state + data-source switch + TimeScrubber render |
| `dashboard/lib/types.ts` | Add `ReplayResult` type |

---

## Constraints and non-goals

- Travel range is limited to the klines present in `results_{symbol}.json` (up to 1000 candles ≈ ~10 days of 15-minute data). Traveling before that window is not supported.
- `swing_neighbours` is hardcoded to 2 in the replay script (matches the live analyzer). Preset-specific settings are not replayed.
- Signals in replay mode use `get_recommendations()` (proximity-based candidates), not the scoring engine. This mirrors what the live export writes for `signals`.
- The live polling interval continues running in the background during replay. When the user returns to LIVE, the latest polled snapshot is shown immediately.
- No visual indicator of klines-per-tick is shown; TICK = 10 is a constant, not configurable from the UI.
