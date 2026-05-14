'use client'

import { useState, useEffect } from 'react'
import { useSymbolContext } from '@/lib/SymbolContext'
import type { TradesData, RealOrder } from '@/lib/types'
import CollapsibleSection from '@/components/CollapsibleSection'
import TradesChart from '@/components/TradesChart'

function winRate(trades: RealOrder[]): string {
  if (trades.length === 0) return '—'
  const wins = trades.filter(t => t.result === 'win' || t.result === 'partial' || t.result === 'trail').length
  return ((wins / trades.length) * 100).toFixed(1) + '%'
}

function totalPnl(trades: RealOrder[]): number {
  return trades.reduce((s, t) => s + (t.pnl_usdt ?? 0), 0)
}

interface PresetRow {
  name: string
  type: 'Real' | 'Virtual'
  isBest: boolean
  tradeCount: number
  totalWinningUsdt: number
  winRateDisplay: string
  totalPnlDisplay: string
  totalPnlRaw: number
}

function buildPresetRows(data: TradesData): PresetRow[] {
  const rows: PresetRow[] = []

  if (data.best_preset) {
    const realTrades = data.real_orders.filter(o => o.preset_name === data.best_preset)
    const pnl = totalPnl(realTrades)
    rows.push({
      name: data.best_preset,
      type: 'Real',
      isBest: true,
      tradeCount: realTrades.length,
      totalWinningUsdt: realTrades.filter(t => t.pnl_usdt > 0).reduce((s, t) => s + t.pnl_usdt, 0),
      winRateDisplay: winRate(realTrades),
      totalPnlDisplay: realTrades.length > 0 ? (pnl >= 0 ? '+' : '') + pnl.toFixed(2) + ' USDT' : '—',
      totalPnlRaw: pnl,
    })
  }

  for (const [name, stats] of Object.entries(data.virtual_summary)) {
    if (name === data.best_preset) continue
    rows.push({
      name,
      type: 'Virtual',
      isBest: false,
      tradeCount: stats.trade_count,
      totalWinningUsdt: stats.total_winning_usdt,
      winRateDisplay: '—',
      totalPnlDisplay: '—',
      totalPnlRaw: stats.total_winning_usdt,
    })
  }

  rows.sort((a, b) => {
    if (a.isBest) return -1
    if (b.isBest) return 1
    return b.totalPnlRaw - a.totalPnlRaw
  })

  return rows
}

function resultColor(result: string): string {
  if (result === 'win' || result === 'partial' || result === 'trail') return 'text-green-400'
  if (result === 'loss') return 'text-red-400'
  return 'text-gray-400'
}

