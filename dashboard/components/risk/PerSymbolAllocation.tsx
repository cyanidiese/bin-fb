'use client'

import { useState } from 'react'
import { RiskConfig, RiskState } from '@/lib/risk-types'
import { INPUT_CLS, SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS } from '@/lib/risk-styles'

type SortCol = 'symbol' | 'score' | 'alloc' | 'usdt' | 'leverage'
type SortDir = 'asc' | 'desc'

interface Props {
  config: RiskConfig
  state: RiskState | null
  availableSymbols: string[]
  patchConfig: (patch: Partial<RiskConfig>) => void
  bgfMode?: boolean
}

export default function PerSymbolAllocation({ config, state, availableSymbols, patchConfig, bgfMode }: Props) {
  const [sortCol, setSortCol] = useState<SortCol>('score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  function toggleSort(col: SortCol) {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('desc') }
  }

  const totalWeight = Object.values(config.symbol_weights).reduce((a, b) => a + b, 0) || 1

  // BGF: compute deployable pool and proportional caps from live performance scores
  const deployable = (() => {
    if (!state) return 0
    const reserve = state.balance * (config.min_balance_pct / 100)
    return Math.max(0, state.balance - reserve) * (state.active_tier.max_deploy_pct / 100)
  })()
  const totalScore = bgfMode
    ? availableSymbols.reduce((sum, sym) => sum + (state?.per_symbol[sym]?.performance_score ?? 0), 0)
    : 0

  // Build sorted symbol list for BGF mode
  const sortedSymbols = bgfMode ? [...availableSymbols].sort((a, b) => {
    const scoreA = state?.per_symbol[a]?.performance_score ?? 0
    const scoreB = state?.per_symbol[b]?.performance_score ?? 0
    const shareA = totalScore > 0 ? scoreA / totalScore : 0
    const shareB = totalScore > 0 ? scoreB / totalScore : 0
    const levA = state?.per_symbol[a]?.leverage ?? 0
    const levB = state?.per_symbol[b]?.leverage ?? 0
    let cmp = 0
    if (sortCol === 'symbol') cmp = a.localeCompare(b)
    else if (sortCol === 'score') cmp = scoreA - scoreB
    else if (sortCol === 'alloc') cmp = shareA - shareB
    else if (sortCol === 'usdt') cmp = (deployable * shareA) - (deployable * shareB)
    else if (sortCol === 'leverage') cmp = levA - levB
    return sortDir === 'asc' ? cmp : -cmp
  }) : availableSymbols

  function SortHdr({ col, children, title }: { col: SortCol; children: React.ReactNode; title?: string }) {
    const active = sortCol === col
    return (
      <th
        className="text-left py-1 pr-4 font-normal cursor-pointer select-none hover:text-gray-400 transition-colors"
        title={title}
        onClick={() => toggleSort(col)}
      >
        {children}
        <span className={`ml-1 ${active ? 'text-indigo-400' : 'text-gray-700'}`}>
          {active ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
        </span>
      </th>
    )
  }

  return (
    <section className={SECTION_CLS}>
      <p
        className={SECTION_HEADER_CLS}
        title={
          bgfMode
            ? 'Read-only. In Best Gets First mode allocation is proportional to each symbol\'s performance score — no manual weights.'
            : 'Relative weights determine how the deployable budget is split across active symbols.'
        }
      >
        B — Per-Symbol Allocation
        {bgfMode && <span className="ml-2 text-xs text-amber-400 font-normal">(auto — score-proportional)</span>}
      </p>
      <div className={SECTION_BODY_CLS}>
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-gray-600 border-b border-gray-800">
              {bgfMode ? (
                <>
                  <SortHdr col="symbol">Symbol</SortHdr>
                  <SortHdr col="score" title="Normalised performance score driving this symbol's allocation share.">Score</SortHdr>
                  <SortHdr col="alloc" title="score ÷ total scores">Alloc %</SortHdr>
                  <SortHdr col="usdt" title="USDT allocated to this symbol from the current deployable pool.">Alloc USDT</SortHdr>
                  <SortHdr col="leverage" title="Dynamic leverage currently assigned to this symbol.">Leverage</SortHdr>
                </>
              ) : (
                <>
                  <th className="text-left py-1 pr-4 font-normal">Symbol</th>
                  <th className="text-left py-1 pr-4 font-normal" title="Relative weight. Higher weight = larger share of deployable capital.">Weight</th>
                  <th className="text-left py-1 pr-4 font-normal" title="weight ÷ total weights">Alloc %</th>
                  <th className="text-left py-1 pr-4 font-normal" title="USDT allocated to this symbol from the current deployable pool.">Alloc USDT</th>
                  <th className="text-left py-1 pr-4 font-normal" title="Dynamic leverage currently assigned to this symbol.">Leverage</th>
                  <th className="text-left py-1 font-normal" title="Normalised performance score (0–1) from the best backtest preset for this symbol.">Perf score</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {sortedSymbols.map(sym => {
              const live = state?.per_symbol[sym]
              if (bgfMode) {
                const score = live?.performance_score ?? 0
                const share = totalScore > 0 ? score / totalScore : 0
                const allocPct = (share * 100).toFixed(1)
                const allocUSDT = deployable * share
                return (
                  <tr key={sym} className="border-b border-gray-900 hover:bg-gray-900/40">
                    <td className="py-1.5 pr-4 text-indigo-300 font-semibold">{sym}</td>
                    <td className="py-1.5 pr-4 text-gray-400">{score.toFixed(3)}</td>
                    <td className="py-1.5 pr-4 text-gray-400">{allocPct}%</td>
                    <td className="py-1.5 pr-4 text-gray-400">
                      {state ? `$${allocUSDT.toFixed(0)}` : '—'}
                    </td>
                    <td className="py-1.5 pr-4 text-gray-400">
                      {live ? `${live.leverage}×` : '—'}
                    </td>
                  </tr>
                )
              }
              const w = config.symbol_weights[sym] ?? 1
              const allocPct = (w / totalWeight * 100).toFixed(1)
              return (
                <tr key={sym} className="border-b border-gray-900 hover:bg-gray-900/40">
                  <td className="py-1.5 pr-4 text-indigo-300 font-semibold">{sym}</td>
                  <td className="py-1.5 pr-4">
                    <input
                      type="number" min={1} step={1}
                      value={w}
                      title={`Relative capital weight for ${sym}.`}
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
