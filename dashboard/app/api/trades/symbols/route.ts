import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../../_utils'

export const dynamic = 'force-dynamic'

function currentMode(): string {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(BOT_ROOT, 'data', 'bot_mode.json'), 'utf8'))
    return d.mode ?? 'test'
  } catch { return 'test' }
}

function registeredSymbols(): string[] {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(BOT_ROOT, 'dashboard', 'public', 'symbols.json'), 'utf8'))
    return Array.isArray(d.symbols) ? d.symbols : []
  } catch { return [] }
}

/**
 * Symbols that have orders in a given mode, plus which of them have a position open
 * right now.
 *
 * `?mode=` selects the instance: the primary runs the mode in bot_mode.json and the
 * shadow mirror runs the opposite, and every file under data/ is mode-suffixed, so the
 * mode alone picks the instance. Defaults to the bot's own mode, which is what every
 * existing caller expects.
 *
 * open_real and open_virtual are returned separately because the Trades symbol picker
 * distinguishes them: a real position is the one that matters most, so it gets a
 * brighter marker than a virtual one.
 */
export async function GET(req: NextRequest) {
  const requested = new URL(req.url).searchParams.get('mode')
  const mode = requested === 'test' || requested === 'live' ? requested : currentMode()
  const all = registeredSymbols()
  const dir = path.join(BOT_ROOT, 'data')

  // Symbols with any closed orders on disk. size > 2 skips an empty "[]".
  const withOrders = new Set(all.filter(sym => {
    const realPath = path.join(dir, `real_orders_${sym}_${mode}.json`)
    const virtPath = path.join(dir, `virtual_orders_${sym}_${mode}.json`)
    const hasReal = fs.existsSync(realPath) && fs.statSync(realPath).size > 2
    const hasVirt = fs.existsSync(virtPath) && fs.statSync(virtPath).size > 2
    return hasReal || hasVirt
  }))

  // Positions open right now, from the snapshot the bot writes after each candle.
  const openReal = new Set<string>()
  const openVirtual = new Set<string>()
  try {
    const openPath = path.join(dir, `open_positions_${mode}.json`)
    if (fs.existsSync(openPath)) {
      const open = JSON.parse(fs.readFileSync(openPath, 'utf8')) as {
        real?: { symbol?: string }[]
        virtual?: { symbol?: string }[]
      }
      for (const o of (open.real ?? [])) {
        if (o.symbol) { openReal.add(o.symbol); withOrders.add(o.symbol) }
      }
      for (const o of (open.virtual ?? [])) {
        if (o.symbol) { openVirtual.add(o.symbol); withOrders.add(o.symbol) }
      }
    }
  } catch { /* snapshot missing or mid-write — treat as nothing open */ }

  return NextResponse.json({
    mode,
    symbols: all.filter(sym => withOrders.has(sym)),
    open_real: [...openReal],
    open_virtual: [...openVirtual],
  })
}
