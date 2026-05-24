import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../_utils'
import { REGISTRY_PATH } from '../symbols/_registry'

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

function detectRankMax(dataDir: string, mode: string): number {
  try {
    const re = new RegExp(`^virtual_balance_rank(\\d+)_${mode}\\.json$`)
    const ranks = fs.readdirSync(dataDir)
      .map(f => { const m = f.match(re); return m ? parseInt(m[1], 10) : 0 })
      .filter(n => n > 0)
    return ranks.length > 0 ? Math.max(...ranks) : 6
  } catch { return 6 }
}

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

  // Mirror VirtualTracker scoring: pure seeded until MIN_TRADES, pure live after.
  // No linear blend — prevents early trades from displacing established preset rankings.
  const MIN_TRADES = 8
  function effectiveScore(stats: { total_winning_usdt: number; trade_count: number; seeded_winning_usdt?: number }): number {
    if ((stats.trade_count ?? 0) >= MIN_TRADES) return stats.total_winning_usdt
    return stats.seeded_winning_usdt ?? 0
  }

  // Check if this symbol has a manually locked preset
  const riskConfig = readJson(path.join(BOT_ROOT, 'risk_config.json'), {}) as Record<string, unknown>
  const lockedPreset: string | null = (riskConfig.locked_presets as Record<string, string> | undefined)?.[symbol] ?? null

  // Determine best preset: locked preset wins; otherwise highest effective score > 0
  let bestPreset: string | null = lockedPreset
  if (!bestPreset) {
    let bestScore = 0
    for (const [name, stats] of Object.entries(symbolEfficiency)) {
      const score = effectiveScore(stats)
      if (score > bestScore) {
        bestScore = score
        bestPreset = name
      }
    }
  }

  // Compute preset ranks for this symbol.
  // When locked: locked preset = rank 1; remaining presets (excluding locked) sorted by score from rank 2.
  // When not locked: sorted by score, rank 1 = best.
  const presetRanks: Record<string, number> = {}
  const sortedByEff = Object.entries(symbolEfficiency)
    .sort(([, a], [, b]) => effectiveScore(b) - effectiveScore(a))
  if (lockedPreset) {
    presetRanks[lockedPreset] = 1
    const others = sortedByEff.filter(([name]) => name !== lockedPreset)
    others.forEach(([name], idx) => {
      presetRanks[name] = idx + 2
    })
  } else {
    sortedByEff.forEach(([name], idx) => {
      presetRanks[name] = idx + 1
    })
  }

  // Read rank orders (ranks 2–rankMax) for this symbol
  const dataDir = path.join(BOT_ROOT, 'data')
  const rankMax = detectRankMax(dataDir, mode)
  const rankOrders: Record<string, unknown[]> = {}
  const rankBalances: Record<string, number> = {}
  for (let rank = 2; rank <= rankMax; rank++) {
    const ordersPath = path.join(BOT_ROOT, 'data', `virtual_orders_rank${rank}_${symbol}_${mode}.json`)
    rankOrders[String(rank)] = readJson(ordersPath, []) as unknown[]
    const balPath = path.join(BOT_ROOT, 'data', `virtual_balance_rank${rank}_${mode}.json`)
    const balData = readJson(balPath, {}) as Record<string, number>
    rankBalances[String(rank)] = balData.balance ?? 0
  }

  // Read disabled_ranks and disabled symbols from the registry
  const registry = readJson(REGISTRY_PATH, {}) as {
    disabled_ranks?: Record<string, number[]>
    disabled?: Record<string, { reason: string; disabled_at: string }>
  }
  const disabledRanks: number[] = registry.disabled_ranks?.[symbol] ?? []
  const disabledSymbols: Record<string, { reason: string; disabled_at: string }> = registry.disabled ?? {}

  // Read currently open positions from in-memory snapshot written after each candle
  const openPositionsPath = path.join(BOT_ROOT, 'data', `open_positions_${mode}.json`)
  const openPositions = readJson(openPositionsPath, { real: [], virtual: [] }) as {
    real?: unknown[]
    virtual?: unknown[]
  }
  const openReal    = (openPositions.real    ?? []).filter((o: unknown) => (o as { symbol: string }).symbol === symbol)
  const openVirtual = (openPositions.virtual ?? []).filter((o: unknown) => (o as { symbol: string }).symbol === symbol)

  return NextResponse.json({
    symbol,
    mode,
    best_preset: bestPreset,
    all_preset_names: allPresetNames,
    real_orders: realOrders,
    rank_orders: rankOrders,
    rank_balances: rankBalances,
    preset_ranks: presetRanks,
    disabled_ranks: disabledRanks,
    disabled_symbols: disabledSymbols,
    open_real: openReal,
    open_virtual: openVirtual,
  })
}
