// Stored Profit% of every preset × date shortcut × symbol, one file per mode.
// Spec: docs/specs/2026-09-26-preset-profit-store.md
//
// The dashboard owns this store — the bot is untouched. A background worker
// (instrumentation.ts) keeps it current; the API routes read it and refresh on demand.

import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../_utils'
import { detectRankMax } from './_rank-files'
import { presetNamesFor } from './_preset-names'
import { orderMarginPct } from '@/lib/presetProfit'
import { RANGE_PRESETS } from '@/lib/tradesDateRange'

export type Mode = 'test' | 'live'
export const MODES: Mode[] = ['test', 'live']

/** [Profit% summed over trades, trade count]. */
export type PresetStat = [number, number]

export interface RangeStats {
  /** Window start, epoch seconds; null = all history. */
  from: number | null
  /** Only presets with at least one trade. Absent = no trades (Profit% null). */
  presets: Record<string, PresetStat>
}

export interface SymbolStats {
  /** Newest mtime across the symbol's order files when computed — changes on any close. */
  fp: number
  /** Computed at, epoch ms. */
  at: number
  /** The "today" date in STORE_TZ it was computed for. */
  day: string
  ranges: Record<string, RangeStats>
}

export interface Store {
  v: 1
  /** Bump when the computation changes: every symbol is then recomputed. */
  formula: number
  mode: Mode
  tz: string
  symbols: Record<string, SymbolStats>
}

/** 4 = every preset stored (3 was top-row-only in symbol_sort_scores_{mode}.json). */
const FORMULA = 4
/** Sliding windows move without any close; 10 min keeps "today"/"24h" honest. */
const MAX_AGE_MS = 10 * 60_000
/** The user's timezone: "today" starts at midnight here. */
export const STORE_TZ = process.env.PROFIT_TZ || 'Europe/Kyiv'

const DATA_DIR = path.join(BOT_ROOT, 'data')
const storePath = (mode: Mode) => path.join(DATA_DIR, `preset_profit_${mode}.json`)

function readJson<T>(filePath: string, fallback: T): T {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')) as T } catch { return fallback }
}

function mtime(filePath: string): number {
  try { return fs.statSync(filePath).mtimeMs } catch { return 0 }
}

// ── Time ────────────────────────────────────────────────────────────────────

/** Local calendar date and midnight (epoch s) of `now` in `tz`. */
export function tzMidnight(tz: string, now: Date = new Date()): { day: string; midnightS: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(now).map(p => [p.type, p.value]),
  )
  const y = +parts.year, m = +parts.month, d = +parts.day
  const wallMs = Date.UTC(y, m - 1, d, +parts.hour, +parts.minute, +parts.second)
  const offsetMs = wallMs - Math.floor(now.getTime() / 1000) * 1000
  return { day: `${parts.year}-${parts.month}-${parts.day}`, midnightS: (Date.UTC(y, m - 1, d) - offsetMs) / 1000 }
}

/** Window start per shortcut, epoch seconds (null = all history). */
function windowStarts(now: Date): Record<string, number | null> {
  const nowS = Math.floor(now.getTime() / 1000)
  const { midnightS } = tzMidnight(STORE_TZ, now)
  const out: Record<string, number | null> = {}
  for (const p of RANGE_PRESETS) {
    out[p.key] = p.days === null ? null : p.days === 0 ? midnightS : nowS - p.days * 86_400
  }
  return out
}

// ── Computation ─────────────────────────────────────────────────────────────

function orderFiles(symbol: string, mode: Mode, rankMax: number): string[] {
  const files = [path.join(DATA_DIR, `real_orders_${symbol}_${mode}.json`)]
  for (let rank = 1; rank <= rankMax; rank++) {
    files.push(path.join(DATA_DIR, `virtual_orders_rank${rank}_${symbol}_${mode}.json`))
  }
  return files
}

const fingerprint = (files: string[]) => Math.max(0, ...files.map(mtime))

