'use client'

import { useState, useMemo } from 'react'
import type { BacktestPreset } from '@/lib/types'

type SortKey = keyof Pick<
  BacktestPreset,
  'preset' | 'total_trades' | 'wins' | 'partials' | 'trails' | 'losses' | 'win_rate' | 'total_profit_pct' | 'avg_rr' | 'max_consecutive_losses' | 'avg_max_tp_reach_pct'
>

interface Props {
  presets: BacktestPreset[]
  selectedPreset: string | null
  onSelect: (name: string) => void
  onDelete?: (name: string) => void
}

const COLS: { key: SortKey; label: string; align: 'left' | 'right'; title: string }[] = [
  { key: 'preset',                label: 'Preset',  align: 'left',  title: 'Parameter preset name — click a row to view its individual trades' },
  { key: 'total_trades',          label: 'Trades',  align: 'right', title: 'Total number of trades opened during the backtest' },
  { key: 'wins',                  label: 'Wins',    align: 'right', title: 'Trades that reached Take Profit in full' },
  { key: 'partials',              label: 'Part',    align: 'right', title: 'Partial exits — price reached the arm threshold then pulled back below it, closing at the partial price' },
  { key: 'trails',                label: 'Trail',   align: 'right', title: 'Trailing stop exits — price pulled back past the dynamic trail level after arming, closed at trail price' },
  { key: 'losses',                label: 'Losses',  align: 'right', title: 'Trades that hit Stop Loss' },
  { key: 'win_rate',              label: 'Win%',    align: 'right', title: 'Win rate = (Wins + Partials + Trails) / Total trades' },
  { key: 'total_profit_pct',      label: 'Profit%', align: 'right', title: 'Sum of all trade P&L as % of entry price (not compounded). Positive = profitable preset overall.' },
  { key: 'avg_rr',                label: 'Avg RR',  align: 'right', title: 'Average risk-to-reward ratio across all trades — TP distance divided by SL distance' },
  { key: 'max_consecutive_losses',label: 'MaxDD',   align: 'right', title: 'Max consecutive losses — longest losing streak. High values signal drawdown risk.' },
  { key: 'avg_max_tp_reach_pct',  label: 'AvgTP%',  align: 'right', title: 'Average % of TP distance price reached on non-winning trades. Above 80% means price nearly hit TP — consider lowering the partial take threshold.' },
]

