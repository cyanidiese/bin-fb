'use client'

import { useState } from 'react'

interface Props {
  mode: 'test' | 'live'
  enabled: boolean
  /** The viewed instance is the virtual-only shadow, which never places real orders. */
  shadow: boolean
  onSaved: (enabled: boolean) => void
}

/** Global switch for NEW real orders in one trading mode (risk_config real_orders_enabled).
 *  Saves immediately — it is not part of Save All — so the state shown is the state on disk.
 *  Weights are untouched; turning it back on resumes trading exactly as configured. */
export default function RealOrdersSwitch({ mode, enabled, shadow, onSaved }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function toggle() {
    const next = !enabled
    const msg = next
      ? `Turn REAL orders back ON for ${mode}?\n\nFrom the next candle the bot places real orders again with the current weights.`
      : `Turn REAL orders OFF for ${mode}?\n\nFrom the next candle no new real orders are placed. Open real positions are still managed until they exit. Virtual orders continue as before. Weights are not changed.`
    if (!window.confirm(msg)) return
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`/api/risk?mode=${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ real_orders_enabled: next }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) { setError(data.error ?? `HTTP ${res.status}`); return }
      onSaved(next)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const on = enabled && !shadow
  return (
    <section
      className={`rounded-lg border px-4 py-3 flex flex-wrap items-center gap-4 ${
        shadow ? 'border-gray-800 bg-gray-900/50'
          : on ? 'border-emerald-700/60 bg-emerald-950/30' : 'border-red-700/70 bg-red-950/40'}`}
    >
      <div className="flex-1 min-w-[220px]">
        <p className="text-sm font-semibold text-white">
          Real orders ({mode}):{' '}
          <span className={shadow ? 'text-gray-400' : on ? 'text-emerald-400' : 'text-red-400'}>
            {shadow ? 'never (virtual-only instance)' : on ? 'ON' : 'OFF'}
          </span>
        </p>
        <p className="text-[11px] text-gray-400 mt-0.5">
          {shadow
            ? 'The shadow instance only simulates; switch to the primary to control real orders.'
            : on
              ? 'New real orders are placed as usual. Turn off to watch virtual orders only — weights stay as they are.'
              : 'No new real orders. Open real positions are still managed to their exit; virtual orders run as usual. Takes effect at the next candle.'}
        </p>
        {error && <p className="text-[11px] text-red-400 font-mono mt-1">{error}</p>}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        onClick={toggle}
        disabled={busy || shadow}
        title={shadow ? 'Not applicable to the virtual-only instance'
          : on ? 'Stop placing new real orders (saves immediately)' : 'Resume real orders (saves immediately)'}
        className={`relative inline-flex h-7 w-14 shrink-0 items-center rounded-full transition-colors
          disabled:opacity-40 disabled:cursor-not-allowed
          ${on ? 'bg-emerald-600' : 'bg-gray-700'}`}
      >
        <span className={`inline-block h-5 w-5 rounded-full bg-white shadow transition-transform
          ${on ? 'translate-x-8' : 'translate-x-1'}`} />
      </button>
    </section>
  )
}
