# Learning Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a candle-by-candle learning overlay to the Strategy dashboard page so the user can accept/reject/override bot signals, place custom orders, add notes at any time, and download the session as JSON for hypothesis analysis.

**Architecture:** Pure frontend feature — Learning Mode overlays the existing Strategy page state (`page.tsx`). A `useLearningSession` hook owns all session state and event appending. The existing `/api/replay` endpoint is reused unchanged. Chart order overlays are Canvas plugins injected into `SwingPointsChart` via a new optional prop. No bot-side changes.

**Tech Stack:** React (Next.js 15 app router), TypeScript, Chart.js canvas plugins (same pattern as `TradesChart.tsx`), Tailwind CSS.

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `dashboard/lib/learningTypes.ts` | Create | All TypeScript types for Learning Mode |
| `dashboard/lib/useLearningSession.ts` | Create | Hook — session state, event log, order close evaluation, download |
| `dashboard/components/LearningStartModal.tsx` | Create | Start / resume / discard modal |
| `dashboard/components/LearningRecommendationPanel.tsx` | Create | Bot signal card with Accept / Reject / Place Custom |
| `dashboard/components/LearningNoteOverlay.tsx` | Create | Sticky note button + floating textarea overlay |
| `dashboard/components/TimeScrubber.tsx` | Modify | Add `learningMode` prop — hide slider + ◀, show counter |
| `dashboard/components/SwingPointsChart.tsx` | Modify | Add `learningOrders` prop — draw order zones as Canvas plugin |
| `dashboard/app/page.tsx` | Modify | Wire hook, activate overlay, pass props |

---

### Task 1: Type definitions

**Files:**
- Create: `dashboard/lib/learningTypes.ts`

- [ ] **Step 1: Create the types file**

```typescript
// dashboard/lib/learningTypes.ts
import type { Signal, TrendLevel, SwingPoint, Kline } from '@/lib/types'

export interface LearningOrder {
  id: string
  placedAtCandleIndex: number
  side: 'BUY' | 'SELL'
  entryPrice: number
  tpPrice: number
  slPrice: number
  closedAtCandleIndex?: number
  closePrice?: number
}

export interface TrendStateSnapshot {
  trend_levels: TrendLevel[]
  all_points: SwingPoint[]
}

export type LearningEvent =
  | { type: 'candle_advanced'; candle_index: number; timestamp: string; trend_state: TrendStateSnapshot }
  | { type: 'signal_accepted'; candle_index: number; signal: Signal; timestamp: string }
  | { type: 'signal_rejected'; candle_index: number; signal: Signal; reason?: string; timestamp: string }
  | { type: 'custom_order_placed'; candle_index: number; order: LearningOrder; timestamp: string }
  | { type: 'order_closed'; order_id: string; candle_index: number; market_outcome: 'tp_hit' | 'sl_hit'; pnl_pct: number; timestamp: string }
  | { type: 'note_added'; candle_index: number; text: string; timestamp: string }

export interface LearningSession {
  id: string
  symbol: string
  startCandleIndex: number
  currentCandleIndex: number
  events: LearningEvent[]
  startedAt: string
}

// Shape written to the download JSON (endedAt added at serialization)
export interface LearningSessionFile extends LearningSession {
  ended_at: string
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors from `learningTypes.ts` (there may be pre-existing errors in other files — ignore those).

- [ ] **Step 3: Commit**

```bash
git add dashboard/lib/learningTypes.ts
git commit -m "feat(learning): add TypeScript types for Learning Mode"
```

---

### Task 2: `useLearningSession` hook

**Files:**
- Create: `dashboard/lib/useLearningSession.ts`

The hook maintains session state and a separate `orders` list (for efficient rendering without replaying all events). It exposes action functions called by `page.tsx` and child components.

- [ ] **Step 1: Create the hook**

```typescript
// dashboard/lib/useLearningSession.ts
'use client'

import { useState, useRef, useCallback } from 'react'
import type { Signal, Kline } from '@/lib/types'
import type {
  LearningSession,
  LearningOrder,
  LearningEvent,
  TrendStateSnapshot,
  LearningSessionFile,
} from '@/lib/learningTypes'

function uuid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

function computePnlPct(order: LearningOrder, closePrice: number): number {
  const sideSign = order.side === 'BUY' ? 1 : -1
  return ((closePrice - order.entryPrice) / order.entryPrice) * 100 * sideSign
}

export interface LearningSessionHandle {
  session: LearningSession | null
  isActive: boolean
  orders: LearningOrder[]
  startSession: (symbol: string, startCandleIndex: number) => void
  discardSession: () => void
  onReplayResult: (candleIndex: number, kline: Kline, trendState: TrendStateSnapshot) => void
  acceptSignal: (signal: Signal) => void
  rejectSignal: (signal: Signal, reason?: string) => void
  placeCustomOrder: (side: 'BUY' | 'SELL', entry: number, tp: number, sl: number) => void
  addNote: (text: string) => void
  saveAndExit: () => void
}

