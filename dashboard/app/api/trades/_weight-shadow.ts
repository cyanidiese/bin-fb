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