interface RawOrder {
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

const isoS = (iso: string | null | undefined): number | null => {
  if (!iso) return null
  const ms = new Date(iso).getTime()
  return Number.isNaN(ms) ? null : ms / 1000
}

/**
 * All windows for one symbol from ONE parse of its order files. Inclusion matches the
 * page's Profit% column (buildPresetRows + filterTradesData): real orders; virtual orders
 * that are CLOSED with a result, ranks 1..max; an order counts in a window if it was open
 * at any point inside it; undated orders always count; only presets the table lists.
 */
function computeSymbol(
  files: string[], names: string[], starts: Record<string, number | null>,
): Record<string, RangeStats> {
  const listed = new Set(names)
  const ranges: Record<string, RangeStats> = {}
  for (const [key, from] of Object.entries(starts)) ranges[key] = { from, presets: {} }

  const take = (o: RawOrder) => {
    const name = o.preset_name ?? ''
    if (!listed.has(name)) return
    const open = isoS(o.open_time), close = isoS(o.close_time)
    const end = close ?? open
    const pct = orderMarginPct(o)
    for (const r of Object.values(ranges)) {
      if (r.from !== null && end !== null && end < r.from) continue
      const s = r.presets[name]
      if (s) { s[0] += pct; s[1] += 1 } else r.presets[name] = [pct, 1]
    }
  }

  const [realFile, ...rankFiles] = files
  for (const o of readJson<RawOrder[]>(realFile, [])) take(o)
  for (const f of rankFiles) {
    for (const o of readJson<RawOrder[]>(f, [])) {
      if (o.status === 'closed' && o.result != null) take(o)
    }
  }
  // Re-key in table order (backtest names, then efficiency keys) so topRow's "first
  // best wins" breaks ties exactly as the table's stable sort does.
  for (const r of Object.values(ranges)) {
    const ordered: Record<string, PresetStat> = {}
    for (const name of names) {
      const st = r.presets[name]
      if (st) ordered[name] = [Math.round(st[0] * 1e4) / 1e4, st[1]]
    }
    r.presets = ordered
  }
  return ranges
}

// ── Store I/O ───────────────────────────────────────────────────────────────

export function loadStore(mode: Mode): Store {
  const s = readJson<Store | null>(storePath(mode), null)
  if (s && s.v === 1 && s.formula === FORMULA && s.mode === mode && s.tz === STORE_TZ) return s
  return { v: 1, formula: FORMULA, mode, tz: STORE_TZ, symbols: {} }
}

function saveStore(store: Store): void {
  const p = storePath(store.mode)
  const tmp = `${p}.${process.pid}.tmp`
  fs.writeFileSync(tmp, JSON.stringify(store))
  fs.renameSync(tmp, p)
}

/** Every symbol the picker can show — the list /api/trades/symbols reads. */
export function registeredSymbols(): string[] {
  const d = readJson<{ symbols?: unknown }>(path.join(BOT_ROOT, 'dashboard', 'public', 'symbols.json'), {})
  return Array.isArray(d.symbols) ? d.symbols.filter((s): s is string => typeof s === 'string') : []
}

// ── Refresh ─────────────────────────────────────────────────────────────────

// The worker (instrumentation bundle) and the routes (route bundles) do not share module
// state, so the lock lives on globalThis. It serialises refreshes and store writes.
const g = globalThis as unknown as { __presetProfitLock?: Promise<unknown> }

function withLock<T>(fn: () => Promise<T>): Promise<T> {
  const prev = g.__presetProfitLock ?? Promise.resolve()
  const next = prev.catch(() => {}).then(fn)
  g.__presetProfitLock = next.catch(() => {})
  return next
}

const yieldToRequests = () => new Promise<void>(r => setImmediate(r))

export interface RefreshResult { mode: Mode; recomputed: string[]; ms: number }

/**
 * Recompute the stale symbols of one mode (or all of `symbols`, when forced) and save.
 * Stale = an order closed since (fp), midnight passed, older than MAX_AGE_MS, or missing.
 */
export function refresh(
  mode: Mode, opts: { symbols?: string[]; force?: boolean } = {},
): Promise<RefreshResult> {
  return withLock(async () => {
    const t0 = Date.now()
    const store = loadStore(mode)
    const efficiency = readJson<Record<string, Record<string, unknown>>>(
      path.join(DATA_DIR, `preset_efficiency_${mode}.json`), {})
    const rankMax = detectRankMax(DATA_DIR, mode)
    const symbols = opts.symbols ?? registeredSymbols()
    const recomputed: string[] = []

    for (const sym of symbols) {
      try {
        const files = orderFiles(sym, mode, rankMax)
        const fp = fingerprint(files)
        const now = new Date()
        const { day } = tzMidnight(STORE_TZ, now)
        const e = store.symbols[sym]
        const stale = opts.force || !e || e.fp !== fp || e.day !== day
          || now.getTime() - e.at >= MAX_AGE_MS
        if (!stale) continue
        const names = presetNamesFor(sym, mode, efficiency[sym] ?? {})
        store.symbols[sym] = {
          fp, at: now.getTime(), day,
          ranges: computeSymbol(files, names, windowStarts(now)),
        }
        recomputed.push(sym)
        await yieldToRequests()
      } catch { /* one bad symbol must not cost the others their numbers */ }
    }
    if (recomputed.length > 0) saveStore(store)
    return { mode, recomputed, ms: Date.now() - t0 }
  })
}

// ── Derived: the Preset Efficiency table's top row ──────────────────────────

export interface TopRow { pct: number | null; preset: string | null; n: number; locked: boolean }

/** Locked preset if any (the table pins it first), else the best Profit%. Presets are
 *  stored in table order, so on a tie the first wins — as in the table's stable sort. */
export function topRow(stats: RangeStats | undefined, lock: string | null): TopRow {
  const presets = stats?.presets ?? {}
  if (lock) {
    const s = presets[lock]
    return { preset: lock, pct: s ? s[0] : null, n: s ? s[1] : 0, locked: true }
  }
  let best: TopRow = { preset: null, pct: null, n: 0, locked: false }
  for (const [name, [pct, n]] of Object.entries(presets)) {
    if (best.pct === null || pct > best.pct) best = { preset: name, pct, n, locked: false }
  }
  return best
}
