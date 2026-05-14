import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const BOT_ROOT = path.resolve(process.cwd(), '..')
const CONFIG_PATH = path.join(BOT_ROOT, 'risk_config.json')
const STATE_PATH = path.join(BOT_ROOT, 'dashboard', 'public', 'risk_state.json')

const DEFAULT_CONFIG = {
  balance_tiers: [
    { min_balance_usdt: 0,    max_deploy_pct: 40, max_leverage_ceiling: 5  },
    { min_balance_usdt: 1000, max_deploy_pct: 50, max_leverage_ceiling: 10 },
    { min_balance_usdt: 5000, max_deploy_pct: 60, max_leverage_ceiling: 15 },
  ],
  base_leverage: 2,
  max_leverage: 10,
  min_profit_factor: 1.2,
  drawdown_warning_pct: 10.0,
  drawdown_hard_stop_pct: 20.0,
  backtest_initial_balance_usdt: 1000.0,
  symbol_weights: {} as Record<string, number>,
  max_leverage_level: 5,
  use_allocation_weighting: false,
  min_balance_pct: 15.0,
  telegram_notify_interval_s: 120,
  scenario: 'default',
}

function readJson(filePath: string, fallback: unknown) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return fallback
  }
}

/** GET /api/risk — returns { config, state } */
export async function GET() {
  const config = { ...DEFAULT_CONFIG, ...readJson(CONFIG_PATH, {}) }
  const state = readJson(STATE_PATH, null)
  return NextResponse.json({ config, state })
}

/** POST /api/risk — save full config object to disk, or merge a partial update (e.g. { telegram }) */
export async function POST(req: NextRequest) {
  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }

  // Allow partial updates (e.g. { telegram }) — merge into existing config
  const required = ['balance_tiers', 'base_leverage', 'max_leverage',
                    'min_profit_factor', 'drawdown_warning_pct',
                    'drawdown_hard_stop_pct', 'symbol_weights']
  const isPartialUpdate = required.every(k => !(k in body))

  let merged: Record<string, unknown>
  if (isPartialUpdate) {
    merged = { ...DEFAULT_CONFIG, ...readJson(CONFIG_PATH, {}), ...body }
  } else {
    for (const key of required) {
      if (!(key in body)) {
        return NextResponse.json({ error: `Missing field: ${key}` }, { status: 400 })
      }
    }
    merged = body
  }

  try {
    // Write directly — atomic rename fails on Docker single-file bind mounts (EBUSY)
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(merged, null, 2))
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
