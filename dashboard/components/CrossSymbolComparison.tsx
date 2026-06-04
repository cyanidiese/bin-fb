'use client'

import { useMemo, useState } from 'react'
import type { BacktestResults } from '@/lib/types'

type TabId = 'combined' | 'side-by-side' | 'best-per-symbol'
type SortDir = 'asc' | 'desc'
interface SortState { key: string; dir: SortDir }

interface RiskConfig {
  balance_tiers: Array<{ min_balance_usdt: number; max_deploy_pct: number; max_leverage_ceiling: number }>
  base_leverage: number
  max_leverage: number
  max_leverage_level?: number
  min_balance_pct?: number
  symbol_weights?: Record<string, number>
  scenario?: string
  bgf_top_n?: number
}

interface RiskStateSnapshot {
  balance: number
  leverage_level?: number
  per_symbol: Record<string, {
    allocation_usdt: number
    leverage: number
    leverage_level?: number
    performance_score: number
  }>
}

interface Props {
  symbols: string[]
  dataBySymbol: Record<string, BacktestResults | null>
  riskConfig?: RiskConfig
  riskState?: RiskStateSnapshot
}

const LEVERAGES = [1, 2, 3, 5, 10, 15, 20, 25, 50, 75, 100, 125]

// ── Risk allocation helpers ───────────────────────────────────────────────────

function activeTier(config: RiskConfig, balance: number) {
  const sorted = [...config.balance_tiers].sort((a, b) => a.min_balance_usdt - b.min_balance_usdt)
  return sorted.reduce((active, t) => balance >= t.min_balance_usdt ? t : active, sorted[0])
}

type ScenarioId = 'default' | 'allocation' | 'first_has_most' | 'best_gets_first' | 'tats'

function computeSizingDefault(
  symbol: string,
  balance: number,
  config: RiskConfig,
  riskState: RiskStateSnapshot | undefined,
  allSymbols: string[],
): { margin: number; lev: number } {
  const lev = Math.max(1, riskState?.per_symbol?.[symbol]?.leverage_level ?? riskState?.leverage_level ?? 1)
  const tier = activeTier(config, balance)
  const reserve = balance * (config.min_balance_pct ?? 0) / 100
  const pool = Math.max(0, balance - reserve) * tier.max_deploy_pct / 100
  const numSymbols = allSymbols.length || 1
  return { margin: pool / numSymbols, lev }
}

function computeSizingAllocation(
  symbol: string,
  balance: number,
  config: RiskConfig,
  riskState: RiskStateSnapshot | undefined,
  allSymbols: string[],
): { margin: number; lev: number } {
  const tier = activeTier(config, balance)
  const reserve = balance * (config.min_balance_pct ?? 0) / 100
  const pool = Math.max(0, balance - reserve) * tier.max_deploy_pct / 100
  const weights = config.symbol_weights ?? {}
  // Sum only active symbols' weights — mirrors the bot's disabled-exclusion fix
  const totalW = allSymbols.reduce((sum, s) => sum + (weights[s] ?? 1), 0) || 1
  const w = weights[symbol] ?? 1
  const margin = pool * (w / totalW)
  const lev = Math.max(1, riskState?.per_symbol?.[symbol]?.leverage_level ?? 1)
  return { margin, lev }
}

function computeSizingFirstHasMost(
  symbol: string,
  balance: number,
  config: RiskConfig,
  riskState: RiskStateSnapshot | undefined,
  allSymbols: string[],
): { margin: number; lev: number } {
  // Use bot's pre-computed leverage — performance_score is now raw profit%, not 0-1
  const lev = Math.max(1, riskState?.per_symbol?.[symbol]?.leverage ?? config.base_leverage ?? 1)
  const tier = activeTier(config, balance)
  const reserve = balance * (config.min_balance_pct ?? 0) / 100
  const pool = Math.max(0, balance - reserve) * tier.max_deploy_pct / 100
  const numSymbols = allSymbols.length || 1
  return { margin: pool / numSymbols, lev }
}

