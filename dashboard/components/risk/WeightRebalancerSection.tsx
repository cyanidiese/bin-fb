'use client'

import { WeightRebalancerConfig, WeightRebalanceLogEntry } from '@/lib/risk-types'
import { useEffect, useState } from 'react'
import { SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS } from '@/lib/risk-styles'
import LabeledInput from './LabeledInput'

interface Props {
  config: WeightRebalancerConfig
  mode: string
  patchConfig: (patch: Record<string, unknown>) => void
}

export default function WeightRebalancerSection({ config, mode, patchConfig }: Props) {
  const [log, setLog] = useState<WeightRebalanceLogEntry[]>([])

  useEffect(() => {
    fetch(`/api/public-file?f=weight_rebalance_log_${mode}.json`)
      .then(r => r.ok ? r.json() : [])
      .then(data => setLog(Array.isArray(data) ? data.slice(-1) : []))
      .catch(() => setLog([]))
  }, [mode])

  const patch = (key: keyof WeightRebalancerConfig, value: unknown) =>
    patchConfig({ weight_rebalancer: { ...config, [key]: value } })

  const lastEntry = log[0]
  const lastTs = lastEntry
    ? `${Math.round((Date.now() - lastEntry.ts) / 60000)} min ago`
    : 'Never'

  const sliders: [keyof WeightRebalancerConfig, string, number, number, number][] = [
    ['real_pnl_alpha', 'Real P&L weight (vs backtest)', 0, 1, 0.05],
    ['blend_rate', 'Blend rate per cycle', 0.05, 0.5, 0.05],
    ['weight_floor_ratio', 'Floor ratio (× equal share)', 0.1, 0.9, 0.05],
  ]

  return (
    <section className={SECTION_CLS}>
      <p className={SECTION_HEADER_CLS}>Weight Rebalancer</p>
      <div className={SECTION_BODY_CLS}>

        {/* Enabled toggle */}
        <div className="flex items-center gap-3">
          <label
            className="text-xs text-gray-500 w-52 shrink-0"
            title="Enable or disable automatic symbol weight rebalancing"
          >
            Enabled
          </label>
          <input
            type="checkbox"
            checked={config.enabled}
            onChange={e => patch('enabled', e.target.checked)}
            className="w-4 h-4"
          />
        </div>

        {/* Numeric inputs */}
        <LabeledInput
          label="Rebalance every N candles"
          tooltip="How often (in closed candles) to run a rebalance cycle. Lower = more reactive, higher = more stable."
          value={config.rebalance_candles}
          onChange={v => patch('rebalance_candles', Number(v))}
          min={10} max={1000} step={1}
        />
        <LabeledInput
          label="Backtest window (candles)"
          tooltip="Number of past candles used to compute backtest P&L scores for each symbol."
          value={config.backtest_window_candles}
          onChange={v => patch('backtest_window_candles', Number(v))}
          min={10} max={1000} step={1}
        />

        {/* Sliders */}
        {sliders.map(([key, label, min, max, step]) => (
          <div key={key} className="flex items-center gap-3">
            <label
              className="text-xs text-gray-500 w-52 shrink-0"
              title={label}
            >
              {label}
            </label>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={config[key] as number}
                onChange={e => patch(key, Number(e.target.value))}
                className="w-32 accent-indigo-500"
              />
              <span className="text-xs font-mono text-gray-300 w-10 text-right">
                {(config[key] as number).toFixed(2)}
              </span>
            </div>
          </div>
        ))}

        {/* Last rebalance status */}
        <p className="text-xs text-gray-500">
          Last rebalance:{' '}
          <span className="text-gray-400 font-mono">{lastTs}</span>
        </p>

        {/* Per-symbol table */}
        {lastEntry && (
          <table className="w-full text-xs font-mono border-collapse">
            <thead>
              <tr className="text-gray-600 border-b border-gray-800">
                <th className="text-left py-1 pr-3 font-normal">Symbol</th>
                <th className="text-right py-1 pr-3 font-normal">BT %</th>
                <th className="text-right py-1 pr-3 font-normal">Real P&L</th>
                <th className="text-right py-1 pr-3 font-normal">Score</th>
                <th className="text-right py-1 font-normal">Weight</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(lastEntry.symbols).map(([sym, d]) => (
                <tr key={sym} className="border-b border-gray-900">
                  <td className="py-1 pr-3 text-gray-300">{sym}</td>
                  <td className="text-right py-1 pr-3 text-gray-300">{d.backtest_pct.toFixed(2)}%</td>
                  <td className="text-right py-1 pr-3 text-gray-300">
                    {d.real_pnl_usdt >= 0 ? '+' : ''}{d.real_pnl_usdt.toFixed(2)}
                  </td>
                  <td className="text-right py-1 pr-3 text-gray-400">{d.score.toFixed(3)}</td>
                  <td className="text-right py-1">
                    <span className="text-gray-400">{d.old_weight.toFixed(3)}</span>
                    {' → '}
                    <span className={
                      d.new_weight > d.old_weight
                        ? 'text-emerald-400'
                        : d.new_weight < d.old_weight
                          ? 'text-red-400'
                          : 'text-gray-400'
                    }>
                      {d.new_weight.toFixed(3)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

      </div>
    </section>
  )
}
