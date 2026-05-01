import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const RESULTS_PATH = path.join(process.cwd(), 'public', 'backtest_results.json')

export async function POST(req: NextRequest) {
  try {
    const { name, result, settings } = await req.json()

    if (!name?.trim() || !result || !settings) {
      return NextResponse.json(
        { error: 'name, result, and settings are required' },
        { status: 400 },
      )
    }

    if (!fs.existsSync(RESULTS_PATH)) {
      return NextResponse.json(
        { error: 'backtest_results.json not found — run backtest.py first' },
        { status: 404 },
      )
    }

    const data = JSON.parse(fs.readFileSync(RESULTS_PATH, 'utf-8'))

    data.presets[name.trim()] = {
      preset: name.trim(),
      total_trades: result.total_trades,
      wins: result.wins,
      partials: result.partials,
      trails: result.trails,
      losses: result.losses,
      win_rate: result.win_rate,
      total_profit_pct: result.total_profit_pct,
      avg_rr: result.avg_rr,
      max_consecutive_losses: result.max_consecutive_losses,
      total_profit_pts: result.total_profit_pts,
      potential_win_pts: result.potential_win_pts,
      potential_loss_pts: result.potential_loss_pts,
      avg_max_tp_reach_pct: result.avg_max_tp_reach_pct,
      trades: result.trades,
      settings,
    }

    fs.writeFileSync(RESULTS_PATH, JSON.stringify(data, null, 2))

    return NextResponse.json({ ok: true })
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
}