function computeSizingBestGetsFirst(
  symbol: string,
  balance: number,
  config: RiskConfig,
  riskState: RiskStateSnapshot | undefined,
  allSymbols: string[],
  bgfTopN: number,
): { margin: number; lev: number } {
  // Use bot's pre-computed leverage — performance_score is now raw profit%, not 0-1
  const lev = Math.max(1, riskState?.per_symbol?.[symbol]?.leverage ?? config.base_leverage ?? 1)
  const tier = activeTier(config, balance)
  const reserve = balance * (config.min_balance_pct ?? 0) / 100
  const pool = Math.max(0, balance - reserve) * tier.max_deploy_pct / 100

  // Apply top-N filter — sort by profit% desc, take first N (mirrors main.py BGF loop)
  const sortedByScore = [...allSymbols].sort(
    (a, b) => (riskState?.per_symbol?.[b]?.performance_score ?? 0) -
               (riskState?.per_symbol?.[a]?.performance_score ?? 0)
  )
  const effectiveN = bgfTopN > 0 && bgfTopN < sortedByScore.length ? bgfTopN : sortedByScore.length
  const activeSet = new Set(sortedByScore.slice(0, effectiveN))

  if (!activeSet.has(symbol)) return { margin: 0, lev }

  const score = riskState?.per_symbol?.[symbol]?.performance_score ?? 0
  const totalScore = [...activeSet].reduce(
    (sum, s) => sum + Math.max(0, riskState?.per_symbol?.[s]?.performance_score ?? 0), 0
  )
  const margin = totalScore > 0
    ? pool * Math.max(0, score) / totalScore
    : pool / Math.max(activeSet.size, 1)
  return { margin, lev }
}

function computeSizing(
  symbol: string,
  balance: number,
  config: RiskConfig,
  riskState: RiskStateSnapshot | undefined,
): { margin: number; lev: number } {
  const tier = activeTier(config, balance)
  const minPct = config.min_balance_pct ?? 0
  const reserve = balance * minPct / 100
  const pool = Math.max(0, balance - reserve) * tier.max_deploy_pct / 100

  const weights = config.symbol_weights ?? {}
  const totalW = Object.values(weights).reduce((a, b) => a + b, 0) || 1
  const w = weights[symbol] ?? 1
  const margin = pool * (w / totalW)

  const score = riskState?.per_symbol[symbol]?.performance_score ?? 0
  const effectiveMax = Math.min(config.max_leverage, tier.max_leverage_ceiling)
  const base = config.base_leverage
  const raw = base + Math.floor(score * (effectiveMax - base))
  const lev = Math.max(base, Math.min(effectiveMax, raw))

  return { margin, lev }
}

// ── Sorting helpers ───────────────────────────────────────────────────────────

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

// ── Main component ────────────────────────────────────────────────────────────

