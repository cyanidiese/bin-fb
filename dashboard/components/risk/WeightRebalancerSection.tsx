'use client'

import { WeightRebalancerConfig, WeightRebalanceLogEntry } from '@/lib/risk-types'
import { useEffect, useState } from 'react'

interface Props {
  config: WeightRebalancerConfig
  mode: string
  patchConfig: (patch: Record<string, unknown>) => void
}

export default function WeightRebalancerSection({ config, mode, patchConfig }: Props) {
  const [log, setLog] = useState<WeightRebalanceLogEntry[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    fetch(`/api/public-file?f=weight_rebalance_log_${mode}.json`)
      .then(r => r.ok ? r.json() : [])
      .then(data => setLog(Array.isArray(data) ? data.slice(-1) : []))
      .catch(() => setLog([]))
  }, [open, mode])

  const patch = (key: keyof WeightRebalancerConfig, value: unknown) =>
    patchConfig({ weight_rebalancer: { ...config, [key]: value } })

  const lastEntry = log[0]
  const lastTs = lastEntry
    ? `${Math.round((Date.now() - lastEntry.ts) / 60000)} min ago`
    : 'Never'

  return (
    <section className="border border-neutral-700 rounded p-4 mt-6">
      <button
        className="w-full flex justify-between items-center text-left font-semibold text-sm"
        onClick={() => setOpen(o => !o)}
      >
        <span>Weight Rebalancer</span>
        <span>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="mt-4 space-y-4">
          {/* Enable toggle */}
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              checked={config.enabled}
              onChange={e => patch('enabled', e.target.checked)}
              className="w-4 h-4"
            />
            Enabled
          </label>

          {/* Numeric controls */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <label className="flex flex-col gap-1">
              Rebalance every N candles
              <input
                type="number" min={10} max={1000} step={1}
                value={config.rebalance_candles}
                onChange={e => patch('rebalance_candles', Number(e.target.value))}
                className="bg-neutral-800 border border-neutral-600 rounded px-2 py-1 w-28"
              />
            </label>
            <label className="flex flex-col gap-1">
              Backtest window (candles)
              <input
                type="number" min={10} max={1000} step={1}
                value={config.backtest_window_candles}
                onChange={e => patch('backtest_window_candles', Number(e.target.value))}
                className="bg-neutral-800 border border-neutral-600 rounded px-2 py-1 w-28"
              />
            </label>
          </div>

          {/* Sliders */}
          {([
            ['real_pnl_alpha', 'Real P&L weight (vs backtest)', 0, 1, 0.05],
            ['blend_rate', 'Blend rate per cycle', 0.05, 0.5, 0.05],
            ['weight_floor_ratio', 'Floor ratio (× equal share)', 0.1, 0.9, 0.05],
          ] as [keyof WeightRebalancerConfig, string, number, number, number][]).map(
            ([key, label, min, max, step]) => (
              <label key={key} className="flex flex-col gap-1 text-sm">
                {label}: <span className="font-mono">{(config[key] as number).toFixed(2)}</span>
                <input
                  type="range" min={min} max={max} step={step}
                  value={config[key] as number}
                  onChange={e => patch(key, Number(e.target.value))}
                  className="w-full"
                />
              </label>
            )
          )}

          {/* Status */}
          <div className="text-sm text-neutral-400">Last rebalance: {lastTs}</div>

          {/* Per-symbol table */}
          {lastEntry && (
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="text-neutral-400 border-b border-neutral-700">
                  <th className="text-left py-1 pr-3">Symbol</th>
                  <th className="text-right py-1 pr-3">BT %</th>
                  <th className="text-right py-1 pr-3">Real P&L</th>
                  <th className="text-right py-1 pr-3">Score</th>
                  <th className="text-right py-1">Weight</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(lastEntry.symbols).map(([sym, d]) => (
                  <tr key={sym} className="border-b border-neutral-800">
                    <td className="py-1 pr-3 font-mono">{sym}</td>
                    <td className="text-right py-1 pr-3">{d.backtest_pct.toFixed(2)}%</td>
                    <td className="text-right py-1 pr-3">{d.real_pnl_usdt >= 0 ? '+' : ''}{d.real_pnl_usdt.toFixed(2)}</td>
                    <td className="text-right py-1 pr-3">{d.score.toFixed(3)}</td>
                    <td className="text-right py-1">
                      <span className="text-neutral-500">{d.old_weight.toFixed(3)}</span>
                      {' → '}
                      <span className={d.new_weight > d.old_weight ? 'text-green-400' : d.new_weight < d.old_weight ? 'text-red-400' : ''}>
                        {d.new_weight.toFixed(3)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  )
}
