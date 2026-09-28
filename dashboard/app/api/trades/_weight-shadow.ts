// Weight shadow calculator: once per day per mode, record what Profit%-driven weight
// policies WOULD set, next to the real weights. Nothing reads it but
// scripts/eval_weight_shadow.py — it measures whether Profit% predicts anything before
// any money follows it. Spec: docs/specs/2026-09-28-weight-shadow-calculator.md

import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../_utils'
import { readRiskConfig } from '../_risk-config'
import { lockedPresetsFor } from '../_locked-presets'
import { loadStore, topRow, type Mode, type RangeStats } from './_preset-profit-store'

const shadowPath = (mode: Mode) => path.join(BOT_ROOT, 'data', `weight_shadow_${mode}.jsonl`)

/** [Profit%, trades, preset] of the Preset Efficiency top row for a range. */
export type Top = [number | null, number, string | null]

export interface ShadowSymbol {
  w: number
  lock: string | null
  p7: Top
  p14: Top
  policies: { static: number; tilt: number; brake: number }
}

const round = (x: number, d = 4) => Math.round(x * 10 ** d) / 10 ** d
const fmtNum = (x: number) => Number.isInteger(x) ? String(x) : x.toFixed(2)

/** Bounded momentum: at most ±30 %, only with ≥ 20 trades in 14 days. */
export function tiltWeight(w: number, p14: Top): number {
  const [pct, n] = p14
  if (pct === null || n < 20) return w
  const f = Math.min(1.3, Math.max(0.7, 1 + 0.3 * Math.tanh(pct / 50)))
  return round(w * f)
}

/** Asymmetric brake: halve only on a sustained, sizeable loss (≥ 30 trades, ≤ −30 %). */
export function brakeWeight(w: number, p14: Top): number {
  const [pct, n] = p14
  return pct !== null && n >= 30 && pct <= -30 ? round(w * 0.5) : w
}

function top(stats: RangeStats | undefined, lock: string | null): Top {
  const t = topRow(stats, lock)
  return [t.pct === null ? null : round(t.pct, 2), t.n, t.preset]
}

export function buildSnapshot(mode: Mode): { day: string; symbols: Record<string, ShadowSymbol> } | null {
  const store = loadStore(mode)
  const entries = Object.entries(store.symbols)
  if (entries.length === 0) return null
  const cfg = readRiskConfig(mode)
  const weights = (cfg.symbol_weights ?? {}) as Record<string, number>
  const locks = lockedPresetsFor(cfg, mode)
  const day = entries.map(([, s]) => s.day).sort().at(-1) as string
  const symbols: Record<string, ShadowSymbol> = {}
  for (const [sym, s] of entries) {
    const w = Number(weights[sym] ?? 0)
    const lock = locks[sym] ?? null
    const p7 = top(s.ranges['7d'], lock)
    const p14 = top(s.ranges['14d'], lock)
    symbols[sym] = { w, lock, p7, p14, policies: { static: w, tilt: tiltWeight(w, p14), brake: brakeWeight(w, p14) } }
  }
  return { day, symbols }
}

function lastDay(file: string): string | null {
  try {
    const text = fs.readFileSync(file, 'utf8').trimEnd()
    const last = text.slice(text.lastIndexOf('\n') + 1)
    return last ? (JSON.parse(last).day ?? null) : null
  } catch { return null }
}

/** Append today's snapshot unless the file already has it. Returns the day written. */
export function recordShadow(mode: Mode): string | null {
  const snap = buildSnapshot(mode)
  if (!snap) return null
  const file = shadowPath(mode)
  if (lastDay(file) === snap.day) return null
  fs.appendFileSync(file, JSON.stringify({ day: snap.day, mode, at: Date.now(), symbols: snap.symbols }) + '\n')
  return snap.day
}

