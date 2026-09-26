// Weight and Alloc% per symbol, exactly as the Risk page's "B — Per-Symbol Allocation"
// table shows them.
//
// Shared by that table and the Trades page symbol picker, so the label on a picker
// item can never disagree with the Risk page.

import type { RiskConfig, RiskState } from '@/lib/risk-types'

/** The Risk page's rule for which table it shows — see app/risk/page.tsx. */
export function isBgf(config: Pick<RiskConfig, 'scenario'>): boolean {
  return config.scenario === 'best_gets_first'
}

export interface SymbolAlloc {
  weight: number
  /** Share of the deployable budget in percent; null = excluded / no data (BGF only). */
  allocPct: number | null
}

/** The weights the Risk page edits: risk_config.symbol_weights, with any symbol it
 *  lists but the config lacks shown at 1 (the Risk page fills those in the same way). */
export function effectiveWeights(
  config: Pick<RiskConfig, 'symbol_weights'>, symbols: string[],
): Record<string, number> {
  const w = { ...(config.symbol_weights ?? {}) }
  for (const sym of symbols) if (!(sym in w)) w[sym] = 1
  return w
}

/**
 * Weight-based scenarios (default, allocation, tats, ...): Alloc% = weight ÷ the sum of
 * ALL weights in the config. Best Gets First: Alloc% = the symbol's backtest score ×
 * weight ÷ the sum over the top-N symbols; outside the top N it gets nothing (null).
 */
export function symbolAllocations(
  config: RiskConfig, state: RiskState | null, symbols: string[], bgfMode: boolean,
): Record<string, SymbolAlloc> {
  const weights = effectiveWeights(config, symbols)
  const out: Record<string, SymbolAlloc> = {}

  if (!bgfMode) {
    const total = Object.values(weights).reduce((a, b) => a + b, 0) || 1
    for (const sym of symbols) {
      const w = weights[sym] ?? 1
      out[sym] = { weight: w, allocPct: (w / total) * 100 }
    }
    return out
  }

  const raw = (sym: string) => state?.per_symbol[sym]?.performance_score ?? null
  const eff = (sym: string) => {
    const r = raw(sym)
    return r === null ? 0 : Math.max(0, r) * Math.max(0, weights[sym] ?? 1)
  }
  const ranked = [...symbols].sort((a, b) => {
    const ra = raw(a), rb = raw(b)
    if (ra === null && rb === null) return 0
    if (ra === null) return 1
    if (rb === null) return -1
    return eff(b) - eff(a)
  })
  const storedN = config.bgf_top_n ?? 0
  const n = storedN > 0 && storedN < ranked.length ? storedN : ranked.length
  const active = new Set(ranked.slice(0, n))
  const total = ranked.slice(0, n).reduce((s, sym) => s + eff(sym), 0)
  for (const sym of symbols) {
    out[sym] = {
      weight: weights[sym] ?? 1,
      allocPct: active.has(sym) && total > 0 ? (eff(sym) / total) * 100 : null,
    }
  }
  return out
}
