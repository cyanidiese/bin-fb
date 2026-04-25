# Dashboard UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Next.js 14 + Tailwind + Chart.js web dashboard at `./dashboard/` that reads `results.json` and displays swing point charts, trend level tables, and all-points tables.

**Architecture:** Static Next.js App Router app — no server, no API calls to Binance. The Python bot writes `dashboard/public/results.json` after each run; the page fetches it client-side on load. All components are client components (Chart.js requires browser).

**Tech Stack:** Next.js 14 (App Router), TypeScript, Tailwind CSS, react-chartjs-2 + chart.js

---

## File Map

| File | Purpose |
|---|---|
| `dashboard/app/layout.tsx` | Root layout, dark mode class, font |
| `dashboard/app/globals.css` | Tailwind directives |
| `dashboard/app/page.tsx` | Main page — fetches results.json, composes components |
| `dashboard/components/Header.tsx` | Symbol, timeframe, mode badge, timestamp |
| `dashboard/components/SwingPointsChart.tsx` | Chart.js line chart: close prices + swing point markers |
| `dashboard/components/TrendLevelsTable.tsx` | Sortable trend levels table |
| `dashboard/components/AllPointsTable.tsx` | Sortable all-points table (50 rows, 2 col layout) |
| `dashboard/components/SignalsPanel.tsx` | Active signals list |
| `dashboard/lib/types.ts` | TypeScript types for results.json schema |
| `dashboard/public/results.json` | Bot-written data file (also initial sample) |
| `dashboard/README.md` | Setup + JSON schema docs |
| `.gitignore` (root) | Add dashboard/node_modules, dashboard/.next, etc. |
| `bot/exporter.py` | Python function to write results.json from bot state |
| `main.py` | Call exporter after each display.show() |

---

## Task 1: Scaffold Next.js app

**Files:**
- Create: `dashboard/` (entire scaffold)

- [ ] **Step 1: Run create-next-app**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
mkdir -p dashboard && cd dashboard
npx create-next-app@latest . --ts --tailwind --eslint --app --no-src-dir --import-alias "@/*" --yes
```

Expected: Next.js 14 scaffold created with `app/`, `public/`, `tailwind.config.ts`, `tsconfig.json`.

- [ ] **Step 2: Install chart dependencies**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npm install react-chartjs-2 chart.js
```

Expected: `chart.js` and `react-chartjs-2` appear in `package.json` dependencies.

- [ ] **Step 3: Verify dev server starts**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npm run dev &
sleep 3 && curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
```

Expected: `200`

Kill the dev server after verifying: `kill %1`

- [ ] **Step 4: Update root .gitignore**

Append to `/Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/.gitignore`:

```
# Dashboard
dashboard/node_modules/
dashboard/.next/
dashboard/.env*.local
```

- [ ] **Step 5: Commit scaffold**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/ .gitignore
git commit -m "feat: scaffold Next.js dashboard"
```

---

## Task 2: TypeScript types

**Files:**
- Create: `dashboard/lib/types.ts`

- [ ] **Step 1: Write types file**

```typescript
// dashboard/lib/types.ts

export interface TrendLevel {
  level: number;           // 1, 2, 3
  direction: 'ASC' | 'DESC' | 'NONE';
  bos: number | null;      // Break of Structure price
  bos_since: string | null; // ISO timestamp
  last_high: { price: number; time: string } | null;
  last_low:  { price: number; time: string } | null;
}

export interface SwingPoint {
  time: string;   // ISO timestamp
  level: number;
  type: 'high' | 'low';
  price: number;
}

export interface Signal {
  level: number;
  side: 'BUY' | 'SELL';
  signal_type: string;    // e.g. "lowering_above_last_low"
  target: number;
  stop: number | null;
}

export interface Kline {
  time: number;   // Unix timestamp seconds
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface BotResults {
  symbol: string;
  timeframe: string;
  mode: 'testnet' | 'live';
  generated_at: string;    // ISO timestamp
  current_price: number;
  trend_levels: TrendLevel[];
  all_points: SwingPoint[];
  klines: Kline[];         // last N candles for chart (e.g. 200)
  signals: Signal[];
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/lib/types.ts
git commit -m "feat: add TypeScript types for results.json schema"
```

---

