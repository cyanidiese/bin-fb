'use client'

import { useState, useEffect, useMemo } from 'react'
import { useSymbolContext } from '@/lib/SymbolContext'
import type { TradesData, RealOrder, VirtualOrder, Kline } from '@/lib/types'
import CollapsibleSection from '@/components/CollapsibleSection'
import TradesChart from '@/components/TradesChart'
import SymbolPicker from '@/components/SymbolPicker'

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

// ── Preset Efficiency ──────────────────────────────────────────────────────

type SortKey = 'preset' | 'trades' | 'wins' | 'partials' | 'trails' | 'losses' | 'winPct' | 'profitPct' | 'gained'

interface PresetRow {
  name: string
  isBest: boolean
  realCount: number
  virtualCount: number
  wins: number
  partials: number
  trails: number
  losses: number
  winPct: number | null
  profitPct: number | null
  totalPnl: number
  totalVirtualPnl: number
}

function buildPresetRows(data: TradesData): PresetRow[] {
  const presetNames = data.all_preset_names.length > 0
    ? data.all_preset_names
    : Array.from(new Set([
        ...data.real_orders.map(o => o.preset_name),
        ...data.virtual_orders.map(o => o.preset_name),
      ]))

  return presetNames.map(name => {
    const real = data.real_orders.filter(o => o.preset_name === name)
    const virt = data.virtual_orders.filter(
      o => o.preset_name === name && o.status === 'closed' && o.result != null,
    )

    const wins    = real.filter(o => o.result === 'win').length    + virt.filter(o => o.result === 'win').length
    const partials = real.filter(o => o.result === 'partial').length + virt.filter(o => o.result === 'partial').length
    const trails  = real.filter(o => o.result === 'trail').length  + virt.filter(o => o.result === 'trail').length
    const losses  = real.filter(o => o.result === 'loss').length   + virt.filter(o => o.result === 'loss').length

    const totalPnl        = real.reduce((s, o) => s + (o.pnl_usdt ?? 0), 0)
    const totalVirtualPnl = virt.reduce((s, o) => s + (o.pnl_usdt ?? 0), 0)

    const realCount    = real.length
    const virtualCount = virt.length
    const totalTrades  = realCount + virtualCount
    const winPct       = totalTrades > 0 ? ((wins + partials + trails) / totalTrades) * 100 : null

    // Sum of per-trade profit%: pnl_usdt / margin * 100
    const sumPct = (orders: { entry_price: number; quantity: number; leverage: number; pnl_usdt: number; virtual_margin?: number | null }[]) =>
      orders.reduce((s, o) => {
        const margin = (o as { virtual_margin?: number | null }).virtual_margin
          ?? (o.leverage > 0 ? (o.entry_price * o.quantity) / o.leverage : 0)
        return s + (margin > 0 ? (o.pnl_usdt / margin) * 100 : 0)
      }, 0)

    const profitPct = totalTrades > 0
      ? sumPct(real) + sumPct(virt.map(o => ({ ...o, pnl_usdt: o.pnl_usdt ?? 0 })))
      : null

    return {
      name,
      isBest: name === data.best_preset,
      realCount,
      virtualCount,
      wins,
      partials,
      trails,
      losses,
      winPct,
      profitPct,
      totalPnl,
      totalVirtualPnl,
    }
  })
}

