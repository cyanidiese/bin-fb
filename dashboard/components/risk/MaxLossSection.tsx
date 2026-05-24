'use client'

import { useState } from 'react'
import { RiskConfig } from '@/lib/risk-types'
import { SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS, INPUT_CLS } from '@/lib/risk-styles'
import LabeledInput from './LabeledInput'

interface Props {
  config: RiskConfig
  availableSymbols: string[]
  patchConfig: (patch: Partial<RiskConfig>) => void
}

export default function MaxLossSection({ config, availableSymbols, patchConfig }: Props) {
  const globalCap = config.max_loss_usdt ?? 25
  const tpRatio = config.max_loss_tp_ratio ?? 0
  const overrides = config.max_loss_usdt_per_symbol ?? {}

  const unassigned = availableSymbols.filter(s => !(s in overrides))
  const [newSymbol, setNewSymbol] = useState(unassigned[0] ?? '')
  const resolved = unassigned.includes(newSymbol) ? newSymbol : unassigned[0] ?? ''

  function patchOverride(sym: string, val: number) {
    patchConfig({ max_loss_usdt_per_symbol: { ...overrides, [sym]: val } })
  }

  function removeOverride(sym: string) {
    const next = { ...overrides }
    delete next[sym]
    patchConfig({ max_loss_usdt_per_symbol: next })
  }

  function addOverride() {
    if (!resolved) return
    patchConfig({ max_loss_usdt_per_symbol: { ...overrides, [resolved]: globalCap } })
    const remaining = unassigned.filter(s => s !== resolved)
    setNewSymbol(remaining[0] ?? '')
  }

  // Preview: effective cap for a symbol given current settings
  function effectiveCap(sym: string, tpUsdt: number): string {
    const symCap = overrides[sym] ?? globalCap
    if (tpRatio > 0 && tpUsdt > 0) {
      const ratioCap = tpRatio * tpUsdt
      const eff = Math.min(symCap, ratioCap)
      return `$${eff.toFixed(1)}`
    }
    return `$${symCap.toFixed(1)}`
  }

  return (
    <section className={SECTION_CLS}>
      <p className={SECTION_HEADER_CLS}>Max Loss per Order</p>
      <div className={SECTION_BODY_CLS}>

        {/* Global USDT cap */}
        <LabeledInput
          label="Global cap (USDT)"
          tooltip="Close any order immediately when its unrealized loss reaches this amount. 0 = disabled. Applies to all symbols unless overridden below."
          value={globalCap}
          onChange={v => patchConfig({ max_loss_usdt: Number(v) })}
          min={0} step={1}
        />

        {/* TP-ratio cap */}
        <div className="flex flex-col gap-1">
          <LabeledInput
            label="TP-ratio cap"
            tooltip="Also cap loss at (ratio × potential TP profit). Takes the tighter of this and the USDT cap. 0 = disabled. Example: 1.5 = never lose more than 1.5× what you could win on this trade."
            value={tpRatio}
            onChange={v => patchConfig({ max_loss_tp_ratio: Number(v) })}
            min={0} max={10} step={0.1}
          />
          {tpRatio > 0 && (
            <p className="text-[11px] text-gray-500 ml-52 leading-snug">
              Small-TP orders get a tighter cap automatically.
              E.g. TP=$10 → loss cap = min(${globalCap}, ${(tpRatio * 10).toFixed(1)}) = ${Math.min(globalCap, tpRatio * 10).toFixed(1)}
            </p>
          )}
        </div>

        {/* Per-symbol overrides */}
        <div className="flex flex-col gap-2">
          <p className="text-xs text-gray-500">
            Per-symbol overrides
            <span className="text-gray-600 ml-1">(replace the global USDT cap for that symbol)</span>
          </p>

          {Object.keys(overrides).length > 0 && (
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="text-gray-500 border-b border-gray-800">
                  <th className="text-left py-1 pr-3 font-medium">Symbol</th>
                  <th className="text-left py-1 pr-3 font-medium">Cap (USDT)</th>
                  {tpRatio > 0 && <th className="text-left py-1 pr-3 font-medium text-gray-600">Effective (TP=$50)</th>}
                  <th className="py-1" />
                </tr>
              </thead>
              <tbody>
                {Object.entries(overrides).map(([sym, val]) => (
                  <tr key={sym} className="border-b border-gray-800/50">
                    <td className="py-1.5 pr-3 font-mono text-gray-300">{sym}</td>
                    <td className="py-1.5 pr-3">
                      <input
                        type="number" min={0} step={1}
                        value={val}
                        onChange={e => patchOverride(sym, Math.max(0, Number(e.target.value)))}
                        className={INPUT_CLS}
                      />
                    </td>
                    {tpRatio > 0 && (
                      <td className="py-1.5 pr-3 text-gray-600 font-mono">{effectiveCap(sym, 50)}</td>
                    )}
                    <td className="py-1.5">
                      <button
                        onClick={() => removeOverride(sym)}
                        className="text-gray-500 hover:text-red-400 transition-colors px-1"
                        title={`Remove override for ${sym}`}
                      >×</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {unassigned.length > 0 && (
            <div className="flex items-center gap-2">
              <select
                value={resolved}
                onChange={e => setNewSymbol(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 text-xs font-mono focus:outline-none focus:border-indigo-500"
              >
                {unassigned.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <button
                onClick={addOverride}
                disabled={!resolved}
                className="px-3 py-1 rounded border border-gray-700 bg-gray-800 text-gray-300 text-xs font-semibold hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                Add
              </button>
            </div>
          )}
        </div>

      </div>
    </section>
  )
}