## Task 3: Sample results.json

**Files:**
- Create: `dashboard/public/results.json`

- [ ] **Step 1: Write sample data file**

Create `dashboard/public/results.json` with realistic data matching the schema. Use the values currently visible in the bot console output:

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "15m",
  "mode": "testnet",
  "generated_at": "2026-04-20T20:14:00Z",
  "current_price": 75335.80,
  "trend_levels": [
    {
      "level": 1,
      "direction": "DESC",
      "bos": 76050.00,
      "bos_since": "2026-04-19T17:59:00Z",
      "last_high": { "price": 75645.30, "time": "2026-04-20T19:14:00Z" },
      "last_low":  { "price": 75288.20, "time": "2026-04-20T20:14:00Z" }
    },
    {
      "level": 2,
      "direction": "ASC",
      "bos": 73570.70,
      "bos_since": "2026-04-16T17:29:00Z",
      "last_high": { "price": 75589.50, "time": "2026-04-20T17:44:00Z" },
      "last_low":  { "price": 74761.80, "time": "2026-04-20T18:14:00Z" }
    },
    {
      "level": 3,
      "direction": "ASC",
      "bos": 70558.90,
      "bos_since": "2026-04-13T01:44:00Z",
      "last_high": { "price": 75853.00, "time": "2026-04-14T17:44:00Z" },
      "last_low":  { "price": 73570.70, "time": "2026-04-16T17:29:00Z" }
    }
  ],
  "all_points": [
    { "time": "2026-04-20T20:14:00Z", "level": 1, "type": "low",  "price": 75288.20 },
    { "time": "2026-04-20T19:14:00Z", "level": 1, "type": "high", "price": 75645.30 },
    { "time": "2026-04-20T18:14:00Z", "level": 1, "type": "low",  "price": 74761.80 },
    { "time": "2026-04-20T18:14:00Z", "level": 2, "type": "low",  "price": 74761.80 },
    { "time": "2026-04-20T17:44:00Z", "level": 1, "type": "high", "price": 75589.50 },
    { "time": "2026-04-20T17:44:00Z", "level": 2, "type": "high", "price": 75589.50 },
    { "time": "2026-04-20T16:59:00Z", "level": 1, "type": "low",  "price": 75022.70 },
    { "time": "2026-04-20T15:29:00Z", "level": 1, "type": "high", "price": 75500.10 },
    { "time": "2026-04-20T14:14:00Z", "level": 1, "type": "low",  "price": 75011.10 },
    { "time": "2026-04-20T13:29:00Z", "level": 1, "type": "high", "price": 75308.70 },
    { "time": "2026-04-20T12:14:00Z", "level": 1, "type": "low",  "price": 74676.70 },
    { "time": "2026-04-20T11:59:00Z", "level": 1, "type": "low",  "price": 74658.70 },
    { "time": "2026-04-20T11:29:00Z", "level": 1, "type": "high", "price": 74952.50 },
    { "time": "2026-04-20T10:44:00Z", "level": 1, "type": "high", "price": 75164.70 },
    { "time": "2026-04-20T08:59:00Z", "level": 1, "type": "low",  "price": 74110.80 },
    { "time": "2026-04-20T06:59:00Z", "level": 1, "type": "high", "price": 74592.20 },
    { "time": "2026-04-20T06:14:00Z", "level": 1, "type": "low",  "price": 74319.40 },
    { "time": "2026-04-20T04:59:00Z", "level": 1, "type": "high", "price": 74680.30 },
    { "time": "2026-04-19T17:59:00Z", "level": 1, "type": "high", "price": 76050.00 },
    { "time": "2026-04-19T17:59:00Z", "level": 2, "type": "high", "price": 76050.00 },
    { "time": "2026-04-19T17:14:00Z", "level": 1, "type": "low",  "price": 75664.40 },
    { "time": "2026-04-19T15:29:00Z", "level": 1, "type": "high", "price": 75620.00 },
    { "time": "2026-04-19T11:44:00Z", "level": 1, "type": "low",  "price": 75075.20 },
    { "time": "2026-04-18T18:14:00Z", "level": 1, "type": "high", "price": 76246.00 },
    { "time": "2026-04-18T16:59:00Z", "level": 1, "type": "low",  "price": 75889.20 },
    { "time": "2026-04-18T12:59:00Z", "level": 1, "type": "high", "price": 76652.70 },
    { "time": "2026-04-18T04:29:00Z", "level": 1, "type": "high", "price": 77283.80 },
    { "time": "2026-04-17T19:29:00Z", "level": 1, "type": "high", "price": 83000.00 },
    { "time": "2026-04-17T19:29:00Z", "level": 2, "type": "high", "price": 83000.00 },
    { "time": "2026-04-17T13:59:00Z", "level": 1, "type": "low",  "price": 75179.40 },
    { "time": "2026-04-16T17:29:00Z", "level": 1, "type": "low",  "price": 73570.70 },
    { "time": "2026-04-16T17:29:00Z", "level": 2, "type": "low",  "price": 73570.70 },
    { "time": "2026-04-16T17:29:00Z", "level": 3, "type": "low",  "price": 73570.70 },
    { "time": "2026-04-15T22:44:00Z", "level": 1, "type": "high", "price": 75203.90 },
    { "time": "2026-04-15T22:44:00Z", "level": 2, "type": "high", "price": 75203.90 },
    { "time": "2026-04-14T17:44:00Z", "level": 1, "type": "high", "price": 75853.00 },
    { "time": "2026-04-14T17:44:00Z", "level": 2, "type": "high", "price": 75853.00 },
    { "time": "2026-04-14T17:44:00Z", "level": 3, "type": "high", "price": 75853.00 },
    { "time": "2026-04-13T01:44:00Z", "level": 1, "type": "low",  "price": 70558.90 },
    { "time": "2026-04-13T01:44:00Z", "level": 2, "type": "low",  "price": 70558.90 },
    { "time": "2026-04-13T01:44:00Z", "level": 3, "type": "low",  "price": 70558.90 }
  ],
  "klines": [],
  "signals": [
    {
      "level": 2,
      "side": "SELL",
      "signal_type": "lowering_above_last_low",
      "target": 73937.43,
      "stop": 76050.00
    }
  ]
}
```

Note: `klines` array will be populated by the Python exporter in Task 8. Leave empty for now — the chart will show a placeholder when empty.

- [ ] **Step 2: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/public/results.json
git commit -m "feat: add sample results.json"
```

