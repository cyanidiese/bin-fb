'use client'

import { useState, useEffect, useRef } from 'react'
import type { BacktestPreset, BacktestApiResponse } from '@/lib/types'
import PresetChart from './PresetChart'
import { useLocalStorage } from '@/lib/useLocalStorage'

interface Props {
  preset: BacktestPreset | null
}

function toDateStr(unixSec: number): string {
  return new Date(unixSec * 1000).toISOString().slice(0, 16)
}

function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr + ':00Z')
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 16)
}

const DATE_INPUT_CLS =
  'bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-300 text-[11px] font-mono focus:outline-none focus:border-gray-500'

const NAV_BTN_CLS =
  'px-2 py-0.5 rounded border border-gray-700 bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700 text-[11px] font-mono transition-colors'

export default function PresetResultsPanel({ preset }: Props) {
  const [open, setOpen] = useLocalStorage<boolean>('db:visualize:open', false)
  const [result, setResult] = useState<BacktestApiResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const [fromDate, setFromDate] = useLocalStorage<string>('db:visualize:fromDate', '')
  const [toDate, setToDate] = useLocalStorage<string>('db:visualize:toDate', '')

  useEffect(() => {
    if (!open || !preset) {
      setResult(null)
      setError(null)
      return
    }

    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl

    setLoading(true)
    setResult(null)
    setError(null)

    fetch('/api/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings: preset.settings }),
      signal: ctrl.signal,
    })
      .then(res => res.json().then(data => ({ ok: res.ok, data })))
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.error ?? 'Request failed')
        setResult(data as BacktestApiResponse)
      })
      .catch(e => { if (e.name !== 'AbortError') setError(String(e)) })
      .finally(() => setLoading(false))

    return () => ctrl.abort()
  }, [open, preset?.preset])

  // Initialise date range from result, but only if the user has no stored value
  useEffect(() => {
    if (!result?.klines.length) return
    setFromDate(prev => prev || toDateStr(result.klines[0].time))
    setToDate(prev => prev || toDateStr(result.klines[result.klines.length - 1].time))
  }, [result])

  const filteredKlines = result
    ? result.klines.filter(k => {
        const d = toDateStr(k.time)
        return d >= fromDate && d <= toDate
      })
    : []

  const minIdx = filteredKlines[0]?.index ?? 0
  const maxIdx = filteredKlines[filteredKlines.length - 1]?.index ?? -1

  // Re-index from 0 so the chart always fills its full width
  const displayKlines = filteredKlines.map((k, i) => ({ ...k, index: i }))

  const filteredTrades = result
    ? result.trades.filter(t =>
        t.open_candle <= maxIdx && (t.close_candle ?? t.open_candle + 1) >= minIdx
      )
    : []

  // Shift trade candle indices to match re-indexed klines
  const displayTrades = filteredTrades.map(t => ({
    ...t,
    open_candle: Math.max(0, t.open_candle - minIdx),
    close_candle: t.close_candle != null
      ? Math.min(displayKlines.length - 1, Math.max(0, t.close_candle - minIdx))
      : null,
  }))

  const klineMinDate = result?.klines.length ? toDateStr(result.klines[0].time) : ''
  const klineMaxDate = result?.klines.length ? toDateStr(result.klines[result.klines.length - 1].time) : ''

  const stats = result
    ? [
        { label: 'Trades',  value: String(result.total_trades),                                                  color: 'text-gray-300' },
        { label: 'W/P/T/L', value: `${result.wins}/${result.partials}/${result.trails}/${result.losses}`,        color: 'text-gray-300' },
        { label: 'Win%',    value: `${(result.win_rate * 100).toFixed(1)}%`,                                     color: 'text-gray-300' },
        { label: 'Profit',  value: `${result.total_profit_pct >= 0 ? '+' : ''}${result.total_profit_pct.toFixed(2)}%`, color: result.total_profit_pct >= 0 ? 'text-emerald-400' : 'text-red-400' },
        { label: 'Avg RR',  value: result.avg_rr.toFixed(2),                                                     color: 'text-gray-300' },
        { label: 'MaxDD',   value: String(result.max_consecutive_losses),                                        color: result.max_consecutive_losses >= 4 ? 'text-amber-400' : 'text-gray-300' },
        { label: 'AvgTP%',  value: `${result.avg_max_tp_reach_pct.toFixed(1)}%`,                                 color: result.avg_max_tp_reach_pct >= 80 ? 'text-amber-400' : 'text-gray-300' },
      ]
    : []

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/50">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3">
        <p className="text-xs text-gray-500 uppercase tracking-wide font-semibold shrink-0">
          Visualize preset
        </p>
        {open && (
          preset
            ? <span className="text-xs font-mono text-gray-400 truncate">{preset.preset}</span>
            : <span className="text-xs text-gray-600 italic">Select a preset in the table above</span>
        )}
        {open && loading && (
          <span className="text-[10px] text-gray-600 italic ml-1">loading…</span>
        )}
        <button
          onClick={() => setOpen(o => !o)}
          className="ml-auto text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
        >
          {open ? '▲ Hide' : '▼ Show'}
        </button>
      </div>

      {open && (
        <div className="px-4 pb-4 border-t border-gray-800">
          {error && (
            <p className="text-xs text-red-400 font-mono mt-3">{error}</p>
          )}
          {!preset && !loading && (
            <p className="text-xs text-gray-600 italic mt-3">
              Click a row in the presets table to visualize it.
            </p>
          )}
          {result && (
            <div className="mt-3 space-y-3">
              {/* Stats line */}
              <div className="flex flex-wrap gap-4 text-xs font-mono">
                {stats.map(s => (
                  <div key={s.label}>
                    <span className="text-gray-600">{s.label}: </span>
                    <span className={s.color}>{s.value}</span>
                  </div>
                ))}
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
                  min={klineMinDate}
                  max={klineMaxDate}
                  onChange={e => setFromDate(e.target.value)}
                  className={DATE_INPUT_CLS}
                />
                <span className="text-[11px] text-gray-500 font-mono">To</span>
                <input
                  type="datetime-local"
                  value={toDate}
                  min={klineMinDate}
                  max={klineMaxDate}
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
                  {filteredKlines.length} candles · {filteredTrades.length} trades
                </span>
              </div>

              {/* Chart */}
              <PresetChart klines={displayKlines} trades={displayTrades} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
