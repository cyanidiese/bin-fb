'use client'

import { useState, useEffect } from 'react'

type BacktestStatus = 'none' | 'running' | 'complete' | 'error' | 'cancelled'
type SortCol = 'symbol' | 'profit'
type SortDir = 'asc' | 'desc'

interface SymbolStatus {
  backtest: BacktestStatus
  pid: number | null
}

interface DisabledEntry {
  reason: string
  disabled_at: string
}

interface RegistryData {
  symbols: string[]
  updated_at: string
  status: Record<string, SymbolStatus>
  disabled?: Record<string, DisabledEntry>
}

interface Props {
  registry: RegistryData | null
  onRefetch: () => void
}

function StatusBadge({ status }: { status: BacktestStatus }) {
  if (status === 'running') {
    return (
      <span className="flex items-center gap-1.5 text-indigo-400" title="Backtest is currently running for this symbol">
        <svg className="animate-spin h-3 w-3 shrink-0" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
        </svg>
        running…
      </span>
    )
  }
  if (status === 'complete') return <span className="text-emerald-400" title="Backtest finished successfully">✓ complete</span>
  if (status === 'error') return <span className="text-red-400" title="Backtest exited with an error — check terminal logs">✗ error</span>
  if (status === 'cancelled') return <span className="text-gray-500" title="Backtest was cancelled when the symbol was removed">cancelled</span>
  return <span className="text-gray-600" title="No backtest has been run for this symbol yet">none</span>
}