---

## Task 4: Root layout and globals

**Files:**
- Modify: `dashboard/app/layout.tsx`
- Modify: `dashboard/app/globals.css`

- [ ] **Step 1: Update layout.tsx**

Replace `dashboard/app/layout.tsx` with:

```tsx
import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'Binance Futures Bot — Dashboard',
  description: 'Bot results viewer',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.className} bg-gray-950 text-gray-100 min-h-screen`}>
        {children}
      </body>
    </html>
  )
}
```

- [ ] **Step 2: Update globals.css**

Replace `dashboard/app/globals.css` with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 3: Update tailwind.config.ts to enable dark mode**

Replace content of `dashboard/tailwind.config.ts`:

```typescript
import type { Config } from 'tailwindcss'

const config: Config = {
  darkMode: 'class',
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: { extend: {} },
  plugins: [],
}
export default config
```

- [ ] **Step 4: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/app/layout.tsx dashboard/app/globals.css dashboard/tailwind.config.ts
git commit -m "feat: configure dark layout and Tailwind"
```

---

## Task 5: Header component

**Files:**
- Create: `dashboard/components/Header.tsx`

- [ ] **Step 1: Write Header component**

```tsx
// dashboard/components/Header.tsx
'use client'

import { BotResults } from '@/lib/types'

interface HeaderProps {
  data: BotResults
}

export default function Header({ data }: HeaderProps) {
  const modeColor = data.mode === 'testnet' ? 'bg-yellow-500 text-black' : 'bg-red-600 text-white'
  const ts = new Date(data.generated_at).toLocaleString()

  return (
    <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-bold tracking-tight">{data.symbol}</h1>
        <span className="text-gray-400 text-sm">{data.timeframe}</span>
        <span className={`text-xs font-semibold px-2 py-0.5 rounded ${modeColor}`}>
          {data.mode.toUpperCase()}
        </span>
      </div>
      <div className="flex items-center gap-6 text-sm">
        <span className="text-gray-400">
          Price: <span className="text-white font-mono font-semibold">
            {data.current_price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        </span>
        <span className="text-gray-500 text-xs">{ts}</span>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/components/Header.tsx
git commit -m "feat: add Header component"
```

