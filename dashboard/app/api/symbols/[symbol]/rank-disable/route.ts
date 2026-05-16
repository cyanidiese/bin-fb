import { NextRequest, NextResponse } from 'next/server'
import { readRegistry, writeRegistry } from '../../_registry'

/** PATCH /api/symbols/[symbol]/rank-disable — enable or disable a specific virtual rank for a symbol. */
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: raw } = await params
  const symbol = raw.toUpperCase()

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

  const reg = readRegistry()
  if (!reg.symbols.includes(symbol)) {
    return NextResponse.json({ error: `${symbol} is not active` }, { status: 404 })
  }

  const disabledRanks: Record<string, number[]> = reg.disabled_ranks ?? {}
  const current = disabledRanks[symbol] ?? []

  if (disabled) {
    if (!current.includes(rank)) current.push(rank)
  } else {
    const idx = current.indexOf(rank)
    if (idx !== -1) current.splice(idx, 1)
  }

  if (current.length > 0) {
    disabledRanks[symbol] = current
  } else {
    delete disabledRanks[symbol]
  }

  reg.disabled_ranks = disabledRanks
  writeRegistry(reg)

  return NextResponse.json({ ok: true, symbol, rank, disabled })
}
