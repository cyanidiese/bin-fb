import { NextRequest, NextResponse } from 'next/server'
import { modeOr, readRiskConfig } from '../_risk-config'
import {
  anchors, buildSnapshot, weightBudget, evaluateShadow, panelSuggestions, readScoreHistory, scoreAllocation,
} from '../trades/_weight-shadow'
import { readSymbolState } from '../symbols/_registry'

/** GET /api/weight-suggestions?mode=test|live&n=N — every symbol's weight with what the
 *  Tilt (score allocation over the top N symbols; the rest 0) and Brake would set, the
 *  reasons to doubt each value, and the track records. Nothing is applied here.
 *  n omitted = every eligible (enabled, positive-score) symbol.
 *  Spec: docs/specs/2026-09-28-weight-shadow-calculator.md */
export async function GET(req: NextRequest) {
  const q = new URL(req.url).searchParams
  const mode = modeOr(q.get('mode'))
  const snap = buildSnapshot(mode)
  const cfg = readRiskConfig(mode)
  const symbols = snap?.symbols ?? {}
  const disabled = Object.keys(readSymbolState(mode).disabled ?? {})
  const disabledSet = new Set(disabled)
  const budget = weightBudget(cfg)

  // Eligible = enabled with a positive score; N is clamped to 1..eligible.
  const all = scoreAllocation(symbols, { disabled: disabledSet, n: Infinity, budget })
  const eligible = Object.values(all).filter(a => a.rank !== null).length
  const nParam = Number(q.get('n'))
  const n = Number.isFinite(nParam) && nParam > 0 ? Math.min(Math.floor(nParam), eligible) : eligible
  const alloc = scoreAllocation(symbols, { disabled: disabledSet, n, budget })

  const history = evaluateShadow(mode, 7, 'history')
  const scoreHistAll = readScoreHistory(mode)
  const scoreHist = scoreHistAll?.[String(n)] ?? undefined
  const current = Object.fromEntries(Object.entries(symbols).map(([s, r]) => [s, r.w]))
  const anchored = anchors(mode, current)
  const suggestions = Object.fromEntries(Object.entries(symbols).map(([sym, row]) => [
    sym, panelSuggestions(row, {
      disabled: disabledSet.has(sym), alloc: alloc[sym], n, eligible, budget,
      history, scoreHist, anchor: anchored[sym],
    }),
  ]))
  return NextResponse.json({
    mode,
    day: snap?.day ?? null,
    tats_min_weight: Number(cfg.tats_min_weight ?? 0),
    budget, n, eligible,
    symbols,
    allocation: alloc,
    suggestions,
    disabled,
    track: evaluateShadow(mode),
    history,
    score_history: scoreHist ?? null,
  })
}
