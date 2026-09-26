import { NextRequest, NextResponse } from 'next/server'
import { modeParam, readRoster, updateSymbolState } from '../../_registry'

/** POST /api/symbols/[symbol]/disable?mode=test|live — manually disable a symbol in that
 *  mode (weight→0, recorded in its disabled map). Default: the bot's mode. */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: raw } = await params
  const symbol = raw.toUpperCase()
  const mode = modeParam(req.url)

  let reason = 'manual'
  try {
    const body = await req.json()
    if (body?.reason) reason = String(body.reason)
  } catch { /* no body — use default */ }

  if (!readRoster().symbols.includes(symbol)) {
    return NextResponse.json({ error: `${symbol} is not active` }, { status: 404 })
  }

  updateSymbolState(mode, st => {
    const prevWeight = st.weights?.[symbol] ?? 1
    return {
      ...st,
      disabled: { ...(st.disabled ?? {}), [symbol]: { reason, disabled_at: new Date().toISOString(), prev_weight: prevWeight } },
      weights: { ...(st.weights ?? {}), [symbol]: 0 },
    }
  })
  return NextResponse.json({ ok: true, symbol, mode })
}
