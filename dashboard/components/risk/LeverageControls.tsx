'use client'

import { RiskConfig } from '@/lib/risk-types'
import { SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS } from '@/lib/risk-styles'
import LabeledInput from './LabeledInput'

interface Props {
  config: RiskConfig
  patchConfig: (patch: Partial<RiskConfig>) => void
  scenario: string
}

export default function LeverageControls({ config, patchConfig, scenario }: Props) {
  const showLevel = scenario !== 'first_has_most' && scenario !== 'best_gets_first'

  return (
    <section className={SECTION_CLS}>
      <p className={SECTION_HEADER_CLS} title="Global leverage bounds. Actual leverage is computed per symbol from the performance score formula, then capped by the active balance tier.">
        C — Leverage Controls
      </p>
      <div className={SECTION_BODY_CLS}>
        <LabeledInput
          label="Base leverage"
          tooltip="Minimum leverage assigned to any symbol, regardless of performance score. Applied when score = 0."
          value={config.base_leverage}
          onChange={v => patchConfig({ base_leverage: Number(v) })}
          min={1} max={20} step={1}
        />
        <LabeledInput
          label="Max leverage"
          tooltip="Upper bound for leverage before the balance-tier ceiling is applied. Applied when performance score = 1."
          value={config.max_leverage}
          onChange={v => patchConfig({ max_leverage: Number(v) })}
          min={1} max={125} step={1}
        />
        <LabeledInput
          label="Min profit factor"
          tooltip="Minimum true profit factor (realized gains ÷ realized losses from best backtest preset) required to allow trading a symbol. Below this, can_open() returns False regardless of capital availability."
          value={config.min_profit_factor}
          onChange={v => patchConfig({ min_profit_factor: Number(v) })}
          min={0.1} max={10} step={0.1}
        />
        {showLevel && (
          <LabeledInput
            label="Max leverage level"
            tooltip="LeverageTracker ceiling — global level will not advance past this value (1–20)"
            value={config.max_leverage_level ?? 5}
            onChange={v => patchConfig({ max_leverage_level: Number(v) })}
            min={1} max={20} step={1}
          />
        )}
        <p className="text-[10px] text-gray-600 font-mono">
          Formula: leverage = base + floor(score × (min(max, tier_ceiling) − base))
        </p>
      </div>
    </section>
  )
}