export default function TradesPage() {
  const { symbol } = useSymbolContext()
  const [data, setData] = useState<TradesData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [klines, setKlines] = useState<Array<{ time: number; close: number }>>([])
  const [hideVirtualOnly, setHideVirtualOnly] = useState(true)

  useEffect(() => {
    if (!symbol) return
    setData(null)
    setError(null)
    fetch(`/api/trades?symbol=${symbol}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(setData)
      .catch(e => setError(String(e)))
  }, [symbol])

  useEffect(() => {
    if (!symbol) return
    fetch(`/results_${symbol}.json?t=${Date.now()}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.klines) setKlines(d.klines)
      })
      .catch(() => {})
  }, [symbol])

  if (error) return <div className="pt-16 p-4 text-red-400">{error}</div>
  if (!data) return <div className="pt-16 p-4 text-gray-400">Loading…</div>

  const allPresetRows = buildPresetRows(data)
  const presetRows = hideVirtualOnly
    ? allPresetRows.filter(r => r.type === 'Real' || r.tradeCount > 0)
    : allPresetRows
  const realOrders = [...data.real_orders].reverse()

  return (
    <div className="pt-14 p-4 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold text-white">{symbol} — Trades</h1>
        <span className="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400">{data.mode}</span>
        {data.best_preset && (
          <span className="text-xs px-2 py-0.5 rounded bg-indigo-900 text-indigo-300">
            Best: {data.best_preset}
          </span>
        )}
      </div>

      <CollapsibleSection
        title={`Preset Efficiency (${presetRows.length} presets)`}
        storageKey="trades-preset-efficiency"
        defaultOpen
        headerExtra={
          <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={hideVirtualOnly}
              onChange={e => setHideVirtualOnly(e.target.checked)}
              className="accent-indigo-500"
            />
            Hide virtual-only
          </label>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                <th className="py-2 pr-4">Preset</th>
                <th className="py-2 pr-4">Type</th>
                <th className="py-2 pr-4 text-right">Trades</th>
                <th className="py-2 pr-4 text-right">Win Rate</th>
                <th className="py-2 pr-4 text-right">Total PnL</th>
                <th className="py-2 text-right">Winning USDT</th>
              </tr>
            </thead>
            <tbody>
              {presetRows.map(row => (
                <tr
                  key={row.name}
                  className={`border-b border-gray-800 ${row.isBest ? 'bg-indigo-950' : ''}`}
                >
                  <td className="py-1.5 pr-4 font-mono text-xs text-white">
                    {row.name}
                    {row.isBest && <span className="ml-2 text-[10px] text-indigo-400">BEST</span>}
                  </td>
                  <td className={`py-1.5 pr-4 text-xs ${row.type === 'Real' ? 'text-green-400' : 'text-gray-400'}`}>
                    {row.type}
                  </td>
                  <td className="py-1.5 pr-4 text-right text-gray-300">{row.tradeCount || '—'}</td>
                  <td className="py-1.5 pr-4 text-right text-gray-300">{row.winRateDisplay}</td>
                  <td className={`py-1.5 pr-4 text-right ${row.totalPnlRaw >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {row.totalPnlDisplay}
                  </td>
                  <td className={`py-1.5 text-right ${row.totalWinningUsdt > 0 ? 'text-green-400' : 'text-gray-500'}`}>
                    {row.totalWinningUsdt > 0 ? '+' + row.totalWinningUsdt.toFixed(2) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CollapsibleSection>

      {klines.length > 0 && (
        <CollapsibleSection title="Price Chart + Trade Markers" storageKey="trades-chart" defaultOpen>
          <TradesChart klines={klines} realOrders={data.real_orders} />
        </CollapsibleSection>
      )}

      <CollapsibleSection
        title={`Real Orders (${data.real_orders.length})`}
        storageKey="trades-real-orders"
        defaultOpen={data.real_orders.length > 0}
      >
        {realOrders.length === 0 ? (
          <p className="text-gray-500 text-sm py-4">No real orders recorded yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead>
                <tr className="text-gray-400 border-b border-gray-700">
                  <th className="py-2 pr-3">Preset</th>
                  <th className="py-2 pr-3">Side</th>
                  <th className="py-2 pr-3 text-right">Entry</th>
                  <th className="py-2 pr-3 text-right">Close</th>
                  <th className="py-2 pr-3 text-right">PnL USDT</th>
                  <th className="py-2 pr-3">Result</th>
                  <th className="py-2 text-right">Closed At</th>
                </tr>
              </thead>
              <tbody>
                {realOrders.map((order, i) => (
                  <tr key={i} className="border-b border-gray-800">
                    <td className="py-1.5 pr-3 font-mono text-xs text-white">{order.preset_name}</td>
                    <td className={`py-1.5 pr-3 ${order.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
                      {order.side}
                    </td>
                    <td className="py-1.5 pr-3 text-right text-gray-300">{order.entry_price.toFixed(2)}</td>
                    <td className="py-1.5 pr-3 text-right text-gray-300">{order.close_price.toFixed(2)}</td>
                    <td className={`py-1.5 pr-3 text-right font-medium ${order.pnl_usdt >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {(order.pnl_usdt >= 0 ? '+' : '') + order.pnl_usdt.toFixed(2)}
                    </td>
                    <td className={`py-1.5 pr-3 capitalize ${resultColor(order.result)}`}>
                      {order.result}
                    </td>
                    <td className="py-1.5 text-right text-gray-500 text-xs">
                      {new Date(order.close_time).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CollapsibleSection>
    </div>
  )
}
