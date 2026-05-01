'use client'

import { useState, useEffect, useMemo, useCallback } from 'react'
import type { BacktestResults, BacktestPreset, BacktestApiResponse } from '@/lib/types'
import { SETTINGS_META } from '@/components/PresetSettingsPanel'
import EditableSettingsPanel from '@/components/EditableSettingsPanel'
import PresetChart from '@/components/PresetChart'
import BacktestTradeList from '@/components/BacktestTradeList'

// ── helpers ────────────────────────────────────────────────────────────────

function toDateStr(unixSec: number): string {
  return new Date(unixSec * 1000).toISOString().slice(0, 16)
}

function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr + ':00Z')
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 16)
}

// Abbreviations used in auto-generated preset names
const NAME_ABBREV: Record<string, string> = {
  swing_neighbours: 'sw',
  min_swing_points: 'msp',
  proximity_zone_pct: 'zone',
  min_profit_pct: 'minp',
  min_profit_loss_ratio: 'rr',
  tp_multiplier: 'tp',
  max_profit_pct: 'maxp',
  min_sl_pct: 'minsl',
  max_sl_pct: 'maxsl',
  sl_adjust_to_rr: 'sladj',
  partial_take_pct: 'partial',
  trailing_stop_pct: 'trail',
  loss_streak_max: 'ls',
  loss_streak_cooldown_candles: 'lsc',
  global_pause_trigger_candles: 'gpt',
  global_pause_candles: 'gp',
}

function generatePresetName(overrides: Record<string, number | boolean>): string {
  const defaults = Object.fromEntries(
    Object.entries(SETTINGS_META).map(([k, v]) => [k, v.default]),
  )
  const changed = Object.entries(overrides)
    .filter(([k, v]) => defaults[k] !== undefined && v !== defaults[k])
    .slice(0, 4)
  if (changed.length === 0) return 'custom'
  const parts = changed.map(([k, v]) => {
    const abbr = NAME_ABBREV[k] ?? k.split('_').map(w => w[0]).join('')
    if (typeof v === 'boolean') return `${abbr}${v ? '1' : '0'}`
    const s = typeof v === 'number' && v % 1 !== 0
      ? v.toFixed(2).replace('.', '')
      : String(v)
    return `${abbr}${s}`
  })
  return `custom_${parts.join('_')}`
}

// ── style constants ────────────────────────────────────────────────────────

const DATE_INPUT_CLS =
  'bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-300 text-[11px] font-mono focus:outline-none focus:border-gray-500'
const NAV_BTN_CLS =
  'px-2 py-0.5 rounded border border-gray-700 bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700 text-[11px] font-mono transition-colors'

// ── page ───────────────────────────────────────────────────────────────────

