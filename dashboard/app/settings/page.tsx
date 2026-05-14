'use client'

import { useState, useEffect, useCallback } from 'react'
import SymbolDiscovery from '@/components/SymbolDiscovery'
import BotControl from '@/components/settings/BotControl'
import TradingMode from '@/components/settings/TradingMode'
import SymbolRegistry from '@/components/settings/SymbolRegistry'
import TelegramSettings from '@/components/settings/TelegramSettings'
import UIPreview from '@/components/settings/UIPreview'

interface SymbolStatus {
  backtest: 'none' | 'running' | 'complete' | 'error' | 'cancelled'
  pid: number | null
}

interface RegistryData {
  symbols: string[]
  updated_at: string
  status: Record<string, SymbolStatus>
}

interface BotState {
  running: boolean
  pid: number | null
  mode: string
  last_heartbeat: string | null
}

const POLL_MS = 3000

export default function SettingsPage() {
  const [registry, setRegistry] = useState<RegistryData | null>(null)
  const [botState, setBotState] = useState<BotState | null>(null)
  const [mode, setMode] = useState<string>('test')

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
        const r = await fetch(`/bot_state.json?t=${Date.now()}`)
        if (!r.ok) { setBotState(null); return }
        const data = await r.json()
        const isStale = data.last_heartbeat
          ? (Date.now() - new Date(data.last_heartbeat).getTime()) > 30_000
          : true
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
        <div>
          <SymbolRegistry registry={registry} onRefetch={fetchRegistry} />
        </div>

        {/* Column 2: Symbol Discovery */}
        <div>
          <SymbolDiscovery />
        </div>

        {/* Column 3: Other */}
        <div className="space-y-6">
          <BotControl botState={botState} onAction={fetchRegistry} />
          <TradingMode
            mode={mode}
            onModeChanged={setMode}
            botState={botState}
            registry={registry}
            onRefetch={fetchRegistry}
          />
          <TelegramSettings />
          <UIPreview />
        </div>

      </div>
    </main>
  )
}
