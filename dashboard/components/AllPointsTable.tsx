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

// A single row shared between the left and right column tables
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

const COLS: { key: SortKey; label: string; title: string }[] = [
  { key: 'time',  label: 'Time',  title: 'Candle open time when this swing point was confirmed (requires SWING_NEIGHBOURS candles on each side to close before detection)' },
  { key: 'level', label: 'Lvl',   title: 'Trend level this swing point belongs to — L1 is the finest/fastest, higher levels require larger price moves to form' },
  { key: 'type',  label: 'Type',  title: 'High = swing high (local price peak confirmed by lower highs on both sides), Low = swing low (local price trough)' },
  { key: 'price', label: 'Price', title: 'Price value of the swing point' },
]

// Full list of ACTIVE swing points for the active level selection, displayed in two
// side-by-side tables. Inactive points (wiped by a BoS) are excluded — they are still
// visible as dimmed dots on the chart for historical context.
export default function AllPointsTable({ points }: Props) {
  points = points.filter(p => p.active)
  // Default: newest points first (sortAsc=false on 'time')
  const [sortKey, setSortKey] = useState<SortKey>('time')
  const [sortAsc, setSortAsc] = useState(false)

  const sorted = useMemo(() => {
    return [...points].sort((a, b) => {
      const va: string | number = a[sortKey]
      const vb: string | number = b[sortKey]
      if (va === vb) return 0
      const cmp = va < vb ? -1 : 1
      return sortAsc ? cmp : -cmp
    })
  }, [points, sortKey, sortAsc])

  // Toggle: same column → flip direction; new column → default to descending (newest first)
  const toggle = (key: SortKey) => {
    if (sortKey === key) setSortAsc(v => !v)
    else { setSortKey(key); setSortAsc(false) }
  }

  const arrow = (key: SortKey) => sortKey !== key ? '' : sortAsc ? ' ↑' : ' ↓'

  // Split sorted rows down the middle so the left column shows the first half
  // (e.g. newest points) and the right column shows the second half (older points)
  const mid   = Math.ceil(sorted.length / 2)
  const left  = sorted.slice(0, mid)
  const right = sorted.slice(mid)

  // Shared header row rendered identically in both tables
  const HeaderRow = () => (
    <tr className="bg-gray-900 text-gray-400 uppercase text-xs">
      {COLS.map(c => (
        <th key={c.key} className="px-3 py-2 text-left cursor-pointer hover:text-white select-none whitespace-nowrap"
            onClick={() => toggle(c.key)}
            title={c.title}>
          {c.label}{arrow(c.key)}
        </th>
      ))}
    </tr>
  )

  return (
    <div className="grid grid-cols-2 gap-4">
      {/* Left column — first half of the sorted points */}
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead><HeaderRow /></thead>
          <tbody className="divide-y divide-gray-800">
            {left.map((p, i) => <PointRow key={i} point={p} />)}
          </tbody>
        </table>
      </div>
      {/* Right column — second half of the sorted points */}
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