export default function CreatePresetPage() {
  const [backtestData, setBacktestData] = useState<BacktestResults | null>(null)

  const [baseName, setBaseName] = useState<string>('')
  const [overrides, setOverrides] = useState<Record<string, number | boolean>>({})
  const [settingsTouched, setSettingsTouched] = useState(false)

  // Controls which settings EditableSettingsPanel initialises from (its key forces a remount
  // on each base-change or restore so the panel re-reads the new baseSettings).
  const [panelBaseSettings, setPanelBaseSettings] = useState<Record<string, number | boolean>>({})
  const [restoreKey, setRestoreKey] = useState(0)

  const [result, setResult] = useState<BacktestApiResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Best result tracking across Check runs (resets when base preset changes)
  const [bestResult, setBestResult] = useState<BacktestApiResponse | null>(null)
  const [bestOverrides, setBestOverrides] = useState<Record<string, number | boolean> | null>(null)

  // Chart date range
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')

  // Save state
  const [saveName, setSaveName] = useState('')
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle')

  // ── initial data load ──────────────────────────────────────────────────

  useEffect(() => {
    fetch(`/backtest_results.json?t=${Date.now()}`)
      .then(r => r.ok ? r.json() : null)
      .then((d: BacktestResults | null) => { if (d) setBacktestData(d) })
      .catch(() => null)
  }, [])

  // Init chart dates when result loads
  useEffect(() => {
    if (!result?.klines.length) return
    setFromDate(toDateStr(result.klines[0].time))
    setToDate(toDateStr(result.klines[result.klines.length - 1].time))
  }, [result])

  // ── derived ────────────────────────────────────────────────────────────

  const sortedPresets: BacktestPreset[] = useMemo(() => {
    if (!backtestData) return []
    return Object.values(backtestData.presets).sort((a, b) => b.total_profit_pct - a.total_profit_pct)
  }, [backtestData])

  const filteredKlines = useMemo(() => {
    if (!result || !fromDate || !toDate) return result?.klines ?? []
    return result.klines.filter(k => {
      const d = toDateStr(k.time)
      return d >= fromDate && d <= toDate
    })
  }, [result, fromDate, toDate])

  const chartKlines = useMemo(
    () => filteredKlines.map((k, i) => ({ ...k, index: i })),
    [filteredKlines],
  )

  const chartTrades = useMemo(() => {
    if (!result || !filteredKlines.length) return result?.trades ?? []
    const minIdx = filteredKlines[0].index
    const maxIdx = filteredKlines[filteredKlines.length - 1].index
    const visible = result.trades.filter(t =>
      t.open_candle <= maxIdx && (t.close_candle ?? t.open_candle + 1) >= minIdx,
    )
    return visible.map(t => ({
      ...t,
      open_candle: Math.max(0, t.open_candle - minIdx),
      close_candle: t.close_candle != null
        ? Math.min(chartKlines.length - 1, Math.max(0, t.close_candle - minIdx))
        : null,
    }))
  }, [result, filteredKlines, chartKlines])

  // Button states
  const canRestoreBest =
    bestResult !== null &&
    (result === null || result.total_profit_pct < bestResult.total_profit_pct)

  // canSave: settings are valid to save (regardless of current save status)
  const canSave = result !== null && settingsTouched

  // ── handlers ───────────────────────────────────────────────────────────

  const handleBaseChange = (name: string) => {
    setBaseName(name)
    setResult(null)
    setError(null)
    setBestResult(null)
    setBestOverrides(null)
    setSettingsTouched(false)
    setSaveName('')
    setSaveStatus('idle')
    const preset = backtestData?.presets[name] ?? null
    const settings = preset ? { ...preset.settings } : {}
    setOverrides(settings)
    setPanelBaseSettings(settings)
    setRestoreKey(0)
  }

  const handleOverridesChange = useCallback((newOverrides: Record<string, number | boolean>) => {
    setOverrides(newOverrides)
    setSettingsTouched(true)
    setSaveStatus('idle')
  }, [])

  const handleCheck = useCallback(async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    setSaveStatus('idle')
    try {
      const res = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: overrides }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`)
      const apiResult = data as BacktestApiResponse
      setResult(apiResult)
      setSaveName(generatePresetName(overrides))
      // Update best if this run is better
      setBestResult(prev => {
        if (!prev || apiResult.total_profit_pct > prev.total_profit_pct) {
          setBestOverrides({ ...overrides })
          return apiResult
        }
        return prev
      })
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [overrides])

  const handleRestoreBest = useCallback(() => {
    if (!bestOverrides || !bestResult) return
    const restored = { ...bestOverrides }
    setOverrides(restored)
    setResult(bestResult)
    setSaveName(generatePresetName(restored))
    setSettingsTouched(true)
    setSaveStatus('idle')
    // Force EditableSettingsPanel to remount with the restored values
    setPanelBaseSettings(restored)
    setRestoreKey(k => k + 1)
  }, [bestOverrides, bestResult])

  const handleSave = useCallback(async () => {
    if (!result || !saveName.trim()) return
    setSaveStatus('saving')
    try {
      const res = await fetch('/api/save-preset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: saveName.trim(), result, settings: overrides }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error)
      setSaveStatus('saved')
      // Refresh preset list so the new preset appears in the dropdown
      fetch(`/backtest_results.json?t=${Date.now()}`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) setBacktestData(d) })
        .catch(() => null)
    } catch (e) {
      setError(`Save failed: ${String(e)}`)
      setSaveStatus('idle')
    }
  }, [result, saveName, overrides])

  // ── render ─────────────────────────────────────────────────────────────

  return (
    <main className="p-4 space-y-6 max-w-full">
      <div className="flex flex-wrap items-baseline gap-4">
        <h1 className="text-lg font-bold text-white">Create Preset</h1>
        <span className="text-gray-500 text-sm">
          Build and test a custom strategy configuration
        </span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[560px_1fr] gap-6 items-start">

        {/* Left column — settings editor */}
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-4">

          {/* Base preset picker */}
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-gray-500 uppercase tracking-wide font-semibold shrink-0">
              Base preset
            </p>
            <select
              value={baseName}
              onChange={e => handleBaseChange(e.target.value)}
              className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 focus:outline-none focus:border-gray-600 min-w-0 max-w-[220px] truncate"
            >
              <option value="">— start from defaults —</option>
              {sortedPresets.map(p => (
                <option key={p.preset} value={p.preset}>
                  {p.preset} ({p.total_profit_pct >= 0 ? '+' : ''}{p.total_profit_pct.toFixed(2)}%)
                </option>
              ))}
            </select>
          </div>

          {/* Active overrides count */}
          {Object.keys(overrides).length > 0 && (
            <p className="text-[10px] text-amber-400/80 font-mono">
              {Object.keys(overrides).length} setting{Object.keys(overrides).length !== 1 ? 's' : ''} differ from defaults
            </p>
          )}

          {/* Editable settings — key includes restoreKey so the panel remounts and
              re-reads panelBaseSettings whenever the base preset changes or best is restored */}
          <EditableSettingsPanel
            key={`${baseName}-${restoreKey}`}
            baseSettings={panelBaseSettings}
            onChange={handleOverridesChange}
          />

          {/* Action buttons */}
          <div className="pt-2 border-t border-gray-800 space-y-2">

            {/* Row 1: Check + Restore best */}
            <div className="flex gap-2">
              <button
                onClick={handleCheck}
                disabled={loading}
                className="flex-1 py-2 rounded border border-indigo-700 bg-indigo-900/60 text-indigo-300 text-sm font-semibold hover:bg-indigo-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? 'Running backtest…' : 'Check'}
              </button>

              <button
                onClick={handleRestoreBest}
                disabled={!canRestoreBest}
                title={bestResult
                  ? `Best result: ${bestResult.total_profit_pct >= 0 ? '+' : ''}${bestResult.total_profit_pct.toFixed(2)}%`
                  : 'No best result yet'}
                className="px-3 py-2 rounded border border-amber-700 bg-amber-900/40 text-amber-300 text-xs font-semibold hover:bg-amber-800/50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
              >
                ↩ Restore best value
              </button>
            </div>

            {/* Row 2: name input + Save */}
            <div className="flex gap-2">
              <input
                type="text"
                value={saveName}
                onChange={e => setSaveName(e.target.value)}
                placeholder="preset name…"
                disabled={!canSave || saveStatus !== 'idle'}
                className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-300 text-xs font-mono placeholder-gray-600 focus:outline-none focus:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed"
              />
              <button
                onClick={handleSave}
                disabled={!canSave || saveStatus !== 'idle' || !saveName.trim()}
                className="px-4 py-2 rounded border border-emerald-700 bg-emerald-900/40 text-emerald-300 text-xs font-semibold hover:bg-emerald-800/50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
              >
                {saveStatus === 'saving' ? 'Saving…' : saveStatus === 'saved' ? '✓ Saved' : 'Save'}
              </button>
            </div>

          </div>
        </div>

        {/* Right column — results */}
        <div className="space-y-4">
          {error && (
            <div className="rounded-lg border border-red-900/60 bg-red-950/30 px-4 py-3 text-sm text-red-400 font-mono">
              {error}
            </div>
          )}

          {!result && !loading && !error && (
            <div className="flex items-center justify-center h-48 rounded-lg border border-gray-800 bg-gray-900/30 text-gray-600 text-sm">
              Adjust settings and press Check to run the backtest.
            </div>
          )}

          {loading && (
            <div className="flex items-center justify-center h-48 rounded-lg border border-gray-800 bg-gray-900/30 text-gray-500 text-sm">
              Running backtest…
            </div>
          )}

          {result && (
            <>
              {/* Stats grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                  {
                    label: 'Actual P&L',
                    value: `${result.total_profit_pts >= 0 ? '+' : ''}${result.total_profit_pts.toFixed(1)} pts`,
                    sub: `${result.total_profit_pct >= 0 ? '+' : ''}${result.total_profit_pct.toFixed(2)}%`,
                    color: result.total_profit_pts >= 0 ? 'text-emerald-400' : 'text-red-400',
                  },
                  {
                    label: 'Win rate',
                    value: `${(result.win_rate * 100).toFixed(1)}%`,
                    sub: `${result.wins}W  ${result.partials}P  ${result.trails}T  ${result.losses}L`,
                    color: result.win_rate >= 0.5 ? 'text-emerald-400' : 'text-amber-400',
                  },
                  {
                    label: 'Avg RR',
                    value: result.avg_rr.toFixed(2),
                    sub: `${result.total_trades} trades`,
                    color: 'text-gray-300',
                  },
                  {
                    label: 'Avg TP reach',
                    value: `${result.avg_max_tp_reach_pct.toFixed(1)}%`,
                    sub: 'raise if > 80%',
                    color: result.avg_max_tp_reach_pct >= 80 ? 'text-amber-400' : 'text-gray-300',
                  },
                ].map(s => (
                  <div key={s.label} className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-3">
                    <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">{s.label}</p>
                    <p className={`text-lg font-bold font-mono ${s.color}`}>{s.value}</p>
                    <p className="text-[10px] text-gray-600 mt-0.5 font-mono">{s.sub}</p>
                  </div>
                ))}
              </div>

              {/* Chart panel */}
              <div className="rounded-lg border border-gray-800 bg-gray-900/50 p-3 space-y-3">
                {/* Panel header + compact stats */}
                <div className="flex items-center gap-3 flex-wrap">
                  <p className="text-[10px] text-gray-600 uppercase tracking-wide font-semibold shrink-0">
                    Price + orders
                  </p>
                  <div className="flex flex-wrap gap-3 text-[11px] font-mono">
                    {[
                      { label: 'Trades',  value: String(result.total_trades),                                                  color: 'text-gray-300' },
                      { label: 'W/P/T/L', value: `${result.wins}/${result.partials}/${result.trails}/${result.losses}`,        color: 'text-gray-300' },
                      { label: 'Win%',    value: `${(result.win_rate * 100).toFixed(1)}%`,                                     color: 'text-gray-300' },
                      { label: 'Profit',  value: `${result.total_profit_pct >= 0 ? '+' : ''}${result.total_profit_pct.toFixed(2)}%`, color: result.total_profit_pct >= 0 ? 'text-emerald-400' : 'text-red-400' },
                      { label: 'Avg RR',  value: result.avg_rr.toFixed(2),                                                     color: 'text-gray-300' },
                      { label: 'MaxDD',   value: String(result.max_consecutive_losses),                                        color: result.max_consecutive_losses >= 4 ? 'text-amber-400' : 'text-gray-300' },
                      { label: 'AvgTP%',  value: `${result.avg_max_tp_reach_pct.toFixed(1)}%`,                                 color: result.avg_max_tp_reach_pct >= 80 ? 'text-amber-400' : 'text-gray-300' },
                    ].map(s => (
                      <span key={s.label}>
                        <span className="text-gray-600">{s.label}: </span>
                        <span className={s.color}>{s.value}</span>
                      </span>
                    ))}
                  </div>
                </div>

                {/* Date controls */}
                <div className="flex items-center flex-wrap gap-2">
                  <button
                    className={NAV_BTN_CLS}
                    onClick={() => { setFromDate(addDays(fromDate, -1)); setToDate(addDays(toDate, -1)) }}
                  >
                    ← Back
                  </button>
                  <span className="text-[11px] text-gray-500 font-mono">From</span>
                  <input
                    type="datetime-local"
                    value={fromDate}
                    onChange={e => setFromDate(e.target.value)}
                    className={DATE_INPUT_CLS}
                  />
                  <span className="text-[11px] text-gray-500 font-mono">To</span>
                  <input
                    type="datetime-local"
                    value={toDate}
                    onChange={e => setToDate(e.target.value)}
                    className={DATE_INPUT_CLS}
                  />
                  <button
                    className={NAV_BTN_CLS}
                    onClick={() => { setFromDate(addDays(fromDate, 1)); setToDate(addDays(toDate, 1)) }}
                  >
                    Fwd →
                  </button>
                  <span className="text-[10px] text-gray-600 font-mono">
                    {chartKlines.length} candles · {chartTrades.length} trades
                  </span>
                </div>

                <PresetChart klines={chartKlines} trades={chartTrades} />
              </div>

              {/* Trades */}
              <div className="space-y-1">
                <p className="text-[10px] text-gray-600 uppercase tracking-wide font-semibold">
                  Orders ({result.total_trades})
                </p>
                <BacktestTradeList presetName="custom" trades={result.trades} />
              </div>
            </>
          )}
        </div>
      </div>
    </main>
  )
}
