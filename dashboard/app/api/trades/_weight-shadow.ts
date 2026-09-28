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

export function evaluateShadow(mode: Mode, days = 7): TrackRecord {
  let lines: string[] = []
  try { lines = fs.readFileSync(shadowPath(mode), 'utf8').split('\n').filter(Boolean) } catch { /* none yet */ }
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
      const ws = Object.entries(snap.symbols).map(([s, v]) => [s, Number(v.policies[pol]) || 0] as const)
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
