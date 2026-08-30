'use client'

import { useState } from 'react'
import { RiskConfig } from '@/lib/risk-types'
import { SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS, INPUT_CLS } from '@/lib/risk-styles'

interface Props {
  config: RiskConfig
  availableSymbols: string[]
  patchConfig: (patch: Partial<RiskConfig>) => void
}

export default function PresetRankingSection({ config, availableSymbols, patchConfig }: Props) {
  const globalMin = config.min_trades_for_ranking ?? 3
  const overrides = config.min_trades_for_ranking_per_symbol ?? {}

  const unassignedSymbols = availableSymbols.filter(s => !(s in overrides))
  const [newSymbol, setNewSymbol] = useState<string>(unassignedSymbols[0] ?? '')

  // Keep newSymbol pointing at a valid unassigned symbol after each overrides change
  const resolvedNewSymbol = unassignedSymbols.includes(newSymbol)
    ? newSymbol
    : unassignedSymbols[0] ?? ''

  function handleGlobalChange(value: number) {
    patchConfig({ min_trades_for_ranking: value })
  }

  function handleOverrideChange(sym: string, value: number) {
    patchConfig({
      min_trades_for_ranking_per_symbol: { ...overrides, [sym]: value },
    })
  }

  function handleOverrideRemove(sym: string) {
    const updated = { ...overrides }
    delete updated[sym]
    patchConfig({ min_trades_for_ranking_per_symbol: updated })
  }

  function handleAdd() {
    if (!resolvedNewSymbol) return
    patchConfig({
      min_trades_for_ranking_per_symbol: { ...overrides, [resolvedNewSymbol]: globalMin },
    })
    const remaining = unassignedSymbols.filter(s => s !== resolvedNewSymbol)
    setNewSymbol(remaining[0] ?? '')
  }

  return (
    <section className={SECTION_CLS}>
      <p className={SECTION_HEADER_CLS}>Preset Ranking</p>
      <div className={SECTION_BODY_CLS}>

        {/* Global threshold */}
        <div className="flex flex-col gap-1">
          <label className="flex items-center gap-3 text-xs text-gray-300">
            <span className="min-w-max">Min trades before live ranking</span>
            <input
              type="number"
              min={1}
              step={1}
              value={globalMin}
              onChange={e => handleGlobalChange(Math.max(1, Number(e.target.value)))}
              className={INPUT_CLS}
            />
          </label>
          <p className="text-[11px] text-gray-500 leading-snug">
            After this many real+virtual trades, a preset uses live P&L for ranking instead of the backtest seed.
          </p>
        </div>

        {/* Substitution — one rank down when the best preset is silent */}
        <div className="flex flex-col gap-1 pt-1 border-t border-gray-800">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={config.substitution_enabled ?? false}
              onChange={e => patchConfig({ substitution_enabled: e.target.checked })}
              className="rounded accent-indigo-500"
            />
            <span className="text-xs text-gray-300">Substitute one rank when best preset has no signal</span>
          </label>
          <p className="text-[11px] text-gray-500 leading-snug">
            When the best preset produces no recommendation, place the order using the next
            preset down that is both live-proven and currently profitable. Exactly one rank —
            measured on real data, one rank was positive while two or more were not.
            Off by default.
          </p>
        </div>

        {/* Per-symbol overrides table */}
        {Object.keys(overrides).length > 0 && (
          <div>
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="text-gray-500 border-b border-gray-800">
                  <th className="text-left py-1 pr-3 font-medium">Symbol</th>
                  <th className="text-left py-1 pr-3 font-medium">Min Trades</th>
                  <th className="py-1" />
                </tr>
              </thead>
              <tbody>
                {Object.entries(overrides).map(([sym, val]) => (
                  <tr key={sym} className="border-b border-gray-800/50">
                    <td className="py-1.5 pr-3 font-mono text-gray-300">{sym}</td>
                    <td className="py-1.5 pr-3">
                      <input
                        type="number"
                        min={1}
                        step={1}
                        value={val}
                        onChange={e => handleOverrideChange(sym, Math.max(1, Number(e.target.value)))}
                        className={INPUT_CLS}
                      />
                    </td>
                    <td className="py-1.5">
                      <button
                        onClick={() => handleOverrideRemove(sym)}
                        className="text-gray-500 hover:text-red-400 transition-colors px-1"
                        title={`Remove override for ${sym}`}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Add override row — hidden when all symbols already have overrides */}
        {unassignedSymbols.length > 0 && (
          <div className="flex items-center gap-2">
            <select
              value={resolvedNewSymbol}
              onChange={e => setNewSymbol(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 text-xs font-mono focus:outline-none focus:border-indigo-500"
            >
              {unassignedSymbols.map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <button
              onClick={handleAdd}
              disabled={!resolvedNewSymbol}
              className="px-3 py-1 rounded border border-gray-700 bg-gray-800 text-gray-300 text-xs font-semibold hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Add
            </button>
          </div>
        )}

      </div>
    </section>
  )
}
