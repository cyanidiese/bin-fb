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
import { readSymbolState } from '../symbols/_registry'

const shadowPath = (mode: Mode) => path.join(BOT_ROOT, 'data', `weight_shadow_${mode}.jsonl`)

/** [Profit%, trades, preset] of the Preset Efficiency top row for a range. */
export type Top = [number | null, number, string | null]

export interface ShadowSymbol {
  w: number
  lock: string | null
  p7: Top
  p14: Top
  /** [presets with Profit% > 0, presets with trades] in the window. */
  c7?: [number, number]
  c14?: [number, number]
  policies: { static: number; tilt: number; brake: number }
}

const round = (x: number, d = 4) => Math.round(x * 10 ** d) / 10 ** d
const fmtNum = (x: number) => Number.isInteger(x) ? String(x) : x.toFixed(2)

// ── Score allocation (the "Tilt" column) ─────────────────────────────────────
// Profit% of each window shrunk by its sample (few trades count for little), blended
// 50/50 so the recent week weighs as much as the fortnight. The weight budget
// (risk_config weight_budget, default 33 — the long-standing total) is shared among the top N positive-score, enabled
// symbols in proportion to score, at most CAP of it each; every other symbol gets 0.
// Computed from evidence, not from the current weight — applying it twice cannot
// compound. Spec: docs/specs/2026-09-28-weight-shadow-calculator.md (score allocation)

export const SHRINK_7D = 10
export const SHRINK_14D = 20
export const SCORE_CAP = 0.30
/** Below this share of profitable presets (7 days) the score is cut proportionally:
 *  2 of 81 (2.5 %) keeps a quarter — one lucky preset is not a symbol that works. */
export const BREADTH_FULL = 0.10
/** 7-day Profit% above half of the 14-day one: the profit is recent, not fading. */
export const MOMENTUM_BONUS = 1.25
/** Whole-number weights need a whole budget. It is NOT the sum of the current weights:
 *  after a decimal apply that sum was 1.26, which rounds to a single symbol weighted 1.
 *  Only ratios matter to sizing, so a fixed total keeps the numbers readable. */
export const WEIGHT_BUDGET_DEFAULT = 33
export function weightBudget(cfg: Record<string, unknown>): number {
  const b = Math.round(Number(cfg.weight_budget))
  return Number.isFinite(b) && b > 0 ? b : WEIGHT_BUDGET_DEFAULT
}

export interface ScoreParts { score: number; base: number; s7: number; s14: number; breadth: number; momentum: boolean }

export function symbolScore(r: { p7: Top; p14: Top; c7?: [number, number] }): ScoreParts {
  const [p7, n7] = r.p7, [p14, n14] = r.p14
  const s7 = (p7 ?? 0) * n7 / (n7 + SHRINK_7D)
  const s14 = (p14 ?? 0) * n14 / (n14 + SHRINK_14D)
  const base = 0.5 * s7 + 0.5 * s14
  const [prof, total] = r.c7 ?? [0, 0]
  const breadth = total > 0 ? Math.min(1, (prof / total) / BREADTH_FULL) : 1
  const momentum = p7 !== null && p14 !== null && p7 > 0 && p7 > p14 / 2
  const score = base > 0 ? base * breadth * (momentum ? MOMENTUM_BONUS : 1) : base
  return { score: round(score, 3), base: round(base, 3), s7: round(s7, 2), s14: round(s14, 2), breadth: round(breadth, 3), momentum }
}

/** Whole-number weights that add up to `total` (largest remainder); shares keep their order. */
export function wholeWeights(shares: Record<string, number>, total: number): Record<string, number> {
  const floors = Object.fromEntries(Object.entries(shares).map(([k, v]) => [k, Math.floor(v)]))
  let left = total - Object.values(floors).reduce((a, b) => a + b, 0)
  const byRemainder = Object.entries(shares).sort((a, b) => (b[1] - Math.floor(b[1])) - (a[1] - Math.floor(a[1])) || b[1] - a[1])
  for (const [k] of byRemainder) {
    if (left <= 0) break
    floors[k] += 1; left -= 1
  }
  return floors
}

export interface AllocationRow extends ScoreParts {
  /** 1-based rank among eligible (enabled, score > 0) symbols; null when not eligible. */
  rank: number | null
  inTopN: boolean
  value: number
}

