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

  // BGF: compute deployable pool from the configured balance and current config values
  // so that editing Balance Tiers, min_balance_pct, or backtest_initial_balance_usdt
  // is reflected immediately without waiting for a live risk_state update.
  const balance = config.backtest_initial_balance_usdt || state?.balance || 0
  // Always derive tier from config (not state?.active_tier) so UI edits take effect instantly.
  // This is the tier that actually controls the Alloc USDT column here.
  const activeTier = balance > 0
    ? ([...config.balance_tiers]
        .sort((a, b) => b.min_balance_usdt - a.min_balance_usdt)
        .find(t => balance >= t.min_balance_usdt) ?? config.balance_tiers[0])
    : null
  const deployable = (() => {
    if (!activeTier || balance <= 0) return 0
    const reserve = balance * (config.min_balance_pct / 100)
    return Math.max(0, balance - reserve) * (activeTier.max_deploy_pct / 100)
  })()

  // BGF: determine which symbols are in the active top-N set.
  // Score ranking is always by profit% desc, independent of the user's display sort.
  // null score (no backtest yet) sorts below all real scores including 0.
  const scoreRanked = bgfMode
    ? [...availableSymbols].sort((a, b) => {
        const sa = state?.per_symbol[a]?.performance_score ?? null
        const sb = state?.per_symbol[b]?.performance_score ?? null
        if (sa === null && sb === null) return 0
        if (sa === null) return 1   // null goes last
        if (sb === null) return -1
        return sb - sa
      })
    : availableSymbols

  const storedN = config.bgf_top_n ?? 0
  const effectiveN = (storedN > 0 && storedN < scoreRanked.length) ? storedN : scoreRanked.length
  const activeSet = bgfMode ? new Set(scoreRanked.slice(0, effectiveN)) : null

  // totalScore sums only the active (top-N) symbols so excluded ones don't dilute shares
  const totalScore = bgfMode
    ? scoreRanked.slice(0, effectiveN).reduce(
        (sum, sym) => sum + Math.max(0, state?.per_symbol[sym]?.performance_score ?? 0), 0
      )
    : 0

  // Build display-sorted list
  const sortedSymbols = bgfMode ? [...availableSymbols].sort((a, b) => {
    const scoreA = state?.per_symbol[a]?.performance_score ?? null
    const scoreB = state?.per_symbol[b]?.performance_score ?? null
    const scoreANum = scoreA ?? 0
    const scoreBNum = scoreB ?? 0
    const activeA = activeSet!.has(a)
    const activeB = activeSet!.has(b)
    // Always pin excluded symbols to the bottom regardless of sort direction
    if (activeA !== activeB) return activeA ? -1 : 1
    const shareA = totalScore > 0 && activeA ? scoreANum / totalScore : 0
    const shareB = totalScore > 0 && activeB ? scoreBNum / totalScore : 0
    const levA = state?.per_symbol[a]?.leverage ?? 0
    const levB = state?.per_symbol[b]?.leverage ?? 0
    let cmp = 0
    if (sortCol === 'symbol') cmp = a.localeCompare(b)
    else if (sortCol === 'score') {
      // null sorts after all real scores
      if (scoreA === null && scoreB === null) cmp = 0
      else if (scoreA === null) cmp = 1
      else if (scoreB === null) cmp = -1
      else cmp = scoreANum - scoreBNum
    }
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

        {/* BGF: active-tier info — tells the user which Balance Tier row drives these numbers */}
        {bgfMode && activeTier && (
          <div className="mb-3 text-xs font-mono text-gray-500 bg-gray-800/50 rounded px-3 py-1.5">
            Using balance <span className="text-gray-300">${balance.toLocaleString()}</span>
            {' '}→ tier ≥${activeTier.min_balance_usdt.toLocaleString()}
            {' '}→ deploy <span className="text-indigo-300">{activeTier.max_deploy_pct}%</span>
            {', '}reserve <span className="text-indigo-300">{config.min_balance_pct}%</span>
            {' '}→ deployable <span className="text-emerald-400">${deployable.toFixed(0)}</span>
          </div>
        )}

        {/* BGF: Top-N control */}
        {bgfMode && (
          <div className="mb-3 flex items-center gap-2 text-xs text-gray-400">
            <span>Allocate only to top</span>
            <input
              type="number"
              min={1}
              max={availableSymbols.length}
              step={1}
              value={storedN > 0 ? storedN : availableSymbols.length}
              title="Only the top-N symbols by profit% receive capital. Others are excluded from order placement."
              onChange={e => {
                const val = Math.max(1, Math.min(availableSymbols.length, Number(e.target.value)))
                // Store 0 when user sets it back to all symbols (= no cap)
                patchConfig({ bgf_top_n: val >= availableSymbols.length ? 0 : val })
              }}
              className={INPUT_CLS + ' w-14'}
            />
            <span>of {availableSymbols.length} symbols</span>
            {storedN > 0 && storedN < availableSymbols.length && (
              <button
                onClick={() => patchConfig({ bgf_top_n: 0 })}
                className="ml-1 text-indigo-400 hover:text-white transition-colors"
                title="Reset to all symbols"
              >
                Reset
              </button>
            )}
          </div>
        )}

        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-gray-600 border-b border-gray-800">
              {bgfMode ? (
                <>
                  <SortHdr col="symbol">Symbol</SortHdr>
                  <SortHdr col="score" title="Best preset's raw total profit % from the last backtest. Higher = larger allocation share.">Profit %</SortHdr>
                  <SortHdr col="alloc" title="score ÷ total scores of active (top-N) symbols">Alloc %</SortHdr>
                  <SortHdr col="usdt" title="USDT allocated to this symbol. Based on Backtest initial balance setting — update that field to see allocation change in real time.">Alloc USDT</SortHdr>
                  <SortHdr col="leverage" title="Dynamic leverage currently assigned to this symbol.">Leverage</SortHdr>
                </>
              ) : (
                <>
                  <th className="text-left py-1 pr-4 font-normal">Symbol</th>
                  <th className="text-left py-1 pr-4 font-normal" title="Relative weight. Higher weight = larger share of deployable capital.">Weight</th>
                  <th className="text-left py-1 pr-4 font-normal" title="weight ÷ total weights">Alloc %</th>
                  <th className="text-left py-1 pr-4 font-normal" title="USDT allocated to this symbol from the current deployable pool.">Alloc USDT</th>
                  <th className="text-left py-1 pr-4 font-normal" title="Dynamic leverage currently assigned to this symbol.">Leverage</th>
                  <th className="text-left py-1 font-normal" title="Best preset's raw total profit % from the last backtest.">Profit %</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {sortedSymbols.map(sym => {
              const live = state?.per_symbol[sym]
              if (bgfMode) {
                const isActive = activeSet!.has(sym)
                const rawScore = live?.performance_score ?? null  // null = no backtest yet
                const score = rawScore ?? 0
                const share = (isActive && totalScore > 0) ? score / totalScore : 0
                const allocPct = (share * 100).toFixed(1)
                const allocUSDT = deployable * share
                const rowCls = isActive
                  ? 'border-b border-gray-900 hover:bg-gray-900/40'
                  : 'border-b border-gray-900 opacity-35'
                return (
                  <tr key={sym} className={rowCls}>
                    <td className="py-1.5 pr-4 text-indigo-300 font-semibold">
                      {sym}
                      {!isActive && <span className="ml-1.5 text-gray-600 font-normal text-[10px]">{rawScore === null ? 'no data' : 'excluded'}</span>}
                    </td>
                    <td className="py-1.5 pr-4 text-gray-400">
                      {rawScore !== null ? `${rawScore.toFixed(2)}%` : '—'}
                    </td>
                    <td className="py-1.5 pr-4 text-gray-400">{isActive ? `${allocPct}%` : '—'}</td>
                    <td className="py-1.5 pr-4 text-gray-400">
                      {isActive && deployable > 0 ? `$${allocUSDT.toFixed(0)}` : '—'}
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
                    {live ? `${live.performance_score.toFixed(2)}%` : '—'}
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
