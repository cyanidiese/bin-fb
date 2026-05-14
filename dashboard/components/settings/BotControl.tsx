'use client'

import { useState } from 'react'

// Startup takes 30-60s (obligatory backtest). We hold the "Starting…" label
// for up to this many ms after the API call succeeds so the UI doesn't
// flicker back to "Start Bot" while the bot is still initialising.
const STARTING_HOLD_MS = 120_000

interface BotState {
  running: boolean
  pid: number | null
  mode: string
  last_heartbeat: string | null
}

interface Props {
  botState: BotState | null
  onAction: () => void
}

export default function BotControl({ botState, onAction }: Props) {
  const [loading, setLoading] = useState(false)
  const [startingUntil, setStartingUntil] = useState(0)
  const [error, setError] = useState('')

  const isStarting = !botState?.running && Date.now() < startingUntil

  async function handleStart() {
    setLoading(true)
    setError('')
    try {
      const r = await fetch('/api/bot/start', { method: 'POST' })
      const d = await r.json()
      if (!d.ok) {
        setError(d.error || 'Failed to start bot')
      } else {
        setStartingUntil(Date.now() + STARTING_HOLD_MS)
        onAction()
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  async function handleStop() {
    if (!window.confirm('Stop the bot? All open orders will be closed at market price.')) return
    setLoading(true)
    setStartingUntil(0)
    setError('')
    try {
      const r = await fetch('/api/bot/stop', { method: 'POST' })
      const d = await r.json()
      if (!d.ok) setError(d.error || 'Failed to stop bot')
      else onAction()
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
        Bot Control
      </h2>
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4">
        <div className="flex items-center gap-4">
          {botState?.running ? (
            <button
              onClick={handleStop}
              disabled={loading}
              className="px-4 py-2 bg-red-600 text-white text-sm font-semibold rounded hover:bg-red-700 disabled:opacity-50 transition-colors"
            >
              {loading ? 'Stopping…' : 'Stop Bot'}
            </button>
          ) : (
            <button
              onClick={handleStart}
              disabled={loading || isStarting}
              className="px-4 py-2 bg-green-600 text-white text-sm font-semibold rounded hover:bg-green-700 disabled:opacity-50 transition-colors"
            >
              {loading || isStarting ? 'Starting…' : 'Start Bot'}
            </button>
          )}
          <span className="text-sm text-gray-400">
            {botState === null
              ? 'Status unknown'
              : botState.running
                ? `Running · ${botState.mode?.toUpperCase() ?? ''} mode`
                : isStarting
                  ? 'Starting up — running backtests…'
                  : 'Stopped'}
          </span>
        </div>
        {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
      </div>
    </section>
  )
}
