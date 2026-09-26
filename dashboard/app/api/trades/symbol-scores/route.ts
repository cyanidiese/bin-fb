import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../../_utils'
import { lockedPresetsFor } from '../../_locked-presets'
import { RANGE_PRESETS } from '@/lib/tradesDateRange'
import { loadStore, refresh, topRow, type Mode, type TopRow } from '../_preset-profit-store'

export const dynamic = 'force-dynamic'

/**
 * Sort keys for the Trades page symbol picker: per symbol, the Profit% of the Preset
 * Efficiency table's top row when sorted by Profit% DESC — the locked preset, else the
 * best — for one date shortcut.
 *
 *   GET ?mode=test&range=7d&ensure=SOLUSDT
 *
 * A reader of the preset Profit% store (docs/specs/2026-09-26-preset-profit-store.md).
 * Locks are applied here, at read time, so a lock change needs no recompute. `ensure`
 * (the symbol just clicked) and any stale symbol are refreshed first — normally the
 * background worker already has, and this is a stat-only check. Nothing here throws to
 * the caller: on any failure the picker keeps registry order.
 */
export async function GET(req: NextRequest) {
  const params = new URL(req.url).searchParams
  const mode = params.get('mode')
  const range = params.get('range') ?? ''
  if ((mode !== 'test' && mode !== 'live') || !RANGE_PRESETS.some(p => p.key === range)) {
    return NextResponse.json({ error: 'mode and range required' }, { status: 400 })
  }
  const ensure = params.get('ensure')?.toUpperCase() || null

  try {
    await refresh(mode as Mode)
    if (ensure && !loadStore(mode as Mode).symbols[ensure]) await refresh(mode as Mode, { symbols: [ensure] })
  } catch { /* serve whatever is stored */ }

  let riskConfig: unknown = {}
  try { riskConfig = JSON.parse(fs.readFileSync(path.join(BOT_ROOT, 'risk_config.json'), 'utf8')) } catch { /* no locks */ }
  const locks = lockedPresetsFor(riskConfig, mode)

  const store = loadStore(mode as Mode)
  const scores: Record<string, TopRow> = {}
  for (const [sym, stats] of Object.entries(store.symbols)) {
    scores[sym] = topRow(stats.ranges[range], locks[sym] ?? null)
  }
  return NextResponse.json({ mode, range, scores })
}
