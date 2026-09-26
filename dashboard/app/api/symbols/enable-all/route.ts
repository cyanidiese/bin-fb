import { NextRequest, NextResponse } from 'next/server'
import { modeParam, readSymbolState, updateSymbolState } from '../_registry'

/** POST /api/symbols/enable-all?mode=test|live — clear that mode's disabled symbols
 *  (default: the bot's mode). */
export async function POST(req: NextRequest) {
  const mode = modeParam(req.url)
  const count = Object.keys(readSymbolState(mode).disabled ?? {}).length
  updateSymbolState(mode, st => ({ ...st, disabled: {} }))
  return NextResponse.json({ ok: true, mode, cleared: count })
}
