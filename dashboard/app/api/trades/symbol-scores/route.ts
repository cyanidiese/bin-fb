import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../../_utils'
import { lockedPresetsFor } from '../../_locked-presets'
import { rankPresets, topPreset, type PresetEfficiency } from '../_top-preset'
import { detectRankMax } from '../_rank-files'
import { presetProfitPct } from '@/lib/presetProfit'
import { orderInRange, RANGE_PRESETS } from '@/lib/tradesDateRange'

export const dynamic = 'force-dynamic'

/**
 * Sort keys for the Trades page symbol picker: the Profit% of each symbol's top preset
 * (the Rank-1 row) per date shortcut. Spec: docs/specs/2026-09-24-trades-picker-profit-sort.md
 *
 *   GET ?mode=test&range=7d&from=<epoch s | empty>&ensure=SOLUSDT
 *
 * Cached in data/symbol_sort_scores_{mode}.json, one entry per symbol × shortcut, because
 * computing one symbol reads ~88 rank files and doing all of them on every page load
 * would be ~150 MB of JSON. An entry is created only for `ensure` (the symbol just
 * clicked). Existing entries are recomputed when stale, so they keep their place:
 *
 *   - the top preset changed  (lock, unlock, or the unlocked rank-1 preset moved)
 *   - a top-preset order closed — detected by the mtimes of real_orders_{SYM} and
 *     virtual_orders_rank1_{SYM}. A real order is always the top preset's, rank 1 only
 *     ever holds the top preset, and both files are written only on close. Ranks >= 2
 *     never hold it, so their constant churn triggers nothing.
 *   - the window slid past the TTL (sliding shortcuts age without any close)
 *
 * `from` comes from the browser because "Today" is local midnight — the server must not
 * guess a timezone. Nothing here throws to the caller: on any failure the picker keeps
 * registry order.
 */

interface ScoreEntry {
  pct: number | null
  preset: string | null
  fp: string
  at: number
  /** Window start the entry was computed for (epoch s, null = unbounded). */
  from?: number | null
  /** FORMULA_VERSION it was computed with. */
  v?: number
}
type ScoreCache = Record<string, Record<string, ScoreEntry>>

/** Bump whenever computePct changes, so cached entries — "all" never expires — are
 *  recomputed instead of silently mixing two formulas in one sort.
 *  2: rank-1 virtual orders included. */
const FORMULA_VERSION = 2

const TTL_MS: Record<string, number | null> = {
  today: 10 * 60_000,
  '24h': 10 * 60_000,
  '7d':  60 * 60_000,
  '30d': 60 * 60_000,
  all:   null,
}

const DATA_DIR = path.join(BOT_ROOT, 'data')

function readJson<T>(filePath: string, fallback: T): T {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')) as T } catch { return fallback }
}

function mtime(filePath: string): number {
  try { return fs.statSync(filePath).mtimeMs } catch { return 0 }
}

/** Changes exactly when an order of the top preset closes — see the header comment. */
function fingerprint(symbol: string, mode: string): string {
  return [
    mtime(path.join(DATA_DIR, `real_orders_${symbol}_${mode}.json`)),
    mtime(path.join(DATA_DIR, `virtual_orders_rank1_${symbol}_${mode}.json`)),
  ].join('|')
}

interface PnlRecord {
  preset_name?: string
  status?: string
  result?: string | null
  open_time?: string | null
  close_time?: string | null
  entry_price: number
  quantity: number
  leverage: number
  pnl_usdt: number | null
}

/** Same inputs the page's Profit% column uses: real orders plus CLOSED virtual orders
 *  from ranks 1..max, filtered by order overlap. Rank 1 is the real slot's stand-in and
 *  holds the top preset's trades whenever no real order was running. */
function computePct(
  symbol: string, mode: string, preset: string, fromS: number | null, rankMax: number,
): number | null {
  const inWindow = (o: PnlRecord) => o.preset_name === preset && orderInRange(o, fromS, null)
  const real = readJson<PnlRecord[]>(
    path.join(DATA_DIR, `real_orders_${symbol}_${mode}.json`), []).filter(inWindow)
  const virt: PnlRecord[] = []
  for (let rank = 1; rank <= rankMax; rank++) {
    const list = readJson<PnlRecord[]>(
      path.join(DATA_DIR, `virtual_orders_rank${rank}_${symbol}_${mode}.json`), [])
    for (const o of list) {
      if (o.status === 'closed' && o.result != null && inWindow(o)) virt.push(o)
    }
  }
  return presetProfitPct(real, virt)
}

export async function GET(req: NextRequest) {
  const params = new URL(req.url).searchParams
  const mode = params.get('mode')
  const range = params.get('range') ?? ''
  if ((mode !== 'test' && mode !== 'live') || !RANGE_PRESETS.some(p => p.key === range)) {
    return NextResponse.json({ error: 'mode and range required' }, { status: 400 })
  }
  const fromRaw = Number(params.get('from'))
  const fromS = range === 'all' || !params.get('from') || !Number.isFinite(fromRaw) ? null : fromRaw
  const ensure = params.get('ensure')?.toUpperCase() || null

  const cachePath = path.join(DATA_DIR, `symbol_sort_scores_${mode}.json`)
  const cache = readJson<ScoreCache>(cachePath, {})

  const efficiency = readJson<Record<string, Record<string, PresetEfficiency>>>(
    path.join(DATA_DIR, `preset_efficiency_${mode}.json`), {})
  const riskConfig = readJson<Record<string, unknown>>(path.join(BOT_ROOT, 'risk_config.json'), {})
  const locks = lockedPresetsFor(riskConfig, mode)
  const rankMax = detectRankMax(DATA_DIR, mode)
  const ttl = TTL_MS[range]
  const now = Date.now()

  const candidates = new Set(Object.keys(cache).filter(sym => cache[sym]?.[range]))
  if (ensure) candidates.add(ensure)

  let dirty = false
  for (const sym of candidates) {
    try {
      const { presetRanks } = rankPresets(efficiency[sym] ?? {}, locks[sym] ?? null, riskConfig)
      const top = topPreset(presetRanks)
      const fp = fingerprint(sym, mode)
      const e = cache[sym]?.[range]
      // "Today" is a fixed midnight, not a sliding window: past midnight, or computed
      // for another timezone, the entry covers a different day whatever its age.
      const sameWindow = range !== 'today' || (e?.from ?? null) === fromS
      const fresh = e && e.v === FORMULA_VERSION && e.preset === top && e.fp === fp && sameWindow
        && (ttl === null || now - e.at < ttl)
      if (fresh) continue
      const pct = top ? computePct(sym, mode, top, fromS, rankMax) : null
      cache[sym] = { ...(cache[sym] ?? {}), [range]: { pct, preset: top, fp, at: now, from: fromS, v: FORMULA_VERSION } }
      dirty = true
    } catch { /* one bad symbol must not cost the others their sort key */ }
  }

  if (dirty) {
    try {
      // Atomic: a concurrent reader never sees half a file. Last writer wins; the
      // loser's entries are simply recomputed next time.
      const tmp = `${cachePath}.${process.pid}.tmp`
      fs.writeFileSync(tmp, JSON.stringify(cache))
      fs.renameSync(tmp, cachePath)
    } catch { /* read-only or full disk — still return what was computed */ }
  }

  const scores: Record<string, { pct: number | null; preset: string | null }> = {}
  for (const [sym, byRange] of Object.entries(cache)) {
    const e = byRange?.[range]
    if (e) scores[sym] = { pct: e.pct, preset: e.preset }
  }
  return NextResponse.json({ mode, range, scores })
}
