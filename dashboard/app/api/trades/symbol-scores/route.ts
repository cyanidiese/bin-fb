import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../../_utils'
import { lockedPresetsFor } from '../../_locked-presets'
import { detectRankMax } from '../_rank-files'
import { presetNamesFor } from '../_preset-names'
import { presetProfitPct } from '@/lib/presetProfit'
import { orderInRange, RANGE_PRESETS } from '@/lib/tradesDateRange'

export const dynamic = 'force-dynamic'

/**
 * Sort keys for the Trades page symbol picker, per date shortcut: the Profit% of the
 * row at the top of the Preset Efficiency table when it is sorted by Profit% DESC.
 * That is the locked preset if there is one (the table pins it first), otherwise the
 * preset with the highest Profit% in the window. Spec:
 * docs/specs/2026-09-24-trades-picker-profit-sort.md
 *
 *   GET ?mode=test&range=7d&from=<epoch s | empty>&ensure=SOLUSDT
 *
 * Cached in data/symbol_sort_scores_{mode}.json, one entry per symbol × shortcut. An
 * entry is created only for `ensure` (the symbol just clicked, or the recalc script).
 * Existing entries are recomputed when stale, so they keep their place:
 *
 *   - the lock changed
 *   - ANY order of the symbol closed. With an unlocked symbol any preset can overtake
 *     the current top, so a close of the top preset alone is not enough. Detected by
 *     the newest mtime across real_orders_{SYM} and every virtual_orders_rank*_{SYM}.
 *     Recomputing one symbol is ~50 ms of file reads, so this stays cheap.
 *   - the window slid past the TTL (sliding shortcuts age without any close)
 *   - "today" was computed for a different midnight
 *   - FORMULA_VERSION changed
 *
 * `from` comes from the browser because "Today" is local midnight — the server must not
 * guess a timezone. Nothing here throws to the caller: on any failure the picker keeps
 * registry order.
 */

interface ScoreEntry {
  pct: number | null
  preset: string | null
  /** Closed trades behind pct — a top row built on 2 trades is mostly luck. */
  n: number
  fp: string
  at: number
  /** Window start the entry was computed for (epoch s, null = unbounded). */
  from: number | null
  /** FORMULA_VERSION it was computed with. */
  v: number
}
type ScoreCache = Record<string, Record<string, ScoreEntry>>

/** Bump whenever the computation changes, so cached entries — "all" never expires —
 *  are recomputed instead of silently mixing two formulas in one sort.
 *  2: rank-1 virtual orders included.
 *  3: top = the table's top row by Profit% (locked, else best Profit%), not Rank 1. */
const FORMULA_VERSION = 3

const TTL_MS: Record<string, number | null> = {
  today: 10 * 60_000,
  '24h': 10 * 60_000,
  '7d':  60 * 60_000,
  '14d': 60 * 60_000,
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

function orderFiles(symbol: string, mode: string, rankMax: number): string[] {
  const files = [path.join(DATA_DIR, `real_orders_${symbol}_${mode}.json`)]
  for (let rank = 1; rank <= rankMax; rank++) {
    files.push(path.join(DATA_DIR, `virtual_orders_rank${rank}_${symbol}_${mode}.json`))
  }
  return files
}

/** Changes whenever any order of the symbol closes, or the lock changes. */
function fingerprint(files: string[], lock: string | null): string {
  return `${lock ?? ''}|${Math.max(0, ...files.map(mtime))}`
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

/** The table's top row for one symbol and window. Same inputs as the page's Profit%
 *  column (buildPresetRows): real orders plus CLOSED virtual orders from ranks 1..max,
 *  filtered by order overlap, restricted to the presets the table lists. */
function computeTop(
  lock: string | null, fromS: number | null, files: string[], names: string[],
): { preset: string | null; pct: number | null; n: number } {
  const listed = new Set(names)
  const real = new Map<string, PnlRecord[]>()
  const virt = new Map<string, PnlRecord[]>()
  const add = (m: Map<string, PnlRecord[]>, o: PnlRecord) => {
    const k = o.preset_name ?? ''
    if (!listed.has(k) || !orderInRange(o, fromS, null)) return
    const list = m.get(k)
    if (list) list.push(o)
    else m.set(k, [o])
  }
  const [realFile, ...rankFiles] = files
  for (const o of readJson<PnlRecord[]>(realFile, [])) add(real, o)
  for (const f of rankFiles) {
    for (const o of readJson<PnlRecord[]>(f, [])) {
      if (o.status === 'closed' && o.result != null) add(virt, o)
    }
  }
  const rowFor = (name: string) => {
    const r = real.get(name) ?? []
    const v = virt.get(name) ?? []
    return { preset: name, pct: presetProfitPct(r, v), n: r.length + v.length }
  }

  if (lock) return rowFor(lock)

  // Highest Profit%; presets with no trades (null) sort last in the table, so they can
  // only be the top when nothing traded. Ties keep table order (first listed wins).
  let best: { preset: string | null; pct: number | null; n: number } = { preset: null, pct: null, n: 0 }
  for (const name of names) {
    const row = rowFor(name)
    if (row.pct !== null && (best.pct === null || row.pct > best.pct)) best = row
  }
  return best
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

  const efficiency = readJson<Record<string, Record<string, unknown>>>(
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
      const lock = locks[sym] ?? null
      const files = orderFiles(sym, mode, rankMax)
      const fp = fingerprint(files, lock)
      const e = cache[sym]?.[range]
      const sameWindow = range !== 'today' || (e?.from ?? null) === fromS
      const fresh = e && e.v === FORMULA_VERSION && e.fp === fp && sameWindow
        && (ttl === null || now - e.at < ttl)
      if (fresh) continue
      const names = presetNamesFor(sym, mode, efficiency[sym] ?? {})
      const top = computeTop(lock, fromS, files, names)
      cache[sym] = {
        ...(cache[sym] ?? {}),
        [range]: { ...top, fp, at: now, from: fromS, v: FORMULA_VERSION },
      }
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

  const scores: Record<string, { pct: number | null; preset: string | null; n: number; locked: boolean }> = {}
  for (const [sym, byRange] of Object.entries(cache)) {
    const e = byRange?.[range]
    if (e) scores[sym] = { pct: e.pct, preset: e.preset, n: e.n ?? 0, locked: !!locks[sym] }
  }
  return NextResponse.json({ mode, range, scores })
}
