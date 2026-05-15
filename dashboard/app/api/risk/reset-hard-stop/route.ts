import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../../_utils'

const SIGNAL_PATH = path.join(BOT_ROOT, 'data', 'reset_hard_stop.signal')

export async function POST() {
  try {
    fs.mkdirSync(path.dirname(SIGNAL_PATH), { recursive: true })
    fs.writeFileSync(SIGNAL_PATH, new Date().toISOString())
    return NextResponse.json({ ok: true })
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 500 })
  }
}
