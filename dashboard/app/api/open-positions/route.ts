import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../_utils'

export const dynamic = 'force-dynamic'

export async function GET() {
  try {
    const modePath = path.join(BOT_ROOT, 'data', 'bot_mode.json')
    let mode = 'test'
    try {
      const modeRaw = fs.readFileSync(modePath, 'utf-8')
      mode = JSON.parse(modeRaw).mode ?? 'test'
    } catch {
      // bot not running or mode file missing — assume test
    }

    const posPath = path.join(BOT_ROOT, 'data', `open_positions_${mode}.json`)
    let symbols: string[] = []
    try {
      const raw = fs.readFileSync(posPath, 'utf-8')
      const data = JSON.parse(raw) as { real?: { symbol?: string }[]; virtual?: { symbol?: string }[] }
      const all = [...(data.real ?? []), ...(data.virtual ?? [])]
      const seen = new Set<string>()
      for (const order of all) {
        if (order.symbol) seen.add(order.symbol)
      }
      symbols = [...seen]
    } catch {
      // file missing or bot not running — return empty list
    }

    return NextResponse.json({ symbols })
  } catch (err) {
    return NextResponse.json({ symbols: [], error: String(err) }, { status: 500 })
  }
}
