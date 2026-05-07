'use client'

import { useMemo, useState } from 'react'
import type { BacktestResults } from '@/lib/types'

type TabId = 'combined' | 'side-by-side' | 'best-per-symbol'
type SortDir = 'asc' | 'desc'
interface SortState { key: string; dir: SortDir }

interface Props {
  symbols: string[]
  dataBySymbol: Record<string, BacktestResults | null>
}

const LEVERAGES = [1, 2, 3, 5, 10, 15, 20, 25, 50, 75, 100, 125]

// Sortable header cell — clicking same key toggles asc/desc; new key defaults to desc.
function Th({
  children, sortKey, sort, onSort, align = 'right', title, accent,
}: {
  children: React.ReactNode
  sortKey: string
  sort: SortState
  onSort: (key: string) => void
  align?: 'left' | 'right'
  title?: string
  accent?: boolean
}) {
  const active = sort.key === sortKey
  const arrow = active ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''
  return (
    <th
      onClick={() => onSort(sortKey)}
      title={title}
      className={[
        'py-1 pr-4 font-normal cursor-pointer select-none whitespace-nowrap',
        `text-${align}`,
        active
          ? accent ? 'text-indigo-300' : 'text-gray-300'
          : accent ? 'text-indigo-400/70 hover:text-indigo-300' : 'text-gray-500 hover:text-gray-300',
        'transition-colors',
      ].join(' ')}
    >
      {children}
      <span className={active ? 'text-indigo-400' : 'text-gray-700'}>{arrow || ' ⇅'}</span>
    </th>
  )
}

function sortRows<T>(arr: T[], key: string, dir: SortDir, getValue: (row: T, k: string) => string | number) {
  return [...arr].sort((a, b) => {
    const av = getValue(a, key)
    const bv = getValue(b, key)
    const cmp = typeof av === 'string' ? av.localeCompare(bv as string) : (av as number) - (bv as number)
    return dir === 'asc' ? cmp : -cmp
  })
}

function toggleSort(current: SortState, key: string): SortState {
  if (current.key === key) return { key, dir: current.dir === 'desc' ? 'asc' : 'desc' }
  return { key, dir: 'desc' }
}