export default function BacktestSummaryTable({ presets, selectedPreset, onSelect, onDelete }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('total_profit_pct')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  // Row hover / delete state
  const [hoveredRow, setHoveredRow] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)

  // Quick-delete: skip per-item confirmation
  const [skipConfirm, setSkipConfirm] = useState(false)
  const [confirmEnableSkip, setConfirmEnableSkip] = useState(false)

  const sorted = useMemo(() => {
    return [...presets].sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      if (typeof av === 'string' && typeof bv === 'string') {
        return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av)
      }
      return sortDir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number)
    })
  }, [presets, sortKey, sortDir])

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  function handleRowEnter(name: string) {
    setHoveredRow(name)
    // Cancel any pending confirmation when moving to a different row
    if (pendingDelete && pendingDelete !== name) setPendingDelete(null)
  }

  function handleDeleteClick(e: React.MouseEvent, name: string) {
    e.stopPropagation()
    if (!onDelete) return
    if (skipConfirm) {
      onDelete(name)
    } else {
      setPendingDelete(name)
    }
  }

  function handleConfirmDelete(e: React.MouseEvent, name: string) {
    e.stopPropagation()
    setPendingDelete(null)
    onDelete?.(name)
  }

  function handleCancelDelete(e: React.MouseEvent) {
    e.stopPropagation()
    setPendingDelete(null)
  }

  const arrow = (key: SortKey) => key === sortKey ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''

  return (
    <div className="space-y-1">

      {/* Panel header — quick-delete toggle (only shown when deletion is enabled) */}
      {onDelete && (
        <div className="flex items-center justify-end px-1 min-h-[22px]">
          {confirmEnableSkip ? (
            <span className="flex items-center gap-2 text-[11px] font-mono">
              <span className="text-amber-400">Enable quick delete? Presets will be removed without confirmation.</span>
              <button
                onClick={() => { setSkipConfirm(true); setConfirmEnableSkip(false) }}
                className="text-emerald-400 hover:text-emerald-300 font-semibold transition-colors"
              >
                Yes, enable
              </button>
              <button
                onClick={() => setConfirmEnableSkip(false)}
                className="text-gray-500 hover:text-gray-300 transition-colors"
              >
                Cancel
              </button>
            </span>
          ) : (
            <label className="flex items-center gap-1.5 text-[11px] text-gray-600 cursor-pointer select-none hover:text-gray-400 transition-colors">
              <input
                type="checkbox"
                checked={skipConfirm}
                onChange={e => {
                  if (e.target.checked) {
                    setConfirmEnableSkip(true)   // require confirmation to enable
                  } else {
                    setSkipConfirm(false)        // disabling needs no confirmation
                  }
                }}
                className="accent-amber-500 w-3 h-3"
              />
              Quick delete (skip confirmation)
            </label>
          )}
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm font-mono">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900">
              {COLS.map(col => (
                <th
                  key={col.key}
                  onClick={() => handleSort(col.key)}
                  title={col.title}
                  className={`px-3 py-2 cursor-pointer select-none whitespace-nowrap text-gray-400 hover:text-white transition-colors ${col.align === 'right' ? 'text-right' : 'text-left'}`}
                >
                  {col.label}{arrow(col.key)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map(p => {
              const isSelected = p.preset === selectedPreset
              const isPending = pendingDelete === p.preset
              const isHovered = hoveredRow === p.preset
              const showActions = onDelete && (isHovered || isPending)
              const profitColor = p.total_profit_pct > 0 ? 'text-emerald-400' : p.total_profit_pct < 0 ? 'text-red-400' : 'text-gray-400'

              return (
                <tr
                  key={p.preset}
                  onClick={() => !isPending && onSelect(p.preset)}
                  onMouseEnter={() => handleRowEnter(p.preset)}
                  onMouseLeave={() => setHoveredRow(null)}
                  className={`border-b border-gray-800/60 transition-colors ${
                    isPending
                      ? 'bg-red-950/20 cursor-default'
                      : isSelected
                        ? 'bg-blue-950/60 cursor-pointer'
                        : 'hover:bg-gray-800/40 cursor-pointer'
                  }`}
                >
                  {/* Preset name cell — button sits inline at the right end */}
                  <td
                    className="px-3 py-2 text-left"
                    onClick={isPending ? e => e.stopPropagation() : undefined}
                  >
                    {showActions ? (
                      <div className="flex items-center gap-2 min-w-0">
                        {/* Name truncates to make room for the inline button */}
                        <span className="font-semibold text-white truncate min-w-0 flex-1">
                          {p.preset}
                        </span>
                        <div
                          className="flex items-center gap-1.5 shrink-0 text-[10px] font-mono"
                          onClick={e => e.stopPropagation()}
                        >
                          {isPending ? (
                            <>
                              <span className="text-gray-500">Delete?</span>
                              <button
                                onClick={e => handleConfirmDelete(e, p.preset)}
                                className="text-red-400 hover:text-red-300 font-semibold transition-colors"
                              >
                                Yes
                              </button>
                              <span className="text-gray-700">|</span>
                              <button
                                onClick={handleCancelDelete}
                                className="text-gray-500 hover:text-gray-300 transition-colors"
                              >
                                No
                              </button>
                            </>
                          ) : (
                            <button
                              onClick={e => handleDeleteClick(e, p.preset)}
                              className="text-gray-600 hover:text-red-400 transition-colors whitespace-nowrap"
                            >
                              🗑 Remove
                            </button>
                          )}
                        </div>
                      </div>
                    ) : (
                      <span className="font-semibold text-white">{p.preset}</span>
                    )}
                  </td>

                  <td className="px-3 py-2 text-right text-gray-300">{p.total_trades}</td>
                  <td className="px-3 py-2 text-right text-emerald-400">{p.wins}</td>
                  <td className="px-3 py-2 text-right text-amber-400">{p.partials}</td>
                  <td className="px-3 py-2 text-right text-sky-400">{p.trails}</td>
                  <td className="px-3 py-2 text-right text-red-400">{p.losses}</td>
                  <td className="px-3 py-2 text-right text-gray-300">{(p.win_rate * 100).toFixed(1)}%</td>
                  <td className={`px-3 py-2 text-right font-semibold ${profitColor}`}>
                    {p.total_profit_pct >= 0 ? '+' : ''}{p.total_profit_pct.toFixed(2)}%
                  </td>
                  <td className="px-3 py-2 text-right text-gray-300">{p.avg_rr.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right text-gray-400">{p.max_consecutive_losses}</td>
                  <td className="px-3 py-2 text-right text-gray-400">{p.avg_max_tp_reach_pct.toFixed(1)}%</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