export function scoreAllocation(
  symbols: Record<string, { p7: Top; p14: Top; c7?: [number, number] }>,
  opts: { disabled: Set<string>; n: number; budget: number; cap?: number },
): Record<string, AllocationRow> {
  const out: Record<string, AllocationRow> = {}
  const scored = Object.entries(symbols).map(([sym, r]) => [sym, symbolScore(r)] as const)
  const eligible = scored.filter(([sym, sc]) => !opts.disabled.has(sym) && sc.score > 0)
    .sort((a, b) => b[1].score - a[1].score || a[0].localeCompare(b[0]))
  const n = Math.max(0, Math.min(Math.floor(opts.n), eligible.length))
  const chosen = eligible.slice(0, n)
  const rankOf = new Map(eligible.map(([sym], i) => [sym, i + 1]))
  const alloc: Record<string, number> = {}
  const total = chosen.reduce((a, [, sc]) => a + sc.score, 0)
  if (total > 0 && opts.budget > 0) {
    // cap each at CAP of the budget, but never below an equal share (N < 1/CAP)
    const cap = Math.max(opts.cap ?? SCORE_CAP, 1 / chosen.length) * opts.budget
    for (const [sym, sc] of chosen) alloc[sym] = opts.budget * sc.score / total
    for (let i = 0; i < 20; i++) {
      const over = Object.entries(alloc).filter(([, v]) => v > cap + 1e-9)
      if (!over.length) break
      const excess = over.reduce((a, [, v]) => a + (v - cap), 0)
      for (const [sym] of over) alloc[sym] = cap
      const free = Object.entries(alloc).filter(([, v]) => v < cap - 1e-9)
      const fs = free.reduce((a, [, v]) => a + v, 0)
      if (fs <= 0) break
      for (const [sym, v] of free) alloc[sym] = v + excess * v / fs
    }
  }
  // whole numbers adding up to the (rounded) budget
  const whole = Object.keys(alloc).length ? wholeWeights(alloc, Math.round(opts.budget)) : {}
  for (const [sym, sc] of scored) {
    out[sym] = { ...sc, rank: rankOf.get(sym) ?? null, inTopN: sym in alloc, value: whole[sym] ?? 0 }
  }
  return out
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

/** [presets with Profit% > 0, presets with any trade] in a window. */
function counts(stats: RangeStats | undefined): [number, number] {
  const vals = Object.values(stats?.presets ?? {})
  return [vals.filter(([pct]) => pct > 0).length, vals.length]
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
    symbols[sym] = { w, lock, p7, p14, c7: counts(s.ranges['7d']), c14: counts(s.ranges['14d']),
                     policies: { static: w, tilt: w, brake: brakeWeight(w, p14) } }
  }
  // tracked tilt = the score allocation over every eligible symbol (the widget lets the
  // user narrow N; the daily record keeps one comparable definition)
  const disabled = new Set(Object.keys(readSymbolState(mode).disabled ?? {}))
  const alloc = scoreAllocation(symbols, { disabled, n: Infinity, budget: weightBudget(cfg) })
  for (const [sym, r] of Object.entries(symbols)) r.policies.tilt = alloc[sym].value
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
      // Tilt is an absolute allocation now (computed from evidence, not the current
      // weight), so like a typed value it is a new baseline; only a brake anchors.
      if (e.policy !== 'brake') anchor = null
      else if (!anchor) anchor = { base: e.old, since: e.ts, policy: e.policy }
    }
    const last = es[es.length - 1]
    // someone changed the weight since (Risk table, SSH): that is the new baseline
    if (anchor && Math.abs((currentWeights[sym] ?? 0) - last.new) < 1e-9) out[sym] = anchor
  }
  return out
}

/** This formula's reconstructed history for each N (scripts/backfill_weight_shadow.py
 *  --write): forward results of the top-N score allocation vs equal weights and vs the
 *  current weights, over the next 7 days. */
export interface ScoreHistEntry { days: number; mean: number; vs_equal: number; vs_current: number; better_equal: number }
export type ScoreHistory = Record<string, { top?: ScoreHistEntry; r1?: ScoreHistEntry }>

export function readScoreHistory(mode: Mode): ScoreHistory | null {
  try {
    return JSON.parse(fs.readFileSync(path.join(BOT_ROOT, 'data', `weight_shadow_${mode}_score_history.json`), 'utf8'))
  } catch { return null }
}

