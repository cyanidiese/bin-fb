'use client'

import { useState } from 'react'

interface BotState {
  running: boolean
  pid: number | null
  mode: string
  last_heartbeat: string | null
}

interface RegistryData {
  symbols: string[]
  updated_at: string
  status: Record<string, { backtest: string; pid: number | null }>
}

interface Props {
  mode: string
  onModeChanged: (mode: string) => void
  botState: BotState | null
  registry: RegistryData | null
  onRefetch: () => void
}

export default function TradingMode({ mode, onModeChanged, botState, registry, onRefetch }: Props) {
  const [switching, setSwitching] = useState(false)
  const [switchError, setSwitchError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [refreshProgress, setRefreshProgress] = useState('')

  async function handleSwitch() {
    const target = mode === 'test' ? 'live' : 'test'
    const botRunning = botState?.running ?? false
    const msg = botRunning
      ? target === 'live'
        ? 'Switch to LIVE mode? Real orders will be placed with real money.'
        : 'Switch to TEST mode? All open orders will be closed at market price.'
      : `Switch to ${target.toUpperCase()} mode?\n\nThe bot is not running — the mode preference will be saved and used on next start.\n\nRun "Refresh Backtests" afterward to load ${target}-mode kline data into the dashboard.`
    if (!confirm(msg)) return
    setSwitching(true)
    setSwitchError('')
    try {
      const r = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_mode: target }),
      })
      const data = await r.json()
      if (data.ok) onModeChanged(target)
      else setSwitchError(data.error || 'Mode switch failed')
    } catch (e) {
      setSwitchError(String(e))
    } finally {
      setSwitching(false)
    }
  }

  async function handleRefresh() {
    if (!registry || registry.symbols.length === 0) return
    if (!confirm(`Re-run backtests for all ${registry.symbols.length} symbols in ${mode.toUpperCase()} mode?\n\nThis fetches fresh klines from the exchange for the current mode.`)) return
    setRefreshing(true)
    setRefreshProgress('')
    const symbols = registry.symbols
    const BATCH = 4
    try {
      for (let i = 0; i < symbols.length; i += BATCH) {
        const batch = symbols.slice(i, i + BATCH)
        setRefreshProgress(`${Math.min(i + BATCH, symbols.length)}/${symbols.length}`)
        await Promise.allSettled(
          batch.map(sym =>
            fetch('/api/run-backtest', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ symbol: sym, klines_count: 1500 }),
            })
          )
        )
      }
      onRefetch()
    } finally {
      setRefreshing(false)
      setRefreshProgress('')
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
        Trading Mode
      </h2>
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className={`px-3 py-1 rounded text-sm font-mono font-bold ${mode === 'live' ? 'bg-amber-500 text-black' : 'bg-slate-600 text-white'}`}>
            {mode.toUpperCase()}
          </span>
          <button
            onClick={handleSwitch}
            disabled={switching}
            className="px-4 py-2 bg-slate-700 text-white text-sm rounded hover:bg-slate-600 disabled:opacity-50 transition-colors"
          >
            {switching ? 'Switching…' : `Switch to ${mode === 'test' ? 'LIVE' : 'TEST'}`}
          </button>
          <button
            onClick={handleRefresh}
            disabled={refreshing || !registry || registry.symbols.length === 0}
            title={`Re-run backtests for all symbols using ${mode.toUpperCase()}-mode klines. Use this after switching mode to load fresh data.`}
            className="px-4 py-2 bg-indigo-900/60 border border-indigo-700 text-indigo-300 text-sm rounded hover:bg-indigo-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {refreshing ? `Refreshing… ${refreshProgress}` : 'Refresh Backtests'}
          </button>
        </div>
        {!botState?.running && (
          <p className="text-xs text-gray-500">
            Bot is stopped — switching mode saves the preference for next start.
            Use <span className="text-indigo-400">Refresh Backtests</span> to pull {mode === 'live' ? 'live' : 'testnet'}-mode klines into the dashboard now.
          </p>
        )}
        {switchError && <p className="text-sm text-red-400">{switchError}</p>}
      </div>
    </section>
  )
}
