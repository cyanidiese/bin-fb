'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import type { CandidateResult, DiscoveryState, DiscoveryCandidatesFile } from '@/lib/types'

const LEVERAGES = [1, 2, 3, 5, 10, 15, 20, 25, 50, 75, 100, 125]
const POLL_MS = 3000

type SortKey = keyof Omit<CandidateResult, 'symbol' | 'best_preset_id'>
type SortDir = 'asc' | 'desc'

const DEFAULT_STATE: DiscoveryState = {
  status: 'idle',
  pid: null,
  total_precandidates: 0,
  processed_count: 0,
  in_progress: [],
  last_run_timestamp: null,
}

function pct(n: number) { return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%` }
function usd(n: number) { return `${n >= 0 ? '+' : '-'}$${Math.abs(n).toFixed(2)}` }

export default function SymbolDiscovery() {
  const [state, setState] = useState<DiscoveryState>(DEFAULT_STATE)
  const [candidates, setCandidates] = useState<CandidateResult[]>([])
  const [adding, setAdding] = useState<string | null>(null)
  const [addedSymbols, setAddedSymbols] = useState<Set<string>>(new Set())
  const [cancelling, setCancelling] = useState(false)
  const [controlsOpen, setControlsOpen] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('efficiency_score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  // Config state
  const [minVolume, setMinVolume]         = useState(1_000_000)
  const [presetCount, setPresetCount]     = useState(12)
  const [batchSize, setBatchSize]         = useState(3)
  const [baselineRatio, setBaselineRatio] = useState(0.7)
  const [minFloor, setMinFloor]           = useState(0.0)
  const [positionSize, setPositionSize]   = useState(1000)
  const [leverage, setLeverage]           = useState(1)
  const [klinesCount, setKlinesCount]     = useState(500)

  const fetchState = useCallback(async () => {
    try {
      const res = await fetch(`/discovery_state.json?t=${Date.now()}`)
      if (res.ok) setState(await res.json())
    } catch { /* keep last */ }
  }, [])

  const fetchCandidates = useCallback(async () => {
    try {
      const res = await fetch(`/discovery_candidates.json?t=${Date.now()}`)
      if (res.ok) {
        const data: DiscoveryCandidatesFile = await res.json()
        setCandidates(data.candidates ?? [])
      }
    } catch { /* keep last */ }
  }, [])

  useEffect(() => {
    fetchState()
    fetchCandidates()
    const id = setInterval(fetchState, POLL_MS)
    return () => clearInterval(id)
  }, [fetchState, fetchCandidates])

  // Reload candidates when a run completes
  useEffect(() => {
    if (state.status === 'complete') fetchCandidates()
  }, [state.status, fetchCandidates])

  async function handleRun() {
    setAddedSymbols(new Set())
    await fetch('/api/discovery/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        min_volume: minVolume, preset_count: presetCount, batch_size: batchSize,
        baseline_ratio: baselineRatio, min_floor: minFloor,
        position_size: positionSize, leverage, klines_count: klinesCount,
      }),
    })
    fetchState()
  }

  async function handleCancel() {
    setCancelling(true)
    try {
      await fetch('/api/discovery/cancel', { method: 'POST' })
      fetchState()
    } finally {
      setCancelling(false)
    }
  }

  async function handleAdd(symbol: string) {
    setAdding(symbol)
    try {
      const res = await fetch('/api/symbols', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      })
      if (res.ok) setAddedSymbols(s => new Set([...s, symbol]))
    } finally {
      setAdding(null)
    }
  }

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const visibleCandidates = useMemo(() => {
    const filtered = candidates.filter(c => !addedSymbols.has(c.symbol))
    return [...filtered].sort((a, b) => {
      const av = a[sortKey] as number
      const bv = b[sortKey] as number
      return sortDir === 'desc' ? bv - av : av - bv
    })
  }, [candidates, addedSymbols, sortKey, sortDir])

  const totalPotential = visibleCandidates.reduce((s, c) => s + c.potential_gain_usdt, 0)
  const bestPotential  = visibleCandidates.reduce((m, c) => Math.max(m, c.potential_gain_usdt), 0)

  const progress = state.total_precandidates > 0
    ? Math.round((state.processed_count / state.total_precandidates) * 100)
    : 0

  const running = state.status === 'running'

  function Th({ label, k, title }: { label: string; k: SortKey; title?: string }) {
    const active = k === sortKey
    const arrow = active ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ' ⇅'
    return (
      <th
        onClick={() => toggleSort(k)}
        title={title}
        className={`py-1 pr-3 font-normal cursor-pointer select-none whitespace-nowrap text-right transition-colors ${
          active ? 'text-indigo-300' : 'text-gray-500 hover:text-gray-300'
        }`}
      >
        {label}<span className={active ? 'text-indigo-400' : 'text-gray-700'}>{arrow}</span>
      </th>
    )
  }

  return (
    <div className="space-y-3">
      {/* ── Section header ── */}
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden">
        <button
          className="w-full px-4 py-2 border-b border-gray-800 flex items-center justify-between"
          onClick={() => setControlsOpen(o => !o)}
          title="Configure and run automatic symbol discovery"
        >
          <p className="text-xs text-gray-500 font-semibold uppercase tracking-wide">
            Symbol Discovery
          </p>
          <span className="text-gray-600 text-xs">{controlsOpen ? '▲' : '▼'}</span>
        </button>

        {/* Controls */}
        {controlsOpen && (
          <div className="px-4 py-4 space-y-4">
            <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs font-mono">
              {[
                { label: 'Min 24h volume (USDT)', val: minVolume,     set: setMinVolume,     step: 100000,
                  title: 'Symbols below this volume are considered illiquid and skipped' },
                { label: 'Fast preset count',     val: presetCount,   set: setPresetCount,   step: 1,
                  title: 'Number of top-performing presets used to evaluate each candidate' },
                { label: 'Batch size',            val: batchSize,     set: setBatchSize,     step: 1,
                  title: 'How many candidate symbols are backtested simultaneously' },
                { label: 'Baseline ratio (0–1)',  val: baselineRatio, set: setBaselineRatio, step: 0.05,
                  title: 'Candidate must score at least this fraction of the average active symbol efficiency' },
                { label: 'Min efficiency floor',  val: minFloor,      set: setMinFloor,      step: 0.01,
                  title: 'Hard minimum profit-per-order regardless of baseline (0 = disabled)' },
                { label: 'Klines count',          val: klinesCount,   set: setKlinesCount,   step: 100,
                  title: 'Number of recent candles used to backtest each candidate — fewer = faster' },
              ].map(({ label, val, set, step, title }) => (
                <label key={label} className="flex flex-col gap-1">
                  <span className="text-gray-500" title={title}>{label}</span>
                  <input
                    type="number"
                    step={step}
                    value={val}
                    onChange={e => (set as (v: number) => void)(Number(e.target.value))}
                    className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 focus:outline-none focus:border-indigo-500"
                  />
                </label>
              ))}

              {/* Margin */}
              <label className="flex flex-col gap-1">
                <span className="text-gray-500" title="Position size used to project potential gains">Margin (USDT)</span>
                <input type="number" step={100} value={positionSize}
                  onChange={e => setPositionSize(Number(e.target.value))}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 focus:outline-none focus:border-indigo-500"
                />
              </label>

              {/* Leverage */}
              <label className="flex flex-col gap-1">
                <span className="text-gray-500" title="Leverage multiplier used to project potential gains">Leverage</span>
                <select value={leverage} onChange={e => setLeverage(Number(e.target.value))}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 focus:outline-none focus:border-indigo-500"
                >
                  {LEVERAGES.map(l => <option key={l} value={l}>{l}×</option>)}
                </select>
              </label>
            </div>

            <button
              onClick={handleRun}
              disabled={running}
              title="Start automatic symbol discovery using the configured parameters"
              className="px-4 py-2 rounded border border-indigo-700 bg-indigo-900/60 text-indigo-300 text-xs font-semibold hover:bg-indigo-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {running ? 'Running…' : 'Discover Symbols'}
            </button>
          </div>
        )}
      </div>

      {/* Progress */}
      {(running || state.status === 'cancelled') && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-3 space-y-2 text-xs font-mono">
          <div className="flex items-center justify-between">
            <span className="text-gray-400">
              Processed: <span className="text-white">{state.processed_count}</span>
              {' / '}{state.total_precandidates} pre-candidates
            </span>
            {running && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                title="Stop the discovery run"
                className="px-2 py-0.5 rounded border border-red-900/60 bg-red-950/30 text-red-400 text-[10px] font-semibold hover:bg-red-900/40 disabled:opacity-40 transition-colors"
              >
                {cancelling ? 'Cancelling…' : 'Cancel'}
              </button>
            )}
          </div>
          <div className="w-full bg-gray-800 rounded-full h-1.5">
            <div
              className="bg-indigo-500 h-1.5 rounded-full transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          {state.in_progress.length > 0 && (
            <p className="text-gray-600">
              In progress: <span className="text-gray-400">{state.in_progress.join(', ')}</span>
            </p>
          )}
          {state.status === 'cancelled' && (
            <p className="text-amber-500/80">Run cancelled.</p>
          )}
        </div>
      )}

      {/* Error */}
      {state.status === 'error' && state.error && (
        <p className="text-xs text-red-400 font-mono px-1">{state.error}</p>
      )}

      {/* Results table */}
      {state.status === 'complete' && candidates.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-3 text-xs font-mono text-gray-500 px-1">
            <span>
              <span className="text-white">{visibleCandidates.length}</span> candidates found
            </span>
            <span>·</span>
            <span>Best single: <span className="text-emerald-400">{usd(bestPotential)}</span></span>
            <span>·</span>
            <span>Total potential: <span className="text-emerald-400">{usd(totalPotential)}</span></span>
          </div>

          <div className="overflow-x-auto rounded-lg border border-gray-800">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-gray-800 bg-gray-900">
                  <th className="py-1 pr-3 text-left font-normal text-gray-500 whitespace-nowrap pl-3">Symbol</th>
                  <Th label="Efficiency"   k="efficiency_score"    title="Total profit % / order count across fast presets" />
                  <Th label="Profit%"      k="total_net_profit"    title="Sum of total_profit_pct across fast presets" />
                  <Th label="Orders"       k="total_order_count"   title="Total orders across fast presets" />
                  <Th label="PF"           k="profit_factor"       title="Profit factor: sum gains / sum losses" />
                  <Th label="Win%"         k="win_rate"            title="Win + partial + trail rate" />
                  <Th label="MaxDD"        k="max_drawdown"        title="Max consecutive losses" />
                  <Th label="Sharpe"       k="sharpe_ratio"        title="Approximate Sharpe ratio from per-trade returns" />
                  <Th label="vs Base%"     k="vs_baseline_pct"     title="How much better/worse than active symbol average" />
                  <Th label="Potential $"  k="potential_gain_usdt" title="Projected gain at configured position size and leverage" />
                  <th className="py-1 pr-3 font-normal text-gray-500 text-left whitespace-nowrap">Best Preset</th>
                  <th className="py-1 pr-3" />
                </tr>
              </thead>
              <tbody>
                {visibleCandidates.map(c => (
                  <tr key={c.symbol} className="border-b border-gray-900 hover:bg-gray-900/40">
                    <td className="py-1 pr-3 pl-3 text-indigo-300 font-semibold">{c.symbol}</td>
                    <td className="text-right pr-3 text-gray-300">{c.efficiency_score.toFixed(4)}</td>
                    <td className={`text-right pr-3 font-semibold ${c.total_net_profit >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {pct(c.total_net_profit)}
                    </td>
                    <td className="text-right pr-3 text-gray-300">{c.total_order_count}</td>
                    <td className="text-right pr-3 text-gray-300">{c.profit_factor.toFixed(2)}</td>
                    <td className="text-right pr-3 text-gray-300">{(c.win_rate * 100).toFixed(1)}%</td>
                    <td className="text-right pr-3 text-gray-400">{c.max_drawdown}</td>
                    <td className="text-right pr-3 text-gray-300">{c.sharpe_ratio.toFixed(2)}</td>
                    <td className={`text-right pr-3 font-semibold ${c.vs_baseline_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {pct(c.vs_baseline_pct)}
                    </td>
                    <td className={`text-right pr-3 font-semibold ${c.potential_gain_usdt >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {usd(c.potential_gain_usdt)}
                    </td>
                    <td className="py-1 pr-3 text-gray-400 max-w-[120px] truncate">{c.best_preset_id}</td>
                    <td className="py-1 pr-3">
                      <button
                        onClick={() => handleAdd(c.symbol)}
                        disabled={adding === c.symbol}
                        title={`Add ${c.symbol} to active symbols and start a full backtest`}
                        className="px-2 py-0.5 rounded border border-indigo-700/60 bg-indigo-950/40 text-indigo-400 text-[10px] font-semibold hover:bg-indigo-900/40 disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
                      >
                        {adding === c.symbol ? '…' : 'Add'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {state.status === 'complete' && candidates.length === 0 && (
        <p className="text-xs text-gray-600 italic px-1">
          No candidates passed the filters. Try lowering the baseline ratio or volume threshold.
        </p>
      )}
    </div>
  )
}
