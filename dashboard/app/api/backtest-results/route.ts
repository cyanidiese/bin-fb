import { NextRequest, NextResponse } from 'next/server'
import { modeOr } from '../_risk-config'
import { readBacktestResults } from '../_backtest-results'

/** GET /api/backtest-results?symbol=SOLUSDT&mode=test|live — that market's backtest
 *  results (default mode: the bot's). 404 when the mode has never been backtested. */
export async function GET(req: NextRequest) {
  const q = new URL(req.url).searchParams
  const symbol = (q.get('symbol') ?? '').toUpperCase()
  if (!/^[A-Z0-9]{2,20}$/.test(symbol)) {
    return NextResponse.json({ error: 'symbol is required' }, { status: 400 })
  }
  const mode = modeOr(q.get('mode'))
  const data = readBacktestResults(symbol, mode)
  if (!data) {
    return NextResponse.json({ error: `No ${mode} backtest for ${symbol}`, mode }, { status: 404 })
  }
  return NextResponse.json({ ...data, mode })
}
