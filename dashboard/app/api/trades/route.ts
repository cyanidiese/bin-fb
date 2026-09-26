import { NextRequest, NextResponse } from 'next/server'
import { lockedPresetsFor } from '../_locked-presets'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../_utils'
import { REGISTRY_PATH } from '../symbols/_registry'
import { rankPresets, type PresetEfficiency } from './_top-preset'
import { detectRankMax } from './_rank-files'
import { currentMode, presetNamesFor } from './_preset-names'

function readJson(filePath: string, fallback: unknown) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return fallback
  }
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

  const realOrders = readJson(realOrdersPath, []) as unknown[]
  const efficiency = readJson(efficiencyPath, {}) as Record<string, Record<string, PresetEfficiency>>

  const symbolEfficiency = efficiency[symbol] ?? {}

  // All known preset names: backtest results union efficiency keys. The shadow
  // instance's backtest lives under a mode-suffixed name (see _preset-names).
  const allPresetNames = presetNamesFor(symbol, mode, symbolEfficiency)

  // Check if this symbol has a manually locked preset
  const riskConfig = readJson(path.join(BOT_ROOT, 'risk_config.json'), {}) as Record<string, unknown>

  // the lock set belonging to the instance being viewed, not a shared one
  const lockedPreset: string | null = lockedPresetsFor(riskConfig, mode)[symbol] ?? null
  const { bestPreset, presetRanks } = rankPresets(symbolEfficiency, lockedPreset, riskConfig)

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

  // Rank 1 separately: see TradesData.rank1_orders.
  const rank1Orders = readJson(
    path.join(dataDir, `virtual_orders_rank1_${symbol}_${mode}.json`), []) as unknown[]

  // Read currently open positions from in-memory snapshot written after each candle
  const openPositionsPath = path.join(BOT_ROOT, 'data', `open_positions_${mode}.json`)
  const openPositions = readJson(openPositionsPath, { real: [], virtual: [] }) as {
    real?: unknown[]
    virtual?: unknown[]
    updated_at?: string
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
    rank1_orders: rank1Orders,
    rank_balances: rankBalances,
    preset_ranks: presetRanks,
    disabled_ranks: disabledRanks,
    disabled_symbols: disabledSymbols,
    // The mode the BOT is running, as opposed to `mode` which is the one being viewed.
    // The Trades page compares them: only the running instance polls bot_command.json,
    // so only its own positions can be closed from the UI.
    bot_mode: currentMode(),
    open_updated_at: openPositions.updated_at ?? null,
    open_real: openReal,
    open_virtual: openVirtual,
  })
}
