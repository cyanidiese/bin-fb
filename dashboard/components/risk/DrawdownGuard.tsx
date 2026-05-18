'use client'

import { RiskConfig, RiskState } from '@/lib/risk-types'
import { SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS } from '@/lib/risk-styles'
import LabeledInput from './LabeledInput'

interface Props {
  config: RiskConfig
  state: RiskState | null
  patchConfig: (patch: Partial<RiskConfig>) => void
}

export default function DrawdownGuard({ config, state, patchConfig }: Props) {
  function handleReset() {
    if (window.confirm(
      'Reset the hard stop latch?\n\n' +
      'This allows new entries again. Only do this after reviewing ' +
      'your drawdown and confirming you are ready to resume trading.'
    )) {
      fetch('/api/risk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...config, _reset_hard_stop: true }),
      })
    }
  }

  return (
    <section className={SECTION_CLS}>
      <p className={SECTION_HEADER_CLS} title="Protects capital from large losing streaks. The hard stop is latched and requires a manual reset.">
        D — Drawdown Guard
      </p>
      <div className={SECTION_BODY_CLS}>
        <LabeledInput
          label="Reserve floor %"
          tooltip="Percentage of balance kept untouched at all times. The allocation engine subtracts this reserve before computing deployable capital. An emergency alert fires if balance drops below this % of the peak."
          value={config.min_balance_pct ?? 15}
          onChange={v => patchConfig({ min_balance_pct: Number(v) })}
          min={0} max={50} step={1}
        />
        <LabeledInput
          label="Warning threshold %"
          tooltip="When drawdown from peak balance exceeds this %, a warning banner appears and a risk event is logged. Resets automatically when balance recovers."
          value={config.drawdown_warning_pct}
          onChange={v => patchConfig({ drawdown_warning_pct: Number(v) })}
          min={1} max={50} step={0.5}
        />
        <LabeledInput
          label="Hard stop threshold %"
          tooltip="When drawdown from peak exceeds this %, all new entries are blocked. Existing orders close naturally. Requires manual reset — does not auto-reset."
          value={config.drawdown_hard_stop_pct}
          onChange={v => patchConfig({ drawdown_hard_stop_pct: Number(v) })}
          min={1} max={100} step={0.5}
        />

        {/* Live drawdown display */}
        <div className="flex items-center gap-6 text-xs font-mono">
          <div title="Current drawdown from peak balance, updated every 5 seconds.">
            <span className="text-gray-600">Current drawdown: </span>
            <span className={
              state
                ? state.drawdown_pct >= config.drawdown_hard_stop_pct
                  ? 'text-red-400'
                  : state.drawdown_pct >= config.drawdown_warning_pct
                  ? 'text-amber-400'
                  : 'text-emerald-400'
                : 'text-gray-600'
            }>
              {state ? `${state.drawdown_pct.toFixed(2)}%` : '—'}
            </span>
          </div>

          <div className="flex items-center gap-2" title="Hard stop status. Red = active, all new entries blocked.">
            <span className="text-gray-600">Hard stop:</span>
            <span className={`font-semibold ${state?.hard_stop_active ? 'text-red-400' : 'text-emerald-400'}`}>
              {state ? (state.hard_stop_active ? '● ACTIVE' : '● OK') : '—'}
            </span>
          </div>

          {state?.hard_stop_active && (
            <button
              onClick={handleReset}
              title="Reset the drawdown hard stop latch. Requires confirmation. The bot must be restarted to take effect."
              className="px-3 py-1 rounded border border-red-700 bg-red-950/40 text-red-400 text-[10px] font-semibold hover:bg-red-900/40 transition-colors"
            >
              Reset drawdown guard
            </button>
          )}
        </div>

        {state?.warning_active && !state.hard_stop_active && (
          <div
            className="rounded border border-amber-700/60 bg-amber-900/20 px-3 py-2 text-xs text-amber-300 font-mono"
            title="Drawdown has crossed the warning threshold. Review your positions."
          >
            ⚠ Drawdown warning — {state.drawdown_pct.toFixed(2)}% from peak {state.peak_balance != null ? `$${state.peak_balance.toFixed(2)}` : '—'}
          </div>
        )}

        {state?.hard_stop_active && (
          <div
            className="rounded border border-red-700/60 bg-red-900/20 px-3 py-2 text-xs text-red-300 font-mono"
            title="Hard stop is active. No new entries will be opened until it is manually reset."
          >
            ✗ HARD STOP ACTIVE — all new entries blocked. Reset to resume.
          </div>
        )}
      </div>
    </section>
  )
}
