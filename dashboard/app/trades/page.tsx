'use client'

import { useState, useEffect, useMemo } from 'react'
import { useSymbolContext } from '@/lib/SymbolContext'
import type { TradesData, RealOrder, RankOrder, VirtualOrder, Kline, DisabledSymbolEntry, OpenRealPosition, OpenVirtualPosition } from '@/lib/types'
import CollapsibleSection from '@/components/CollapsibleSection'
import TradesChart from '@/components/TradesChart'
import SymbolPicker from '@/components/SymbolPicker'
import {
  toDatetimeLocal, toEpochSeconds, dataBounds, defaultRange,
  filterTradesData, filterKlines,
} from '@/lib/tradesDateRange'

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

function fmtPrice(price: number): string {
  if (!price) return '0'
  const a = Math.abs(price)
  if (a >= 1000) return price.toFixed(2)
  if (a >= 10)   return price.toFixed(3)
  if (a >= 1)    return price.toFixed(4)
  if (a >= 0.1)  return price.toFixed(5)
  if (a >= 0.01) return price.toFixed(6)
  if (a >= 0.001) return price.toFixed(7)
  return price.toPrecision(4)
}

function fmtQty(qty: number): string {
  if (qty >= 10000) return qty.toLocaleString('en', { maximumFractionDigits: 0 })
  if (qty >= 1)     return qty.toFixed(2)
  return qty.toPrecision(4)
}

// ── Preset Efficiency ──────────────────────────────────────────────────────

type SortKey = 'preset' | 'trades' | 'wins' | 'partials' | 'trails' | 'losses' | 'winPct' | 'profitPct' | 'gained' | 'rank'

interface PresetRow {
  name: string
  isBest: boolean
  rank: number | null
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
    : data.real_orders.map(o => o.preset_name)

  // Flatten all rank orders into a single list for easy lookup per preset
  const allRankOrders: RankOrder[] = Object.values(data.rank_orders).flat() as RankOrder[]

