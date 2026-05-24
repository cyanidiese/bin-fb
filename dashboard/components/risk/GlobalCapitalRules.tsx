'use client'

import { BalanceTier, RiskConfig, RiskState } from '@/lib/risk-types'
import { INPUT_CLS, SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS } from '@/lib/risk-styles'
import LabeledInput from './LabeledInput'

interface Props {
  config: RiskConfig
  state: RiskState | null
  patchConfig: (patch: Partial<RiskConfig>) => void
}

export default function GlobalCapitalRules({ config, state, patchConfig }: Props) {
  // Which tier is active for the configured backtest balance (same logic as allocation widget)
  const btBalance = config.backtest_initial_balance_usdt || state?.balance || 0
  const activeTierIdx = btBalance > 0
    ? config.balance_tiers.reduce((best, t, i) =>
        t.min_balance_usdt <= btBalance &&
        t.min_balance_usdt >= (config.balance_tiers[best]?.min_balance_usdt ?? -1)
          ? i : best, 0)
    : -1

  function patchTier(idx: number, patch: Partial<BalanceTier>) {
    const tiers = config.balance_tiers.map((t, i) => i === idx ? { ...t, ...patch } : t)
    patchConfig({ balance_tiers: tiers })
  }

  function addTier() {
    patchConfig({
      balance_tiers: [
        ...config.balance_tiers,
        { min_balance_usdt: 0, max_deploy_pct: 40, max_leverage_ceiling: 5 },
      ],
    })
  }

  function removeTier(idx: number) {
    if (config.balance_tiers.length <= 1) return
    patchConfig({ balance_tiers: config.balance_tiers.filter((_, i) => i !== idx) })
  }

  return (
    <section className={SECTION_CLS}>
      <p className={SECTION_HEADER_CLS} title="Controls how much of your balance is deployed across all symbols combined.">
        A — Global Capital Rules
      </p>
      <div className={SECTION_BODY_CLS}>

        {/* Active tier display */}
        {state && (
          <div className="text-xs font-mono text-gray-500 bg-gray-800/60 rounded px-3 py-2">
            <span title="The balance tier currently active, based on your live balance.">
              Active tier:
            </span>{' '}
            balance ≥ ${state.active_tier.min_balance_usdt.toLocaleString()} →{' '}
            deploy up to{' '}
            <span className="text-indigo-300">{state.active_tier.max_deploy_pct}%</span>,{' '}
            max leverage{' '}
            <span className="text-indigo-300">{state.active_tier.max_leverage_ceiling}×</span>
          </div>
        )}

        {/* Balance tiers editor */}
        <div>
          <p
            className="text-xs text-gray-500 mb-2"
            title="Define balance thresholds that unlock higher deployment caps and leverage ceilings. The highest tier whose min_balance ≤ current balance is active."
          >
            Balance tiers
          </p>
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-600 border-b border-gray-800">
                <th className="text-left py-1 pr-4 font-normal" title="Minimum balance in USDT to activate this tier.">Min balance (USDT)</th>
                <th className="text-left py-1 pr-4 font-normal" title="Maximum % of available balance that may be deployed across all symbols when this tier is active.">Max deploy %</th>
                <th className="text-left py-1 pr-4 font-normal" title="Maximum leverage ceiling for any symbol when this tier is active. Further limited by max_leverage.">Leverage ceiling</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {config.balance_tiers.map((tier, idx) => (
                <tr
                  key={idx}
                  className={`border-b border-gray-900 ${idx === activeTierIdx ? 'bg-indigo-950/40' : ''}`}
                  title={idx === activeTierIdx ? `Active tier for balance $${btBalance.toLocaleString()} — changes here update the allocation widget` : ''}
                >
                  <td className="py-1 pr-4">
                    <div className="flex items-center gap-2">
                      <input
                        type="number" min={0} step={100}
                        value={tier.min_balance_usdt}
                        title="Minimum account balance (USDT) to activate this tier."
                        onChange={e => patchTier(idx, { min_balance_usdt: Number(e.target.value) })}
                        className={INPUT_CLS}
                      />
                      {idx === activeTierIdx && (
                        <span className="text-[10px] text-indigo-400 whitespace-nowrap">active</span>
                      )}
                    </div>
                  </td>
                  <td className="py-1 pr-4">
                    <input
                      type="number" min={1} max={100} step={1}
                      value={tier.max_deploy_pct}
                      title="Maximum % of available balance to deploy across all symbols when this tier is active."
                      onChange={e => patchTier(idx, { max_deploy_pct: Number(e.target.value) })}
                      className={INPUT_CLS}
                    />
                  </td>
                  <td className="py-1 pr-4">
                    <input
                      type="number" min={1} max={125} step={1}
                      value={tier.max_leverage_ceiling}
                      title="Hard cap on leverage for any symbol in this tier. Overrides max_leverage if lower."
                      onChange={e => patchTier(idx, { max_leverage_ceiling: Number(e.target.value) })}
                      className={INPUT_CLS}
                    />
                  </td>
                  <td className="py-1 text-right">
                    <button
                      onClick={() => removeTier(idx)}
                      disabled={config.balance_tiers.length <= 1}
                      title="Remove this tier. At least one tier must remain."
                      className="text-[10px] text-red-500 hover:text-red-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            onClick={addTier}
            title="Add a new balance tier row."
            className="mt-2 text-[10px] text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            + Add tier
          </button>
        </div>

        <LabeledInput
          label="Backtest initial balance (USDT)"
          tooltip="Starting balance used when simulating capital deployment during backtests. Higher values produce more stable drawdown percentages."
          value={config.backtest_initial_balance_usdt}
          onChange={v => patchConfig({ backtest_initial_balance_usdt: Number(v) })}
          min={10} step={100}
        />


      </div>
    </section>
  )
}
