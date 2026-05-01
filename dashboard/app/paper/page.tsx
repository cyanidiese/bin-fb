'use client'

import { useState, useEffect, useMemo } from 'react'
import type { PaperResults, PaperPreset, PaperOpenOrder } from '@/lib/types'
import BacktestSummaryTable from '@/components/BacktestSummaryTable'
import BacktestTradeList from '@/components/BacktestTradeList'
import PresetSettingsPanel from '@/components/PresetSettingsPanel'
import PresetFilters from '@/components/PresetFilters'
import PresetResultsPanel from '@/components/PresetResultsPanel'
import {
  FILTER_SPECS, TABLE_FILTER_SPECS,
  initFilters, initTableFilters,
  applyFilters, applyTableFilters,
  type FilterState,
} from '@/lib/presetFilters'

const REFRESH_MS = 15_000

function sideBadge(side: 'BUY' | 'SELL') {
  return side === 'BUY'
    ? <span className="rounded px-1.5 py-0.5 text-[10px] font-bold bg-emerald-900/60 text-emerald-400">BUY</span>
    : <span className="rounded px-1.5 py-0.5 text-[10px] font-bold bg-red-900/60 text-red-400">SELL</span>
}

function OpenOrderCard({ name, order, currentCandle }: {
  name: string
  order: PaperOpenOrder
  currentCandle: number
}) {
  const unreal = order.unrealized_pct
  const unrealColor = unreal > 0 ? 'text-emerald-400' : unreal < 0 ? 'text-red-400' : 'text-gray-400'
  const candlesOpen = currentCandle - order.open_candle

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-900/60 p-3 space-y-2 min-w-[220px]">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs font-semibold text-white truncate">{name}</span>
        {sideBadge(order.side)}
      </div>

      <div className="grid grid-cols-3 gap-1 text-[11px] font-mono">
        <div>
          <p className="text-gray-500 text-[9px] uppercase tracking-wide">Entry</p>
          <p className="text-gray-200">{order.entry.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-gray-500 text-[9px] uppercase tracking-wide">TP</p>
          <p className="text-emerald-400">{order.tp.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-gray-500 text-[9px] uppercase tracking-wide">SL</p>
          <p className="text-red-400">{order.sl.toLocaleString()}</p>
        </div>
      </div>

      <div className="flex items-center justify-between text-[11px] font-mono">
        <div>
          <p className="text-gray-500 text-[9px] uppercase tracking-wide">Unrealized</p>
          <p className={`font-bold ${unrealColor}`}>
            {unreal >= 0 ? '+' : ''}{unreal.toFixed(3)}%
          </p>
        </div>
        <div className="text-right">
          <p className="text-gray-500 text-[9px] uppercase tracking-wide">MaxTP reach</p>
          <p className="text-gray-300">{order.max_tp_reach_pct.toFixed(1)}%</p>
        </div>
      </div>

      <div className="flex items-center justify-between text-[10px] text-gray-500 font-mono border-t border-gray-800 pt-1.5">
        <span>{candlesOpen} candle{candlesOpen !== 1 ? 's' : ''} open</span>
        {order.partial_price != null && (
          <span
            className={order.armed ? 'text-amber-400' : 'text-gray-600'}
            title={order.armed ? 'Partial armed — trailing active' : 'Waiting to arm at partial price'}
          >
            {order.armed ? '● Armed' : '○ Unnarmed'}
          </span>
        )}
      </div>
    </div>
  )
}

export default function PaperPage() {
  const [data, setData] = useState<PaperResults | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null)

  const [tableFilters, setTableFilters] = useState(initTableFilters)
  const [tableFiltersOpen, setTableFiltersOpen] = useState(false)

  const [settingsFilters, setSettingsFilters] = useState(initFilters)
  const [settingsFiltersOpen, setSettingsFiltersOpen] = useState(false)

  function patchTableFilter(key: string, patch: Partial<FilterState>) {
    setTableFilters(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }))
  }
  function patchSettingsFilter(key: string, patch: Partial<FilterState>) {
    setSettingsFilters(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }))
  }

  function fetchData() {
    fetch(`/paper_results.json?t=${Date.now()}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((json: PaperResults) => {
        setData(json)
        setError(null)
        if (!selectedPreset) {
          const entries = Object.values(json.presets)
          if (entries.length > 0) {
            const best = entries.reduce((a, b) =>
              b.total_profit_pct > a.total_profit_pct ? b : a
            )
            setSelectedPreset(best.preset)
          }
        }
      })
      .catch(e => setError(String(e)))
  }

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const presetList: PaperPreset[] = useMemo(() => {
    if (!data) return []
    return Object.values(data.presets)
  }, [data])

  const filteredByTable = useMemo(
    () => tableFiltersOpen ? applyTableFilters(presetList, tableFilters) : presetList,
    [presetList, tableFilters, tableFiltersOpen]
  )
  const filteredPresets = useMemo(
    () => settingsFiltersOpen ? applyFilters(filteredByTable, settingsFilters) : filteredByTable,
    [filteredByTable, settingsFilters, settingsFiltersOpen]
  )

  const activePreset = useMemo(() => {
    if (!data || !selectedPreset) return null
    const found = filteredPresets.find(p => p.preset === selectedPreset)
    return found ? (data.presets[selectedPreset] ?? null) : null
  }, [data, selectedPreset, filteredPresets])

  const openOrders = useMemo(() => {
    if (!data) return []
    return Object.entries(data.presets)
      .filter(([, p]) => p.open_order !== null)
      .map(([name, p]) => ({ name, order: p.open_order! }))
  }, [data])

  if (error) {
    return (
      <main className="p-6 text-red-400 text-sm font-mono space-y-2">
        <p>Failed to load paper trading results: {error}</p>
        <p className="text-gray-500">
          Run <code className="text-gray-300">python paper_trade.py</code> to start the paper trader.
        </p>
      </main>
    )
  }

  if (!data) {
    return (
      <main className="p-6 text-gray-500 text-sm">Loading paper trading results…</main>
    )
  }

  const runningDays = (() => {
    const ms = Date.now() - new Date(data.started_at).getTime()
    const h = Math.floor(ms / 3_600_000)
    if (h < 24) return `${h}h`
    return `${Math.floor(h / 24)}d ${h % 24}h`
  })()

  return (
    <main className="p-4 space-y-6 max-w-full">
      {/* Header */}
      <div className="flex flex-wrap items-baseline gap-4">
        <h1 className="text-lg font-bold text-white">Paper Trading</h1>
        <span className="text-gray-500 text-sm font-mono">
          {data.symbol} · {data.timeframe} · {presetList.length} presets
        </span>
        <span className="text-gray-400 text-sm font-mono font-semibold">
          {data.current_price.toLocaleString()} USDT
        </span>
        <span className="text-gray-600 text-xs ml-auto">
          running {runningDays} · updated {new Date(data.generated_at).toLocaleTimeString()}
        </span>
      </div>

      {/* Open orders */}
      <section className="space-y-2">
        <h2 className="text-xs uppercase tracking-wide text-gray-500 font-semibold">
          Active Orders ({openOrders.length})
        </h2>
        {openOrders.length === 0 ? (
          <p className="text-sm text-gray-600 italic">No open orders — waiting for signals.</p>
        ) : (
          <div className="flex flex-wrap gap-3">
            {openOrders.map(({ name, order }) => (
              <OpenOrderCard
                key={name}
                name={name}
                order={order}
                currentCandle={data.candle_index}
              />
            ))}
          </div>
        )}
      </section>

      {/* Table column filters — above Presets table */}
      <PresetFilters
        title="Preset filters"
        specs={TABLE_FILTER_SPECS}
        filters={tableFilters}
        open={tableFiltersOpen}
        onToggle={() => setTableFiltersOpen(o => !o)}
        onChange={patchTableFilter}
        onClear={() => setTableFilters(initTableFilters())}
      />

      {/* Summary table */}
      <BacktestSummaryTable
        presets={filteredPresets}
        selectedPreset={selectedPreset}
        onSelect={setSelectedPreset}
      />

      {/* Visualize panel */}
      <PresetResultsPanel preset={activePreset} />

      {/* Trade drill-down */}
      {activePreset && (
        <section className="space-y-3">
          <div className="flex items-baseline gap-3">
            <h2 className="font-semibold text-white font-mono">{activePreset.preset}</h2>
            <span className="text-xs text-gray-500">
              {activePreset.total_trades} trades ·{' '}
              <span className="text-emerald-400">{activePreset.wins}W</span>{' '}
              <span className="text-amber-400">{activePreset.partials}P</span>{' '}
              <span className="text-sky-400">{activePreset.trails}T</span>{' '}
              <span className="text-red-400">{activePreset.losses}L</span>
            </span>
            {activePreset.open_order && (
              <span className="text-xs font-mono">
                {sideBadge(activePreset.open_order.side)}
                <span className={`ml-1.5 ${activePreset.open_order.unrealized_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {activePreset.open_order.unrealized_pct >= 0 ? '+' : ''}
                  {activePreset.open_order.unrealized_pct.toFixed(3)}% open
                </span>
              </span>
            )}
          </div>

          <BacktestTradeList
            presetName={activePreset.preset}
            trades={activePreset.trades}
          />

          {/* P&L stats */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              {
                label: 'Actual P&L',
                value: `${activePreset.total_profit_pts >= 0 ? '+' : ''}${activePreset.total_profit_pts.toFixed(1)} pts`,
                sub: `${activePreset.total_profit_pct >= 0 ? '+' : ''}${activePreset.total_profit_pct.toFixed(2)}%`,
                color: activePreset.total_profit_pts >= 0 ? 'text-emerald-400' : 'text-red-400',
              },
              {
                label: 'Potential win (all TP)',
                value: `+${activePreset.potential_win_pts.toFixed(1)} pts`,
                sub: `${activePreset.total_trades} trades × avg TP`,
                color: 'text-emerald-500',
              },
              {
                label: 'Potential loss (all SL)',
                value: `-${activePreset.potential_loss_pts.toFixed(1)} pts`,
                sub: `${activePreset.total_trades} trades × avg SL`,
                color: 'text-red-500',
              },
              {
                label: 'Avg TP reach (non-wins)',
                value: `${activePreset.avg_max_tp_reach_pct.toFixed(1)}%`,
                sub: 'of TP distance — raise if > 80%',
                color: activePreset.avg_max_tp_reach_pct >= 80 ? 'text-amber-400' : 'text-gray-300',
              },
            ].map(s => (
              <div key={s.label} className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-3">
                <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">{s.label}</p>
                <p className={`text-lg font-bold font-mono ${s.color}`}>{s.value}</p>
                <p className="text-[10px] text-gray-600 mt-0.5">{s.sub}</p>
              </div>
            ))}
          </div>

          {/* Settings filters — above All settings panel */}
          <PresetFilters
            title="Settings filters"
            specs={FILTER_SPECS}
            filters={settingsFilters}
            open={settingsFiltersOpen}
            onToggle={() => setSettingsFiltersOpen(o => !o)}
            onChange={patchSettingsFilter}
            onClear={() => setSettingsFilters(initFilters())}
          />

          <PresetSettingsPanel
            settings={activePreset.settings}
            presets={filteredPresets.map(p => ({ name: p.preset, total_profit_pct: p.total_profit_pct }))}
            selectedPreset={selectedPreset ?? undefined}
            onSelect={setSelectedPreset}
          />
        </section>
      )}
    </main>
  )
}