export function useLearningSession(): LearningSessionHandle {
  const [session, setSession] = useState<LearningSession | null>(null)
  const [orders, setOrders] = useState<LearningOrder[]>([])

  // Ref mirrors orders state so callbacks that close over stale state can read current value
  const ordersRef = useRef<LearningOrder[]>([])
  ordersRef.current = orders

  const isActive = session !== null

  const startSession = useCallback((symbol: string, startCandleIndex: number) => {
    const newSession: LearningSession = {
      id: uuid(),
      symbol,
      startCandleIndex,
      currentCandleIndex: startCandleIndex,
      events: [],
      startedAt: new Date().toISOString(),
    }
    setSession(newSession)
    setOrders([])
  }, [])

  const discardSession = useCallback(() => {
    setSession(null)
    setOrders([])
  }, [])

  const appendEvent = useCallback((event: LearningEvent) => {
    setSession(prev => prev ? { ...prev, events: [...prev.events, event] } : prev)
  }, [])

  // Called from page.tsx after each successful replay API response in learning mode.
  // Evaluates open orders against the new kline, appends candle_advanced + any order_closed events.
  const onReplayResult = useCallback((candleIndex: number, kline: Kline, trendState: TrendStateSnapshot) => {
    const now = new Date().toISOString()
    const currentOrders = ordersRef.current

    type Closure = { orderId: string; outcome: 'tp_hit' | 'sl_hit'; closePrice: number; pnlPct: number }
    const closures: Closure[] = []

    const updatedOrders = currentOrders.map(order => {
      if (order.closedAtCandleIndex !== undefined) return order

      const isBuy = order.side === 'BUY'
      const tpHit = isBuy ? kline.high >= order.tpPrice : kline.low <= order.tpPrice
      const slHit = isBuy ? kline.low <= order.slPrice  : kline.high >= order.slPrice

      if (tpHit) {
        const closed = { ...order, closedAtCandleIndex: candleIndex, closePrice: order.tpPrice }
        closures.push({ orderId: order.id, outcome: 'tp_hit', closePrice: order.tpPrice, pnlPct: computePnlPct(order, order.tpPrice) })
        return closed
      }
      if (slHit) {
        const closed = { ...order, closedAtCandleIndex: candleIndex, closePrice: order.slPrice }
        closures.push({ orderId: order.id, outcome: 'sl_hit', closePrice: order.slPrice, pnlPct: computePnlPct(order, order.slPrice) })
        return closed
      }
      return order
    })

    setOrders(updatedOrders)
    ordersRef.current = updatedOrders

    const newEvents: LearningEvent[] = [
      { type: 'candle_advanced', candle_index: candleIndex, timestamp: now, trend_state: trendState },
      ...closures.map((c): LearningEvent => ({
        type: 'order_closed',
        order_id: c.orderId,
        candle_index: candleIndex,
        market_outcome: c.outcome,
        pnl_pct: c.pnlPct,
        timestamp: now,
      })),
    ]

    setSession(prev => {
      if (!prev) return prev
      return { ...prev, currentCandleIndex: candleIndex, events: [...prev.events, ...newEvents] }
    })
  }, [])

  const acceptSignal = useCallback((signal: Signal) => {
    setSession(prev => {
      if (!prev) return prev
      const event: LearningEvent = {
        type: 'signal_accepted',
        candle_index: prev.currentCandleIndex,
        signal,
        timestamp: new Date().toISOString(),
      }
      return { ...prev, events: [...prev.events, event] }
    })
  }, [])

  const rejectSignal = useCallback((signal: Signal, reason?: string) => {
    setSession(prev => {
      if (!prev) return prev
      const event: LearningEvent = {
        type: 'signal_rejected',
        candle_index: prev.currentCandleIndex,
        signal,
        reason,
        timestamp: new Date().toISOString(),
      }
      return { ...prev, events: [...prev.events, event] }
    })
  }, [])

  const placeCustomOrder = useCallback((side: 'BUY' | 'SELL', entry: number, tp: number, sl: number) => {
    setSession(prev => {
      if (!prev) return prev
      const order: LearningOrder = {
        id: uuid(),
        placedAtCandleIndex: prev.currentCandleIndex,
        side,
        entryPrice: entry,
        tpPrice: tp,
        slPrice: sl,
      }
      setOrders(o => [...o, order])
      ordersRef.current = [...ordersRef.current, order]
      const event: LearningEvent = {
        type: 'custom_order_placed',
        candle_index: prev.currentCandleIndex,
        order,
        timestamp: new Date().toISOString(),
      }
      return { ...prev, events: [...prev.events, event] }
    })
  }, [])

  const addNote = useCallback((text: string) => {
    setSession(prev => {
      if (!prev) return prev
      const event: LearningEvent = {
        type: 'note_added',
        candle_index: prev.currentCandleIndex,
        text,
        timestamp: new Date().toISOString(),
      }
      return { ...prev, events: [...prev.events, event] }
    })
  }, [])

  const saveAndExit = useCallback(() => {
    if (!session) return
    const file: LearningSessionFile = {
      ...session,
      ended_at: new Date().toISOString(),
    }
    const json = JSON.stringify(file, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const dateStr = new Date().toISOString().slice(0, 10)
    const idPrefix = session.id.slice(0, 8)
    const a = document.createElement('a')
    a.href = url
    a.download = `learning-${session.symbol}-${dateStr}-${idPrefix}.json`
    a.click()
    URL.revokeObjectURL(url)
    setSession(null)
    setOrders([])
  }, [session])

  return {
    session,
    isActive,
    orders,
    startSession,
    discardSession,
    onReplayResult,
    acceptSignal,
    rejectSignal,
    placeCustomOrder,
    addNote,
    saveAndExit,
  }
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```

Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/lib/useLearningSession.ts
git commit -m "feat(learning): add useLearningSession hook"
```

---

### Task 3: TimeScrubber — learning mode

**Files:**
- Modify: `dashboard/components/TimeScrubber.tsx`

In learning mode: hide the slider and ◀ back button, add a candle counter label. The ▶ button still calls `onScrub` — navigation logic stays in the page.

- [ ] **Step 1: Add `learningMode` prop and conditional render**

Replace the entire file content:

```typescript
// dashboard/components/TimeScrubber.tsx
'use client'

const TICK = 1

interface Props {
  klines: { time: number }[]
  scrubberIdx: number | null
  isLoading: boolean
  onScrub: (idx: number | null) => void
  learningMode?: boolean
}

function fmtTime(unixSec: number): string {
  const d = new Date(unixSec * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export default function TimeScrubber({ klines, scrubberIdx, isLoading, onScrub, learningMode }: Props) {
  if (klines.length === 0) return null

  const maxIdx     = klines.length - 1
  const isLive     = scrubberIdx === null
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

  if (learningMode) {
    return (
      <div className="flex items-center gap-2">
        <button onClick={stepForward} disabled={isLoading || isLive} className={btnCls}>
          Next Candle ▶
        </button>
        <span className="text-xs font-mono text-amber-400 min-w-[120px]">
          Candle {displayIdx} / {maxIdx}
        </span>
        {isLoading && <span className="text-xs text-gray-600">loading…</span>}
      </div>
    )
  }

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

      {isLoading && <span className="text-xs text-gray-600">updating…</span>}
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/components/TimeScrubber.tsx
git commit -m "feat(learning): add learningMode prop to TimeScrubber"
```

---

### Task 4: SwingPointsChart — learning order overlay

**Files:**
- Modify: `dashboard/components/SwingPointsChart.tsx`

Add a `learningOrders` optional prop. When present, draw order zones (TP green / SL red rectangles + entry line) on the chart using a Canvas plugin — the same pattern used in `TradesChart.tsx`. Open orders extend to the last kline. Closed orders are faded.

- [ ] **Step 1: Add import and prop to `SwingPointsChart.tsx`**

At the top of the file, add the import after the existing imports:

```typescript
import type { LearningOrder } from '@/lib/learningTypes'
```

Change the Props interface from:

```typescript
interface Props {
  klines: Kline[]
  points: SwingPoint[]
}
```

to:

```typescript
interface Props {
  klines: Kline[]
  points: SwingPoint[]
  learningOrders?: LearningOrder[]
}
```

Change the component signature from:

```typescript
export default function SwingPointsChart({ klines, points }: Props) {
```

to:

```typescript
export default function SwingPointsChart({ klines, points, learningOrders }: Props) {
```

- [ ] **Step 2: Add the Canvas plugin factory (add before the `export default` line)**

Insert this function between `SHARED_LEGEND` and the component definition:

```typescript
import type { Plugin } from 'chart.js'

function makeLearningOrdersPlugin(klines: Kline[], orders: LearningOrder[]): Plugin {
  return {
    id: 'learningOrders',
    beforeDatasetsDraw(chart) {
      if (!orders || orders.length === 0) return
      const ctx = chart.ctx
      const xs = chart.scales.x
      const ys = chart.scales.y
      if (!xs || !ys) return

      ctx.save()

      for (const order of orders) {
        const openKline = klines[order.placedAtCandleIndex]
        if (!openKline) continue
        const closeKline = order.closedAtCandleIndex !== undefined
          ? klines[order.closedAtCandleIndex]
          : klines[klines.length - 1]
        if (!closeKline) continue

        const x1 = xs.getPixelForValue(openKline.time * 1000)
        const x2 = xs.getPixelForValue(closeKline.time * 1000)
        const w  = Math.max(Math.abs(x2 - x1), 3)

        const isClosed = order.closedAtCandleIndex !== undefined
        const alpha = isClosed ? 0.15 : 0.30

        const ey = ys.getPixelForValue(order.entryPrice)
        const ty = ys.getPixelForValue(order.tpPrice)
        const sy = ys.getPixelForValue(order.slPrice)

        // TP zone (green)
        ctx.fillStyle = `rgba(52,211,153,${alpha})`
        ctx.fillRect(x1, Math.min(ey, ty), w, Math.abs(ty - ey))
        ctx.strokeStyle = `rgba(52,211,153,${alpha * 2.5})`
        ctx.lineWidth = 1
        ctx.strokeRect(x1, Math.min(ey, ty), w, Math.abs(ty - ey))

        // SL zone (red)
        ctx.fillStyle = `rgba(248,113,113,${alpha})`
        ctx.fillRect(x1, Math.min(ey, sy), w, Math.abs(sy - ey))
        ctx.strokeStyle = `rgba(248,113,113,${alpha * 2.5})`
        ctx.lineWidth = 1
        ctx.strokeRect(x1, Math.min(ey, sy), w, Math.abs(sy - ey))

        // Entry line
        ctx.strokeStyle = `rgba(209,213,219,0.7)`
        ctx.lineWidth = 1.5
        ctx.setLineDash([])
        ctx.beginPath()
        ctx.moveTo(x1, ey)
        ctx.lineTo(x2, ey)
        ctx.stroke()
      }

      ctx.restore()
    },
  }
}
```

- [ ] **Step 3: Pass the plugin to both chart render paths**

Inside `SwingPointsChart`, add this after `chartData` is computed:

```typescript
const learningPlugin = useMemo(
  () => makeLearningOrdersPlugin(klines, learningOrders ?? []),
  [klines, learningOrders]
)
```

Then in the candleView return, change:
```typescript
<Chart type={'candlestick' as any} data={candleData as any} options={candleOptions as any} />
```
to:
```typescript
<Chart type={'candlestick' as any} data={candleData as any} options={candleOptions as any} plugins={[learningPlugin]} />
```

And in the lineView return, change:
```typescript
<Line data={lineData} options={lineOptions as Parameters<typeof Line>[0]['options']} />
```
to:
```typescript
<Line data={lineData} options={lineOptions as Parameters<typeof Line>[0]['options']} plugins={[learningPlugin]} />
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -30
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/components/SwingPointsChart.tsx
git commit -m "feat(learning): draw learning order zones on SwingPointsChart"
```

---

### Task 5: LearningStartModal

**Files:**
- Create: `dashboard/components/LearningStartModal.tsx`

A modal that appears when the user clicks "Learning Mode". If there is an existing in-progress session, it shows a resume prompt with a "Discard & Start New" option. Otherwise it shows a start form with the candle index pre-filled.

- [ ] **Step 1: Create the component**

```typescript
// dashboard/components/LearningStartModal.tsx
'use client'

import { useState } from 'react'
import type { LearningSession } from '@/lib/learningTypes'

interface Props {
  isOpen: boolean
  currentCandleIndex: number
  totalCandles: number
  existingSession: LearningSession | null
  onStart: (startCandleIndex: number) => void
  onResume: () => void
  onDiscard: () => void
  onClose: () => void
}

export default function LearningStartModal({
  isOpen,
  currentCandleIndex,
  totalCandles,
  existingSession,
  onStart,
  onResume,
  onDiscard,
  onClose,
}: Props) {
  const [candleInput, setCandleInput] = useState(String(currentCandleIndex))

  if (!isOpen) return null

  const overlayClass =
    'fixed inset-0 z-50 flex items-center justify-center bg-black/60'

  const cardClass =
    'bg-gray-900 border border-gray-700 rounded-lg p-6 w-80 space-y-4 shadow-xl'

  const btnPrimary =
    'w-full px-4 py-2 text-sm font-semibold rounded bg-amber-500 text-black hover:bg-amber-400 transition-colors'

  const btnSecondary =
    'w-full px-4 py-2 text-sm font-semibold rounded border border-gray-600 text-gray-300 hover:text-white hover:border-gray-400 transition-colors'

  const btnDanger =
    'w-full px-4 py-2 text-sm font-semibold rounded border border-red-700 text-red-400 hover:text-red-300 hover:border-red-500 transition-colors'

  if (existingSession) {
    return (
      <div className={overlayClass} onClick={onClose}>
        <div className={cardClass} onClick={e => e.stopPropagation()}>
          <h2 className="text-white font-semibold text-base">Resume Session?</h2>
          <p className="text-gray-400 text-sm">
            You have an in-progress session for{' '}
            <span className="text-white font-mono">{existingSession.symbol}</span> from
            candle {existingSession.startCandleIndex} ({existingSession.events.length} events recorded).
          </p>
          <button onClick={onResume} className={btnPrimary}>Resume</button>
          <button onClick={onDiscard} className={btnDanger}>Discard &amp; Start New</button>
          <button onClick={onClose} className={btnSecondary}>Cancel</button>
        </div>
      </div>
    )
  }

  function handleStart() {
    const idx = Math.max(0, Math.min(totalCandles - 1, Number(candleInput) || 0))
    onStart(idx)
  }

  return (
    <div className={overlayClass} onClick={onClose}>
      <div className={cardClass} onClick={e => e.stopPropagation()}>
        <h2 className="text-white font-semibold text-base">Start Learning Mode</h2>
        <p className="text-gray-400 text-sm">
          Navigate candle-by-candle, accept/reject signals, place custom orders, and add notes.
          Save the session to analyze with Claude.
        </p>
        <div className="space-y-1">
          <label className="text-xs text-gray-500 uppercase tracking-wider">
            Start from candle index
          </label>
          <input
            type="number"
            min={0}
            max={totalCandles - 1}
            value={candleInput}
            onChange={e => setCandleInput(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500"
          />
          <p className="text-xs text-gray-600">0 – {totalCandles - 1} available</p>
        </div>
        <button onClick={handleStart} className={btnPrimary}>Start</button>
        <button onClick={onClose} className={btnSecondary}>Cancel</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/components/LearningStartModal.tsx
git commit -m "feat(learning): add LearningStartModal component"
```

---

### Task 6: LearningRecommendationPanel

**Files:**
- Create: `dashboard/components/LearningRecommendationPanel.tsx`

Shows the current replay signal with Accept / Reject / Place Custom buttons. Reject opens an optional reason form inline. Place Custom opens a price input form. Both forms close on submit.

`Signal.target` is the TP price, `Signal.stop` is the SL price (can be null).

- [ ] **Step 1: Create the component**

```typescript
// dashboard/components/LearningRecommendationPanel.tsx
'use client'

import { useState } from 'react'
import type { Signal } from '@/lib/types'
import { formatPrice } from '@/lib/formatPrice'

interface Props {
  signal: Signal | null
  currentKlineClose: number
  onAccept: (signal: Signal) => void
  onReject: (signal: Signal, reason?: string) => void
  onPlaceCustom: (side: 'BUY' | 'SELL', entry: number, tp: number, sl: number) => void
}

type PanelState = 'idle' | 'reject-form' | 'custom-form'

export default function LearningRecommendationPanel({
  signal,
  currentKlineClose,
  onAccept,
  onReject,
  onPlaceCustom,
}: Props) {
  const [panelState, setPanelState] = useState<PanelState>('idle')
  const [rejectReason, setRejectReason] = useState('')
  const [customSide, setCustomSide] = useState<'BUY' | 'SELL'>('BUY')
  const [customEntry, setCustomEntry] = useState('')
  const [customTp, setCustomTp] = useState('')
  const [customSl, setCustomSl] = useState('')

  function handleAccept() {
    if (!signal) return
    onAccept(signal)
    setPanelState('idle')
  }

  function handleRejectSubmit() {
    if (!signal) return
    onReject(signal, rejectReason.trim() || undefined)
    setRejectReason('')
    setPanelState('idle')
  }

  function handleCustomSubmit() {
    const entry = Number(customEntry)
    const tp    = Number(customTp)
    const sl    = Number(customSl)
    if (!entry || !tp || !sl) return
    onPlaceCustom(customSide, entry, tp, sl)
    setCustomEntry('')
    setCustomTp('')
    setCustomSl('')
    setPanelState('idle')
  }

  function openCustomForm() {
    setCustomEntry(String(currentKlineClose))
    setPanelState('custom-form')
  }

  const cardCls = 'rounded-lg border border-gray-800 bg-gray-900 p-4 space-y-3'
  const labelCls = 'text-xs text-gray-500 uppercase tracking-wider'
  const inputCls = 'w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-amber-500'
  const btnPrimary = 'px-3 py-1.5 text-xs font-semibold rounded bg-emerald-700 text-white hover:bg-emerald-600 transition-colors'
  const btnDanger  = 'px-3 py-1.5 text-xs font-semibold rounded bg-red-900 text-white hover:bg-red-800 transition-colors border border-red-700'
  const btnNeutral = 'px-3 py-1.5 text-xs font-semibold rounded border border-gray-600 text-gray-300 hover:text-white hover:border-gray-400 transition-colors'

  if (panelState === 'reject-form' && signal) {
    return (
      <div className={cardCls}>
        <p className={labelCls}>Reject reason (optional)</p>
        <textarea
          autoFocus
          value={rejectReason}
          onChange={e => setRejectReason(e.target.value)}
          placeholder="Why are you rejecting this signal? (leave blank to skip)"
          className={`${inputCls} resize-none h-16`}
        />
        <div className="flex gap-2">
          <button onClick={handleRejectSubmit} className={btnDanger}>Confirm Reject</button>
          <button onClick={() => setPanelState('idle')} className={btnNeutral}>Cancel</button>
        </div>
      </div>
    )
  }

  if (panelState === 'custom-form') {
    return (
      <div className={cardCls}>
        <p className={labelCls}>Place custom order</p>
        <div className="flex gap-2">
          {(['BUY', 'SELL'] as const).map(s => (
            <button
              key={s}
              onClick={() => setCustomSide(s)}
              className={`flex-1 py-1 text-xs font-semibold rounded border transition-colors ${
                customSide === s
                  ? s === 'BUY'
                    ? 'bg-emerald-800 border-emerald-600 text-white'
                    : 'bg-red-900 border-red-700 text-white'
                  : 'border-gray-700 text-gray-400 hover:text-white'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-3 gap-2">
          <div className="space-y-1">
            <label className={labelCls}>Entry</label>
            <input type="number" value={customEntry} onChange={e => setCustomEntry(e.target.value)} className={inputCls} />
          </div>
          <div className="space-y-1">
            <label className={labelCls}>TP</label>
            <input type="number" value={customTp} onChange={e => setCustomTp(e.target.value)} className={inputCls} />
          </div>
          <div className="space-y-1">
            <label className={labelCls}>SL</label>
            <input type="number" value={customSl} onChange={e => setCustomSl(e.target.value)} className={inputCls} />
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={handleCustomSubmit} className={btnPrimary}>Place Order</button>
          <button onClick={() => setPanelState('idle')} className={btnNeutral}>Cancel</button>
        </div>
      </div>
    )
  }

  // Default: signal display or no-signal state
  return (
    <div className={cardCls}>
      {signal ? (
        <>
          <div className="flex items-center gap-2">
            <p className={labelCls}>Bot Recommendation</p>
            <span className={`text-xs font-mono font-semibold px-1.5 py-0.5 rounded ${
              signal.side === 'BUY' ? 'bg-emerald-900 text-emerald-300' : 'bg-red-900 text-red-300'
            }`}>
              {signal.side}
            </span>
            <span className="text-xs text-gray-500 font-mono">{signal.signal_type}</span>
          </div>
          <div className="grid grid-cols-4 gap-3 text-xs font-mono">
            <div><span className="text-gray-500">Entry</span><br />{formatPrice(signal.entry)}</div>
            <div><span className="text-gray-500">TP</span><br />{formatPrice(signal.target)}</div>
            <div><span className="text-gray-500">SL</span><br />{signal.stop ? formatPrice(signal.stop) : '—'}</div>
            <div><span className="text-gray-500">RR</span><br />{signal.rr.toFixed(2)}x</div>
          </div>
          <div className="flex gap-2 flex-wrap">
            <button onClick={handleAccept} className={btnPrimary}>Accept ✓</button>
            <button onClick={() => setPanelState('reject-form')} className={btnDanger}>Reject ✗</button>
            <button onClick={openCustomForm} className={btnNeutral}>Place Custom</button>
          </div>
        </>
      ) : (
        <>
          <p className={labelCls}>Bot Recommendation</p>
          <p className="text-gray-600 text-sm">No signal this candle.</p>
          <button onClick={openCustomForm} className={btnNeutral}>Place Custom Order</button>
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/components/LearningRecommendationPanel.tsx
git commit -m "feat(learning): add LearningRecommendationPanel component"
```

---

### Task 7: LearningNoteOverlay

**Files:**
- Create: `dashboard/components/LearningNoteOverlay.tsx`

A sticky fixed-position note button in the bottom-right corner. Always visible when Learning Mode is active. Clicking it opens a floating overlay with a textarea. Never disabled by other UI.

- [ ] **Step 1: Create the component**

```typescript
// dashboard/components/LearningNoteOverlay.tsx
'use client'

import { useState } from 'react'

interface Props {
  onAddNote: (text: string) => void
}

export default function LearningNoteOverlay({ onAddNote }: Props) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')

  function handleSubmit() {
    const trimmed = text.trim()
    if (!trimmed) { setOpen(false); return }
    onAddNote(trimmed)
    setText('')
    setOpen(false)
  }

  return (
    <>
      {/* Sticky trigger button — always visible, fixed position */}
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-40 px-3 py-2 text-xs font-semibold rounded-full bg-amber-600 text-black hover:bg-amber-500 shadow-lg transition-colors"
      >
        + Note
      </button>

      {/* Floating overlay */}
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div
            className="bg-gray-900 border border-gray-700 rounded-lg p-4 w-80 space-y-3 shadow-xl"
            onClick={e => e.stopPropagation()}
          >
            <p className="text-xs text-gray-500 uppercase tracking-wider">Add Note</p>
            <textarea
              autoFocus
              value={text}
              onChange={e => setText(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleSubmit() }}
              placeholder="Anything you observe at this candle…"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-white resize-none h-24 focus:outline-none focus:border-amber-500"
            />
            <p className="text-xs text-gray-600">Ctrl+Enter to submit</p>
            <div className="flex gap-2">
              <button
                onClick={handleSubmit}
                className="flex-1 py-1.5 text-xs font-semibold rounded bg-amber-600 text-black hover:bg-amber-500 transition-colors"
              >
                Save Note
              </button>
              <button
                onClick={() => { setText(''); setOpen(false) }}
                className="flex-1 py-1.5 text-xs font-semibold rounded border border-gray-600 text-gray-300 hover:text-white transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/components/LearningNoteOverlay.tsx
git commit -m "feat(learning): add LearningNoteOverlay sticky note component"
```

---

### Task 8: Wire Learning Mode into `page.tsx`

**Files:**
- Modify: `dashboard/app/page.tsx`

This is the main wiring task. The goal is to:
1. Import and call `useLearningSession`
2. Show a "Learning Mode" button that opens `LearningStartModal`
3. Show an amber header banner when active
4. Pass `learningMode` to `TimeScrubber`
5. Pass `learningOrders` to `SwingPointsChart`
6. Render `LearningRecommendationPanel` below the chart when active
7. Render `LearningNoteOverlay` when active
8. Call `learningSession.onReplayResult(...)` after each replay response in learning mode
9. Add "Stop & Save" and "Discard" buttons when active

- [ ] **Step 1: Add imports at the top of `page.tsx`**

After the existing imports, add:

```typescript
import { useLearningSession } from '@/lib/useLearningSession'
import LearningStartModal from '@/components/LearningStartModal'
import LearningRecommendationPanel from '@/components/LearningRecommendationPanel'
import LearningNoteOverlay from '@/components/LearningNoteOverlay'
```

- [ ] **Step 2: Add learning session hook and modal state inside `PageContent`**

Inside `PageContent`, after the existing state declarations (after `const [toDate, setToDate]...`), add:

```typescript
const learningSession = useLearningSession()
const [learningModalOpen, setLearningModalOpen] = useState(false)
```

- [ ] **Step 3: Update the replay useEffect to notify the learning session**

Replace the existing replay `useEffect`:

```typescript
useEffect(() => {
  if (scrubberIdx === null) return
  const timer = setTimeout(() => {
    setIsReplaying(true)
    fetch('/api/replay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, candle_index: scrubberIdx }),
    })
      .then(r => r.json())
      .then((d: Omit<ReplayResult, 'candle_index'>) => {
        setReplayData({ ...d, candle_index: scrubberIdx })
        setIsReplaying(false)
        if (learningSession.isActive && data) {
          const kline = data.klines[scrubberIdx]
          if (kline) {
            learningSession.onReplayResult(
              scrubberIdx,
              kline,
              { trend_levels: d.trend_levels, all_points: d.all_points },
            )
          }
        }
      })
      .catch(() => setIsReplaying(false))
  }, 300)
  return () => clearTimeout(timer)
}, [scrubberIdx, symbol, learningSession.isActive, learningSession.onReplayResult, data])
```

Note: `data` must be added to the dep array. ESLint may warn about exhaustive deps — this is correct.

- [ ] **Step 4: Derive the current signal for the recommendation panel**

After the `srcSignals` line, add:

```typescript
const currentSignal = learningSession.isActive
  ? (srcSignals.length > 0 ? srcSignals[0] : null)
  : null
```

- [ ] **Step 5: Add the Learning Mode button to the toolbar (next to the Clear button)**

In the JSX, find the "Clear" button block:

```typescript
<button
  onClick={() => { setFromDate(''); setToDate('') }}
  className="px-3 py-1.5 text-xs font-semibold rounded border border-gray-700 bg-gray-900 text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
>
  Clear
</button>
```

Replace it with:

```typescript
<button
  onClick={() => { setFromDate(''); setToDate('') }}
  className="px-3 py-1.5 text-xs font-semibold rounded border border-gray-700 bg-gray-900 text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
>
  Clear
</button>

{!learningSession.isActive ? (
  <button
    onClick={() => setLearningModalOpen(true)}
    className="px-3 py-1.5 text-xs font-semibold rounded border border-amber-700 bg-amber-900/30 text-amber-400 hover:text-amber-300 hover:bg-amber-900/50 transition-colors"
  >
    Learning Mode
  </button>
) : (
  <div className="flex items-center gap-2">
    <span className="text-xs font-semibold text-amber-400 animate-pulse">● LEARNING</span>
    <button
      onClick={() => {
        if (confirm(`Save session? (${learningSession.session?.events.length ?? 0} events recorded)`)) {
          learningSession.saveAndExit()
        }
      }}
      className="px-3 py-1.5 text-xs font-semibold rounded bg-amber-600 text-black hover:bg-amber-500 transition-colors"
    >
      Stop &amp; Save
    </button>
    <button
      onClick={() => {
        if (confirm('Discard this session? All events will be lost.')) {
          learningSession.discardSession()
        }
      }}
      className="px-3 py-1.5 text-xs font-semibold rounded border border-red-800 text-red-400 hover:text-red-300 transition-colors"
    >
      Discard
    </button>
  </div>
)}
```

- [ ] **Step 6: Pass `learningMode` to `TimeScrubber`**

Find the `<TimeScrubber` usage and add the prop:

```typescript
<TimeScrubber
  klines={data.klines}
  scrubberIdx={scrubberIdx}
  isLoading={effectiveIsReplaying}
  onScrub={setScrubberIdx}
  learningMode={learningSession.isActive}
/>
```

- [ ] **Step 7: Pass `learningOrders` to `SwingPointsChart`**

Find the `<SwingPointsChart` usage and add the prop:

```typescript
<SwingPointsChart
  key={selectedLevel ?? 0}
  klines={filteredKlines}
  points={filteredPoints}
  learningOrders={learningSession.isActive ? learningSession.orders : undefined}
/>
```

- [ ] **Step 8: Add LearningRecommendationPanel and LearningNoteOverlay to the JSX**

After the Swing Points `<CollapsibleSection>` block, add:

```typescript
{learningSession.isActive && (
  <LearningRecommendationPanel
    signal={currentSignal}
    currentKlineClose={filteredKlines.length > 0 ? filteredKlines[filteredKlines.length - 1].close : 0}
    onAccept={learningSession.acceptSignal}
    onReject={learningSession.rejectSignal}
    onPlaceCustom={learningSession.placeCustomOrder}
  />
)}
```

At the very end of the `PageContent` return (before the closing `</main>`), add the modal and note overlay:

```typescript
<LearningStartModal
  isOpen={learningModalOpen}
  currentCandleIndex={scrubberIdx ?? (data.klines.length - 1)}
  totalCandles={data.klines.length}
  existingSession={learningSession.session}
  onStart={(idx) => {
    learningSession.startSession(symbol, idx)
    setScrubberIdx(idx)
    setLearningModalOpen(false)
  }}
  onResume={() => setLearningModalOpen(false)}
  onDiscard={() => {
    learningSession.discardSession()
    setLearningModalOpen(false)
  }}
  onClose={() => setLearningModalOpen(false)}
/>

{learningSession.isActive && (
  <LearningNoteOverlay onAddNote={learningSession.addNote} />
)}
```

- [ ] **Step 9: Verify TypeScript compiles**

```bash
cd dashboard && npx tsc --noEmit 2>&1 | head -30
```

Fix any type errors before committing.

- [ ] **Step 10: Commit**

```bash
git add dashboard/app/page.tsx
git commit -m "feat(learning): wire Learning Mode into Strategy page"
```

---

### Task 9: Manual smoke test

No automated tests exist for dashboard components. Verify manually:

- [ ] **Step 1: Start the dev server**

```bash
cd dashboard && npm run dev
```

Open `http://localhost:3000` in the browser.

- [ ] **Step 2: Verify Learning Mode button is visible**

Click "Learning Mode" in the toolbar. Modal should appear with a candle index input.

- [ ] **Step 3: Start a session and navigate**

Click "Start". The scrubber slider should disappear and "Next Candle ▶" should appear. Click Next Candle several times. Candle counter should increment.

- [ ] **Step 4: Verify recommendation panel**

The Bot Recommendation panel should appear below the chart. If the current replay has a signal, the signal details (side, entry, TP, SL, RR) should be visible. Click "Accept" — verify no UI error. Click "Reject" — the reason form should appear. Submit with an empty reason — should work fine.

- [ ] **Step 5: Verify custom order placement and chart overlay**

Click "Place Custom Order", fill in entry/TP/SL, submit. Green (TP) and red (SL) rectangles should appear on the chart. Advance several candles until the TP or SL is crossed — the rectangles should fade (become translucent).

- [ ] **Step 6: Verify notes are always accessible**

While any panel is open (reject form, custom form), the amber "+ Note" button should still be visible in the bottom-right. Click it — note textarea should open on top of everything.

- [ ] **Step 7: Verify Stop & Save**

Click "Stop & Save". Confirm the dialog. A JSON file should download to your Downloads folder. Open it and verify: `session_id`, `symbol`, `events` array with at least `candle_advanced` entries, and `order_closed` entries for any orders that hit TP/SL.

- [ ] **Step 8: Verify Discard**

Start a new session, add events, click "Discard". Confirm the dialog. Learning Mode should deactivate and the normal scrubber should reappear.

- [ ] **Step 9: Final commit (if any polish fixes were needed)**

```bash
git add -p   # stage only intentional changes
git commit -m "fix(learning): polish from smoke test"
```

---

## Self-review checklist (completed)

**Spec coverage:**
- ✓ Candle-by-candle navigation (Task 3, 8)
- ✓ Accept / Reject signal with optional reason (Task 6, 8)
- ✓ Place custom order with side/entry/TP/SL (Task 6, 8)
- ✓ Chart order zones: TP green / SL red / faded when closed (Task 4)
- ✓ Note button always visible, never disabled (Task 7)
- ✓ Auto-close orders when kline crosses TP/SL, append `order_closed` event (Task 2)
- ✓ `pnl_pct` computed at close (Task 2)
- ✓ `TrendStateSnapshot` captured at each `candle_advanced` (Task 2, 8)
- ✓ Session download as JSON on Stop & Save (Task 2)
- ✓ Discard with confirmation (Task 8)
- ✓ Resume prompt for existing session (Task 5)
- ✓ Amber LEARNING MODE indicator (Task 8)
- ✓ Scrubber slider/◀ hidden in learning mode (Task 3)
- ✓ `docs/learning/hypotheses.md` stub (already committed in spec task)
- ✓ No bot-side changes

**Type consistency confirmed:**
- `LearningOrder.tpPrice` / `.slPrice` / `.entryPrice` — used consistently in Task 2, 4, 6
- `Signal.target` (TP) / `Signal.stop` (SL) — correct per `types.ts`
- `TrendStateSnapshot` matches spec definition
- `onReplayResult` signature matches call site in page.tsx
