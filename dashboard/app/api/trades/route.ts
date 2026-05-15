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

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const symbol = searchParams.get('symbol')?.toUpperCase()
  if (!symbol) {
    return NextResponse.json({ error: 'symbol required' }, { status: 400 })
  }

  const mode = searchParams.get('mode') ?? currentMode()

  const realOrdersPath    = path.join(BOT_ROOT, 'data', `real_orders_${symbol}_${mode}.json`)
  const efficiencyPath    = path.join(BOT_ROOT, 'data', `preset_efficiency_${mode}.json`)
  const virtualOrdersPath = path.join(BOT_ROOT, 'data', `virtual_orders_${symbol}_${mode}.json`)
  const backtestPath      = path.join(BOT_ROOT, 'dashboard', 'public', `backtest_results_${symbol}.json`)

  const realOrders   = readJson(realOrdersPath, []) as unknown[]
  const efficiency   = readJson(efficiencyPath, {}) as Record<string, Record<string, { total_winning_usdt: number; trade_count: number; seeded_winning_usdt?: number }>>
  const virtualOrders = readJson(virtualOrdersPath, []) as unknown[]
  const backtest     = readJson(backtestPath, null) as { presets?: Record<string, unknown> } | null

  const symbolEfficiency = efficiency[symbol] ?? {}

  // All known preset names: backtest results union efficiency keys
  const allPresetNames: string[] = backtest?.presets
    ? Array.from(new Set([...Object.keys(backtest.presets), ...Object.keys(symbolEfficiency)]))
    : Object.keys(symbolEfficiency)

  // Determine best preset: max total_winning_usdt with >= 4 trades
  let bestPreset: string | null = null
  let bestWinning = -Infinity
  for (const [name, stats] of Object.entries(symbolEfficiency)) {
    if (stats.trade_count >= 4 && stats.total_winning_usdt > bestWinning) {
      bestWinning = stats.total_winning_usdt
      bestPreset = name
    }
  }

  return NextResponse.json({
    symbol,
    mode,
    best_preset: bestPreset,
    all_preset_names: allPresetNames,
    real_orders: realOrders,
    virtual_summary: symbolEfficiency,
    virtual_orders: virtualOrders,
  })
}