  return presetNames.map(name => {
    const real = data.real_orders.filter(o => o.preset_name === name)
    const virt = allRankOrders.filter(
      o => o.preset_name === name && o.status === 'closed' && o.result != null,
    )

    const wins     = real.filter(o => o.result === 'win').length    + virt.filter(o => o.result === 'win').length
    const partials = real.filter(o => o.result === 'partial').length + virt.filter(o => o.result === 'partial').length
    const trails   = real.filter(o => o.result === 'trail').length  + virt.filter(o => o.result === 'trail').length
    const losses   = real.filter(o => o.result === 'loss').length   + virt.filter(o => o.result === 'loss').length

    const totalPnl        = real.reduce((s, o) => s + (o.pnl_usdt ?? 0), 0)
    const totalVirtualPnl = virt.reduce((s, o) => s + (o.pnl_usdt ?? 0), 0)

    const realCount    = real.length
    const virtualCount = virt.length
    const totalTrades  = realCount + virtualCount
    const winPct       = totalTrades > 0 ? ((wins + partials + trails) / totalTrades) * 100 : null

    const sumPct = (orders: { entry_price: number; quantity: number; leverage: number; pnl_usdt: number }[]) =>
      orders.reduce((s, o) => {
        const margin = o.leverage > 0 ? (o.entry_price * o.quantity) / o.leverage : 0
        return s + (margin > 0 ? (o.pnl_usdt / margin) * 100 : 0)
      }, 0)

    const profitPct = totalTrades > 0
      ? sumPct(real) + sumPct(virt.map(o => ({ ...o, pnl_usdt: o.pnl_usdt ?? 0 })))
      : null

    const rank = data.preset_ranks[name] ?? null

    return {
      name,
      isBest: name === data.best_preset,
      rank,
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
  const [rankBalances, setRankBalances] = useState<Record<string, number>>({})
  const [disabledRanks, setDisabledRanks] = useState<number[]>([])
  const [disabledSymbols, setDisabledSymbols] = useState<Record<string, DisabledSymbolEntry>>({})

  // Chart date range filter
  // Page-wide date range. Drives EVERY widget on this page — see lib/tradesDateRange.
  // null = "user has not touched it", so the computed default applies. Storing the
  // override rather than the resolved value means the default can be derived during
  // render instead of written by an effect (no cascading renders, no flash of an
  // unfiltered page on first paint).
  const [rangeFromOverride, setRangeFromOverride] = useState<string | null>(null)
  const [rangeToOverride, setRangeToOverride]     = useState<string | null>(null)
  // Tracks which symbol the current override belongs to, so switching symbols falls
  // back to that symbol's own default range.
  const [rangeSymbol, setRangeSymbol] = useState<string>(symbol)

  // Preset Efficiency filters
  const [hideNoOrders, setHideNoOrders]       = useState(true)   // hide presets with 0 total trades
  const [hideHasVirtual, setHideHasVirtual]   = useState(false)  // hide presets that have any virtual orders

  const [selectedPreset, setSelectedPreset]   = useState<string | null>(null)
  const [sortKey, setSortKey]                 = useState<SortKey | null>(null)
  const [sortDir, setSortDir]                 = useState<'asc' | 'desc'>('desc')
  const [lockedPreset, setLockedPreset]       = useState<string | null>(null)
  const [lockBusy, setLockBusy]               = useState(false)

  useEffect(() => {
    if (!symbol) return
    setData(null)
    setError(null)
    setSelectedPreset(null)
    setSortKey('rank')
    setSortDir('asc')
    fetch(`/api/trades?symbol=${symbol}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then((d: TradesData) => {
        setData(d)
        setDisabledRanks(d.disabled_ranks ?? [])
        setDisabledSymbols(d.disabled_symbols ?? {})
      })
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
        setRankBalances(d.rankBalances ?? {})
      })
      .catch(() => {})
  }, [data])

  useEffect(() => {
    fetch('/api/risk')
      .then(r => r.json())
      .then(({ config }) => setLockedPreset(config?.locked_presets?.[symbol] ?? null))
      .catch(() => setLockedPreset(null))
  }, [symbol])

  // ── Page-wide date range ────────────────────────────────────────────────
  // Bounds come from everything loaded for this symbol (klines + all order lists).
  const bounds = useMemo(() => dataBounds(data, klines), [data, klines])

  // Default range: from = max(now - 1 month, earliest data for this symbol), to = now.
  const seeded = useMemo(() => defaultRange(bounds.minMs), [bounds.minMs])

  // Drop the user's override when the symbol changes — React's documented
  // "adjust state during render" pattern, which does not cascade the way an
  // effect would. Runs before this render commits, so no stale range is painted.
  if (rangeSymbol !== symbol) {
    setRangeSymbol(symbol)
    setRangeFromOverride(null)
    setRangeToOverride(null)
  }

  const rangeFrom = rangeFromOverride ?? seeded.from
  const rangeTo   = rangeToOverride   ?? seeded.to

  const fromS = useMemo(() => toEpochSeconds(rangeFrom), [rangeFrom])
  const toS   = useMemo(() => toEpochSeconds(rangeTo),   [rangeTo])

  // Filter ONCE, here. Everything below consumes `fdata` / `fklines`, so a widget
  // added later inherits the range instead of quietly showing unfiltered history.
  // Current-state fields (preset_ranks, rank_balances, best_preset, open positions)
  // deliberately pass through unfiltered — see lib/tradesDateRange.
  const fdata  = useMemo(() => data ? filterTradesData(data, fromS, toS) : null, [data, fromS, toS])
  const fklines = useMemo(() => filterKlines(klines, fromS, toS), [klines, fromS, toS])

  const allRows = useMemo(() => fdata ? buildPresetRows(fdata) : [], [fdata])

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
          case 'rank':      av = a.rank ?? 999; bv = b.rank ?? 999; break
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
    // Locked preset always pins to position 0, above any sort order.
    if (lockedPreset) {
      const lockedIdx = rows.findIndex(r => r.name === lockedPreset)
      if (lockedIdx > 0) {
        const [locked] = rows.splice(lockedIdx, 1)
        rows.unshift(locked)
      }
    }
    return rows
  }, [allRows, hideNoOrders, hideHasVirtual, sortKey, sortDir, lockedPreset])

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

  async function handleEnableSymbol(sym: string) {
    setDisabledSymbols(prev => { const n = { ...prev }; delete n[sym]; return n })
    try {
      await fetch(`/api/symbols/${sym}/enable`, { method: 'PATCH' })
    } catch {
      // revert on failure — re-fetch would be needed but this is rare
    }
  }

  async function handleEnableAll() {
    setDisabledSymbols({})
    try {
      await fetch('/api/symbols/enable-all', { method: 'POST' })
    } catch {
      // revert
    }
  }

  async function handleRankToggle(rank: number, e: React.MouseEvent) {
    e.stopPropagation()
    if (!symbol) return
    const willDisable = !disabledRanks.includes(rank)
    // Optimistic update
    setDisabledRanks(prev => willDisable ? [...prev, rank] : prev.filter(r => r !== rank))
    try {
      await fetch(`/api/symbols/${symbol}/rank-disable`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rank, disabled: willDisable }),
      })
    } catch {
      // Revert on error
      setDisabledRanks(prev => willDisable ? prev.filter(r => r !== rank) : [...prev, rank])
    }
  }

  async function handleLockToggle(presetName: string, e: React.MouseEvent) {
    e.stopPropagation()
    if (lockBusy) return
    setLockBusy(true)
    const isAlreadyLocked = lockedPreset === presetName
    try {
      const res = await fetch('/api/risk/lock-preset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, preset: isAlreadyLocked ? null : presetName }),
      })
      if (res.ok) {
        setLockedPreset(isAlreadyLocked ? null : presetName)
      }
    } finally {
      setLockBusy(false)
    }
  }

  if (error) return (
    <div className="pt-14 p-4 space-y-4 max-w-7xl mx-auto">
      {symbolsWithOrders.length > 0 && (
        <SymbolPicker symbols={symbolsWithOrders} selected={symbol} onSelect={setSymbol} />
      )}
      <div className="text-red-400">{error}</div>
    </div>
  )
  if (!data || !fdata) return (
    <div className="pt-14 p-4 space-y-4 max-w-7xl mx-auto">
      {symbolsWithOrders.length > 0 && (
        <SymbolPicker symbols={symbolsWithOrders} selected={symbol} onSelect={setSymbol} />
      )}
      <div className="text-gray-400">Loading…</div>
    </div>
  )

  // Flatten rank orders for the selected preset (or all) for the orders table and chart
  const allRankOrdersFlat: RankOrder[] = Object.values(fdata.rank_orders).flat() as RankOrder[]
  const filteredRankOrders: RankOrder[] = selectedPreset
    ? allRankOrdersFlat.filter(o => o.preset_name === selectedPreset)
    : allRankOrdersFlat

  const tradingOrders: (RealOrder | RankOrder)[] = (selectedPreset
    ? [
        ...fdata.real_orders.filter(o => o.preset_name === selectedPreset),
        ...filteredRankOrders,
      ]
    : [...fdata.real_orders, ...allRankOrdersFlat]
  ).sort((a, b) => {
    const ta = a.open_time ?? ''
    const tb = b.open_time ?? ''
    return tb.localeCompare(ta)
  })

  // Open positions are LIVE state, so they ignore the date range for the same reason
  // Ranks do: they describe the bot right now, not a historical window.
  const openRealPositions: OpenRealPosition[] = (data.open_real ?? []).filter(
    o => !selectedPreset || o.preset_name === selectedPreset
  )
  const openVirtualPositions: OpenVirtualPosition[] = (data.open_virtual ?? []).filter(
    o => !selectedPreset || o.preset_name === selectedPreset
  )
  const totalOpen = openRealPositions.length + openVirtualPositions.length

  const tradingOrdersLabel = selectedPreset
    ? `Trading Orders — ${selectedPreset} (${tradingOrders.length}${totalOpen > 0 ? ` · ${totalOpen} live` : ''})`
    : `Trading Orders (${fdata.real_orders.length} real · ${allRankOrdersFlat.length} rank virtual${totalOpen > 0 ? ` · ${totalOpen} live` : ''})`

  // Trend level of the originating recommendation. Orders recorded before the field
  // existed have no level — show a dash rather than inventing one.
  const levelCell = (lvl: number | null | undefined) => (
    <td className="py-1.5 pr-2 text-center font-mono text-xs text-gray-400">
      {lvl ? lvl : <span className="text-gray-700">—</span>}
    </td>
  )

  const thProps = { sortKey, sortDir, onSort: handleSort }

  // Cast rank orders to the shape TradesChart expects for virtual orders
  const chartVirtualOrders = filteredRankOrders as unknown as VirtualOrder[]

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
        </div>
      </div>

      {/* ── Disabled Symbols Banner ── */}
      {Object.keys(disabledSymbols).length > 0 && (
        <div className="rounded-lg border border-yellow-700/50 bg-yellow-950/30 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-yellow-400 uppercase tracking-wide">
              Auto-disabled symbols ({Object.keys(disabledSymbols).length})
            </span>
            <button
              onClick={handleEnableAll}
              className="text-xs px-2 py-0.5 rounded bg-yellow-800/60 text-yellow-200 hover:bg-yellow-700/60 transition-colors"
            >
              Enable All
            </button>
          </div>
          <div className="space-y-1">
            {Object.entries(disabledSymbols).map(([sym, entry]) => (
              <div key={sym} className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <span className="text-sm font-medium text-yellow-200">{sym}</span>
                  <span className="ml-2 text-xs text-gray-400 truncate">
                    {entry.reason.replace('consecutive_failures: ', '')}
                  </span>
                  <span className="ml-2 text-xs text-gray-600">
                    {new Date(entry.disabled_at).toLocaleString()}
                  </span>
                </div>
                <button
                  onClick={() => handleEnableSymbol(sym)}
                  className="shrink-0 text-xs px-2 py-0.5 rounded bg-green-900/60 text-green-300 hover:bg-green-800/60 transition-colors"
                >
                  Enable
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Date range (drives every widget below except Ranks) ── */}
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-gray-500">From</span>
            <input
              type="datetime-local"
              value={rangeFrom}
              min={bounds.minMs !== null ? toDatetimeLocal(new Date(bounds.minMs)) : undefined}
              onChange={e => setRangeFromOverride(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-gray-500">To</span>
            <input
              type="datetime-local"
              value={rangeTo}
              onChange={e => setRangeToOverride(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-blue-500"
            />
          </label>
          <button
            onClick={() => { setRangeFromOverride(null); setRangeToOverride(null) }}
            className="px-2 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white hover:border-gray-500 transition-colors"
          >
            Last month
          </button>
          <button
            onClick={() => {
              setRangeFromOverride(bounds.minMs !== null ? toDatetimeLocal(new Date(bounds.minMs)) : '')
              setRangeToOverride(toDatetimeLocal(new Date()))
            }}
            className="px-2 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white hover:border-gray-500 transition-colors"
          >
            All history
          </button>
          <p className="text-[11px] text-gray-600 ml-auto">
            Applies to every widget on this page. Ranks and live positions always show current state.
          </p>
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
                <SortTh label="Rank"     col="rank"      align="left" {...thProps} />
                <SortTh label="V.Bal"    col="gained"    align="right" {...thProps} />
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

                // Rank label: "★ Real" for rank 1, "#N" for others, "—" if unranked
                let rankLabel = '—'
                if (row.rank === 1) rankLabel = '★ Real'
                else if (row.rank != null) rankLabel = `#${row.rank}`

                // V.Bal: show the rank pool balance for ranks 2+; real balance for rank 1; "—" otherwise
                let vBalLabel = '—'
                if (row.rank === 1 && realBalance !== null) {
                  vBalLabel = `$${realBalance.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
                } else if (row.rank != null && row.rank >= 2) {
                  const bal = rankBalances[String(row.rank)]
                  vBalLabel = bal != null ? `$${bal.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'
                }

                return (
                  <tr
                    key={row.name}
                    onClick={() => handlePresetClick(row.name)}
                    className={`border-b border-gray-800 cursor-pointer transition-colors ${
                      isSelected ? 'bg-indigo-950 ring-1 ring-inset ring-indigo-700' :
                      lockedPreset === row.name ? 'bg-amber-950/30 hover:bg-amber-950/50' :
                      row.isBest ? 'bg-indigo-950/40 hover:bg-indigo-950/70' :
                      'hover:bg-gray-900/60'
                    }`}
                  >
                    <td className="py-1.5 pr-4 text-white">
                      {row.name}
                      {row.isBest && <span className="ml-2 text-[10px] text-indigo-400">BEST</span>}
                      <button
                        onClick={(e) => handleLockToggle(row.name, e)}
                        disabled={lockBusy}
                        title={lockedPreset === row.name ? `Unlock — resume scored selection` : `Lock ${row.name} as best preset for ${symbol}`}
                        className={`ml-2 text-[10px] leading-none transition-colors disabled:opacity-40 ${
                          lockedPreset === row.name
                            ? 'text-amber-400 hover:text-amber-200'
                            : 'text-gray-600 hover:text-amber-400'
                        }`}
                      >
                        {lockedPreset === row.name ? '🔒' : '🔓'}
                      </button>
                    </td>
                    <td className={`py-1.5 pr-4 text-left text-xs ${row.rank === 1 ? 'text-indigo-400' : row.rank != null ? 'text-gray-400' : 'text-gray-600'}`}>
                      <span className="flex items-center gap-1.5">
                        <span className={row.rank != null && row.rank >= 2 && disabledRanks.includes(row.rank) ? 'line-through opacity-40' : ''}>
                          {rankLabel}
                        </span>
                        {row.rank != null && row.rank >= 2 && (
                          <button
                            onClick={e => handleRankToggle(row.rank!, e)}
                            title={disabledRanks.includes(row.rank) ? `Re-enable rank ${row.rank} virtual pool for ${symbol}` : `Disable rank ${row.rank} virtual pool for ${symbol}`}
                            className={`text-[9px] leading-none px-1 py-0.5 rounded transition-colors ${
                              disabledRanks.includes(row.rank)
                                ? 'text-gray-500 hover:text-green-400 border border-gray-700 hover:border-green-600'
                                : 'text-gray-600 hover:text-red-400 border border-gray-800 hover:border-red-700'
                            }`}
                          >
                            {disabledRanks.includes(row.rank) ? '↑' : '×'}
                          </button>
                        )}
                      </span>
                    </td>
                    <td className={`py-1.5 pr-4 text-right text-xs ${row.rank != null && row.rank >= 2 ? 'text-gray-300' : 'text-gray-600'}`}>
                      {vBalLabel}
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
      {/* Range pickers used to live here; they are now the page-wide toolbar above
          Preset Efficiency, so the chart simply consumes the already-filtered data. */}
      {fklines.length > 0 && (
        <CollapsibleSection title="Price Chart + Trade Markers" storageKey="trades-chart" defaultOpen>
          {fklines.length < klines.length && (
            <p className="mb-2 text-[11px] text-gray-600">
              {fklines.length.toLocaleString()} of {klines.length.toLocaleString()} candles in range
            </p>
          )}
          <TradesChart
            klines={fklines}
            realOrders={selectedPreset ? fdata.real_orders.filter(o => o.preset_name === selectedPreset) : fdata.real_orders}
            virtualOrders={chartVirtualOrders}
          />
        </CollapsibleSection>
      )}

      {/* ── Trading Orders ── */}
      <CollapsibleSection
        title={tradingOrdersLabel}
        storageKey="trades-real-orders"
        defaultOpen={fdata.real_orders.length > 0}
      >
        {totalOpen === 0 && tradingOrders.length === 0 ? (
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
                  <th className="py-2 pr-2 text-center w-8" title="Trend level of the recommendation this order came from">L</th>
                  <th className="py-2 pr-3 text-right">Lev</th>
                  <th className="py-2 pr-3">Scenario</th>
                  <th className="py-2 pr-3 text-right">Qty</th>
                  <th className="py-2 pr-3 text-right">Entry</th>
                  <th className="py-2 pr-3 text-right">Close</th>
                  <th className="py-2 pr-3 text-right">PnL USDT</th>
                  <th className="py-2 pr-3">Result</th>
                  <th className="py-2 text-right">Time</th>
                </tr>
              </thead>
              <tbody>
                {/* ── Live open positions (real) ── */}
                {openRealPositions.map((order, i) => (
                  <tr key={`open-real-${i}`} className="border-b border-gray-800 bg-emerald-950/30">
                    <td className="py-1.5 pr-3 font-mono text-xs text-white">{order.preset_name}</td>
                    <td className="py-1.5 pr-3 text-xs">
                      <span className="inline-flex items-center gap-1">
                        <span className="text-green-400">Real</span>
                        <span className="text-[9px] px-1 py-0.5 rounded bg-emerald-800/60 text-emerald-300 font-semibold tracking-wide">LIVE</span>
                      </span>
                    </td>
                    <td className={`py-1.5 pr-3 ${order.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>{order.side}</td>
                    {levelCell(order.signal_level)}
                    <td className="py-1.5 pr-3 text-right text-gray-400 font-mono text-xs">{order.leverage != null ? `${order.leverage}×` : '—'}</td>
                    <td className="py-1.5 pr-3 text-gray-500 font-mono text-xs">{order.scenario || '—'}</td>
                    <td className="py-1.5 pr-3 text-right text-gray-400 font-mono text-xs">{fmtQty(order.quantity)}</td>
                    <td className="py-1.5 pr-3 text-right text-gray-300 font-mono text-xs">{fmtPrice(order.entry_price)}</td>
                    <td className="py-1.5 pr-3 text-right text-gray-600 font-mono text-xs">—</td>
                    <td className="py-1.5 pr-3 text-right text-gray-600">—</td>
                    <td className="py-1.5 pr-3 text-gray-600">—</td>
                    <td className="py-1.5 text-right text-gray-500 text-xs">
                      {order.open_time ? new Date(order.open_time).toLocaleString() : '—'}
                    </td>
                  </tr>
                ))}
                {/* ── Live open positions (virtual / rank) ── */}
                {openVirtualPositions.map((order, i) => (
                  <tr key={`open-virt-${i}`} className="border-b border-gray-800 bg-sky-950/30">
                    <td className="py-1.5 pr-3 font-mono text-xs text-white">{order.preset_name}</td>
                    <td className="py-1.5 pr-3 text-xs">
                      <span className="inline-flex items-center gap-1">
                        <span className="text-gray-400">Rank #{order.rank}</span>
                        <span className="text-[9px] px-1 py-0.5 rounded bg-sky-800/60 text-sky-300 font-semibold tracking-wide">LIVE</span>
                      </span>
                    </td>
                    <td className={`py-1.5 pr-3 ${order.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>{order.side}</td>
                    {levelCell(order.signal_level)}
                    <td className="py-1.5 pr-3 text-right text-gray-400 font-mono text-xs">{order.leverage != null ? `${order.leverage}×` : '—'}</td>
                    <td className="py-1.5 pr-3 text-gray-500 font-mono text-xs">{order.scenario || '—'}</td>
                    <td className="py-1.5 pr-3 text-right text-gray-400 font-mono text-xs">{fmtQty(order.quantity)}</td>
                    <td className="py-1.5 pr-3 text-right text-gray-300 font-mono text-xs">{fmtPrice(order.entry_price)}</td>
                    <td className="py-1.5 pr-3 text-right text-gray-600 font-mono text-xs">—</td>
                    <td className="py-1.5 pr-3 text-right text-gray-600">—</td>
                    <td className="py-1.5 pr-3 text-gray-600">—</td>
                    <td className="py-1.5 text-right text-gray-500 text-xs">
                      {new Date(order.open_time).toLocaleString()}
                    </td>
                  </tr>
                ))}
                {/* ── Closed orders ── */}
                {tradingOrders.map((order, i) => {
                  const isReal = !('status' in order)
                  const realOrder = isReal ? (order as RealOrder) : null
                  const rankOrder = !isReal ? (order as RankOrder) : null
                  const pnl = realOrder?.pnl_usdt ?? rankOrder?.pnl_usdt ?? null
                  const entryPrice = realOrder?.entry_price ?? rankOrder?.entry_price ?? 0
                  const closePrice = realOrder?.close_price ?? rankOrder?.close_price ?? null
                  const quantity = realOrder?.quantity ?? rankOrder?.quantity ?? null
                  const closedAt = realOrder?.close_time ?? rankOrder?.close_time ?? null
                  const result = realOrder?.result ?? rankOrder?.result ?? ''
                  const leverage = order.leverage ?? null
                  const scenario = realOrder?.scenario ?? rankOrder?.scenario ?? null
                  const rankLabel = rankOrder?.rank != null ? `Rank #${rankOrder.rank}` : 'Virtual'
                  return (
                    <tr key={i} className="border-b border-gray-800">
                      <td className="py-1.5 pr-3 font-mono text-xs text-white">{order.preset_name}</td>
                      <td className={`py-1.5 pr-3 text-xs ${realOrder ? 'text-green-400' : 'text-gray-500'}`}>
                        {realOrder ? 'Real' : rankLabel}
                      </td>
                      <td className={`py-1.5 pr-3 ${order.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
                        {order.side}
                      </td>
                      {levelCell(realOrder?.signal_level ?? rankOrder?.signal_level)}
                      <td className="py-1.5 pr-3 text-right text-gray-400 font-mono text-xs">
                        {leverage != null ? `${leverage}×` : '—'}
                      </td>
                      <td className="py-1.5 pr-3 text-gray-500 font-mono text-xs">
                        {scenario || '—'}
                      </td>
                      <td className="py-1.5 pr-3 text-right text-gray-400 font-mono text-xs">
                        {quantity != null ? fmtQty(quantity) : '—'}
                      </td>
                      <td className="py-1.5 pr-3 text-right text-gray-300 font-mono text-xs">{fmtPrice(entryPrice)}</td>
                      <td className="py-1.5 pr-3 text-right text-gray-300 font-mono text-xs">
                        {closePrice != null ? fmtPrice(closePrice) : '—'}
                      </td>
                      <td className={`py-1.5 pr-3 text-right font-medium ${pnl != null ? pnlClass(pnl) : 'text-gray-600'}`}>
                        {pnl != null ? pnlFmt(pnl) : '—'}
                      </td>
                      <td className={`py-1.5 pr-3 capitalize ${resultColor(String(result))}`}>{result || '—'}</td>
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
