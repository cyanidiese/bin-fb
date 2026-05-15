'use client'

import { useState } from 'react'
import { RiskConfig, RiskState } from '@/lib/risk-types'
import { SECTION_CLS, SECTION_HEADER_CLS } from '@/lib/risk-styles'

interface Props {
  config: RiskConfig
  state: RiskState | null
}

export default function LiveRiskState({ config, state }: Props) {
  const [resetting, setResetting] = useState(false)
  const [resetMsg, setResetMsg] = useState<{ ok: boolean; text: string } | null>(null)

  async function handleResetHardStop() {
    if (!window.confirm('Reset the hard stop? The bot will resume placing orders on the next candle.')) return
    setResetting(true)
    setResetMsg(null)
    try {
      const r = await fetch('/api/risk/reset-hard-stop', { method: 'POST' })
      const d = await r.json()
      if (d.ok) {
        setResetMsg({ ok: true, text: 'Signal sent — takes effect on next candle close' })
      } else {
        setResetMsg({ ok: false, text: d.error ?? 'Failed' })
      }
    } catch (e) {
      setResetMsg({ ok: false, text: String(e) })
    } finally {
      setResetting(false)
      setTimeout(() => setResetMsg(null), 6000)
    }
  }

  return (
    <section className={SECTION_CLS}>
      <p
        className={SECTION_HEADER_CLS}
        title="Read-only snapshot from risk_state.json, updated by the bot after each balance change. Polling every 5 seconds."
      >
        E — Live Risk State
        {state && (
          <span className="ml-2 text-[10px] text-gray-600 normal-case tracking-normal font-normal">
            updated {new Date(state.generated_at).toLocaleTimeString()}
          </span>
        )}
      </p>
      <div className="px-4 py-4">
        {!state ? (
          <p className="text-xs text-gray-600 italic">
            No risk_state.json yet — start the bot to generate it.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-6 text-xs font-mono">
              {[
                { label: 'Mode',       value: state.mode,                          color: 'text-gray-300' },
                { label: 'Balance',    value: `$${state.balance.toFixed(2)}`,      color: 'text-gray-300' },
                { label: 'Peak',       value: `$${state.peak_balance.toFixed(2)}`, color: 'text-gray-300' },
                { label: 'Drawdown',   value: `${state.drawdown_pct.toFixed(2)}%`,
                  color: state.drawdown_pct >= config.drawdown_hard_stop_pct
                    ? 'text-red-400'
                    : state.drawdown_pct >= config.drawdown_warning_pct
                    ? 'text-amber-400'
                    : 'text-emerald-400' },
                { label: 'Last event', value: state.last_event || 'none',         color: 'text-gray-500' },
              ].map(s => (
                <div key={s.label}>
                  <span className="text-gray-600">{s.label}: </span>
                  <span className={s.color}>{s.value}</span>
                </div>
              ))}
            </div>

            {/* Hard stop alert + reset button */}
            {state.hard_stop_active && (
              <div className="flex items-center gap-3 rounded border border-red-800 bg-red-950/50 px-3 py-2">
                <span className="text-xs text-red-300 font-mono font-semibold">
                  ⛔ Hard stop active — all orders blocked ({state.drawdown_pct.toFixed(1)}% drawdown)
                </span>
                <button
                  onClick={handleResetHardStop}
                  disabled={resetting}
                  className="ml-auto px-3 py-1 text-xs font-semibold rounded border border-red-600 text-red-300 hover:bg-red-800/50 disabled:opacity-50 transition-colors shrink-0"
                >
                  {resetting ? 'Sending…' : 'Reset Hard Stop'}
                </button>
                {resetMsg && (
                  <span className={`text-xs font-mono ${resetMsg.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                    {resetMsg.text}
                  </span>
                )}
              </div>
            )}

            <details>
              <summary
                className="text-[10px] text-gray-600 cursor-pointer hover:text-gray-400 transition-colors"
                title="Expand to see the full raw risk_state.json snapshot."
              >
                Raw snapshot
              </summary>
              <pre className="mt-2 text-[10px] text-gray-500 font-mono bg-gray-900 rounded p-3 overflow-x-auto max-h-64">
                {JSON.stringify(state, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </div>
    </section>
  )
}
