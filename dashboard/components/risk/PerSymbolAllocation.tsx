'use client'

import { RiskConfig, RiskState } from '@/lib/risk-types'
import { INPUT_CLS, SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS } from '@/lib/risk-styles'

interface Props {
  config: RiskConfig
  state: RiskState | null
  availableSymbols: string[]
  patchConfig: (patch: Partial<RiskConfig>) => void
}

export default function PerSymbolAllocation({ config, state, availableSymbols, patchConfig }: Props) {
  const totalWeight = Object.values(config.symbol_weights).reduce((a, b) => a + b, 0) || 1

  return (
    <section className={SECTION_CLS}>
      <p className={SECTION_HEADER_CLS} title="Relative weights determine how the deployable budget is split across active symbols.">
        B — Per-Symbol Allocation
      </p>
      <div className={SECTION_BODY_CLS}>
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-gray-600 border-b border-gray-800">
              <th className="text-left py-1 pr-4 font-normal" title="Binance USD-M Futures symbol.">Symbol</th>
              <th className="text-left py-1 pr-4 font-normal" title="Relative weight. Higher weight = larger share of deployable capital.">Weight</th>
              <th className="text-left py-1 pr-4 font-normal" title="Computed share of the deployable budget this symbol receives (weight ÷ total weights).">Alloc %</th>
              <th className="text-left py-1 pr-4 font-normal" title="Current USDT allocation for this symbol, based on live balance and active tier.">Alloc USDT</th>
              <th className="text-left py-1 pr-4 font-normal" title="Dynamic leverage currently assigned to this symbol by the risk manager.">Leverage</th>
              <th className="text-left py-1 font-normal" title="Normalised performance score (0–1) from the best backtest preset for this symbol. Drives the leverage formula.">Perf score</th>
            </tr>
          </thead>
          <tbody>
            {availableSymbols.map(sym => {
              const w = config.symbol_weights[sym] ?? 1
              const allocPct = (w / totalWeight * 100).toFixed(1)
              const live = state?.per_symbol[sym]
              return (
                <tr key={sym} className="border-b border-gray-900 hover:bg-gray-900/40">
                  <td className="py-1.5 pr-4 text-indigo-300 font-semibold">{sym}</td>
                  <td className="py-1.5 pr-4">
                    <input
                      type="number" min={1} step={1}
                      value={w}
                      title={`Relative capital weight for ${sym}. Increase to allocate more USDT to this symbol.`}
                      onChange={e => patchConfig({
                        symbol_weights: { ...config.symbol_weights, [sym]: Number(e.target.value) },
                      })}
                      className={INPUT_CLS + ' w-16'}
                    />
                  </td>
                  <td className="py-1.5 pr-4 text-gray-400">{allocPct}%</td>
                  <td className="py-1.5 pr-4 text-gray-400">
                    {live ? `$${live.allocation_usdt.toFixed(0)}` : '—'}
                  </td>
                  <td className="py-1.5 pr-4 text-gray-400">
                    {live ? `${live.leverage}×` : '—'}
                  </td>
                  <td className="py-1.5 text-gray-400">
                    {live ? live.performance_score.toFixed(3) : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
