import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import {
  SHARED_KEYS, botMode, modeOr, readRiskConfig, riskConfigPath, saveRiskPatch, type Config,
} from '../_risk-config'

const BOT_ROOT = path.resolve(process.cwd(), '..')

/** The live state of whichever instance trades `mode`: the primary writes the
 *  unsuffixed risk_state.json, the mirror risk_state_{mode}.json (bot/instance_paths.py). */
function statePath(mode: string): string {
  const name = mode === botMode() ? 'risk_state.json' : `risk_state_${mode}.json`
  return path.join(BOT_ROOT, 'dashboard', 'public', name)
}

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
  backtest_klines: 1500,
  bgf_top_n: 0,
  symbol_weights: {} as Record<string, number>,
  max_leverage_level: 5,
  use_allocation_weighting: false,
  min_balance_pct: 15.0,
  telegram_notify_interval_s: 120,
  scenario: 'default',
  weight_rebalancer: {
    enabled: false,
    rebalance_candles: 96,
    backtest_window_candles: 96,
    real_pnl_alpha: 0.5,
    blend_rate: 0.15,
    weight_floor_ratio: 0.3,
  },
  min_trades_for_ranking: 3,
  min_trades_for_ranking_per_symbol: {} as Record<string, number>,
  max_loss_usdt: 25,
  max_loss_usdt_per_symbol: {} as Record<string, number>,
  max_loss_tp_ratio: 0,
  locked_presets: {} as Record<string, string>,
  substitution_enabled: false,
  substitution_enabled_per_symbol: {} as Record<string, boolean>,
}

function readJson(filePath: string, fallback: unknown) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return fallback
  }
}

/** GET /api/risk?mode=test|live — { mode, file, config, state, shared_keys } for that
 *  trading mode (default: the bot's). Live falls back to test key by key; shared keys come
 *  from risk_config_shared.json. */
export async function GET(req: NextRequest) {
  const mode = modeOr(new URL(req.url).searchParams.get('mode'))
  const config = { ...DEFAULT_CONFIG, ...readRiskConfig(mode) }
  const state = readJson(statePath(mode), null)
  return NextResponse.json({
    mode, file: path.basename(riskConfigPath(mode)), config, state, shared_keys: SHARED_KEYS,
  })
}

/**
 * POST /api/risk?mode=test|live — merge an update into the config.
 * Specs: docs/specs/2026-09-26-per-mode-risk-config.md,
 *        docs/specs/2026-09-26-shared-settings-and-per-mode-registry.md
 *
 *  - Every POST is a merge onto a FRESH read, so fields not in the body are never lost.
 *    The Risk page sends only the fields it changed: a full snapshot would revert
 *    anything edited elsewhere (Settings, /bfb-config, the other mode) since it loaded.
 *  - Shared keys (SHARED_KEYS: Telegram, signal filters, preset ranking, …) go to
 *    risk_config_shared.json — one value for both modes.
 *  - Everything else goes to the requested mode's file (default: the bot's mode).
 *  - `locked_presets` is never taken from the body: per mode, edited via lock-preset only.
 */
export async function POST(req: NextRequest) {
  let body: Config
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return NextResponse.json({ error: 'Body must be a JSON object' }, { status: 400 })
  }
  const mode = modeOr(new URL(req.url).searchParams.get('mode') ?? body._mode)
  const { locked_presets: _l, _reset_hard_stop: _r, _mode: _m, ...rest } = body
  void _l; void _r; void _m

  try {
    const written = saveRiskPatch(mode, rest)
    return NextResponse.json({
      ok: true, mode, file: path.basename(riskConfigPath(mode)),
      shared: written.shared, own: written.own,
    })
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
}
