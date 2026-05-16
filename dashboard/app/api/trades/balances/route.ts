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

export async function GET() {
  const mode = currentMode()

  let realBalance: number | null = null
  try {
    const d = JSON.parse(
      fs.readFileSync(path.join(BOT_ROOT, 'dashboard', 'public', 'risk_state.json'), 'utf8'),
    )
    realBalance = typeof d.balance === 'number' ? d.balance : null
  } catch { /* no risk_state yet */ }

  let virtualBalance: number | null = null
  try {
    const d = JSON.parse(
      fs.readFileSync(path.join(BOT_ROOT, 'data', `virtual_balance_${mode}.json`), 'utf8'),
    )
    virtualBalance = typeof d.virtual_balance === 'number' ? d.virtual_balance : null
  } catch { /* no virtual balance yet */ }

  return NextResponse.json({ mode, realBalance, virtualBalance })
}