// Sortable column header — defined at module level to avoid re-creating component type on each render
function SortTh({
  label, col, align = 'right', sortKey, sortDir, onSort,
}: {
  label: string
  col: SortKey
  align?: 'left' | 'right'
  sortKey: SortKey | null
  sortDir: 'asc' | 'desc'
  onSort: (key: SortKey) => void
}) {
  const active = sortKey === col
  const arrow = active ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''
  return (
    <th
      onClick={() => onSort(col)}
      className={`py-2 pr-4 cursor-pointer select-none whitespace-nowrap
        ${active ? 'text-gray-300' : 'text-gray-500'} hover:text-gray-300
        ${align === 'right' ? 'text-right' : ''}`}
    >
      {label}{arrow}
    </th>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function TradesPage() {
  const { symbol, setSymbol } = useSymbolContext()
  const [data, setData] = useState<TradesData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [klines, setKlines] = useState<Kline[]>([])
  const [symbolsWithOrders, setSymbolsWithOrders] = useState<string[]>([])
  const [realBalance, setRealBalance] = useState<number | null>(null)
  const [virtualBalance, setVirtualBalance] = useState<number | null>(null)

  // Preset Efficiency filters
  const [hideNoOrders, setHideNoOrders]       = useState(true)   // hide presets with 0 total trades
  const [hideHasVirtual, setHideHasVirtual]   = useState(false)  // hide presets that have any virtual orders

  const [selectedPreset, setSelectedPreset]   = useState<string | null>(null)
  const [sortKey, setSortKey]                 = useState<SortKey | null>(null)
  const [sortDir, setSortDir]                 = useState<'asc' | 'desc'>('desc')

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

  useEffect(() => {
    fetch('/api/trades/symbols')
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.symbols) setSymbolsWithOrders(d.symbols) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetch('/api/trades/balances')
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return
        setRealBalance(d.realBalance ?? null)
        setVirtualBalance(d.virtualBalance ?? null)
      })
      .catch(() => {})
  }, [data])

  const allRows = useMemo(() => data ? buildPresetRows(data) : [], [data])

  const presetRows = useMemo(() => {
    let rows = allRows
    if (hideNoOrders)   rows = rows.filter(r => r.realCount + r.virtualCount > 0)
    if (hideHasVirtual) rows = rows.filter(r => r.virtualCount === 0)

    if (sortKey) {
      rows = [...rows].sort((a, b) => {
        if (sortKey === 'preset') {
          const c = a.name.localeCompare(b.name)
          return sortDir === 'asc' ? c : -c
        }
        let av: number, bv: number
        switch (sortKey) {
          case 'trades':    av = a.realCount + a.virtualCount; bv = b.realCount + b.virtualCount; break
          case 'wins':      av = a.wins;      bv = b.wins;      break
          case 'partials':  av = a.partials;  bv = b.partials;  break
          case 'trails':    av = a.trails;    bv = b.trails;    break
          case 'losses':    av = a.losses;    bv = b.losses;    break
          case 'winPct':    av = a.winPct    ?? -1;         bv = b.winPct    ?? -1;         break
          case 'profitPct': av = a.profitPct ?? -Infinity;  bv = b.profitPct ?? -Infinity;  break
          case 'gained':    av = a.totalPnl + a.totalVirtualPnl; bv = b.totalPnl + b.totalVirtualPnl; break
          default:          av = 0; bv = 0
        }
        return sortDir === 'asc' ? av - bv : bv - av
      })
    } else {
      rows = [...rows].sort((a, b) => {
        if (a.isBest && !b.isBest) return -1
        if (!a.isBest && b.isBest) return 1
        const ta = a.realCount + a.virtualCount
        const tb = b.realCount + b.virtualCount
        if (ta !== tb) return tb - ta
        return (b.totalPnl + b.totalVirtualPnl) - (a.totalPnl + a.totalVirtualPnl)
      })
    }
    return rows
  }, [allRows, hideNoOrders, hideHasVirtual, sortKey, sortDir])

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  function handlePresetClick(name: string) {
    setSelectedPreset(prev => prev === name ? null : name)
  }

  if (error) return (
    <div className="pt-14 p-4 space-y-4 max-w-7xl mx-auto">
      {symbolsWithOrders.length > 0 && (
        <SymbolPicker symbols={symbolsWithOrders} selected={symbol} onSelect={setSymbol} />
      )}
      <div className="text-red-400">{error}</div>
    </div>
  )
  if (!data) return (
    <div className="pt-14 p-4 space-y-4 max-w-7xl mx-auto">
      {symbolsWithOrders.length > 0 && (
        <SymbolPicker symbols={symbolsWithOrders} selected={symbol} onSelect={setSymbol} />
      )}
      <div className="text-gray-400">Loading…</div>
    </div>
  )

  const tradingOrders: (RealOrder | VirtualOrder)[] = selectedPreset
    ? [
        ...data.real_orders.filter(o => o.preset_name === selectedPreset),
        ...data.virtual_orders.filter(o => o.preset_name === selectedPreset),
      ]
    : [...data.real_orders, ...data.virtual_orders]

  const tradingOrdersLabel = selectedPreset
    ? `Trading Orders — ${selectedPreset} (${tradingOrders.length})`
    : `Trading Orders (${data.real_orders.length} real · ${data.virtual_orders.length} virtual)`

  const thProps = { sortKey, sortDir, onSort: handleSort }

  return (
    <div className="pt-14 p-4 space-y-6 max-w-7xl mx-auto">
      {symbolsWithOrders.length > 0 && (
        <SymbolPicker symbols={symbolsWithOrders} selected={symbol} onSelect={setSymbol} />
      )}
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold text-white">{symbol} — Trades</h1>
        <span className="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400">{data.mode}</span>
        {data.best_preset && (
          <span className="text-xs px-2 py-0.5 rounded bg-indigo-900 text-indigo-300">
            Best: {data.best_preset}
          </span>
        )}
        <div className="ml-auto flex items-center gap-3">
          {realBalance !== null && (
            <span className="text-xs text-gray-400">
              Real: <span className="text-white font-semibold">${realBalance.toFixed(2)}</span>
            </span>
          )}
          {virtualBalance !== null && (
            <span className="text-xs text-gray-400">
              Virtual: <span className={`font-semibold ${virtualBalance < 0 ? 'text-red-400' : 'text-green-400'}`}>
                ${virtualBalance.toFixed(2)}
              </span>
            </span>
          )}
        </div>
      </div>

      {/* ── Preset Efficiency ── */}
      <CollapsibleSection
        title={`Preset Efficiency (${presetRows.length}${allRows.length !== presetRows.length ? ` of ${allRows.length}` : ''})`}
        storageKey="trades-preset-efficiency"
        defaultOpen
        headerExtra={
          <div className="flex items-center gap-3 text-xs text-gray-400">
            <label className="flex items-center gap-1.5 cursor-pointer select-none">
              <input type="checkbox" checked={hideNoOrders}
                onChange={e => setHideNoOrders(e.target.checked)} className="accent-indigo-500" />
              No orders
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer select-none">
              <input type="checkbox" checked={hideHasVirtual}
                onChange={e => setHideHasVirtual(e.target.checked)} className="accent-indigo-500" />
              Has virtual
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
              <tr className="border-b border-gray-700">
                <SortTh label="Preset"   col="preset"    align="left" {...thProps} />
                <SortTh label="Trades"   col="trades"               {...thProps} />
                <SortTh label="Wins"     col="wins"                 {...thProps} />
                <SortTh label="Part"     col="partials"             {...thProps} />
                <SortTh label="Trail"    col="trails"               {...thProps} />
                <SortTh label="Losses"   col="losses"               {...thProps} />
                <SortTh label="Win%"     col="winPct"               {...thProps} />
                <SortTh label="Profit%"  col="profitPct"            {...thProps} />
                <SortTh label="Gained"   col="gained"               {...thProps} />
              </tr>
            </thead>
            <tbody>
              {presetRows.map(row => {
                const isSelected = selectedPreset === row.name
                const gained = row.totalPnl + row.totalVirtualPnl
                const totalCount = row.realCount + row.virtualCount

                let tradesLabel = '—'
                if (row.realCount > 0 && row.virtualCount > 0) {
                  tradesLabel = `${row.realCount}r · ${row.virtualCount}v`
                } else if (row.realCount > 0) {
                  tradesLabel = `${row.realCount}r`
                } else if (row.virtualCount > 0) {
                  tradesLabel = `${row.virtualCount}v`
                }

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
                    <td className={`py-1.5 pr-4 text-right ${totalCount > 0 ? 'text-gray-300' : 'text-gray-600'}`}>
                      {tradesLabel}
                    </td>
                    <td className={`py-1.5 pr-4 text-right ${row.wins > 0 ? 'text-emerald-400' : 'text-gray-600'}`}>
                      {row.wins || '—'}
                    </td>
                    <td className={`py-1.5 pr-4 text-right ${row.partials > 0 ? 'text-amber-400' : 'text-gray-600'}`}>
                      {row.partials || '—'}
                    </td>
                    <td className={`py-1.5 pr-4 text-right ${row.trails > 0 ? 'text-sky-400' : 'text-gray-600'}`}>
                      {row.trails || '—'}
                    </td>
                    <td className={`py-1.5 pr-4 text-right ${row.losses > 0 ? 'text-red-400' : 'text-gray-600'}`}>
                      {row.losses || '—'}
                    </td>
                    <td className="py-1.5 pr-4 text-right text-gray-300">
                      {row.winPct != null ? row.winPct.toFixed(1) + '%' : '—'}
                    </td>
                    <td className={`py-1.5 pr-4 text-right ${row.profitPct != null ? pnlClass(row.profitPct) : 'text-gray-600'}`}>
                      {row.profitPct != null
                        ? (row.profitPct >= 0 ? '+' : '') + row.profitPct.toFixed(1) + '%'
                        : '—'}
                    </td>
                    <td className={`py-1.5 pr-4 text-right font-semibold ${totalCount > 0 ? pnlClass(gained) : 'text-gray-600'}`}>
                      {totalCount > 0 ? pnlFmt(gained) : '—'}
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
          <TradesChart
            klines={klines}
            realOrders={selectedPreset ? data.real_orders.filter(o => o.preset_name === selectedPreset) : data.real_orders}
            virtualOrders={selectedPreset ? data.virtual_orders.filter(o => o.preset_name === selectedPreset) : data.virtual_orders}
          />
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
