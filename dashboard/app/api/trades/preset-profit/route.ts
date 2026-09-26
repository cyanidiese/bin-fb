import { NextRequest, NextResponse } from 'next/server'
import { RANGE_PRESETS } from '@/lib/tradesDateRange'
import { loadStore, refresh, type Mode } from '../_preset-profit-store'

export const dynamic = 'force-dynamic'

/**
 * The preset Profit% store: every preset × shortcut × symbol, per mode.
 * Spec: docs/specs/2026-09-26-preset-profit-store.md
 *
 *   GET  ?mode=test[&symbol=SOLUSDT][&range=7d]  stored numbers, presets → [pct, trades]
 *   POST { mode, force?: true, symbols?: [...] } recompute (the recalc script forces all)
 */

const isMode = (m: unknown): m is Mode => m === 'test' || m === 'live'

export async function GET(req: NextRequest) {
  const params = new URL(req.url).searchParams
  const mode = params.get('mode')
  if (!isMode(mode)) return NextResponse.json({ error: 'mode must be test or live' }, { status: 400 })
  const symbol = params.get('symbol')?.toUpperCase() || null
  const range = params.get('range')
  if (range && !RANGE_PRESETS.some(p => p.key === range)) {
    return NextResponse.json({ error: `unknown range ${range}` }, { status: 400 })
  }

  const store = loadStore(mode)
  const symbols = symbol ? { [symbol]: store.symbols[symbol] } : store.symbols
  if (!range) return NextResponse.json({ ...store, symbols })
  const out: Record<string, unknown> = {}
  for (const [sym, s] of Object.entries(symbols)) {
    if (s) out[sym] = { fp: s.fp, at: s.at, day: s.day, ranges: { [range]: s.ranges[range] } }
  }
  return NextResponse.json({ ...store, symbols: out })
}

export async function POST(req: NextRequest) {
  let body: { mode?: unknown; force?: unknown; symbols?: unknown }
  try { body = await req.json() } catch { return NextResponse.json({ error: 'invalid JSON' }, { status: 400 }) }
  if (!isMode(body.mode)) return NextResponse.json({ error: 'mode must be test or live' }, { status: 400 })
  const symbols = Array.isArray(body.symbols)
    ? body.symbols.filter((s): s is string => typeof s === 'string').map(s => s.toUpperCase())
    : undefined
  const res = await refresh(body.mode, { force: body.force === true, symbols })
  return NextResponse.json(res)
}
