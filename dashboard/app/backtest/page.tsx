'use client'

import { useState, useEffect, useMemo } from 'react'
import type { BacktestResults, BacktestPreset } from '@/lib/types'
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

export default function BacktestPage() {
  const [data, setData] = useState<BacktestResults | null>(null)
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

  async function handleDelete(name: string) {
    try {
      const res = await fetch('/api/delete-preset', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (!res.ok) return
      // Refetch so all derived state (filteredPresets, activePreset) updates atomically
      const r = await fetch(`/backtest_results.json?t=${Date.now()}`)
      if (!r.ok) return
      const json: BacktestResults = await r.json()
      setData(json)
      if (selectedPreset === name) setSelectedPreset(null)
    } catch { /* silently ignore network errors */ }
  }

  useEffect(() => {
    fetch(`/backtest_results.json?t=${Date.now()}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((json: BacktestResults) => {
        setData(json)
        const entries = Object.values(json.presets)
        if (entries.length > 0) {
          const best = entries.reduce((a, b) =>
            b.total_profit_pct > a.total_profit_pct ? b : a
          )
          setSelectedPreset(best.preset)
        }
      })
      .catch(e => setError(String(e)))
  }, [])

  const presetList: BacktestPreset[] = useMemo(() => {
    if (!data) return []
    return Object.values(data.presets)
  }, [data])

  // Chain: table filters first, then settings filters.
  // Each filter set is skipped entirely when its panel is hidden.
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

  if (error) {
    return (
      <main className="p-6 text-red-400 text-sm font-mono">
        <p>Failed to load backtest results: {error}</p>
        <p className="mt-2 text-gray-500">Run <code className="text-gray-300">python backtest.py</code> first to generate results.</p>
      </main>
    )
  }

  if (!data) {
    return (
      <main className="p-6 text-gray-500 text-sm">Loading backtest results…</main>
    )
  }

  return (
    <main className="p-4 space-y-6 max-w-full">
      {/* Header */}
      <div className="flex flex-wrap items-baseline gap-4">
        <h1 className="text-lg font-bold text-white">Backtest Results</h1>
        <span className="text-gray-500 text-sm font-mono">
          {data.symbol} · {data.timeframe} · {data.total_klines.toLocaleString()} candles
        </span>
        <span className="text-gray-600 text-xs ml-auto">
          {new Date(data.generated_at).toLocaleString()}
        </span>
      </div>

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
        onDelete={handleDelete}
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
