'use client'

import { BotResults } from '@/lib/types'

interface HeaderProps {
  data: BotResults
}

// Top bar showing the trading context at a glance:
// symbol, timeframe, safety mode badge, live price, and snapshot age.
export default function Header({ data }: HeaderProps) {
  // Yellow badge for testnet (safe), red for live (real money at risk)
  const modeColor = data.mode === 'testnet' ? 'bg-yellow-500 text-black' : 'bg-red-600 text-white'

  // Human-readable local time of the last bot export
  const ts = new Date(data.generated_at).toLocaleString()

  return (
    <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-bold tracking-tight">{data.symbol}</h1>
        <span className="text-gray-400 text-sm">{data.timeframe}</span>
        <span className={`text-xs font-semibold px-2 py-0.5 rounded ${modeColor}`}>
          {data.mode.toUpperCase()}
        </span>
      </div>
      <div className="flex items-center gap-6 text-sm">
        <span className="text-gray-400">
          Price: <span className="text-white font-mono font-semibold">
            {data.current_price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        </span>
        {/* Snapshot timestamp — tells the user how stale the data is */}
        <span className="text-gray-500 text-xs">{ts}</span>
      </div>
    </div>
  )
}
