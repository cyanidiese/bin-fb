import { NextRequest, NextResponse } from 'next/server'
import { isMode, updateRiskConfig } from '../../_risk-config'

/** POST /api/weight-suggestions/apply {mode, changes: {SYMBOL: weight}} — set those
 *  symbols' weights in that mode's risk config, merged onto a FRESH read so nothing else
 *  changes. The bot picks it up within one candle and audits it in
 *  data/weight_changes_{mode}.json. Only existing symbols; weights must be > 0 (0 is the
 *  on/off switch for real orders and stays a deliberate edit on the allocation table). */
export async function POST(req: NextRequest) {
  let body: { mode?: unknown; changes?: Record<string, unknown> }
  try { body = await req.json() } catch { return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 }) }
  if (!isMode(body.mode)) return NextResponse.json({ error: 'mode must be test or live' }, { status: 400 })
  const changes = body.changes ?? {}
  const clean: Record<string, number> = {}
  for (const [sym, v] of Object.entries(changes)) {
    const w = Math.round(Number(v) * 100) / 100
    if (!/^[A-Z0-9]{2,20}$/.test(sym) || !Number.isFinite(w) || w <= 0 || w > 1000) {
      return NextResponse.json({ error: `invalid weight for ${sym}: ${String(v)}` }, { status: 400 })
    }
    clean[sym] = w
  }
  if (!Object.keys(clean).length) return NextResponse.json({ error: 'no changes' }, { status: 400 })
  const before: Record<string, number> = {}
  try {
    updateRiskConfig(body.mode, cfg => {
      const w = { ...((cfg.symbol_weights ?? {}) as Record<string, number>) }
      for (const [sym, val] of Object.entries(clean)) {
        if (!(sym in w)) throw new Error(`${sym} has no weight entry in this mode`)
        before[sym] = w[sym]
        w[sym] = val
      }
      return { ...cfg, symbol_weights: w }
    })
  } catch (e) {
    return NextResponse.json({ error: String(e instanceof Error ? e.message : e) }, { status: 400 })
  }
  return NextResponse.json({ ok: true, mode: body.mode, before, after: clean })
}
