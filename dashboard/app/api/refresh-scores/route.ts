import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { botMode } from '../_risk-config'
import { readBacktestResults, symbolsWithBacktest } from '../_backtest-results'

const BOT_ROOT = path.resolve(process.cwd(), '..')
const PUBLIC_DIR = path.join(BOT_ROOT, 'dashboard', 'public')
const STATE_PATH = path.join(PUBLIC_DIR, 'risk_state.json')

function readJsonSafe(p: string): unknown {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')) } catch { return null }
}

/**
 * POST /api/refresh-scores
 *
 * Computes each symbol's best total_profit_pct from the backtest of the market the
 * TRADING bot runs, and patches the matching performance_score fields in its
 * risk_state.json so the Risk page B widget reflects the latest backtest without
 * waiting for the bot's 60-second cache TTL to expire.
 *
 * Only the bot mode's files: this used to read every backtest_results_* file, the
 * mirror's _live ones included, and keep whichever it read last for each symbol.
 */
export async function POST() {
  const mode = botMode()
  const scores: Record<string, number> = {}
  for (const sym of symbolsWithBacktest(mode)) {
    const data = readBacktestResults(sym, mode) as Record<string, unknown> | null
    if (!data?.presets || typeof data.symbol !== 'string') continue
    const presets = Object.values(data.presets) as Array<{ total_profit_pct: number }>
    if (presets.length === 0) continue
    const best = presets.reduce((a, b) => b.total_profit_pct > a.total_profit_pct ? b : a)
    scores[data.symbol] = Math.max(0, best.total_profit_pct)
  }

  const state = readJsonSafe(STATE_PATH) as Record<string, unknown> | null
  if (!state) {
    return NextResponse.json({ ok: true, scores, note: 'risk_state.json not found — bot may not have started yet' })
  }

  const perSymbol = (state.per_symbol ?? {}) as Record<string, Record<string, unknown>>
  let updated = 0
  for (const [sym, score] of Object.entries(scores)) {
    if (perSymbol[sym]) {
      perSymbol[sym].performance_score = Math.round(score * 1000) / 1000
      updated++
    }
  }
  state.per_symbol = perSymbol

  try {
    fs.writeFileSync(STATE_PATH, JSON.stringify(state, null, 2))
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }

  return NextResponse.json({ ok: true, updated, scores })
}
