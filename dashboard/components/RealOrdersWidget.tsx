'use client'

import { useEffect, useState } from 'react'
import { fmtDuration } from '@/lib/datetime'
import { formatPrice } from '@/lib/formatPrice'

type RealOrderRow = {
  symbol: string
  preset_name: string
  side: string
  entry_price: number
  close_price: number | null
  quantity: number | null
  leverage: number | null
  pnl_usdt: number | null
  result: string | null
  open_time: string | null
  close_time: string | null
  duration_s: number | null
  is_open: boolean
  unrealized_pnl_usdt?: number | null
}

type Payload = {
  mode: string
  total: number
  open_count: number
  closed_count: number
  wins: number
  net_pnl_usdt: number
  open_updated_at: string | null
  orders: RealOrderRow[]
}

/**
 * Real orders across every symbol, newest first, open ones at the top.
 *
 * Deliberately cross-symbol: the page's main table is scoped to the selected symbol,
 * and real orders are few (79 in a month) while being the only ones that move money —
 * so the useful at-a-glance view is all of them at once, not a filtered copy of what
 * the table below already shows.
 *
 * `mode` selects the instance, matching the rest of the page.
 */
export default function RealOrdersWidget({
  mode,
  onSelectSymbol,
  selectedSymbol,
}: {
  mode: string
  onSelectSymbol?: (symbol: string) => void
  selectedSymbol?: string
}) {
  const [data, setData] = useState<Payload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    let alive = true
    fetch(`/api/trades/real?mode=${mode}&limit=100`)
      .then(r => (r.ok ? r.json() : Promise.reject(r.statusText)))
      .then((d: Payload) => { if (alive) setData(d) })
      .catch(e => { if (alive) setError(String(e)) })
    return () => { alive = false }
  }, [mode])

  if (error) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3 text-xs text-red-400">
        Real orders unavailable: {error}
      </div>
    )
  }
  if (!data) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3 text-xs text-gray-500">
        Loading real orders…
      </div>
    )
  }
  if (data.total === 0) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3 text-xs text-gray-500">
        No real orders in <span className="text-gray-400">{data.mode}</span> yet.
      </div>
    )
  }

  const rows = expanded ? data.orders : data.orders.slice(0, 8)
  const winPct = data.closed_count > 0 ? (data.wins / data.closed_count) * 100 : null
  const pnlClass = data.net_pnl_usdt > 0
    ? 'text-emerald-400' : data.net_pnl_usdt < 0 ? 'text-red-400' : 'text-gray-400'

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mb-2">
        <h2 className="text-sm font-semibold text-white">
          Real orders
          <span className="ml-2 text-xs font-normal text-gray-500">all symbols</span>
        </h2>
        <span className="text-xs text-gray-500">
          {data.open_count > 0 && (
            <span className="text-emerald-400 font-semibold">{data.open_count} open</span>
          )}
          {data.open_count > 0 && ' · '}
          {data.closed_count} closed
          {winPct !== null && ` · ${winPct.toFixed(0)}% win`}
        </span>
        <span className={`text-xs font-semibold ${pnlClass}`}>
          net {data.net_pnl_usdt >= 0 ? '+' : ''}{data.net_pnl_usdt.toFixed(2)} USDT
        </span>
        {data.orders.length > 8 && (
          <button
            onClick={() => setExpanded(v => !v)}
            className="ml-auto text-xs text-gray-500 hover:text-gray-300"
          >
            {expanded ? '▲ Show less' : `▼ Show all ${data.orders.length}`}
          </button>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs text-left">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800">
              <th className="py-1 pr-3">Symbol</th>
              <th className="py-1 pr-3">Preset</th>
              <th className="py-1 pr-3">Side</th>
              <th className="py-1 pr-3 text-right">Entry</th>
              <th className="py-1 pr-3 text-right">Close</th>
              <th className="py-1 pr-3 text-right" title="How long the order was open. Counts up while it is still running.">Duration</th>
              <th className="py-1 pr-3 text-right">PnL USDT</th>
              <th className="py-1 pr-3">Result</th>
              <th className="py-1 text-right">Opened</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o, i) => {
              const pnl = o.is_open ? o.unrealized_pnl_usdt : o.pnl_usdt
              const cls = pnl == null ? 'text-gray-600'
                : pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-gray-400'
              const isSel = o.symbol === selectedSymbol
              return (
                <tr
                  key={`${o.symbol}-${o.open_time}-${i}`}
                  onClick={onSelectSymbol ? () => onSelectSymbol(o.symbol) : undefined}
                  className={`border-b border-gray-800/60 ${
                    o.is_open ? 'bg-emerald-950/25' : ''
                  } ${isSel ? 'bg-blue-950/40' : ''} ${
                    onSelectSymbol ? 'cursor-pointer hover:bg-gray-800/40' : ''
                  }`}
                >
                  <td className="py-1 pr-3 font-mono text-white">
                    {o.symbol}
                    {o.is_open && (
                      <span
                        className="ml-1 text-[9px] px-1 rounded bg-emerald-800/60 text-emerald-300 font-semibold"
                        title="Still open"
                      >
                        NOW
                      </span>
                    )}
                  </td>
                  <td className="py-1 pr-3 font-mono text-gray-300">{o.preset_name || '—'}</td>
                  <td className={`py-1 pr-3 ${o.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
                    {o.side || '—'}
                  </td>
                  <td className="py-1 pr-3 text-right font-mono text-gray-300">
                    {formatPrice(o.entry_price)}
                  </td>
                  <td className="py-1 pr-3 text-right font-mono text-gray-400">
                    {o.close_price != null ? formatPrice(o.close_price) : '—'}
                  </td>
                  <td className="py-1 pr-3 text-right font-mono text-gray-300">
                    {fmtDuration(o.duration_s)}
                  </td>
                  <td className={`py-1 pr-3 text-right font-semibold ${cls}`}>
                    {pnl != null ? `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}` : '—'}
                  </td>
                  <td className="py-1 pr-3 capitalize text-gray-400">
                    {o.result ? o.result.replace(/_/g, ' ') : 'open'}
                  </td>
                  <td className="py-1 text-right text-gray-500">
                    {o.open_time ? new Date(o.open_time).toLocaleString() : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {data.open_count > 0 && data.open_updated_at && (
        <p className="text-[10px] text-gray-600 mt-1">
          Open positions and their result as of{' '}
          {new Date(data.open_updated_at).toLocaleTimeString()} — written once per candle.
        </p>
      )}
    </div>
  )
}