---

## Task 6: TrendLevelsTable component

**Files:**
- Create: `dashboard/components/TrendLevelsTable.tsx`

- [ ] **Step 1: Write component**

```tsx
// dashboard/components/TrendLevelsTable.tsx
'use client'

import { useState } from 'react'
import { TrendLevel } from '@/lib/types'

interface Props {
  levels: TrendLevel[]
}

function fmt(price: number | null) {
  if (price === null) return '—'
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtTime(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

export default function TrendLevelsTable({ levels }: Props) {
  const [sortKey, setSortKey] = useState<'level' | 'direction'>('level')
  const [sortAsc, setSortAsc] = useState(true)

  const sorted = [...levels].sort((a, b) => {
    const va = a[sortKey]
    const vb = b[sortKey]
    if (va === vb) return 0
    const cmp = va < vb ? -1 : 1
    return sortAsc ? cmp : -cmp
  })

  const toggle = (key: typeof sortKey) => {
    if (sortKey === key) setSortAsc(v => !v)
    else { setSortKey(key); setSortAsc(true) }
  }

  const arrow = (key: typeof sortKey) =>
    sortKey !== key ? '' : sortAsc ? ' ↑' : ' ↓'

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-800">
      <table className="w-full text-sm">
        <thead className="bg-gray-900 text-gray-400 uppercase text-xs">
          <tr>
            <th className="px-4 py-3 text-left cursor-pointer hover:text-white select-none" onClick={() => toggle('level')}>
              Lvl{arrow('level')}
            </th>
            <th className="px-4 py-3 text-left cursor-pointer hover:text-white select-none" onClick={() => toggle('direction')}>
              Direction{arrow('direction')}
            </th>
            <th className="px-4 py-3 text-left">Break of Structure</th>
            <th className="px-4 py-3 text-left">BoS Since</th>
            <th className="px-4 py-3 text-left">Last High</th>
            <th className="px-4 py-3 text-left">Last Low</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-800">
          {sorted.map(row => (
            <tr key={row.level} className="hover:bg-gray-900 transition-colors">
              <td className="px-4 py-3 font-mono font-semibold">L{row.level}</td>
              <td className="px-4 py-3">
                {row.direction === 'ASC' && <span className="text-green-400 font-semibold">▲ ASC</span>}
                {row.direction === 'DESC' && <span className="text-red-400 font-semibold">▼ DESC</span>}
                {row.direction === 'NONE' && <span className="text-gray-500">— NONE</span>}
              </td>
              <td className="px-4 py-3 font-mono">{fmt(row.bos)}</td>
              <td className="px-4 py-3 text-gray-400 text-xs">{fmtTime(row.bos_since)}</td>
              <td className="px-4 py-3 font-mono text-green-300">
                {row.last_high ? `${fmt(row.last_high.price)} @ ${fmtTime(row.last_high.time)}` : '—'}
              </td>
              <td className="px-4 py-3 font-mono text-red-300">
                {row.last_low ? `${fmt(row.last_low.price)} @ ${fmtTime(row.last_low.time)}` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/components/TrendLevelsTable.tsx
git commit -m "feat: add TrendLevelsTable component"
```

---

## Task 7: AllPointsTable component

**Files:**
- Create: `dashboard/components/AllPointsTable.tsx`

- [ ] **Step 1: Write component**

