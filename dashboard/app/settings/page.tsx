'use client'

import { useState, useEffect, useCallback } from 'react'
import SymbolDiscovery from '@/components/SymbolDiscovery'
import BotControl from '@/components/settings/BotControl'
import TradingMode from '@/components/settings/TradingMode'
import SymbolRegistry from '@/components/settings/SymbolRegistry'
import TelegramSettings from '@/components/settings/TelegramSettings'
import UIPreview from '@/components/settings/UIPreview'
import { useLocalStorage } from '@/lib/useLocalStorage'

interface SymbolStatus {
  backtest: 'none' | 'running' | 'complete' | 'error' | 'cancelled'
  pid: number | null
}

interface RegistryData {
  symbols: string[]
  updated_at: string
  status: Record<string, SymbolStatus>
  disabled?: Record<string, { reason: string; disabled_at: string }>
}

interface BotState {
  running: boolean
  phase: 'starting' | 'running' | null
  pid: number | null
  mode: string
  last_heartbeat: string | null
}

const POLL_MS = 3000

export default function SettingsPage() {
  const [registry, setRegistry] = useState<RegistryData | null>(null)
  const [botState, setBotState] = useState<BotState | null>(null)
  const [mode, setMode] = useState<string>('test')
  const [candleView,  setCandleView]  = useLocalStorage<boolean>('db:chart:candleView',  false)
  const [clampSpikes, setClampSpikes] = useLocalStorage<boolean>('db:chart:clampSpikes', false)

  useEffect(() => {
    fetch('/api/mode').then(r => r.json()).then(d => setMode(d.mode)).catch(() => {})
  }, [])

  const fetchRegistry = useCallback(async () => {
    try {
      const res = await fetch('/api/symbols')
      if (res.ok) setRegistry(await res.json())
    } catch { /* keep last state */ }
  }, [])

  useEffect(() => {
    fetchRegistry()
    const id = setInterval(fetchRegistry, POLL_MS)
    return () => clearInterval(id)
  }, [fetchRegistry])

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`/api/public-file?f=bot_state.json`)
        if (!r.ok) { setBotState(null); return }
        const data = await r.json()
        // Only apply staleness when phase='running'; during 'starting' the
        // backtest subprocess blocks all heartbeat writes for up to ~60s.
        const isStale = data.phase === 'running' && data.last_heartbeat
          ? (Date.now() - new Date(data.last_heartbeat).getTime()) > 30_000
          : false
        setBotState({ ...data, running: data.running && !isStale })
      } catch {
        setBotState(null)
      }
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [])

  return (
    <main className="p-4">
      <h1 className="text-lg font-bold text-white mb-6">Settings</h1>

      <div className="grid grid-cols-1 min-[480px]:grid-cols-2 lg:grid-cols-3 gap-6 items-start">

        {/* Column 1: Symbol-related */}
        <div className="space-y-6">
          <SymbolRegistry registry={registry} onRefetch={fetchRegistry} />
          <SymbolDiscovery />
        </div>

        {/* Column 2: Bot */}
        <div className="space-y-6">
          <BotControl botState={botState} onAction={fetchRegistry} />
          <TradingMode
            mode={mode}
            onModeChanged={setMode}
            botState={botState}
            registry={registry}
            onRefetch={fetchRegistry}
          />
        </div>

        {/* Column 3: UI & display */}
        <div className="space-y-6">
          <TelegramSettings />
          <UIPreview />
          <section className="space-y-3">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
              Strategy Chart
            </h2>
            <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={candleView}
                  onChange={e => setCandleView(e.target.checked)}
                  className="rounded accent-indigo-500"
                />
                <span className="text-sm text-gray-300">Candlestick chart view</span>
              </label>
              <p className="text-xs text-gray-600">
                When enabled, charts display OHLC candlesticks instead of a close-price line.
              </p>
              <hr className="border-gray-800" />
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={clampSpikes}
                  onChange={e => setClampSpikes(e.target.checked)}
                  className="rounded accent-indigo-500"
                />
                <span className="text-sm text-gray-300">Clamp spike wicks</span>
              </label>
              <p className="text-xs text-gray-600">
                Repositions swing point dots on spike candles (wick &gt;5× avg of previous 10) to the clamped height. Candles themselves are unchanged.
              </p>
            </div>
          </section>
        </div>

      </div>
    </main>
  )
}
