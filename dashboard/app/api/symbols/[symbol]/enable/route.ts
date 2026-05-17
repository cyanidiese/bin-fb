import { NextRequest, NextResponse } from 'next/server'
import { readRegistry, writeRegistry } from '../../_registry'

/** PATCH /api/symbols/[symbol]/enable — remove a symbol from the disabled list. */
export async function PATCH(
  _req: NextRequest,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: raw } = await params
  const symbol = raw.toUpperCase()

  const reg = readRegistry()
  if (!reg.disabled?.[symbol]) {
    return NextResponse.json({ ok: true, symbol, was_disabled: false })
  }

  delete reg.disabled[symbol]
  if (Object.keys(reg.disabled).length === 0) {
    delete reg.disabled
  }
  writeRegistry(reg)

  return NextResponse.json({ ok: true, symbol, was_disabled: true })
}
