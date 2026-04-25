'use client'

import { useState } from 'react'
import { TrendLevel } from '@/lib/types'

interface Props {
  levels: TrendLevel[]
}

// Format a price for display, or show a dash if the value is missing
function fmt(price: number | null) {
  if (price === null) return '—'
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// Format an ISO timestamp to a short readable form (e.g. "Apr 21, 14:30")
function fmtTime(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

// Summary table of active trend levels. Each row shows the current trend direction,
// the Break of Structure price (the price level the trend must breach to confirm the move),
// and the most recent confirmed high and low at that level.
export default function TrendLevelsTable({ levels }: Props) {
  // Only 'level' and 'direction' are sortable; other columns are reference data
  const [sortKey, setSortKey] = useState<'level' | 'direction'>('level')
  const [sortAsc, setSortAsc] = useState(true)

  const sorted = [...levels].sort((a, b) => {
    const va = a[sortKey]
    const vb = b[sortKey]
    if (va === vb) return 0
    const cmp = va < vb ? -1 : 1
    return sortAsc ? cmp : -cmp
  })

  // Toggle: clicking an already-active column reverses direction;
  // clicking a new column resets to ascending
  const toggle = (key: typeof sortKey) => {
    if (sortKey === key) setSortAsc(v => !v)
    else { setSortKey(key); setSortAsc(true) }
  }

  // Show ↑ or ↓ next to the active sort column header
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
            {/* BoS = the price the market must close beyond to confirm this trend direction */}
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
                {row.direction === 'ASC'  && <span className="text-green-400 font-semibold">▲ ASC</span>}
                {row.direction === 'DESC' && <span className="text-red-400 font-semibold">▼ DESC</span>}
                {row.direction === 'NONE' && <span className="text-gray-500">— NONE</span>}
              </td>
              <td className="px-4 py-3 font-mono">{fmt(row.bos)}</td>
              <td className="px-4 py-3 text-gray-400 text-xs">{fmtTime(row.bos_since)}</td>
              {/* Last high/low are the most recent confirmed swing points at this level */}
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
