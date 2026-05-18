import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const BOT_ROOT = path.resolve(process.cwd(), '..')
const PUBLIC_DIR = path.join(BOT_ROOT, 'dashboard', 'public')
const STATE_PATH = path.join(PUBLIC_DIR, 'risk_state.json')

function readJsonSafe(p: string): unknown {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')) } catch { return null }
}

/**
 * POST /api/refresh-scores
 *
 * Reads all backtest_results_*.json files, computes each symbol's best
 * total_profit_pct, and patches the matching performance_score fields in
 * risk_state.json so the Risk page B widget reflects the latest backtest
 * without waiting for the bot's 60-second cache TTL to expire.
 */
export async function POST() {
  const files = fs.readdirSync(PUBLIC_DIR).filter(
    f => f.startsWith('backtest_results_') && f.endsWith('.json')
  )

  const scores: Record<string, number> = {}
  for (const file of files) {
    const data = readJsonSafe(path.join(PUBLIC_DIR, file)) as Record<string, unknown> | null
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
