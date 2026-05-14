'use client'

import { RiskConfig } from '@/lib/risk-types'
import { SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS } from '@/lib/risk-styles'

interface Props {
  config: RiskConfig
  patchConfig: (patch: Partial<RiskConfig>) => void
}

export default function ScenarioSection({ config, patchConfig }: Props) {
  const scenario = (config.scenario ?? 'default') as string

  return (
    <section className={SECTION_CLS}>
      <p className={SECTION_HEADER_CLS} title="Controls both how leverage is assigned and how deployable capital is allocated across symbols.">
        Scenario
      </p>
      <div className={SECTION_BODY_CLS}>
        <div className="flex items-center gap-3">
          <label className="text-xs text-gray-500 w-52 shrink-0" title="Controls how each symbol's leverage is determined and how the deployable budget is distributed.">
            Active scenario
          </label>
          <select
            value={scenario}
            onChange={e => patchConfig({ scenario: e.target.value })}
            className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-indigo-500 cursor-pointer"
          >
            <option value="default">Default — cross-symbol progression, weight-based allocation</option>
            <option value="allocation">Allocation — per-symbol independent, weight-based allocation</option>
            <option value="first_has_most">First Has the Most — score-based leverage, weight-based allocation</option>
            <option value="best_gets_first">Best Gets First — score-based leverage, priority allocation</option>
          </select>
        </div>
        <p className="text-[10px] text-gray-500 font-mono">
          {scenario === 'allocation'
            ? 'Leverage: each symbol advances independently after 1 close at its current level. Allocation: weight-based split.'
            : scenario === 'first_has_most'
            ? 'Leverage: base + floor(score × (max − base)), instant. Allocation: weight-based split.'
            : scenario === 'best_gets_first'
            ? 'Leverage: base + floor(score × (max − base)), instant. Allocation: best-scoring symbol gets the full deployable budget; each next symbol gets the remainder.'
            : 'Leverage: all symbols must complete level N before any advances to N+1. Allocation: weight-based split.'}
        </p>
      </div>
    </section>
  )
}