```tsx
// dashboard/components/AllPointsTable.tsx
'use client'

import { useState, useMemo } from 'react'
import { SwingPoint } from '@/lib/types'

interface Props {
  points: SwingPoint[]
}

type SortKey = 'time' | 'level' | 'type' | 'price'

function fmt(price: number) {
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function PointRow({ point }: { point: SwingPoint }) {
  return (
    <tr className="hover:bg-gray-900 transition-colors">
      <td className="px-3 py-2 text-gray-400 text-xs whitespace-nowrap">{fmtTime(point.time)}</td>
      <td className="px-3 py-2 font-mono font-semibold text-xs">L{point.level}</td>
      <td className="px-3 py-2 text-xs">
        {point.type === 'high'
          ? <span className="text-green-400">▲ High</span>
          : <span className="text-red-400">▼ Low</span>}
      </td>
      <td className="px-3 py-2 font-mono text-xs">{fmt(point.price)}</td>
    </tr>
  )
}

const COLS: { key: SortKey; label: string }[] = [
  { key: 'time',  label: 'Time'  },
  { key: 'level', label: 'Lvl'   },
  { key: 'type',  label: 'Type'  },
  { key: 'price', label: 'Price' },
]

export default function AllPointsTable({ points }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('time')
  const [sortAsc, setSortAsc] = useState(false)

  const sorted = useMemo(() => {
    return [...points].sort((a, b) => {
      let va: string | number = a[sortKey]
      let vb: string | number = b[sortKey]
      if (va === vb) return 0
      const cmp = va < vb ? -1 : 1
      return sortAsc ? cmp : -cmp
    }).slice(0, 50)
  }, [points, sortKey, sortAsc])

  const toggle = (key: SortKey) => {
    if (sortKey === key) setSortAsc(v => !v)
    else { setSortKey(key); setSortAsc(false) }
  }

  const arrow = (key: SortKey) => sortKey !== key ? '' : sortAsc ? ' ↑' : ' ↓'

  const mid = Math.ceil(sorted.length / 2)
  const left  = sorted.slice(0, mid)
  const right = sorted.slice(mid)

  const HeaderRow = () => (
    <tr className="bg-gray-900 text-gray-400 uppercase text-xs">
      {COLS.map(c => (
        <th key={c.key} className="px-3 py-2 text-left cursor-pointer hover:text-white select-none whitespace-nowrap"
            onClick={() => toggle(c.key)}>
          {c.label}{arrow(c.key)}
        </th>
      ))}
    </tr>
  )

  return (
    <div className="grid grid-cols-2 gap-4">
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead><HeaderRow /></thead>
          <tbody className="divide-y divide-gray-800">
            {left.map((p, i) => <PointRow key={i} point={p} />)}
          </tbody>
        </table>
      </div>
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead><HeaderRow /></thead>
          <tbody className="divide-y divide-gray-800">
            {right.map((p, i) => <PointRow key={i} point={p} />)}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/components/AllPointsTable.tsx
git commit -m "feat: add AllPointsTable component with 2-column layout"
```

---

## Task 8: SwingPointsChart component

**Files:**
- Create: `dashboard/components/SwingPointsChart.tsx`

- [ ] **Step 1: Write component**

