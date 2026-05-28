'use client'

import { useState, useEffect, useMemo, useCallback } from 'react'
import type { BacktestResults, BacktestPreset, BacktestApiResponse, BacktestTrade } from '@/lib/types'
import { SETTINGS_META } from '@/components/PresetSettingsPanel'
import EditableSettingsPanel from '@/components/EditableSettingsPanel'
import PresetChart from '@/components/PresetChart'
import BacktestTradeList from '@/components/BacktestTradeList'
import { useSymbolContext } from '@/lib/SymbolContext'

// ── helpers ────────────────────────────────────────────────────────────────

function toDateStr(unixSec: number): string {
  const d = new Date(unixSec * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function addDays(dateStr: string, days: number): string {
  if (!dateStr) return dateStr
  const d = new Date(dateStr)
  d.setDate(d.getDate() + days)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

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
  trail_activation_pct: 'tact',
  trail_min_distance_pct: 'tmin',
  min_precision_score: 'mprec',
  range_position_max: 'rpos',
  zone_sl_max: 'zslm',
  zone_sl_cooldown_candles: 'zslc',
  loss_streak_max: 'ls',
  loss_streak_cooldown_candles: 'lsc',
  global_pause_trigger_candles: 'gpt',
  global_pause_candles: 'gp',
  duplicate_skip_candles: 'dsc',
  duplicate_skip_pct: 'dsp',
  max_losing_pct: 'mlp',
  max_losing_amount_usdt: 'mla',
  max_losing_candles: 'mlc',
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

function avgProfit(results: Record<string, BacktestApiResponse | null>): number | null {
  const vals = Object.values(results).filter((r): r is BacktestApiResponse => r !== null)
  if (vals.length === 0) return null
  return vals.reduce((s, r) => s + r.total_profit_pct, 0) / vals.length
}

// ── style constants ────────────────────────────────────────────────────────

const DATE_INPUT_CLS =
  'bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-300 text-[11px] font-mono focus:outline-none focus:border-gray-500'
const NAV_BTN_CLS =
  'px-2 py-0.5 rounded border border-gray-700 bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700 text-[11px] font-mono transition-colors'

// ── page ───────────────────────────────────────────────────────────────────

export default function CreatePresetPage() {
  const { symbol, availableSymbols } = useSymbolContext()

  const [backtestData, setBacktestData] = useState<BacktestResults | null>(null)

  const [baseName, setBaseName] = useState<string>('')
  const [overrides, setOverrides] = useState<Record<string, number | boolean>>({})
  const [settingsTouched, setSettingsTouched] = useState(false)
  const [panelBaseSettings, setPanelBaseSettings] = useState<Record<string, number | boolean>>({})
  const [restoreKey, setRestoreKey] = useState(0)

  // Per-symbol results
  const [results, setResults] = useState<Record<string, BacktestApiResponse | null>>({})

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Best run tracking (keyed by avg profit across all symbols)
  const [bestResults, setBestResults] = useState<Record<string, BacktestApiResponse | null>>({})
  const [bestOverrides, setBestOverrides] = useState<Record<string, number | boolean> | null>(null)
  const [bestAvgProfit, setBestAvgProfit] = useState<number | null>(null)

  // Chart date range — scoped to active tab
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')

  const [hoveredTradeIdx, setHoveredTradeIdx] = useState<number | null>(null)

  // Test scope — all symbols or only the currently selected one
  const [testAllSymbols, setTestAllSymbols] = useState(true)

  // Save state
  const [saveName, setSaveName] = useState('')
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle')

  // Reload preset list whenever the selected symbol changes
  useEffect(() => {
    fetch(`/api/public-file?f=backtest_results_${symbol}.json`)
      .then(r => r.ok ? r.json() : null)
      .then((d: BacktestResults | null) => { if (d) setBacktestData(d) })
      .catch(() => null)
  }, [symbol])

  // Reset chart date range and hover when the active symbol's result changes
  const activeResult = results[symbol] ?? null
  useEffect(() => {
    if (!activeResult?.klines.length) return
    setFromDate(toDateStr(activeResult.klines[0].time))
    setToDate(toDateStr(activeResult.klines[activeResult.klines.length - 1].time))
    setHoveredTradeIdx(null)
  }, [activeResult])

  // ── derived ────────────────────────────────────────────────────────────

  const sortedPresets: BacktestPreset[] = useMemo(() => {
    if (!backtestData) return []
    return Object.values(backtestData.presets).sort((a, b) => b.total_profit_pct - a.total_profit_pct)
  }, [backtestData])

  const hasAnyResult = Object.values(results).some(r => r !== null)

  const filteredKlines = useMemo(() => {
    if (!activeResult || !fromDate || !toDate) return activeResult?.klines ?? []
    return activeResult.klines.filter(k => {
      const d = toDateStr(k.time)
      return d >= fromDate && d <= toDate
    })
  }, [activeResult, fromDate, toDate])

  const chartKlines = useMemo(
    () => filteredKlines.map((k, i) => ({ ...k, index: i })),
    [filteredKlines],
  )

  const { chartTrades, chartTradeOriginalIdxs } = useMemo(() => {
    const all = activeResult?.trades ?? []
    if (!activeResult || !filteredKlines.length) {
      return { chartTrades: all, chartTradeOriginalIdxs: all.map((_, i) => i) }
    }
    const minIdx = filteredKlines[0].index
    const maxIdx = filteredKlines[filteredKlines.length - 1].index
    const trades: BacktestTrade[] = []
    const idxs: number[] = []
    activeResult.trades.forEach((t, i) => {
      if (t.open_candle <= maxIdx && (t.close_candle ?? t.open_candle + 1) >= minIdx) {
        trades.push({
          ...t,
          open_candle: Math.max(0, t.open_candle - minIdx),
          close_candle: t.close_candle != null
            ? Math.min(chartKlines.length - 1, Math.max(0, t.close_candle - minIdx))
            : null,
        })
        idxs.push(i)
      }
    })
    return { chartTrades: trades, chartTradeOriginalIdxs: idxs }
  }, [activeResult, filteredKlines, chartKlines])

  // Original trade objects at the filtered positions — used by the trade list
  // so hover indices stay in sync with the chart (both iterate the same subset).
  const filteredTradeList = useMemo(
    () => chartTradeOriginalIdxs.map(i => (activeResult?.trades ?? [])[i]),
    [chartTradeOriginalIdxs, activeResult],
  )

  const currentAvg = avgProfit(results)
  const canRestoreBest = bestAvgProfit !== null && (currentAvg === null || currentAvg < bestAvgProfit)
  const canSave = hasAnyResult && settingsTouched

  // ── handlers ───────────────────────────────────────────────────────────

  const handleBaseChange = (name: string) => {
    setBaseName(name)
    setResults({})
    setError(null)
    setBestResults({})
    setBestOverrides(null)
    setBestAvgProfit(null)
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
    setResults({})
    setSaveStatus('idle')

    const symbols = testAllSymbols
      ? (availableSymbols.length > 0 ? availableSymbols : ['BTCUSDT'])
      : [symbol]

    try {
      const entries = await Promise.all(
        symbols.map(sym =>
          fetch('/api/backtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: overrides, symbol: sym }),
          })
            .then(async r => {
              const data = await r.json()
              if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`)
              return [sym, data as BacktestApiResponse] as const
            })
            .catch(e => [sym, null] as const)
        )
      )

      const newResults = Object.fromEntries(entries)
      setResults(newResults)
      setSaveName(generatePresetName(overrides))

      // Track best by avg profit across all symbols
      const newAvg = avgProfit(newResults)
      if (newAvg !== null && (bestAvgProfit === null || newAvg > bestAvgProfit)) {
        setBestResults(newResults)
        setBestOverrides({ ...overrides })
        setBestAvgProfit(newAvg)
      }

      const errors = entries.filter(([, r]) => r === null).map(([s]) => s)
      if (errors.length > 0) {
        setError(`Failed for: ${errors.join(', ')}`)
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [overrides, availableSymbols, symbol, testAllSymbols, bestAvgProfit])

  const handleRestoreBest = useCallback(() => {
    if (!bestOverrides || Object.keys(bestResults).length === 0) return
    setOverrides(bestOverrides)
    setResults(bestResults)
    setSaveName(generatePresetName(bestOverrides))
    setSettingsTouched(true)
    setSaveStatus('idle')
    setPanelBaseSettings(bestOverrides)
    setRestoreKey(k => k + 1)
  }, [bestOverrides, bestResults])

  const handleSave = useCallback(async () => {
    // Save uses the active tab's result (or first available)
    const saveResult = activeResult ?? Object.values(results).find(r => r !== null) ?? null
    if (!saveResult || !saveName.trim()) return
    setSaveStatus('saving')
    try {
      const res = await fetch('/api/save-preset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: saveName.trim(), result: saveResult, settings: overrides }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error)
      setSaveStatus('saved')
      fetch(`/api/public-file?f=backtest_results_${symbol}.json`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) setBacktestData(d) })
        .catch(() => null)
    } catch (e) {
      setError(`Save failed: ${String(e)}`)
      setSaveStatus('idle')
    }
  }, [activeResult, results, saveName, overrides, availableSymbols])

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

          {Object.keys(overrides).length > 0 && (
            <p className="text-[10px] text-amber-400/80 font-mono">
              {Object.keys(overrides).length} setting{Object.keys(overrides).length !== 1 ? 's' : ''} differ from defaults
            </p>
          )}

          <EditableSettingsPanel
            key={`${baseName}-${restoreKey}`}
            baseSettings={panelBaseSettings}
            onChange={handleOverridesChange}
          />

          <div className="pt-2 border-t border-gray-800 space-y-2">
            <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={testAllSymbols}
                onChange={e => setTestAllSymbols(e.target.checked)}
                className="w-3 h-3 accent-indigo-500"
              />
              {testAllSymbols
                ? `Test all ${availableSymbols.length} symbols`
                : `Test selected symbol only (${symbol})`}
            </label>
            <div className="flex gap-2">
              <button
                onClick={handleCheck}
                disabled={loading}
                className="flex-1 py-2 rounded border border-indigo-700 bg-indigo-900/60 text-indigo-300 text-sm font-semibold hover:bg-indigo-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? 'Running…' : 'Check'}
              </button>

              <button
                onClick={handleRestoreBest}
                disabled={!canRestoreBest}
                title={bestAvgProfit !== null
                  ? `Best avg: ${bestAvgProfit >= 0 ? '+' : ''}${bestAvgProfit.toFixed(2)}%`
                  : 'No best result yet'}
                className="px-3 py-2 rounded border border-amber-700 bg-amber-900/40 text-amber-300 text-xs font-semibold hover:bg-amber-800/50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
              >
                ↩ Restore best
              </button>
            </div>

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

          {!hasAnyResult && !loading && !error && (
            <div className="flex items-center justify-center h-48 rounded-lg border border-gray-800 bg-gray-900/30 text-gray-600 text-sm">
              Adjust settings and press Check to run the backtest.
            </div>
          )}

          {loading && (
            <div className="flex items-center justify-center h-48 rounded-lg border border-gray-800 bg-gray-900/30 text-gray-500 text-sm gap-2">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
              </svg>
              {testAllSymbols
                ? `Running across ${availableSymbols.length} symbol${availableSymbols.length !== 1 ? 's' : ''}…`
                : `Running for ${symbol}…`}
            </div>
          )}

          {hasAnyResult && (
            <>
              {/* ── Stats table — all symbols always visible ── */}
              <div className="rounded-lg border border-gray-800 bg-gray-900/50 overflow-x-auto">
                <table className="w-full text-xs font-mono">
                  <thead>
                    <tr className="border-b border-gray-800 text-gray-500">
                      <th className="text-left px-3 py-2 font-normal">Symbol</th>
                      <th className="text-right px-3 py-2 font-normal">Profit%</th>
                      <th className="text-right px-3 py-2 font-normal">Win%</th>
                      <th className="text-right px-3 py-2 font-normal">Trades</th>
                      <th className="text-right px-3 py-2 font-normal">W/P/T/L</th>
                      <th className="text-right px-3 py-2 font-normal">Avg RR</th>
                      <th className="text-right px-3 py-2 font-normal">MaxDD</th>
                      <th className="text-right px-3 py-2 font-normal">AvgTP%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {availableSymbols.map(sym => {
                      const r = results[sym]
                      if (!r) return (
                        <tr key={sym} className="border-b border-gray-900">
                          <td className="px-3 py-2 text-indigo-300 font-semibold">{sym}</td>
                          <td colSpan={7} className="px-3 py-2 text-gray-600 italic">no data</td>
                        </tr>
                      )
                      const profitColor = r.total_profit_pct >= 0 ? 'text-emerald-400' : 'text-red-400'
                      const winColor = r.win_rate >= 0.5 ? 'text-emerald-400' : 'text-amber-400'
                      return (
                        <tr key={sym} className="border-b border-gray-900 hover:bg-gray-900/40">
                          <td className="px-3 py-2 text-indigo-300 font-semibold">{sym}</td>
                          <td className={`text-right px-3 py-2 font-semibold ${profitColor}`}>
                            {r.total_profit_pct >= 0 ? '+' : ''}{r.total_profit_pct.toFixed(2)}%
                          </td>
                          <td className={`text-right px-3 py-2 ${winColor}`}>
                            {(r.win_rate * 100).toFixed(1)}%
                          </td>
                          <td className="text-right px-3 py-2 text-gray-300">{r.total_trades}</td>
                          <td className="text-right px-3 py-2 text-gray-400">
                            {r.wins}/{r.partials}/{r.trails}/{r.losses}
                          </td>
                          <td className="text-right px-3 py-2 text-gray-300">{r.avg_rr.toFixed(2)}</td>
                          <td className={`text-right px-3 py-2 ${r.max_consecutive_losses >= 4 ? 'text-amber-400' : 'text-gray-300'}`}>
                            {r.max_consecutive_losses}
                          </td>
                          <td className={`text-right px-3 py-2 ${r.avg_max_tp_reach_pct >= 80 ? 'text-amber-400' : 'text-gray-300'}`}>
                            {r.avg_max_tp_reach_pct.toFixed(1)}%
                          </td>
                        </tr>
                      )
                    })}
                    {/* Avg row when multiple symbols loaded */}
                    {availableSymbols.length > 1 && currentAvg !== null && (
                      <tr className="bg-gray-900/60">
                        <td className="px-3 py-2 text-gray-500 text-[10px] uppercase tracking-wide">avg</td>
                        <td className={`text-right px-3 py-2 font-semibold ${currentAvg >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {currentAvg >= 0 ? '+' : ''}{currentAvg.toFixed(2)}%
                        </td>
                        <td colSpan={6} />
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              {/* ── Chart + orders for the selected symbol ── */}
              <div className="rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden">
                {activeResult && (
                  <div className="p-3 space-y-4">
                    {/* Date controls */}
                    <div className="flex items-center flex-wrap gap-2">
                      <button className={NAV_BTN_CLS}
                        onClick={() => { setFromDate(addDays(fromDate, -1)); setToDate(addDays(toDate, -1)) }}>
                        ← Back
                      </button>
                      <span className="text-[11px] text-gray-500 font-mono">From</span>
                      <input type="datetime-local" value={fromDate}
                        onChange={e => setFromDate(e.target.value)} className={DATE_INPUT_CLS} />
                      <span className="text-[11px] text-gray-500 font-mono">To</span>
                      <input type="datetime-local" value={toDate}
                        onChange={e => setToDate(e.target.value)} className={DATE_INPUT_CLS} />
                      <button className={NAV_BTN_CLS}
                        onClick={() => { setFromDate(addDays(fromDate, 1)); setToDate(addDays(toDate, 1)) }}>
                        Fwd →
                      </button>
                      <span className="text-[10px] text-gray-600 font-mono">
                        {chartKlines.length} candles · {chartTrades.length} trades
                      </span>
                    </div>

                    <PresetChart
                      klines={chartKlines}
                      trades={chartTrades}
                      originalIdxs={chartTradeOriginalIdxs}
                      hoveredTradeIdx={hoveredTradeIdx}
                      onTradeHover={setHoveredTradeIdx}
                    />

                    <div className="space-y-1">
                      <p className="text-[10px] text-gray-600 uppercase tracking-wide font-semibold">
                        Orders ({filteredTradeList.length} of {activeResult.total_trades})
                      </p>
                      <BacktestTradeList
                        presetName="custom"
                        trades={filteredTradeList}
                        hoveredIdx={hoveredTradeIdx}
                        onHover={setHoveredTradeIdx}
                      />
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </main>
  )
}
