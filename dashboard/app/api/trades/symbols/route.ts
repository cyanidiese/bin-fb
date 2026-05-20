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

function registeredSymbols(): string[] {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(BOT_ROOT, 'dashboard', 'public', 'symbols.json'), 'utf8'))
    return Array.isArray(d.symbols) ? d.symbols : []
  } catch { return [] }
}

export async function GET() {
  const mode = currentMode()
  const all  = registeredSymbols()
  const dir  = path.join(BOT_ROOT, 'data')

  // Symbols with any closed orders on disk
  const withClosed = new Set(all.filter(sym => {
    const realPath = path.join(dir, `real_orders_${sym}_${mode}.json`)
    const virtPath = path.join(dir, `virtual_orders_${sym}_${mode}.json`)
    const hasReal  = fs.existsSync(realPath) && fs.statSync(realPath).size > 2
    const hasVirt  = fs.existsSync(virtPath) && fs.statSync(virtPath).size > 2
    return hasReal || hasVirt
  }))

  // Symbols with currently open orders (in-memory, written by bot after each candle)
  try {
    const openPath = path.join(dir, `open_positions_${mode}.json`)
    if (fs.existsSync(openPath)) {
      const open = JSON.parse(fs.readFileSync(openPath, 'utf8')) as {
        real?: { symbol: string }[]
        virtual?: { symbol: string }[]
      }
      for (const o of (open.real ?? [])) withClosed.add(o.symbol)
      for (const o of (open.virtual ?? [])) withClosed.add(o.symbol)
    }
  } catch { /* ignore */ }

  const withOrders = all.filter(sym => withClosed.has(sym))
  return NextResponse.json({ symbols: withOrders, mode })
}