```tsx
// dashboard/components/SwingPointsChart.tsx
'use client'

import { useMemo } from 'react'
import {
  Chart as ChartJS,
  CategoryScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import { SwingPoint, Kline } from '@/lib/types'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler)

interface Props {
  klines: Kline[]
  points: SwingPoint[]
}

function fmt(price: number) {
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function SwingPointsChart({ klines, points }: Props) {
  const { labels, closes, pointData } = useMemo(() => {
    // Use klines if available, else derive x-axis from swing points
    if (klines.length > 0) {
      const labels = klines.map(k =>
        new Date(k.time * 1000).toLocaleString('en-US', {
          month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
        })
      )
      const closes = klines.map(k => k.close)

      // Map each swing point to its nearest kline index
      const pointData = klines.map((k, i) => {
        const kTime = k.time * 1000
        const match = points.find(p => Math.abs(new Date(p.time).getTime() - kTime) < 15 * 60 * 1000)
        return match ? match.price : null
      })

      return { labels, closes, pointData }
    }

    // Fallback: use swing points as the x-axis
    const sorted = [...points].sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime())
    const labels = sorted.map(p =>
      new Date(p.time).toLocaleString('en-US', {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
      })
    )
    const closes = sorted.map(p => p.price)
    const pointData = sorted.map(p => p.price)
    return { labels, closes, pointData }
  }, [klines, points])

  const data = {
    labels,
    datasets: [
      {
        label: 'Close Price',
        data: closes,
        borderColor: 'rgb(99, 102, 241)',
        backgroundColor: 'rgba(99, 102, 241, 0.05)',
        borderWidth: 1.5,
        pointRadius: 0,
        fill: true,
        tension: 0.1,
      },
      {
        label: 'Swing Points',
        data: pointData,
        borderColor: 'transparent',
        backgroundColor: (ctx: any) => {
          if (ctx.raw === null) return 'transparent'
          const idx = ctx.dataIndex
          const pt = points.find(p => {
            const t = new Date(p.time).getTime()
            const kt = klines.length > 0 ? klines[idx]?.time * 1000 : t
            return Math.abs(t - (kt ?? t)) < 15 * 60 * 1000
          })
          return pt?.type === 'high' ? 'rgba(74, 222, 128, 0.9)' : 'rgba(248, 113, 113, 0.9)'
        },
        pointRadius: (ctx: any) => ctx.raw === null ? 0 : 6,
        pointHoverRadius: 8,
        showLine: false,
      },
    ],
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        labels: { color: '#9ca3af', boxWidth: 12, font: { size: 11 } },
      },
      tooltip: {
        backgroundColor: '#1f2937',
        titleColor: '#f9fafb',
        bodyColor: '#d1d5db',
        callbacks: {
          label: (ctx: any) => ` ${fmt(ctx.raw)}`,
        },
      },
    },
    scales: {
      x: {
        ticks: { color: '#6b7280', maxTicksLimit: 10, font: { size: 10 } },
        grid: { color: '#1f2937' },
      },
      y: {
        ticks: {
          color: '#6b7280',
          font: { size: 10 },
          callback: (v: any) => fmt(v),
        },
        grid: { color: '#1f2937' },
      },
    },
  }

  if (labels.length === 0) {
    return (
      <div className="h-72 flex items-center justify-center text-gray-600 border border-gray-800 rounded-lg">
        No chart data available
      </div>
    )
  }

  return (
    <div className="h-72 rounded-lg border border-gray-800 p-4 bg-gray-900">
      <Line data={data} options={options as any} />
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/components/SwingPointsChart.tsx
git commit -m "feat: add SwingPointsChart component"
```

---

## Task 9: SignalsPanel component

**Files:**
- Create: `dashboard/components/SignalsPanel.tsx`

- [ ] **Step 1: Write component**

