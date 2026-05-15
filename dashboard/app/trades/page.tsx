'use client'

import { useState, useEffect, useMemo } from 'react'
import { useSymbolContext } from '@/lib/SymbolContext'
import type { TradesData, RealOrder, VirtualOrder } from '@/lib/types'
import CollapsibleSection from '@/components/CollapsibleSection'
import TradesChart from '@/components/TradesChart'

// ── helpers ────────────────────────────────────────────────────────────────

function resultColor(result: string): string {
  if (result === 'win' || result === 'partial' || result === 'trail') return 'text-green-400'
  if (result === 'loss') return 'text-red-400'
  return 'text-gray-400'
}

function pnlClass(v: number) {
  return v > 0 ? 'text-green-400' : v < 0 ? 'text-red-400' : 'text-gray-500'
}

function pnlFmt(v: number) {
  return (v >= 0 ? '+' : '') + v.toFixed(2)
}

// ── Preset Efficiency row ──────────────────────────────────────────────────

interface PresetRow {
  name: string
  isBest: boolean
  realCount: number
  virtualCount: number
  winCount: number
  totalPnl: number
  totalWinningUsdt: number
  virtualTotalWinning: number
}

function buildPresetRows(data: TradesData): PresetRow[] {
  const presetNames = data.all_preset_names.length > 0
    ? data.all_preset_names
    : Array.from(new Set([
        ...data.real_orders.map(o => o.preset_name),
        ...Object.keys(data.virtual_summary),
      ]))

  return presetNames.map(name => {
    const real = data.real_orders.filter(o => o.preset_name === name)
    const virtStats = data.virtual_summary[name]
    const wins = real.filter(o => o.result === 'win' || o.result === 'partial' || o.result === 'trail').length
    const totalPnl = real.reduce((s, o) => s + (o.pnl_usdt ?? 0), 0)
    return {
      name,
      isBest: name === data.best_preset,
      realCount: real.length,
      virtualCount: virtStats?.trade_count ?? 0,
      winCount: wins,
      totalPnl,
      totalWinningUsdt: real.filter(o => (o.pnl_usdt ?? 0) > 0).reduce((s, o) => s + o.pnl_usdt, 0),
      virtualTotalWinning: virtStats?.total_winning_usdt ?? 0,
    }
  })
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function TradesPage() {
  const { symbol } = useSymbolContext()
  const [data, setData] = useState<TradesData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [klines, setKlines] = useState<Array<{ time: number; close: number }>>([])

  // Preset Efficiency filters
  const [hideNoReal, setHideNoReal] = useState(false)
  const [hideNoVirtual, setHideNoVirtual] = useState(false)
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null)

  useEffect(() => {
    if (!symbol) return
    setData(null)
    setError(null)
    setSelectedPreset(null)
    fetch(`/api/trades?symbol=${symbol}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(setData)
      .catch(e => setError(String(e)))
  }, [symbol])

  useEffect(() => {
    if (!symbol) return
    fetch(`/api/public-file?f=results_${symbol}.json`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.klines) setKlines(d.klines) })
      .catch(() => {})
  }, [symbol])

  const allRows = useMemo(() => data ? buildPresetRows(data) : [], [data])

  const presetRows = useMemo(() => {
    let rows = allRows
    if (hideNoReal)    rows = rows.filter(r => r.realCount > 0)
    if (hideNoVirtual) rows = rows.filter(r => r.virtualCount > 0)
    return rows.sort((a, b) => {
      if (a.isBest && !b.isBest) return -1
      if (!a.isBest && b.isBest) return 1
      // Sort: real orders first, then by total PnL desc
      if (a.realCount !== b.realCount) return b.realCount - a.realCount
      return b.totalPnl - a.totalPnl
    })
  }, [allRows, hideNoReal, hideNoVirtual])

  if (error) return <div className="pt-16 p-4 text-red-400">{error}</div>
  if (!data)  return <div className="pt-16 p-4 text-gray-400">Loading…</div>

  // Orders for the Trading Orders widget
  const tradingOrders: (RealOrder | VirtualOrder)[] = selectedPreset
    ? [
        ...data.real_orders.filter(o => o.preset_name === selectedPreset),
        ...data.virtual_orders.filter(o => o.preset_name === selectedPreset),
      ]
    : [...data.real_orders, ...data.virtual_orders]

  const tradingOrdersLabel = selectedPreset
    ? `Trading Orders — ${selectedPreset} (${tradingOrders.length})`
    : `Trading Orders (${data.real_orders.length} real · ${data.virtual_orders.length} virtual)`

  function handlePresetClick(name: string) {
    setSelectedPreset(prev => prev === name ? null : name)
  }

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

      {/* ── Preset Efficiency ── */}
      <CollapsibleSection
        title={`Preset Efficiency (${presetRows.length}${allRows.length !== presetRows.length ? ` of ${allRows.length}` : ''})`}
        storageKey="trades-preset-efficiency"
        defaultOpen
        headerExtra={
          <div className="flex items-center gap-3 text-xs text-gray-400">
            <label className="flex items-center gap-1.5 cursor-pointer select-none">
              <input type="checkbox" checked={hideNoReal}
                onChange={e => setHideNoReal(e.target.checked)} className="accent-indigo-500" />
              Hide no-real
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer select-none">
              <input type="checkbox" checked={hideNoVirtual}
                onChange={e => setHideNoVirtual(e.target.checked)} className="accent-indigo-500" />
              Hide no-virtual
            </label>
            {selectedPreset && (
              <button onClick={() => setSelectedPreset(null)}
                className="px-2 py-0.5 rounded bg-indigo-800 text-indigo-200 hover:bg-indigo-700 transition-colors">
                ✕ {selectedPreset}
              </button>
            )}
          </div>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono text-left">
            <thead>
              <tr className="text-gray-500 border-b border-gray-700">
                <th className="py-2 pr-4">Preset</th>
                <th className="py-2 pr-4 text-right">Real</th>
                <th className="py-2 pr-4 text-right">Virtual</th>
                <th className="py-2 pr-4 text-right">Win%</th>
                <th className="py-2 pr-4 text-right">Total PnL</th>
                <th className="py-2 pr-4 text-right">Avg PnL</th>
                <th className="py-2 pr-4 text-right">Winning USDT</th>
                <th className="py-2 text-right">Virt Winning</th>
              </tr>
            </thead>
            <tbody>
              {presetRows.map(row => {
                const isSelected = selectedPreset === row.name
                const winPct = row.realCount > 0
                  ? ((row.winCount / row.realCount) * 100).toFixed(1) + '%'
                  : row.virtualCount > 0 ? '—' : '—'
                const avgPnl = row.realCount > 0 ? row.totalPnl / row.realCount : null
                return (
                  <tr
                    key={row.name}
                    onClick={() => handlePresetClick(row.name)}
                    className={`border-b border-gray-800 cursor-pointer transition-colors ${
                      isSelected ? 'bg-indigo-950 ring-1 ring-inset ring-indigo-700' :
                      row.isBest ? 'bg-indigo-950/40 hover:bg-indigo-950/70' :
                      'hover:bg-gray-900/60'
                    }`}
                  >
                    <td className="py-1.5 pr-4 text-white">
                      {row.name}
                      {row.isBest && <span className="ml-2 text-[10px] text-indigo-400">BEST</span>}
                    </td>
                    <td className={`py-1.5 pr-4 text-right ${row.realCount > 0 ? 'text-green-400' : 'text-gray-600'}`}>
                      {row.realCount || '—'}
                    </td>
                    <td className={`py-1.5 pr-4 text-right ${row.virtualCount > 0 ? 'text-gray-300' : 'text-gray-600'}`}>
                      {row.virtualCount || '—'}
                    </td>
                    <td className="py-1.5 pr-4 text-right text-gray-300">{winPct}</td>
                    <td className={`py-1.5 pr-4 text-right font-semibold ${row.realCount > 0 ? pnlClass(row.totalPnl) : 'text-gray-600'}`}>
                      {row.realCount > 0 ? pnlFmt(row.totalPnl) : '—'}
                    </td>
                    <td className={`py-1.5 pr-4 text-right ${avgPnl !== null ? pnlClass(avgPnl) : 'text-gray-600'}`}>
                      {avgPnl !== null ? pnlFmt(avgPnl) : '—'}
                    </td>
                    <td className={`py-1.5 pr-4 text-right ${row.totalWinningUsdt > 0 ? 'text-green-400' : 'text-gray-600'}`}>
                      {row.totalWinningUsdt > 0 ? '+' + row.totalWinningUsdt.toFixed(2) : '—'}
                    </td>
                    <td className={`py-1.5 text-right ${row.virtualTotalWinning !== 0 ? pnlClass(row.virtualTotalWinning) : 'text-gray-600'}`}>
                      {row.virtualCount > 0 ? pnlFmt(row.virtualTotalWinning) : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {selectedPreset && (
          <p className="mt-2 text-[11px] text-indigo-400 font-mono">
            Showing orders for <strong>{selectedPreset}</strong> — click row again or ✕ to clear filter
          </p>
        )}
      </CollapsibleSection>

      {/* ── Price chart ── */}
      {klines.length > 0 && (
        <CollapsibleSection title="Price Chart + Trade Markers" storageKey="trades-chart" defaultOpen>
          <TradesChart klines={klines} realOrders={data.real_orders} />
        </CollapsibleSection>
      )}

      {/* ── Trading Orders ── */}
      <CollapsibleSection
        title={tradingOrdersLabel}
        storageKey="trades-real-orders"
        defaultOpen={data.real_orders.length > 0}
      >
        {tradingOrders.length === 0 ? (
          <p className="text-gray-500 text-sm py-4">
            {selectedPreset ? `No orders for preset "${selectedPreset}".` : 'No orders recorded yet.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead>
                <tr className="text-gray-400 border-b border-gray-700">
                  <th className="py-2 pr-3">Preset</th>
                  <th className="py-2 pr-3">Type</th>
                  <th className="py-2 pr-3">Side</th>
                  <th className="py-2 pr-3 text-right">Lev</th>
                  <th className="py-2 pr-3">Scenario</th>
                  <th className="py-2 pr-3 text-right">Entry</th>
                  <th className="py-2 pr-3 text-right">Close</th>
                  <th className="py-2 pr-3 text-right">PnL USDT</th>
                  <th className="py-2 pr-3">Result</th>
                  <th className="py-2 text-right">Closed At</th>
                </tr>
              </thead>
              <tbody>
                {tradingOrders.map((order, i) => {
                  const isReal = !('status' in order)
                  const realOrder = isReal ? (order as RealOrder) : null
                  const virtOrder = !isReal ? (order as VirtualOrder) : null
                  const pnl = realOrder?.pnl_usdt ?? virtOrder?.pnl_usdt ?? null
                  const entryPrice = realOrder?.entry_price ?? virtOrder?.entry_price ?? 0
                  const closePrice = realOrder?.close_price ?? virtOrder?.close_price ?? null
                  const closedAt = realOrder?.close_time ?? virtOrder?.close_time ?? null
                  const result = realOrder?.result ?? virtOrder?.result ?? ''
                  const leverage = order.leverage ?? null
                  const scenario = realOrder?.scenario ?? virtOrder?.scenario ?? null
                  return (
                    <tr key={i} className="border-b border-gray-800">
                      <td className="py-1.5 pr-3 font-mono text-xs text-white">{order.preset_name}</td>
                      <td className={`py-1.5 pr-3 text-xs ${realOrder ? 'text-green-400' : 'text-gray-500'}`}>
                        {realOrder ? 'Real' : 'Virtual'}
                      </td>
                      <td className={`py-1.5 pr-3 ${order.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
                        {order.side}
                      </td>
                      <td className="py-1.5 pr-3 text-right text-gray-400 font-mono text-xs">
                        {leverage != null ? `${leverage}×` : '—'}
                      </td>
                      <td className="py-1.5 pr-3 text-gray-500 font-mono text-xs">
                        {scenario || '—'}
                      </td>
                      <td className="py-1.5 pr-3 text-right text-gray-300">{entryPrice.toFixed(2)}</td>
                      <td className="py-1.5 pr-3 text-right text-gray-300">
                        {closePrice != null ? closePrice.toFixed(2) : '—'}
                      </td>
                      <td className={`py-1.5 pr-3 text-right font-medium ${pnl != null ? pnlClass(pnl) : 'text-gray-600'}`}>
                        {pnl != null ? pnlFmt(pnl) : '—'}
                      </td>
                      <td className={`py-1.5 pr-3 capitalize ${resultColor(result)}`}>{result || '—'}</td>
                      <td className="py-1.5 text-right text-gray-500 text-xs">
                        {closedAt ? new Date(closedAt).toLocaleString() : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </CollapsibleSection>
    </div>
  )
}
