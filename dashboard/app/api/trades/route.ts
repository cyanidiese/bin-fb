import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../_utils'

function readJson(filePath: string, fallback: unknown) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return fallback
  }
}

function currentMode(): string {
  const data = readJson(path.join(BOT_ROOT, 'data', 'bot_mode.json'), {}) as Record<string, string>
  return data.mode ?? 'test'
}

const RANK_MAX = 6

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const symbol = searchParams.get('symbol')?.toUpperCase()
  if (!symbol) {
    return NextResponse.json({ error: 'symbol required' }, { status: 400 })
  }

  const mode = searchParams.get('mode') ?? currentMode()

  const realOrdersPath = path.join(BOT_ROOT, 'data', `real_orders_${symbol}_${mode}.json`)
  const efficiencyPath = path.join(BOT_ROOT, 'data', `preset_efficiency_${mode}.json`)
  const backtestPath   = path.join(BOT_ROOT, 'dashboard', 'public', `backtest_results_${symbol}.json`)

  const realOrders = readJson(realOrdersPath, []) as unknown[]
  const efficiency = readJson(efficiencyPath, {}) as Record<string, Record<string, { total_winning_usdt: number; trade_count: number; seeded_winning_usdt?: number }>>
  const backtest   = readJson(backtestPath, null) as { presets?: Record<string, unknown> } | null

  const symbolEfficiency = efficiency[symbol] ?? {}

  // All known preset names: backtest results union efficiency keys
  const allPresetNames: string[] = backtest?.presets
    ? Array.from(new Set([...Object.keys(backtest.presets), ...Object.keys(symbolEfficiency)]))
    : Object.keys(symbolEfficiency)

  // Mirror VirtualTracker._MIN_TRADES: use live score once enough trades exist,
  // otherwise fall back to the backtest-seeded score so ranking works from day 1.
  const MIN_TRADES = 8
  function effectiveScore(stats: { total_winning_usdt: number; trade_count: number; seeded_winning_usdt?: number }): number {
    if ((stats.trade_count ?? 0) >= MIN_TRADES) return stats.total_winning_usdt
    return stats.seeded_winning_usdt ?? 0
  }

  // Determine best preset: highest effective score > 0
  let bestPreset: string | null = null
  let bestScore = 0
  for (const [name, stats] of Object.entries(symbolEfficiency)) {
    const score = effectiveScore(stats)
    if (score > bestScore) {
      bestScore = score
      bestPreset = name
    }
  }

  // Compute preset ranks for this symbol (sorted by effective score descending)
  const presetRanks: Record<string, number> = {}
  const sortedByEff = Object.entries(symbolEfficiency)
    .sort(([, a], [, b]) => effectiveScore(b) - effectiveScore(a))
  sortedByEff.forEach(([name], idx) => {
    presetRanks[name] = idx + 1  // rank 1 = best
  })

  // Read rank orders (ranks 2–RANK_MAX) for this symbol
  const rankOrders: Record<string, unknown[]> = {}
  const rankBalances: Record<string, number> = {}
  for (let rank = 2; rank <= RANK_MAX; rank++) {
    const ordersPath = path.join(BOT_ROOT, 'data', `virtual_orders_rank${rank}_${symbol}_${mode}.json`)
    rankOrders[String(rank)] = readJson(ordersPath, []) as unknown[]
    const balPath = path.join(BOT_ROOT, 'data', `virtual_balance_rank${rank}_${mode}.json`)
    const balData = readJson(balPath, {}) as Record<string, number>
    rankBalances[String(rank)] = balData.balance ?? 0
  }

  return NextResponse.json({
    symbol,
    mode,
    best_preset: bestPreset,
    all_preset_names: allPresetNames,
    real_orders: realOrders,
    rank_orders: rankOrders,
    rank_balances: rankBalances,
    preset_ranks: presetRanks,
  })
}