// ── Track record: how each policy did vs the current weights ─────────────────
// Same method as scripts/eval_weight_shadow.py: for each snapshot day with a complete
// forward window, weight-normalised forward Profit% of the would-be-real trades
// (real orders + rank-1 virtual orders).

export const POLICIES = ['static', 'tilt', 'brake'] as const
export type Policy = typeof POLICIES[number]

export interface PolicyScore { mean: number; vsStatic: number; betterDays: number }
export interface TrackRecord { snapshots: number; evaluated: number; days: number; scores: Record<Policy, PolicyScore> | null }

const EXCLUDED = new Set(['promoted_to_real', 'max_age', 'closed_early'])

function wouldBeRealTrades(mode: Mode): Record<string, [number, number][]> {
  const dir = path.join(BOT_ROOT, 'data')
  const out: Record<string, [number, number][]> = {}
  let files: string[] = []
  try { files = fs.readdirSync(dir) } catch { return out }
  for (const f of files) {
    const v = f.match(new RegExp(`^virtual_orders_rank1_([A-Z0-9]+)_${mode}\\.json$`))
    const r = f.match(new RegExp(`^real_orders_([A-Z0-9]+)_${mode}\\.json$`))
    const sym = (v ?? r)?.[1]
    if (!sym) continue
    let rows: Record<string, unknown>[] = []
    try { rows = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')) } catch { continue }
    if (!Array.isArray(rows)) continue
    for (const o of rows) {
      const res = o.result as string | undefined
      if (!res || EXCLUDED.has(res) || !o.open_time) continue
      const t = Date.parse(String(o.open_time)) / 1000
      const lev = Number(o.leverage) || 1
      const margin = (Number(o.entry_price) * Number(o.quantity)) / lev
      if (!(margin > 0) || !Number.isFinite(t)) continue
      ;(out[sym] ??= []).push([t, (Number(o.pnl_usdt) || 0) / margin * 100])
    }
  }
  return out
}

/** Midnight of `day` (YYYY-MM-DD) in Europe/Kyiv, epoch seconds. */
function kyivMidnight(day: string): number {
  const utcMidnight = Date.parse(`${day}T00:00:00Z`)
  const probe = new Date(utcMidnight)
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Kyiv', hour: '2-digit', hourCycle: 'h23' }).format(probe)
  return utcMidnight / 1000 - Number(parts) * 3600
}

/** Reconstructed past snapshots (scripts/backfill_weight_shadow.py --write): same shape,
 *  built from order history with today's weights and locks. */
const historyPath = (mode: Mode) => path.join(BOT_ROOT, 'data', `weight_shadow_${mode}_history.jsonl`)

export function evaluateShadow(mode: Mode, days = 7, source: 'live' | 'history' = 'live'): TrackRecord {
  let lines: string[] = []
  try {
    lines = fs.readFileSync(source === 'live' ? shadowPath(mode) : historyPath(mode), 'utf8')
      .split('\n').filter(Boolean)
  } catch { /* none yet */ }
  const snaps = lines.map(l => { try { return JSON.parse(l) } catch { return null } }).filter(Boolean) as
    { day: string; symbols: Record<string, ShadowSymbol> }[]
  const now = Date.now() / 1000
  const trades = snaps.length ? wouldBeRealTrades(mode) : {}
  const per: Record<Policy, number[]> = { static: [], tilt: [], brake: [] }
  for (const snap of snaps) {
    const start = kyivMidnight(snap.day)
    const end = start + days * 86400
    if (end > now) continue
    const fwd: Record<string, number> = {}
    for (const sym of Object.keys(snap.symbols)) {
      fwd[sym] = (trades[sym] ?? []).filter(([t]) => t >= start && t < end).reduce((s, [, p]) => s + p, 0)
    }
    for (const pol of POLICIES) {
      const ws = Object.entries(snap.symbols)
        .filter(([, v]) => Number(v.w) > 0)          // the funded book, as traded
        .map(([s, v]) => [s, Number(v.policies[pol]) || 0] as const)
      const tot = ws.reduce((s, [, w]) => s + (w > 0 ? w : 0), 0)
      per[pol].push(tot > 0 ? ws.reduce((s, [sym, w]) => s + (w > 0 ? (w / tot) * fwd[sym] : 0), 0) : 0)
    }
  }
  const n = per.static.length
  if (!n) return { snapshots: snaps.length, evaluated: 0, days, scores: null }
  const mean = (v: number[]) => v.reduce((a, b) => a + b, 0) / v.length
  const scores = {} as Record<Policy, PolicyScore>
  for (const pol of POLICIES) {
    const diff = per[pol].map((v, i) => v - per.static[i])
    scores[pol] = { mean: round(mean(per[pol]), 2), vsStatic: round(mean(diff), 2), betterDays: diff.filter(d => d > 0).length }
  }
  return { snapshots: snaps.length, evaluated: n, days, scores }
}

// ── Panel suggestions: a value for EVERY symbol, with the reasons to doubt it ──
// The tracked policies above stay evidence-gated (they are what the track record
// scores). The panel shows the raw value for every symbol and flags each one that
// is not well supported, so the user decides with the caveats in view.

/** flags = reasons to doubt the value (shown as "!"); note = plain explanation (tooltip only). */
export interface Suggestion { value: number; flags: string[]; note?: string }
export interface PanelSuggestions { tilt: Suggestion; brake: Suggestion }

const TILT_MIN_TRADES = 20
const BRAKE_MIN_TRADES = 30
const BRAKE_LOSS_PCT = -30

// ── Applied-suggestion log: anchors suggestions so applying twice does not compound ──

export type AppliedPolicy = 'tilt' | 'brake' | 'custom'
export interface AppliedEntry { ts: number; symbol: string; policy: AppliedPolicy; old: number; new: number; note?: string }
const appliedPath = (mode: Mode) => path.join(BOT_ROOT, 'data', `weight_suggestion_applies_${mode}.jsonl`)

export function logApplied(mode: Mode, entries: AppliedEntry[]): void {
  if (entries.length) fs.appendFileSync(appliedPath(mode), entries.map(e => JSON.stringify(e)).join('\n') + '\n')
}

export interface Anchor { base: number; since: number; policy: AppliedPolicy }

/** Per symbol, the weight suggestions should be computed from: the weight before the first
 *  tilt/brake applied in the last `days`, unless a typed (custom) value was set later — that
 *  is the user's new baseline. Only while the current weight is still the one applied. */
export function anchors(mode: Mode, currentWeights: Record<string, number>, days = 14): Record<string, Anchor> {
  let lines: string[] = []
  try { lines = fs.readFileSync(appliedPath(mode), 'utf8').split('\n').filter(Boolean) } catch { return {} }
  const cutoff = Date.now() - days * 86400_000
  const bySym: Record<string, AppliedEntry[]> = {}
  for (const l of lines) {
    try {
      const e = JSON.parse(l) as AppliedEntry
      if (e.ts >= cutoff) (bySym[e.symbol] ??= []).push(e)
    } catch { /* torn line */ }
  }
  const out: Record<string, Anchor> = {}
  for (const [sym, es] of Object.entries(bySym)) {
    es.sort((a, b) => a.ts - b.ts)
    let anchor: Anchor | null = null
    for (const e of es) {
      if (e.policy === 'custom') anchor = null
      else if (!anchor) anchor = { base: e.old, since: e.ts, policy: e.policy }
    }
    const last = es[es.length - 1]
    // someone changed the weight since (Risk table, SSH): that is the new baseline
    if (anchor && Math.abs((currentWeights[sym] ?? 0) - last.new) < 1e-9) out[sym] = anchor
  }
  return out
}

export function panelSuggestions(
  row: ShadowSymbol,
  opts: { disabled: boolean; baseWeight: number; history?: TrackRecord; anchor?: Anchor },
): PanelSuggestions {
  const [pct, n] = row.p14
  // Compute from the pre-suggestion weight, so applying again does not compound
  // (3 -> brake 1.5 -> brake 0.75 ...). The current weight stays what "=" compares to.
  const anchor = opts.anchor
  const baseW = anchor ? anchor.base : row.w
  const anchorNote = anchor
    ? `computed from ${fmtNum(anchor.base)} — the weight before ${anchor.policy} was applied on ${new Date(anchor.since).toISOString().slice(0, 10)} — so applying again does not compound`
    : undefined
  const common: string[] = []
  if (opts.disabled) common.push('disabled in this mode — the weight has no effect until the symbol is enabled in Settings')
  if (row.lock && n === 0) common.push(`the locked preset ${row.lock} has no trades in 14 days`)

  // Tilt
  const tf: string[] = [...common]
  let tilt: number
  if (pct === null) {
    tilt = row.w
    tf.push('no trades in the last 14 days — nothing to tilt on')
  } else {
    const factor = Math.min(1.3, Math.max(0.7, 1 + 0.3 * Math.tanh(pct / 50)))
    if (baseW > 0) {
      tilt = round(baseW * factor)
    } else if (pct > 0) {
      tilt = round(opts.baseWeight * factor)
      tf.push(`weight is 0: starts from ${opts.baseWeight} (tats_min_weight) and turns REAL orders ON`)
    } else {
      tilt = 0
      tf.push('weight is 0 and 14-day Profit% is not positive — no reason to fund it')
    }
    if (n < TILT_MIN_TRADES) tf.push(`only ${n} trade(s) in 14 days — needs ≥ ${TILT_MIN_TRADES} to be meaningful`)
  }
  const ht = opts.history?.scores?.tilt
  if (ht && opts.history!.evaluated >= 20 && ht.vsStatic <= 0.1) {
    tf.push(`history: tilt added ${ht.vsStatic >= 0 ? '+' : ''}${ht.vsStatic.toFixed(2)} %/week over ${opts.history!.evaluated} days — no real edge`)
  }

  // Brake
  const bf: string[] = [...common]
  let brake = row.w
  let bnote: string | undefined
  if (baseW === 0) {
    bnote = 'brake only lowers a weight — this one is already 0'
  } else if (pct === null) {
    bnote = 'no trades in the last 14 days — nothing to judge'
  } else if (pct <= BRAKE_LOSS_PCT) {
    brake = round(baseW * 0.5)
    if (n < BRAKE_MIN_TRADES) bf.push(`only ${n} trade(s) in 14 days — needs ≥ ${BRAKE_MIN_TRADES} before halving on it`)
  } else {
    bnote = `14-day Profit% ${pct.toFixed(1)} % is above the ${BRAKE_LOSS_PCT} % brake level — no cut`
  }
  const hb = opts.history?.scores?.brake
  if (brake !== row.w && hb && opts.history!.evaluated >= 20 && hb.vsStatic <= 0.1) {
    bf.push(`history: brake added ${hb.vsStatic >= 0 ? '+' : ''}${hb.vsStatic.toFixed(2)} %/week over ${opts.history!.evaluated} days — no real edge`)
  }
  // a symbol-level doubt (e.g. disabled) only matters when the brake would change something
  if (anchor && brake === row.w && pct !== null && pct <= BRAKE_LOSS_PCT) {
    bnote = `already braked on ${new Date(anchor.since).toISOString().slice(0, 10)} (${fmtNum(anchor.base)} → ${fmtNum(row.w)}); it will not halve again while the same loss window is in force`
  }
  const tnote = anchorNote
  return {
    tilt: { value: tilt, flags: tf, note: tnote },
    brake: { value: brake, flags: brake !== row.w ? bf : [], note: [bnote, anchorNote].filter(Boolean).join(' · ') || undefined },
  }
}
