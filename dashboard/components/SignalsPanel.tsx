'use client'

import { Signal } from '@/lib/types'

interface Props {
  signals: Signal[]
}

function fmt(price: number) {
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// List of active trading signals produced by the bot's strategy.
// Each signal names the trend level that triggered it, the direction (BUY/SELL),
// the strategy pattern that matched, and the suggested target and stop prices.
// Signals are not filtered by the level selector — all active signals are always shown.
export default function SignalsPanel({ signals }: Props) {
  if (signals.length === 0) {
    return (
      <div className="rounded-lg border border-gray-800 p-4 text-gray-600 text-sm">
        No active signals
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-800 divide-y divide-gray-800">
      {signals.map((s, i) => (
        <div key={i} className="flex items-center gap-4 px-4 py-3 text-sm">
          {/* Level that generated this signal */}
          <span className="font-mono font-semibold text-gray-400">L{s.level}</span>

          {/* Trade direction — green for long, red for short */}
          <span className={`font-bold ${s.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
            {s.side}
          </span>

          {/* Strategy pattern name (underscores replaced with spaces for readability) */}
          <span className="text-gray-500">{s.signal_type.replace(/_/g, ' ')}</span>

          {/* Target and optional stop prices, right-aligned */}
          <span className="ml-auto text-xs text-gray-400">
            target <span className="font-mono text-white">{fmt(s.target)}</span>
            {s.stop != null && <> · stop <span className="font-mono text-white">{fmt(s.stop)}</span></>}
          </span>
        </div>
      ))}
    </div>
  )
}
