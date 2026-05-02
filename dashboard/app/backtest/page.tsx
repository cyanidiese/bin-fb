'use client'

import { useState, useEffect, useMemo } from 'react'
import type { BacktestResults, BacktestPreset } from '@/lib/types'
import BacktestSummaryTable from '@/components/BacktestSummaryTable'
import BacktestTradeList from '@/components/BacktestTradeList'
import PresetSettingsPanel from '@/components/PresetSettingsPanel'
import PresetFilters from '@/components/PresetFilters'
import PresetResultsPanel from '@/components/PresetResultsPanel'
import CollapsibleSection from '@/components/CollapsibleSection'
import {
  FILTER_SPECS, TABLE_FILTER_SPECS,
  initFilters, initTableFilters,
  applyFilters, applyTableFilters,
  type FilterState,
} from '@/lib/presetFilters'
import { useLocalStorage } from '@/lib/useLocalStorage'

export default function BacktestPage() {
  const [data, setData] = useState<BacktestResults | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedPreset, setSelectedPreset] = useLocalStorage<string | null>('db:backtest:selectedPreset', null)
  const [klinesCount, setKlinesCount] = useLocalStorage<number>('db:backtest:klinesCount', 1500)
  const [isRunning, setIsRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)

  const [tableFilters, setTableFilters] = useLocalStorage('db:backtest:tableFilters', initTableFilters())
  const [tableFiltersOpen, setTableFiltersOpen] = useLocalStorage<boolean>('db:backtest:tableFiltersOpen', false)

  const [settingsFilters, setSettingsFilters] = useLocalStorage('db:backtest:settingsFilters', initFilters())
  const [settingsFiltersOpen, setSettingsFiltersOpen] = useLocalStorage<boolean>('db:backtest:settingsFiltersOpen', false)

  function patchTableFilter(key: string, patch: Partial<FilterState>) {
    setTableFilters(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }))
  }
  function patchSettingsFilter(key: string, patch: Partial<FilterState>) {
    setSettingsFilters(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }))
  }

  async function handleToggleLock(name: string, action: 'lock' | 'unlock') {
    try {
      const res = await fetch('/api/toggle-preset-lock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, action }),
      })
      if (!res.ok) return
      const r = await fetch(`/backtest_results.json?t=${Date.now()}`)
      if (!r.ok) return
      setData(await r.json())
    } catch { /* silently ignore network errors */ }
  }

  async function handleRunBacktest() {
    setIsRunning(true)
    setRunError(null)
    try {
      const res = await fetch('/api/run-backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ klines_count: klinesCount }),
      })
      const json = await res.json()
      if (!res.ok) {
        setRunError(json.error ?? 'Backtest failed')
        return
      }
      // Reload results after successful run
      const r = await fetch(`/backtest_results.json?t=${Date.now()}`)
      if (r.ok) {
        const updated: BacktestResults = await r.json()
        setData(updated)
      }
    } catch (e) {
      setRunError(String(e))
    } finally {
      setIsRunning(false)
    }
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
          setSelectedPreset(prev => prev ?? best.preset)
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
    return data.presets[selectedPreset] ?? null
  }, [data, selectedPreset])

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
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-bold text-white">Backtest Results</h1>
        <span className="text-gray-500 text-sm font-mono">
          {data.symbol} · {data.timeframe} · {data.total_klines.toLocaleString()} candles
        </span>
        <span className="text-gray-600 text-xs">
          {new Date(data.generated_at).toLocaleString()}
        </span>

        {/* Run controls */}
        <div className="ml-auto flex items-center gap-2">
          {runError && (
            <span className="text-[11px] text-red-400 font-mono max-w-xs truncate" title={runError}>
              {runError}
            </span>
          )}
          <label className="flex items-center gap-1.5 text-xs text-gray-500">
            <span className="uppercase tracking-wider">Klines</span>
            <input
              type="number"
              min={50}
              max={10000}
              step={50}
              value={klinesCount}
              onChange={e => setKlinesCount(Math.max(50, Number(e.target.value)))}
              disabled={isRunning}
              className="w-20 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-gray-300 text-xs focus:outline-none focus:border-indigo-500 disabled:opacity-40"
            />
          </label>
          <button
            onClick={handleRunBacktest}
            disabled={isRunning}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded border border-indigo-700 bg-indigo-900/60 text-indigo-300 hover:bg-indigo-800/80 hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {isRunning ? (
              <>
                <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
                </svg>
                Running…
              </>
            ) : '▶ Run Backtest'}
          </button>
        </div>
      </div>

      {/* Panels — dimmed + spinner overlay while backtest is running */}
      <div className="relative">
        {isRunning && (
          <div className="absolute inset-0 z-10 flex flex-col items-center justify-start pt-24 bg-gray-950/60 rounded-lg">
            <svg className="animate-spin h-8 w-8 text-indigo-400" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
            </svg>
            <p className="mt-3 text-sm text-indigo-300 font-mono">Running backtest over {klinesCount.toLocaleString()} klines…</p>
          </div>
        )}

        <div className={`space-y-6 transition-opacity duration-300 ${isRunning ? 'opacity-30 pointer-events-none' : ''}`}>

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
      <CollapsibleSection title="Presets" storageKey="db:backtest:s:table">
        <BacktestSummaryTable
          presets={filteredPresets}
          selectedPreset={selectedPreset}
          onSelect={setSelectedPreset}
          onDelete={handleDelete}
          onToggleLock={handleToggleLock}
          lockedPresets={new Set(data.locked_presets ?? [])}
        />
      </CollapsibleSection>

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
          <CollapsibleSection title={`Orders (${activePreset.total_trades})`} storageKey="db:backtest:s:trades">
            <BacktestTradeList
              presetName={activePreset.preset}
              trades={activePreset.trades}
            />
          </CollapsibleSection>

          <CollapsibleSection title="P&L" storageKey="db:backtest:s:pnl">
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
          </CollapsibleSection>

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

          <CollapsibleSection title="Settings" storageKey="db:backtest:s:settings">
            <PresetSettingsPanel
              settings={activePreset.settings}
              presets={filteredPresets.map(p => ({ name: p.preset, total_profit_pct: p.total_profit_pct }))}
              selectedPreset={selectedPreset ?? undefined}
              onSelect={setSelectedPreset}
            />
          </CollapsibleSection>
        </section>
      )}

        </div>{/* end dimmed content */}
      </div>{/* end relative overlay wrapper */}
    </main>
  )
}