export default function CrossSymbolComparison({ symbols, dataBySymbol, riskConfig, riskState }: Props) {
  const [tab, setTab] = useState<TabId>('combined')
  const [positionSize, setPositionSize] = useState(1000)
  const [leverage, setLeverage] = useState(1)
  const [minTotal, setMinTotal] = useState<string>('')

  const [useSharedBalance, setUseSharedBalance] = useState(false)
  const [totalBalance, setTotalBalance] = useState<number>(() => riskState?.balance ?? 1000)
  // null = follow riskConfig.scenario; set when user manually picks a different tab
  const [scenarioOverride, setScenarioOverride] = useState<ScenarioId | null>(null)
  const scenarioTab: ScenarioId = scenarioOverride ?? ((riskConfig?.scenario as ScenarioId | undefined) ?? 'default')

  const [combinedSort,   setCombinedSort]   = useState<SortState>({ key: 'total',  dir: 'desc' })
  const [sideBySideSort, setSideBySideSort] = useState<SortState>({ key: '',       dir: 'desc' })
  const [bestSort,       setBestSort]       = useState<SortState>({ key: 'usdt',   dir: 'desc' })

  const loadedSymbols = symbols.filter(s => dataBySymbol[s] != null)

  // Per-symbol sizing: either flat (position size × leverage) or risk-manager allocation
  const sizingBySymbol = useMemo<Record<string, { margin: number; lev: number }>>(() => {
    const out: Record<string, { margin: number; lev: number }> = {}
    for (const sym of loadedSymbols) {
      if (useSharedBalance && riskConfig) {
        switch (scenarioTab) {
          case 'allocation':
            out[sym] = computeSizingAllocation(sym, totalBalance, riskConfig, riskState, loadedSymbols)
            break
          case 'first_has_most':
            out[sym] = computeSizingFirstHasMost(sym, totalBalance, riskConfig, riskState, loadedSymbols)
            break
          case 'best_gets_first':
          case 'tats':
            out[sym] = computeSizingBestGetsFirst(sym, totalBalance, riskConfig, riskState, loadedSymbols, riskConfig.bgf_top_n ?? 0)
            break
          default:
            out[sym] = computeSizingDefault(sym, totalBalance, riskConfig, riskState, loadedSymbols)
        }
      } else {
        out[sym] = { margin: positionSize, lev: leverage }
      }
    }
    return out
  }, [useSharedBalance, scenarioTab, riskConfig, riskState, totalBalance, positionSize, leverage, loadedSymbols])

  const allPresetNames = useMemo(() => {
    const names = new Set<string>()
    for (const sym of loadedSymbols) {
      const d = dataBySymbol[sym]
      if (d) Object.keys(d.presets).forEach(n => names.add(n))
    }
    return Array.from(names)
  }, [loadedSymbols, dataBySymbol])

  // Per-symbol USDT = profit_pct / 100 × margin × lev
  const rows = useMemo(() => {
    return allPresetNames.map(name => {
      const perSymbol: Record<string, number> = {}
      for (const sym of loadedSymbols) {
        const pct = dataBySymbol[sym]?.presets[name]?.total_profit_pct ?? null
        const { margin, lev } = sizingBySymbol[sym]
        perSymbol[sym] = pct !== null ? (pct / 100) * margin * lev : 0
      }
      const total = Object.values(perSymbol).reduce((a, b) => a + b, 0)
      const missing = loadedSymbols.filter(s => dataBySymbol[s]?.presets[name] == null)
      return { name, total, perSymbol, missing }
    })
  }, [allPresetNames, loadedSymbols, dataBySymbol, sizingBySymbol])

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
      const { margin, lev } = sizingBySymbol[sym]
      return {
        symbol: sym,
        preset: best.preset,
        usdt: (best.total_profit_pct / 100) * margin * lev,
        winRate: best.win_rate,
        trades: best.total_trades,
        maxdd: best.max_consecutive_losses,
        margin,
        lev,
      }
    }).filter(Boolean) as Array<{
      symbol: string; preset: string; usdt: number
      winRate: number; trades: number; maxdd: number
      margin: number; lev: number
    }>

    return sortRows(base, bestSort.key, bestSort.dir, (r, k) =>
      r[k as keyof typeof r] as string | number
    )
  }, [loadedSymbols, dataBySymbol, sizingBySymbol, bestSort])

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

  // Summary row for shared balance allocation display
  const tier = riskConfig ? activeTier(riskConfig, totalBalance) : null

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

          {/* Shared balance toggle */}
          <label
            className="flex items-center gap-1.5 cursor-pointer select-none"
            title="When enabled, each symbol's position size and leverage are computed from your Risk Manager config (balance × allocation weight × leverage formula). Reflects the real portfolio behaviour."
          >
            <input
              type="checkbox"
              checked={useSharedBalance}
              onChange={e => setUseSharedBalance(e.target.checked)}
              disabled={!riskConfig}
              className="accent-indigo-500 h-3.5 w-3.5 cursor-pointer disabled:opacity-40"
            />
            <span className={useSharedBalance ? 'text-indigo-300 font-semibold' : riskConfig ? '' : 'opacity-40'}>
              Shared balance
            </span>
          </label>

          {useSharedBalance && riskConfig ? (
            // Shared balance mode — single balance input
            <>
              <label className="flex items-center gap-1.5">
                <span>Balance</span>
                <span className="text-gray-400">$</span>
                <input
                  type="number"
                  min={1}
                  step={100}
                  value={totalBalance}
                  onChange={e => { const v = Number(e.target.value); if (v > 0) setTotalBalance(v) }}
                  className="w-28 bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-300 text-xs font-mono focus:outline-none focus:border-indigo-500"
                />
                <span className="text-gray-600">USDT</span>
              </label>
              {tier && (
                <span className="text-gray-600 font-mono" title="Active balance tier from Risk Manager config">
                  tier: deploy <span className="text-gray-400">{tier.max_deploy_pct}%</span>
                  {' '}· lev ceiling <span className="text-gray-400">{tier.max_leverage_ceiling}×</span>
                </span>
              )}
            </>
          ) : (
            // Flat mode — per-trade margin + uniform leverage
            <>
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
            </>
          )}
        </div>
      </div>

      {/* Scenario tabs — only visible in shared balance mode */}
      {useSharedBalance && riskConfig && (
        <div className="flex items-center gap-1 flex-wrap">
          <span className="text-gray-600 text-[10px] mr-1">Scenario:</span>
          {([
            ['default',         'Default'],
            ['allocation',      'Allocation'],
            ['first_has_most',  'First Has Most'],
            ['best_gets_first', 'Best Gets First'],
            ['tats',            'TATS'],
          ] as [ScenarioId, string][]).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setScenarioOverride(id)}
              className={`px-2 py-0.5 rounded text-[10px] font-semibold transition-colors ${
                scenarioTab === id
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
              }`}
            >
              {label}
            </button>
          ))}
          {scenarioOverride !== null && (
            <button
              onClick={() => setScenarioOverride(null)}
              className="ml-1 text-[10px] text-indigo-400 hover:text-white transition-colors"
              title={`Reset to configured scenario: ${riskConfig?.scenario ?? 'default'}`}
            >
              ↺ config
            </button>
          )}
        </div>
      )}

      {/* Shared balance: per-symbol allocation breakdown */}
      {useSharedBalance && riskConfig && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] font-mono text-gray-600 bg-gray-900/50 rounded px-3 py-2 border border-gray-800">
          {loadedSymbols.map(sym => {
            const { margin, lev } = sizingBySymbol[sym]
            const score = riskState?.per_symbol[sym]?.performance_score
            const excluded = (scenarioTab === 'best_gets_first' || scenarioTab === 'tats') && margin === 0
            return (
              <span key={sym} title={score != null ? `Profit score: ${score.toFixed(2)}%` : 'No live score — using base leverage'}>
                <span className={excluded ? 'text-gray-600' : 'text-indigo-400'}>{sym}</span>
                {' '}{excluded
                  ? <span className="text-gray-700">(excluded)</span>
                  : <>{`$${margin.toFixed(0)} × ${lev}×`}{score == null && <span className="text-gray-700"> (base lev)</span>}</>
                }
              </span>
            )
          })}
        </div>
      )}

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
                    title="USDT profit using the configured margin and leverage for this symbol">
                  USDT profit
                </Th>
                {useSharedBalance ? (
                  <>
                    <Th sortKey="margin" sort={bestSort} onSort={k => setBestSort(s => toggleSort(s, k))}
                        title="Allocated margin for this symbol based on balance, weights and tier">
                      Alloc $
                    </Th>
                    <Th sortKey="lev" sort={bestSort} onSort={k => setBestSort(s => toggleSort(s, k))}
                        title="Dynamic leverage from Risk Manager formula (base + score × range)">
                      Lev
                    </Th>
                  </>
                ) : null}
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
                  {useSharedBalance ? (
                    <>
                      <td className="text-right pr-4 text-gray-400">${row.margin.toFixed(0)}</td>
                      <td className="text-right pr-4 text-gray-400">{row.lev}×</td>
                    </>
                  ) : null}
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
