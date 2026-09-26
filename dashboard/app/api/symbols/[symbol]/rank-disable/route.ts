import { NextRequest, NextResponse } from 'next/server'
import { modeParam, readRoster, updateSymbolState } from '../../_registry'

/** PATCH /api/symbols/[symbol]/rank-disable?mode=test|live — enable or disable a specific
 *  virtual rank for a symbol in that mode (default: the bot's mode). */
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: raw } = await params
  const symbol = raw.toUpperCase()
  const mode = modeParam(req.url)

  let rank: number
  let disabled: boolean
  try {
    const body = await req.json()
    rank = Number(body.rank)
    disabled = Boolean(body.disabled)
    if (!Number.isInteger(rank) || rank < 2) {
      return NextResponse.json({ error: 'rank must be an integer >= 2' }, { status: 400 })
    }
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
  }

  if (!readRoster().symbols.includes(symbol)) {
    return NextResponse.json({ error: `${symbol} is not active` }, { status: 404 })
  }

  updateSymbolState(mode, st => {
    const disabledRanks: Record<string, number[]> = { ...(st.disabled_ranks ?? {}) }
    const current = [...(disabledRanks[symbol] ?? [])]
    if (disabled) {
      if (!current.includes(rank)) current.push(rank)
    } else {
      const idx = current.indexOf(rank)
      if (idx !== -1) current.splice(idx, 1)
    }
    if (current.length > 0) disabledRanks[symbol] = current
    else delete disabledRanks[symbol]
    return { ...st, disabled_ranks: disabledRanks }
  })

  return NextResponse.json({ ok: true, symbol, mode, rank, disabled })
}
