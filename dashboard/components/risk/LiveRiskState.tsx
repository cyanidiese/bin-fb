'use client'

import { RiskConfig, RiskState } from '@/lib/risk-types'
import { SECTION_CLS, SECTION_HEADER_CLS } from '@/lib/risk-styles'

interface Props {
  config: RiskConfig
  state: RiskState | null
}

export default function LiveRiskState({ config, state }: Props) {
  return (
    <section className={SECTION_CLS}>
      <p
        className={SECTION_HEADER_CLS}
        title="Read-only snapshot from risk_state.json, updated by the bot after each balance change. Polling every 5 seconds."
      >
        E — Live Risk State
        {state && (
          <span className="ml-2 text-[10px] text-gray-600 normal-case tracking-normal font-normal">
            updated {new Date(state.generated_at).toLocaleTimeString()}
          </span>
        )}
      </p>
      <div className="px-4 py-4">
        {!state ? (
          <p className="text-xs text-gray-600 italic">
            No risk_state.json yet — start the bot to generate it.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-6 text-xs font-mono">
              {[
                { label: 'Mode',       value: state.mode,                          color: 'text-gray-300' },
                { label: 'Balance',    value: `$${state.balance.toFixed(2)}`,      color: 'text-gray-300' },
                { label: 'Peak',       value: `$${state.peak_balance.toFixed(2)}`, color: 'text-gray-300' },
                { label: 'Drawdown',   value: `${state.drawdown_pct.toFixed(2)}%`,
                  color: state.drawdown_pct >= config.drawdown_hard_stop_pct
                    ? 'text-red-400'
                    : state.drawdown_pct >= config.drawdown_warning_pct
                    ? 'text-amber-400'
                    : 'text-emerald-400' },
                { label: 'Last event', value: state.last_event || 'none',         color: 'text-gray-500' },
              ].map(s => (
                <div key={s.label}>
                  <span className="text-gray-600">{s.label}: </span>
                  <span className={s.color}>{s.value}</span>
                </div>
              ))}
            </div>
            <details>
              <summary
                className="text-[10px] text-gray-600 cursor-pointer hover:text-gray-400 transition-colors"
                title="Expand to see the full raw risk_state.json snapshot."
              >
                Raw snapshot
              </summary>
              <pre className="mt-2 text-[10px] text-gray-500 font-mono bg-gray-900 rounded p-3 overflow-x-auto max-h-64">
                {JSON.stringify(state, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </div>
    </section>
  )
}