export default function CrossSymbolComparison({ symbols, dataBySymbol }: Props) {
  const [tab, setTab] = useState<TabId>('combined')
  const [positionSize, setPositionSize] = useState(1000)
  const [leverage, setLeverage] = useState(1)

  const [minTotal, setMinTotal] = useState<string>('')

  const [combinedSort,   setCombinedSort]   = useState<SortState>({ key: 'total',  dir: 'desc' })
  const [sideBySideSort, setSideBySideSort] = useState<SortState>({ key: '',       dir: 'desc' })
  const [bestSort,       setBestSort]       = useState<SortState>({ key: 'usdt',   dir: 'desc' })

  const loadedSymbols = symbols.filter(s => dataBySymbol[s] != null)

  const allPresetNames = useMemo(() => {
    const names = new Set<string>()
    for (const sym of loadedSymbols) {
      const d = dataBySymbol[sym]
      if (d) Object.keys(d.presets).forEach(n => names.add(n))
    }
    return Array.from(names)
  }, [loadedSymbols, dataBySymbol])

  // Per-symbol USDT value = profit_pct / 100 × positionSize × leverage.
  // Missing symbol → 0 so presets absent from some symbols are penalised in the total.
  const rows = useMemo(() => {
    return allPresetNames.map(name => {
      const perSymbol: Record<string, number> = {}
      for (const sym of loadedSymbols) {
        const pct = dataBySymbol[sym]?.presets[name]?.total_profit_pct ?? null
        perSymbol[sym] = pct !== null ? (pct / 100) * positionSize * leverage : 0
      }
      const total = Object.values(perSymbol).reduce((a, b) => a + b, 0)
      const missing = loadedSymbols.filter(s => dataBySymbol[s]?.presets[name] == null)
      return { name, total, perSymbol, missing }
    })
  }, [allPresetNames, loadedSymbols, dataBySymbol, positionSize, leverage])

  const combinedRows = useMemo(() => {
    return sortRows(rows, combinedSort.key, combinedSort.dir, (r, k) => {
      if (k === 'name')  return r.name
      if (k === 'total') return r.total
      return r.perSymbol[k] ?? 0
    })
  }, [rows, combinedSort])

  const sideBySideRows = useMemo(() => {
    const key = sideBySideSort.key || loadedSymbols[0] || ''
    return sortRows(rows, key, sideBySideSort.dir, (r, k) =>
      k === 'name' ? r.name : (r.perSymbol[k] ?? 0)
    )
  }, [rows, sideBySideSort, loadedSymbols])

  const bestPerSymbol = useMemo(() => {
    const base = loadedSymbols.map(sym => {
      const d = dataBySymbol[sym]
      if (!d) return null
      const presets = Object.values(d.presets)
      if (presets.length === 0) return null
      const best = presets.reduce((a, b) => b.total_profit_pct > a.total_profit_pct ? b : a)
      return {
        symbol: sym,
        preset: best.preset,
        usdt: (best.total_profit_pct / 100) * positionSize * leverage,
        winRate: best.win_rate,
        trades: best.total_trades,
        maxdd: best.max_consecutive_losses,
      }
    }).filter(Boolean) as Array<{
      symbol: string; preset: string; usdt: number
      winRate: number; trades: number; maxdd: number
    }>

    return sortRows(base, bestSort.key, bestSort.dir, (r, k) =>
      r[k as keyof typeof r] as string | number
    )
  }, [loadedSymbols, dataBySymbol, positionSize, leverage, bestSort])

  if (loadedSymbols.length < 2) {
    return (
      <p className="text-sm text-gray-600 italic">
        Cross-symbol comparison available when 2+ symbols have backtest results.
      </p>
    )
  }

  function usdtCell(v: number, isMissing = false) {
    if (isMissing) return <span className="text-gray-600">—</span>
    return (
      <span className={v >= 0 ? 'text-emerald-400' : 'text-red-400'}>
        {v >= 0 ? '+' : ''}${Math.abs(v).toFixed(2)}
      </span>
    )
  }

  const tabs: { id: TabId; label: string }[] = [
    { id: 'combined',        label: 'Total USDT' },
    { id: 'side-by-side',    label: 'Side-by-side' },
    { id: 'best-per-symbol', label: 'Best per symbol' },
  ]

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex gap-1">
          {tabs.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-1 rounded text-xs font-semibold transition-colors ${
                tab === t.id
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3 ml-auto flex-wrap justify-end text-xs text-gray-500">
          <label className="flex items-center gap-1.5">
            <span>Margin</span>
            <span className="text-gray-400">$</span>
            <input
              type="number"
              min={1}
              step={100}
              value={positionSize}
              onChange={e => { const v = Number(e.target.value); if (v > 0) setPositionSize(v) }}
              className="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-300 text-xs font-mono focus:outline-none focus:border-indigo-500"
            />
            <span className="text-gray-600">USDT / trade</span>
          </label>

          <label className="flex items-center gap-1.5">
            <span>Leverage</span>
            <select
              value={leverage}
              onChange={e => setLeverage(Number(e.target.value))}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-300 text-xs font-mono focus:outline-none focus:border-indigo-500"
            >
              {LEVERAGES.map(l => (
                <option key={l} value={l}>{l}×</option>
              ))}
            </select>
          </label>

          <span className="text-gray-600 font-mono">
            Exposure <span className="text-gray-400">${(positionSize * leverage).toLocaleString()}</span>
          </span>

          {leverage >= 20 && (
            <span className="text-amber-500/80 font-mono" title="Approximate liquidation distance ignoring fees">
              ⚠ liq within {(100 / leverage).toFixed(1)}%
            </span>
          )}
        </div>
      </div>

      {tab === 'combined' && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-gray-800">
                <Th sortKey="name"  sort={combinedSort} onSort={k => setCombinedSort(s => toggleSort(s, k))} align="left">
                  <span className="flex items-center gap-2">
                    Preset
                    <span className="flex items-center gap-1 font-normal text-gray-600">
                      <span>min $</span>
                      <input
                        type="number"
                        step={1}
                        placeholder="0"
                        value={minTotal}
                        onChange={e => setMinTotal(e.target.value)}
                        onClick={e => e.stopPropagation()}
                        className="w-16 bg-gray-900 border border-gray-700 rounded px-1.5 py-0 text-gray-300 font-mono focus:outline-none focus:border-indigo-500"
                      />
                    </span>
                  </span>
                </Th>
                <Th sortKey="total" sort={combinedSort} onSort={k => setCombinedSort(s => toggleSort(s, k))} accent
                    title="Sum of USDT profit across all symbols. Presets absent from a symbol contribute $0.">
                  Total USDT
                </Th>
                {loadedSymbols.map(s => (
                  <Th key={s} sortKey={s} sort={combinedSort} onSort={k => setCombinedSort(sv => toggleSort(sv, k))}>
                    {s}
                  </Th>
                ))}
              </tr>
            </thead>
            <tbody>
              {combinedRows.filter(row => minTotal === '' || row.total >= Number(minTotal)).map(row => (
                <tr key={row.name} className="border-b border-gray-900 hover:bg-gray-900/40">
                  <td className="py-1 pr-4 text-gray-300">{row.name}</td>
                  <td className="text-right pr-4 font-semibold">
                    {usdtCell(row.total)}
                    {row.missing.length > 0 && (
                      <span className="ml-1 text-gray-600 text-[10px]" title={`Missing: ${row.missing.join(', ')}`}>
                        ({loadedSymbols.length - row.missing.length}/{loadedSymbols.length})
                      </span>
                    )}
                  </td>
                  {loadedSymbols.map(s => (
                    <td key={s} className="text-right pr-4">
                      {usdtCell(row.perSymbol[s], dataBySymbol[s]?.presets[row.name] == null)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-indigo-900 bg-indigo-950/30">
                <td
                  className="py-1 pr-4 text-indigo-400 font-semibold whitespace-nowrap"
                  title="Maximum possible earnings if the best-performing preset were used for each symbol independently"
                >
                  Best per symbol
                </td>
                <td className="text-right pr-4 font-semibold">
                  {usdtCell(bestPerSymbol.reduce((sum, r) => sum + r.usdt, 0))}
                </td>
                {loadedSymbols.map(s => {
                  const r = bestPerSymbol.find(x => x.symbol === s)
                  return (
                    <td key={s} className="text-right pr-4" title={r ? `Best preset: ${r.preset}` : undefined}>
                      {r ? usdtCell(r.usdt) : <span className="text-gray-600">—</span>}
                    </td>
                  )
                })}
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {tab === 'side-by-side' && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-gray-800">
                <Th sortKey="name" sort={sideBySideSort} onSort={k => setSideBySideSort(s => toggleSort(s, k))} align="left">Preset</Th>
                {loadedSymbols.map(s => (
                  <Th key={s} sortKey={s} sort={{ ...sideBySideSort, key: sideBySideSort.key || loadedSymbols[0] }}
                      onSort={k => setSideBySideSort(sv => toggleSort(sv, k))}>
                    {s}
                  </Th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sideBySideRows.map(row => (
                <tr key={row.name} className="border-b border-gray-900 hover:bg-gray-900/40">
                  <td className="py-1 pr-4 text-gray-300">{row.name}</td>
                  {loadedSymbols.map(s => (
                    <td key={s} className="text-right pr-4">
                      {usdtCell(row.perSymbol[s], dataBySymbol[s]?.presets[row.name] == null)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'best-per-symbol' && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-gray-800">
                <Th sortKey="symbol" sort={bestSort} onSort={k => setBestSort(s => toggleSort(s, k))} align="left">Symbol</Th>
                <Th sortKey="preset" sort={bestSort} onSort={k => setBestSort(s => toggleSort(s, k))} align="left">Best preset</Th>
                <Th sortKey="usdt"    sort={bestSort} onSort={k => setBestSort(s => toggleSort(s, k))} accent
                    title="USDT profit at the configured position size and leverage">
                  USDT profit
                </Th>
                <Th sortKey="winRate" sort={bestSort} onSort={k => setBestSort(s => toggleSort(s, k))}>Win%</Th>
                <Th sortKey="trades"  sort={bestSort} onSort={k => setBestSort(s => toggleSort(s, k))}>Trades</Th>
                <Th sortKey="maxdd"   sort={bestSort} onSort={k => setBestSort(s => toggleSort(s, k))}>MaxDD</Th>
              </tr>
            </thead>
            <tbody>
              {bestPerSymbol.map(row => (
                <tr key={row.symbol} className="border-b border-gray-900 hover:bg-gray-900/40">
                  <td className="py-1 pr-4 text-indigo-300 font-semibold">{row.symbol}</td>
                  <td className="py-1 pr-4 text-gray-300">{row.preset}</td>
                  <td className="text-right pr-4 font-semibold">{usdtCell(row.usdt)}</td>
                  <td className="text-right pr-4 text-gray-300">{(row.winRate * 100).toFixed(1)}%</td>
                  <td className="text-right pr-4 text-gray-300">{row.trades}</td>
                  <td className="text-right pr-4 text-gray-300">{row.maxdd}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
