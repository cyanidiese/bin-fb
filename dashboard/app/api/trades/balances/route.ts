import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../../_utils'

function currentMode(): string {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(BOT_ROOT, 'data', 'bot_mode.json'), 'utf8'))
    return d.mode ?? 'test'
  } catch { return 'test' }
}

const RANK_MAX = 6

export async function GET() {
  const mode = currentMode()

  let realBalance: number | null = null
  try {
    const d = JSON.parse(
      fs.readFileSync(path.join(BOT_ROOT, 'dashboard', 'public', 'risk_state.json'), 'utf8'),
    )
    realBalance = typeof d.balance === 'number' ? d.balance : null
  } catch { /* no risk_state yet */ }

  const rankBalances: Record<string, number> = {}
  for (let rank = 2; rank <= RANK_MAX; rank++) {
    try {
      const d = JSON.parse(
        fs.readFileSync(path.join(BOT_ROOT, 'data', `virtual_balance_rank${rank}_${mode}.json`), 'utf8'),
      )
      rankBalances[String(rank)] = typeof d.balance === 'number' ? d.balance : 0
    } catch { /* no file yet */ }
  }

  return NextResponse.json({ mode, realBalance, rankBalances })
}