export default function SymbolRegistry({ registry, onRefetch }: Props) {
  const [addInput, setAddInput] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)
  const [perfScores, setPerfScores] = useState<Record<string, number | null>>({})
  const [sortCol, setSortCol] = useState<SortCol>('profit')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  useEffect(() => {
    fetch('/api/public-file?f=risk_state.json')
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (!data?.per_symbol) return
        const scores: Record<string, number | null> = {}
        for (const [sym, info] of Object.entries(data.per_symbol as Record<string, { performance_score?: number | null }>)) {
          const ps = (info as { performance_score?: number | null }).performance_score
          scores[sym] = ps !== undefined ? ps : null
        }
        setPerfScores(scores)
      })
      .catch(() => {})
  }, [registry])  // re-fetch whenever registry changes (symbol added/removed/backtest done)

  function toggleSort(col: SortCol) {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir(col === 'profit' ? 'desc' : 'asc') }
  }

  const sortedSymbols = [...(registry?.symbols ?? [])].sort((a, b) => {
    if (sortCol === 'symbol') {
      const cmp = a.localeCompare(b)
      return sortDir === 'asc' ? cmp : -cmp
    }
    // Profit sort: null (no backtest) always goes to the bottom regardless of direction
    const sa = perfScores[a] ?? null
    const sb = perfScores[b] ?? null
    if (sa === null && sb === null) return 0
    if (sa === null) return 1
    if (sb === null) return -1
    const cmp = sa - sb
    return sortDir === 'asc' ? cmp : -cmp
  })

  const updatedAt = registry?.updated_at
    ? new Date(registry.updated_at).toLocaleTimeString()
    : '—'

  async function handleAdd() {
    const symbol = addInput.trim().toUpperCase()
    setAddError(null)
    if (!symbol) { setAddError('Enter a symbol name'); return }
    setAdding(true)
    try {
      const res = await fetch('/api/symbols', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      })
      const data = await res.json()
      if (!res.ok) { setAddError(data.error ?? `HTTP ${res.status}`); return }
      setAddInput('')
      onRefetch()
    } catch (e) {
      setAddError(String(e))
    } finally {
      setAdding(false)
    }
  }

  async function handleToggleDisable(symbol: string) {
    const isDisabled = !!registry?.disabled?.[symbol]
    if (!isDisabled) {
      if (!window.confirm(`Disable ${symbol}?\n\nThe bot will stop placing new orders for this symbol. Open positions are not affected.`)) return
    }
    setToggling(symbol)
    try {
      if (isDisabled) {
        await fetch(`/api/symbols/${symbol}/enable`, { method: 'PATCH' })
      } else {
        await fetch(`/api/symbols/${symbol}/disable`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: 'manual' }),
        })
      }
      onRefetch()
    } finally {
      setToggling(null)
    }
  }

  async function handleRemove(symbol: string) {
    const status = registry?.status[symbol]?.backtest
    const isRunning = status === 'running'
    const msg = isRunning
      ? `Cancel the running backtest for ${symbol} and remove it?\n\nPartial results will be discarded.`
      : `Remove ${symbol} from active symbols?`
    if (!window.confirm(msg)) return
    setRemoving(symbol)
    try {
      await fetch(`/api/symbols/${symbol}`, { method: 'DELETE' })
      onRefetch()
    } finally {
      setRemoving(null)
    }
  }

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
        Symbol Registry
      </h2>

      {/* Active symbols table */}
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden">
        <div className="px-4 py-2 border-b border-gray-800 flex items-center justify-between">
          <p
            className="text-xs text-gray-500 font-semibold uppercase tracking-wide"
            title="Symbols currently tracked by the bot. Add or remove symbols below."
          >
            Active Symbols
          </p>
          <span
            className="text-[10px] text-gray-600 font-mono"
            title="Timestamp of the last change to symbol_registry.json"
          >
            {registry ? `${registry.symbols.length} symbol${registry.symbols.length !== 1 ? 's' : ''} · updated ${updatedAt}` : 'Loading…'}
          </span>
        </div>

        {registry && registry.symbols.length === 0 && (
          <p className="px-4 py-6 text-sm text-gray-600 italic text-center">
            No symbols registered. Add one below.
          </p>
        )}

        {registry && registry.symbols.length > 0 && (
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-gray-800 text-gray-500">
                <th
                  className="text-left px-4 py-2 font-normal cursor-pointer select-none hover:text-gray-300 transition-colors"
                  title="Binance USD-M Futures symbol — click to sort"
                  onClick={() => toggleSort('symbol')}
                >
                  Symbol{sortCol === 'symbol' ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ' ⇅'}
                </th>
                <th className="text-left px-4 py-2 font-normal" title="Status of the most recent backtest run for this symbol">Backtest</th>
                <th
                  className="text-right px-4 py-2 font-normal cursor-pointer select-none hover:text-gray-300 transition-colors"
                  title="Best preset's total profit % from the last backtest — click to sort"
                  onClick={() => toggleSort('profit')}
                >
                  Profit %{sortCol === 'profit' ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ' ⇅'}
                </th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {sortedSymbols.map(sym => {
                const st = registry.status[sym] ?? { backtest: 'none', pid: null }
                const score = perfScores[sym]
                const isDisabled = !!registry.disabled?.[sym]
                return (
                  <tr key={sym} className={`border-b border-gray-900 ${isDisabled ? 'opacity-50' : 'hover:bg-gray-900/40'}`}>
                    <td className="px-4 py-2 font-semibold">
                      <span className={isDisabled ? 'text-gray-500' : 'text-indigo-300'}>{sym}</span>
                      {isDisabled && (
                        <span className="ml-1.5 text-[9px] text-red-400 font-semibold uppercase tracking-wide">off</span>
                      )}
                    </td>
                    <td className="px-4 py-2">
                      <StatusBadge status={st.backtest} />
                    </td>
                    <td className="px-4 py-2 text-right">
                      {score != null
                        ? <span className={score > 0 ? 'text-emerald-400' : 'text-gray-500'}>{score.toFixed(2)}%</span>
                        : <span className="text-gray-700">—</span>
                      }
                    </td>
                    <td className="px-4 py-2 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          onClick={() => handleToggleDisable(sym)}
                          disabled={toggling === sym}
                          title={isDisabled ? `Re-enable ${sym} for trading` : `Disable ${sym} — bot will stop placing new orders`}
                          className={isDisabled
                            ? 'px-2 py-0.5 rounded border border-emerald-900/60 bg-emerald-950/30 text-emerald-400 text-[10px] font-semibold hover:bg-emerald-900/40 hover:text-emerald-300 disabled:opacity-40 disabled:cursor-not-allowed transition-colors'
                            : 'px-2 py-0.5 rounded border border-yellow-900/60 bg-yellow-950/30 text-yellow-600 text-[10px] font-semibold hover:bg-yellow-900/40 hover:text-yellow-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors'
                          }
                        >
                          {toggling === sym ? '…' : isDisabled ? 'Enable' : 'Disable'}
                        </button>
                        <button
                          onClick={() => handleRemove(sym)}
                          disabled={removing === sym}
                          title={st.backtest === 'running' ? `Cancel backtest and remove ${sym}` : `Remove ${sym} from active symbols`}
                          className="px-2 py-0.5 rounded border border-red-900/60 bg-red-950/30 text-red-400 text-[10px] font-semibold hover:bg-red-900/40 hover:text-red-300 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >
                          {removing === sym ? '…' : 'Remove'}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Add symbol */}
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-3">
        <p
          className="text-xs text-gray-500 font-semibold uppercase tracking-wide"
          title="Add a new Binance USD-M Futures symbol. A backtest will start immediately."
        >
          Add Symbol
        </p>
        <div className="flex gap-2">
          <input
            type="text"
            value={addInput}
            onChange={e => { setAddInput(e.target.value.toUpperCase()); setAddError(null) }}
            onKeyDown={e => e.key === 'Enter' && !adding && handleAdd()}
            placeholder="e.g. ETHUSDT"
            disabled={adding}
            title="Enter a Binance USD-M Futures symbol (e.g. ETHUSDT, SOLUSDT). Must end in USDT."
            className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-300 text-xs font-mono placeholder-gray-600 focus:outline-none focus:border-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed uppercase"
          />
          <button
            onClick={handleAdd}
            disabled={adding || !addInput.trim()}
            title="Add the symbol and start a backtest immediately"
            className="px-4 py-2 rounded border border-indigo-700 bg-indigo-900/60 text-indigo-300 text-xs font-semibold hover:bg-indigo-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
          >
            {adding ? 'Adding…' : 'Add'}
          </button>
        </div>
        {addError && <p className="text-xs text-red-400 font-mono">{addError}</p>}
        <p className="text-[10px] text-gray-600">
          A backtest over the last 1500 klines starts immediately after adding.
          Results appear on the Backtest page once complete.
        </p>
      </div>
    </section>
  )
}
