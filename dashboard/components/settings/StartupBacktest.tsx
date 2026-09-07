'use client'

import { useState, useEffect } from 'react'

/** Whether the bot runs a full backtest when it starts.
 *
 *  Off by default. It is a blocking subprocess across every symbol and it dominated
 *  restarts — measured 256-567s, worst 9m11s, about 96% of startup — during which there
 *  is no WebSocket, no candle processing and no position monitoring (open positions rely
 *  on their exchange-side stop-loss). Deploys are the main source of restarts, so that
 *  was being paid constantly for results that persist on disk anyway.
 *
 *  Tick it when you want the next start to refresh the backtest data; it stays on until
 *  unticked. For a one-off refresh without restarting, use the Backtest page instead.
 */
export default function StartupBacktest() {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [klines, setKlines] = useState(1500)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/api/risk')
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        setEnabled(Boolean(d?.config?.startup_backtest))
        if (d?.config?.backtest_klines != null) setKlines(Number(d.config.backtest_klines))
      })
      .catch(() => setEnabled(false))
  }, [])

  async function save(next: boolean) {
    setSaving(true)
    setError('')
    const prev = enabled
    setEnabled(next)                        // optimistic, reverted below on failure
    try {
      const r = await fetch('/api/risk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ startup_backtest: next }),
      })
      if (!r.ok) throw new Error(await r.text())
    } catch (e) {
      setEnabled(prev)
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
        Startup Backtest
      </h2>
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-3">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={enabled === true}
            disabled={enabled === null || saving}
            onChange={e => save(e.target.checked)}
            className="mt-0.5 h-4 w-4 accent-blue-500 disabled:opacity-50"
          />
          <span className="text-sm text-white">
            Run a backtest when the bot starts
            <span className="block text-xs text-gray-400 mt-1">
              {enabled === null
                ? 'Loading…'
                : enabled
                  ? `On — the next start will backtest all symbols over ${klines} klines. `
                    + 'Measured at 4–9 minutes, during which the bot is not watching the market.'
                  : 'Off — the bot starts in seconds and reuses the existing backtest results. '
                    + 'This is the recommended setting; refresh the data from the Backtest page '
                    + 'when you need it.'}
            </span>
          </span>
        </label>

        {enabled && (
          <div className="text-xs text-amber-400/90 border-l-2 border-amber-500/40 pl-3">
            While the backtest runs there is no WebSocket and no position monitoring in
            the bot. Open positions are still protected by their stop-loss on Binance,
            but nothing else reacts until it finishes.
          </div>
        )}

        {error && <div className="text-xs text-red-400">{error}</div>}

        <div className="text-xs text-gray-500">
          Takes effect on the next bot start. Applies to restarts from a deploy as well
          as from this page.
        </div>
      </div>
    </section>
  )
}
