'use client'

import { useState, useEffect } from 'react'

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
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState('')
  const [clearingHistory, setClearingHistory] = useState(false)
  const [clearMsg, setClearMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const isStarting = !botState?.running && Date.now() < startingUntil
  const isStopping = stopping

  // Clear stopping flag once the bot process actually exits
  useEffect(() => {
    if (stopping && botState !== null && !botState.running) {
      setStopping(false)
    }
  }, [stopping, botState])

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
      if (!d.ok) {
        setError(d.error || 'Failed to stop bot')
      } else {
        setStopping(true)
        onAction()
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  async function handleClearHistory() {
    if (!window.confirm('Archive all trade history for all symbols? This cannot be undone.')) return
    setClearingHistory(true)
    setClearMsg(null)
    try {
      const r = await fetch('/api/bot/clear-history', { method: 'POST' })
      const d = await r.json()
      if (d.ok) {
        setClearMsg({ ok: true, text: `Cleared — ${d.archived?.length ?? 0} file(s) archived` })
      } else {
        setClearMsg({ ok: false, text: d.error ?? 'Failed' })
      }
    } catch (e) {
      setClearMsg({ ok: false, text: String(e) })
    } finally {
      setClearingHistory(false)
      setTimeout(() => setClearMsg(null), 4000)
    }
  }

  const showStopButton = botState?.running || isStopping

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
        Bot Control
      </h2>
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-3">
        <div className="flex items-center gap-4">
          {showStopButton ? (
            <button
              onClick={handleStop}
              disabled={loading || isStopping}
              className="px-4 py-2 bg-red-600 text-white text-sm font-semibold rounded hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading || isStopping ? 'Stopping…' : 'Stop Bot'}
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
              : isStopping
                ? 'Stopping — waiting for current candle to close…'
                : botState.running
                  ? `Running · ${botState.mode?.toUpperCase() ?? ''} mode`
                  : isStarting
                    ? 'Starting up — running backtests…'
                    : 'Stopped'}
          </span>
        </div>
        {error && <p className="text-sm text-red-400">{error}</p>}
        <div className="flex items-center gap-3 pt-1 border-t border-gray-800">
          <button
            onClick={handleClearHistory}
            disabled={clearingHistory}
            className="px-3 py-1.5 bg-gray-700 text-gray-300 text-xs font-semibold rounded hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {clearingHistory ? 'Clearing…' : 'Clear History'}
          </button>
          {clearMsg && (
            <span className={`text-xs font-mono ${clearMsg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
              {clearMsg.text}
            </span>
          )}
          {!clearMsg && (
            <span className="text-xs text-gray-600">Archives all trade & virtual order history</span>
          )}
        </div>
      </div>
    </section>
  )
}
