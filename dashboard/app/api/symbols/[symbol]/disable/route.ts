import { NextRequest, NextResponse } from 'next/server'
import { readRegistry, writeRegistry } from '../../_registry'

/** POST /api/symbols/[symbol]/disable — manually disable a symbol (weight→0, records in disabled map). */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: raw } = await params
  const symbol = raw.toUpperCase()

  let reason = 'manual'
  try {
    const body = await req.json()
    if (body?.reason) reason = String(body.reason)
  } catch { /* no body — use default */ }

  const reg = readRegistry()
  if (!reg.symbols.includes(symbol)) {
    return NextResponse.json({ error: `${symbol} is not active` }, { status: 404 })
  }

  const prevWeight = reg.weights?.[symbol] ?? 1

  if (!reg.disabled) reg.disabled = {}
  reg.disabled[symbol] = { reason, disabled_at: new Date().toISOString(), prev_weight: prevWeight }
  if (!reg.weights) reg.weights = {}
  reg.weights[symbol] = 0

  writeRegistry(reg)
  return NextResponse.json({ ok: true, symbol })
}
