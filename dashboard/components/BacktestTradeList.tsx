'use client'

import type { BacktestTrade } from '@/lib/types'

interface Props {
  presetName: string
  trades: BacktestTrade[]
}

const RESULT_STYLE: Record<string, string> = {
  win:     'text-emerald-400 bg-emerald-950/40',
  partial: 'text-amber-400  bg-amber-950/40',
  trail:   'text-sky-400    bg-sky-950/40',
  loss:    'text-red-400    bg-red-950/40',
}

function tpReachColor(pct: number): string {
  if (pct >= 90) return 'text-emerald-400'
  if (pct >= 60) return 'text-amber-400'
  if (pct >= 30) return 'text-orange-400'
  return 'text-red-400'
}

export default function BacktestTradeList({ presetName, trades }: Props) {
  if (trades.length === 0) {
    return (
      <div className="text-gray-500 text-sm py-4 text-center">
        No trades for <span className="text-white font-mono">{presetName}</span>.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-800">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="border-b border-gray-800 bg-gray-900 text-gray-400">
            <th className="px-3 py-2 text-left"    title="Trade number in chronological order">#</th>
            <th className="px-3 py-2 text-left"    title="Trade direction — BUY (long) or SELL (short)">Side</th>
            <th className="px-3 py-2 text-left"    title="Strategy pattern that triggered this entry signal">Signal</th>
            <th className="px-3 py-2 text-left"    title="Trend level that generated the signal — L1 is the finest/fastest, higher levels are broader">Lvl</th>
            <th className="px-3 py-2 text-right"   title="Entry price — open of the candle immediately after the signal fired">Entry</th>
            <th className="px-3 py-2 text-right"   title="Take Profit price — adjusted by tp_multiplier if set">TP</th>
            <th className="px-3 py-2 text-right"   title="Stop Loss price — the Break of Structure invalidation level">SL</th>
            <th className="px-3 py-2 text-right"   title="Partial take activation price — order arms when price first reaches this level; on a later candle a pullback through it closes the trade here">Part@</th>
            <th className="px-3 py-2 text-right"   title="Actual close price of the trade (TP, SL, partial, or trail level)">Close</th>
            <th className="px-3 py-2 text-center"  title="win = full TP hit | partial = armed then pulled back | trail = trailing stop fired | loss = SL hit">Result</th>
            <th className="px-3 py-2 text-right"   title="Profit or loss as % of entry price (positive = profit)">PnL%</th>
            <th className="px-3 py-2 text-right"   title="Highest % of TP distance price reached before the trade closed. Green ≥90%, amber ≥60%, orange ≥30%, red &lt;30%. High values on losses suggest lowering the partial take threshold.">MaxTP%</th>
            <th className="px-3 py-2 text-right"   title="How many candles the trade was open (1 = closed on the entry candle itself)">Candles</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, idx) => {
            const style = RESULT_STYLE[t.result] ?? 'text-gray-400'
            const pnlColor = t.profit_pct >= 0 ? 'text-emerald-400' : 'text-red-400'
            const duration = t.close_candle != null ? t.close_candle - t.open_candle : '—'
            return (
              <tr key={idx} className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
                <td className="px-3 py-1 text-gray-500">{idx + 1}</td>
                <td className={`px-3 py-1 font-bold ${t.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>{t.side}</td>
                <td className="px-3 py-1 text-gray-400 max-w-[160px] truncate">{t.signal_type}</td>
                <td className="px-3 py-1 text-gray-500">{t.level ?? '—'}</td>
                <td className="px-3 py-1 text-right text-gray-300">{t.entry.toFixed(2)}</td>
                <td className="px-3 py-1 text-right text-emerald-500">{t.tp.toFixed(2)}</td>
                <td className="px-3 py-1 text-right text-red-500">{t.sl.toFixed(2)}</td>
                <td className="px-3 py-1 text-right text-amber-500">
                  {t.partial_price != null ? t.partial_price.toFixed(2) : '—'}
                </td>
                <td className="px-3 py-1 text-right text-gray-300">{t.close_price.toFixed(2)}</td>
                <td className="px-3 py-1 text-center">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${style}`}>
                    {t.result}
                  </span>
                </td>
                <td className={`px-3 py-1 text-right font-semibold ${pnlColor}`}>
                  {t.profit_pct >= 0 ? '+' : ''}{t.profit_pct.toFixed(4)}%
                </td>
                <td className={`px-3 py-1 text-right font-semibold ${tpReachColor(t.max_tp_reach_pct)}`}>
                  {t.max_tp_reach_pct.toFixed(1)}%
                </td>
                <td className="px-3 py-1 text-right text-gray-500">{duration}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