```tsx
// dashboard/components/SignalsPanel.tsx
'use client'

import { Signal } from '@/lib/types'

interface Props {
  signals: Signal[]
}

function fmt(price: number) {
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function SignalsPanel({ signals }: Props) {
  if (signals.length === 0) {
    return (
      <div className="rounded-lg border border-gray-800 p-4 text-gray-600 text-sm">
        No active signals
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-800 divide-y divide-gray-800">
      {signals.map((s, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3 text-sm">
          <span className="font-mono font-semibold text-gray-400">L{s.level}</span>
          <span className={`font-bold ${s.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
            {s.side}
          </span>
          <span className="text-gray-500">{s.signal_type.replace(/_/g, ' ')}</span>
          <span className="ml-auto text-xs text-gray-400">
            target <span className="font-mono text-white">{fmt(s.target)}</span>
            {s.stop && <> · stop <span className="font-mono text-white">{fmt(s.stop)}</span></>}
          </span>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/components/SignalsPanel.tsx
git commit -m "feat: add SignalsPanel component"
```

---

## Task 10: Main page

**Files:**
- Modify: `dashboard/app/page.tsx`

- [ ] **Step 1: Write main page**

```tsx
// dashboard/app/page.tsx
'use client'

import { useEffect, useState } from 'react'
import { BotResults } from '@/lib/types'
import Header from '@/components/Header'
import SwingPointsChart from '@/components/SwingPointsChart'
import TrendLevelsTable from '@/components/TrendLevelsTable'
import AllPointsTable from '@/components/AllPointsTable'
import SignalsPanel from '@/components/SignalsPanel'

export default function Page() {
  const [data, setData] = useState<BotResults | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/results.json')
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(setData)
      .catch(e => setError(e.message))
  }, [])

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen text-red-400">
        Failed to load results.json: {error}
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center min-h-screen text-gray-500">
        Loading…
      </div>
    )
  }

  return (
    <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      <Header data={data} />

      <section>
        <h2 className="text-sm font-semibold uppercase text-gray-500 mb-3 tracking-wider">Swing Points</h2>
        <SwingPointsChart klines={data.klines} points={data.all_points} />
      </section>

      <section>
        <h2 className="text-sm font-semibold uppercase text-gray-500 mb-3 tracking-wider">Trend Levels</h2>
        <TrendLevelsTable levels={data.trend_levels} />
      </section>

      <section>
        <h2 className="text-sm font-semibold uppercase text-gray-500 mb-3 tracking-wider">
          All Points <span className="text-gray-700 font-normal normal-case">(newest first, up to 50)</span>
        </h2>
        <AllPointsTable points={data.all_points} />
      </section>

      <section>
        <h2 className="text-sm font-semibold uppercase text-gray-500 mb-3 tracking-wider">Signals</h2>
        <SignalsPanel signals={data.signals} />
      </section>
    </main>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/app/page.tsx
git commit -m "feat: wire up main dashboard page"
```

---

## Task 11: Python exporter

**Files:**
- Create: `bot/exporter.py`
- Modify: `main.py`

- [ ] **Step 1: Write exporter**

```python
# bot/exporter.py
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.trend import Trend

logger = logging.getLogger(__name__)

_OUTPUT_PATH = Path('dashboard/public/results.json')
_MAX_KLINES = 200


def export(
    symbol: str,
    timeframe: str,
    mode: str,
    current_price: float,
    trend: Optional[Trend],
    klines: list,
    recommendations: list,
) -> None:
    if trend is None:
        return

    trend_levels = []
    current = trend
    while current is not None:
        last_high = current.getLastHigh()
        last_low = current.getLastLow()
        trend_levels.append({
            'level': current.getLevel(),
            'direction': (
                'ASC' if current.isAscending()
                else 'DESC' if current.isDescending()
                else 'NONE'
            ),
            'bos': current.getBreakOfStructure(),
            'bos_since': _ts(current.getBreakOfStructureTime()),
            'last_high': {'price': last_high.getHighValue(), 'time': _ts(last_high.getTime())} if last_high else None,
            'last_low':  {'price': last_low.getLowValue(),  'time': _ts(last_low.getTime())}  if last_low  else None,
        })
        current = current.getBiggerTrend() if current.hasBiggerTrend() else None

    seen: set = set()
    all_points = []
    current = trend
    while current is not None:
        level = current.getLevel()
        for pt in current.getHighPoints():
            key = (pt.getTime(), level, True)
            if key not in seen:
                seen.add(key)
                all_points.append({'time': _ts(pt.getTime()), 'level': level, 'type': 'high', 'price': pt.getHighValue()})
        for pt in current.getLowPoints():
            key = (pt.getTime(), level, False)
            if key not in seen:
                seen.add(key)
                all_points.append({'time': _ts(pt.getTime()), 'level': level, 'type': 'low', 'price': pt.getLowValue()})
        current = current.getBiggerTrend() if current.hasBiggerTrend() else None

    all_points.sort(key=lambda p: p['time'], reverse=True)

    kline_data = [
        {'time': int(k[0]) // 1000, 'open': float(k[1]), 'high': float(k[2]), 'low': float(k[3]), 'close': float(k[4])}
        for k in klines[-_MAX_KLINES:]
    ]

    signals = [
        {
            'level': rec.getLevel(),
            'side': rec.getSide(),
            'signal_type': rec.getType(),
            'target': rec.getTarget(),
            'stop': rec.getStop(),
        }
        for rec in recommendations
    ]

    result = {
        'symbol': symbol,
        'timeframe': timeframe,
        'mode': mode,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'current_price': current_price,
        'trend_levels': trend_levels,
        'all_points': all_points,
        'klines': kline_data,
        'signals': signals,
    }

    try:
        _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OUTPUT_PATH.write_text(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Failed to write results.json: {e}")


def _ts(unix_seconds: Optional[int]) -> Optional[str]:
    if unix_seconds is None:
        return None
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat()
```

- [ ] **Step 2: Check what methods Recommendation exposes**

Read `bot/recommendation.py` to verify method names `getLevel()`, `getSide()`, `getType()`, `getTarget()`, `getStop()` exist. Adjust exporter if names differ.

- [ ] **Step 3: Wire exporter into main.py**

Add import at top of `main.py`:
```python
from bot import exporter
```

After the initial `display.show(...)` call, add:
```python
exporter.export(
    settings.symbol, settings.timeframe, settings.trading_mode,
    analyzer.get_current_price(), analyzer.get_trend(),
    klines, analyzer.get_recommendations(),
)
```

After the `display.show(...)` call inside `on_candle_close`, add:
```python
exporter.export(
    settings.symbol, settings.timeframe, settings.trading_mode,
    analyzer.get_current_price(), analyzer.get_trend(),
    feed._klines_cache(settings.symbol, settings.timeframe),
    recs,
)
```

**Note:** `feed._klines_cache` does not exist yet. Since the `DataFeed` doesn't expose a kline list getter, pass `analyzer._klines` directly (it's internal but acceptable for now). Alternative: add a `get_klines()` method to `Analyzer`. Add this to `Analyzer`:

```python
def get_klines(self) -> list:
    return list(self._klines)
```

Then use `analyzer.get_klines()` in both exporter calls.

- [ ] **Step 4: Commit**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add bot/exporter.py bot/analyzer.py main.py
git commit -m "feat: add Python exporter for results.json"
```

---

## Task 12: README and final verification

**Files:**
- Create: `dashboard/README.md`

- [ ] **Step 1: Write dashboard README**

```markdown
# Bot Dashboard

A Next.js web UI for viewing Binance Futures bot results.

## Start

```bash
cd dashboard
npm install
npm run dev
```

Open http://localhost:3000

## How it works

The Python bot writes `dashboard/public/results.json` after each candle close.
The dashboard fetches that file on page load and renders charts and tables.
Refresh the page to see latest data.

## JSON Schema

`public/results.json` must match this shape:

```typescript
{
  symbol: string           // e.g. "BTCUSDT"
  timeframe: string        // e.g. "15m"
  mode: "testnet" | "live"
  generated_at: string     // ISO 8601 UTC
  current_price: number
  trend_levels: Array<{
    level: number          // 1, 2, 3
    direction: "ASC" | "DESC" | "NONE"
    bos: number | null     // Break of Structure price
    bos_since: string | null
    last_high: { price: number; time: string } | null
    last_low:  { price: number; time: string } | null
  }>
  all_points: Array<{
    time: string           // ISO 8601 UTC
    level: number
    type: "high" | "low"
    price: number
  }>
  klines: Array<{
    time: number           // Unix timestamp (seconds)
    open: number
    high: number
    low: number
    close: number
  }>
  signals: Array<{
    level: number
    side: "BUY" | "SELL"
    signal_type: string
    target: number
    stop: number | null
  }>
}
```

## Python integration

The bot calls `bot/exporter.export(...)` after each candle close.
It writes the latest 200 klines and all swing points to this file automatically.
```

- [ ] **Step 2: Verify the app builds and runs**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npm run build 2>&1 | tail -20
```

Expected: `✓ Compiled successfully` or similar — no TypeScript errors.

- [ ] **Step 3: Commit README**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
git add dashboard/README.md
git commit -m "docs: add dashboard README with JSON schema"
```

---

## Self-Review

**Spec coverage:**
- ✅ Next.js 14 App Router + Tailwind + Chart.js — Task 1
- ✅ TypeScript types — Task 2
- ✅ results.json sample data — Task 3
- ✅ Dark mode (className="dark" on html, dark bg/text throughout) — Task 4
- ✅ Header with symbol, timeframe, mode, timestamp — Task 5
- ✅ Trend levels sortable table — Task 6
- ✅ All points sortable table, 2-column, 50 rows — Task 7
- ✅ Swing points chart — Task 8
- ✅ Signals panel — Task 9
- ✅ Main page wiring — Task 10
- ✅ Python bot writes results.json — Task 11
- ✅ dashboard/README.md — Task 12
- ✅ .gitignore updated — Task 1 Step 4
- ✅ Responsive (max-w-7xl, overflow-x-auto, grid-cols-2) — throughout

**Placeholder check:** None found.

**Type consistency:**
- `BotResults`, `TrendLevel`, `SwingPoint`, `Signal`, `Kline` defined in Task 2, used consistently across Tasks 5–11.
- `getLevel()`, `getSide()`, `getType()`, `getTarget()`, `getStop()` on Recommendation — verified against `bot/recommendation.py` required in Task 11 Step 2.
