import { NextRequest, NextResponse } from 'next/server'
import { modeParam, updateSymbolState, readSymbolState } from '../../_registry'

/** PATCH /api/symbols/[symbol]/enable?mode=test|live — remove a symbol from that mode's
 *  disabled list (default: the bot's mode). The other mode is not touched. */
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: raw } = await params
  const symbol = raw.toUpperCase()
  const mode = modeParam(req.url)

  if (!readSymbolState(mode).disabled?.[symbol]) {
    return NextResponse.json({ ok: true, symbol, mode, was_disabled: false })
  }

  updateSymbolState(mode, st => {
    const disabled = { ...(st.disabled ?? {}) }
    const prevWeight = disabled[symbol]?.prev_weight ?? 1
    delete disabled[symbol]
    return { ...st, disabled, weights: { ...(st.weights ?? {}), [symbol]: prevWeight } }
  })

  return NextResponse.json({ ok: true, symbol, mode, was_disabled: true })
}