const TILT_THIN_7D = 5

export function panelSuggestions(
  row: ShadowSymbol,
  opts: {
    disabled: boolean; alloc: AllocationRow; n: number; eligible: number; budget: number
    history?: TrackRecord; scoreHist?: { top?: ScoreHistEntry; r1?: ScoreHistEntry }; anchor?: Anchor
  },
): PanelSuggestions {
  const [pct, n] = row.p14
  const common: string[] = []
  if (opts.disabled) common.push('disabled in this mode — the weight has no effect until the symbol is enabled in Settings')
  if (row.lock && n === 0) common.push(`the locked preset ${row.lock} has no trades in 14 days`)

  // Tilt = score allocation (independent of the current weight: cannot compound).
  // Defaults keep an older/partial row renderable.
  const a = {
    ...opts.alloc,
    base: opts.alloc.base ?? opts.alloc.score,
    breadth: opts.alloc.breadth ?? 1,
    momentum: opts.alloc.momentum ?? false,
  }
  const tilt = a.value
  const tf: string[] = [...common]
  const why =
    opts.disabled ? 'disabled' :
    a.score <= 0 ? `score ${a.score.toFixed(1)} is not positive` :
    !a.inTopN ? `rank ${a.rank} of ${opts.eligible} — outside the top ${opts.n}` : ''
  if (tilt > 0 && row.w === 0) tf.push('turns REAL orders ON for this symbol')
  if (tilt === 0 && row.w > 0) tf.push(`turns REAL orders OFF for this symbol (${why})`)
  if (tilt > 0 && row.p7[1] < TILT_THIN_7D && row.p14[1] < TILT_MIN_TRADES) {
    tf.push(`only ${row.p7[1]} / ${row.p14[1]} trades in 7 / 14 days — the score is mostly shrinkage`)
  }
  const [prof7, tot7] = row.c7 ?? [0, 0]
  if (a.base > 0 && a.breadth < 1) {
    tf.push(`bad sign: only ${prof7} of ${tot7} presets profitable in 7 days — score cut to ×${a.breadth.toFixed(2)}`)
  }
  const h = opts.scoreHist?.top
  if (tilt !== row.w && h && h.days >= 20 && h.vs_equal <= 0) {
    tf.push(`history: this allocation (top ${opts.n}) made ${h.vs_equal >= 0 ? '+' : ''}${h.vs_equal.toFixed(2)} %/week vs equal weights over ${h.days} days — it lost to simply equal-weighting`)
  }
  const parts = [`½ × 7d ${a.s7 >= 0 ? '+' : ''}${a.s7.toFixed(1)} + ½ × 14d ${a.s14 >= 0 ? '+' : ''}${a.s14.toFixed(1)} (Profit% shrunk by trade count) = ${a.base.toFixed(1)}`]
  if (a.base > 0 && a.breadth < 1) parts.push(`× ${a.breadth.toFixed(2)} (few profitable presets in 7 days)`)
  if (a.base > 0 && a.momentum) parts.push(`× ${MOMENTUM_BONUS} (7d above half of 14d — recent profit)`)
  const tnote = `score ${a.score.toFixed(1)}: ${parts.join(' ')}` +
    (a.inTopN ? ` · rank ${a.rank} of ${opts.eligible} → ${fmtNum(tilt)} of budget ${fmtNum(opts.budget)}` : why ? ` · ${why} → 0` : '')

  // Brake: halves; anchored so applying again does not compound (3 -> 1.5 -> 0.75 ...)
  const anchor = opts.anchor?.policy === 'brake' ? opts.anchor : undefined
  const baseW = anchor ? anchor.base : row.w
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
  if (anchor && brake === row.w && pct !== null && pct <= BRAKE_LOSS_PCT) {
    bnote = `already braked on ${new Date(anchor.since).toISOString().slice(0, 10)} (${fmtNum(anchor.base)} → ${fmtNum(row.w)}); it will not halve again while the same loss window is in force`
  }
  // doubts only matter when the value would change something; otherwise the note explains
  return {
    tilt: { value: tilt, flags: tilt !== row.w ? tf : [], note: tnote },
    brake: { value: brake, flags: brake !== row.w ? bf : [], note: bnote },
  }
}
