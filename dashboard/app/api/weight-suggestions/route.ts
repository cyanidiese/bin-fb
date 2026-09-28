import { NextRequest, NextResponse } from 'next/server'
import { modeOr, readRiskConfig } from '../_risk-config'
import { anchors, buildSnapshot, evaluateShadow, panelSuggestions } from '../trades/_weight-shadow'
import { readSymbolState } from '../symbols/_registry'

/** GET /api/weight-suggestions?mode=test|live — current weights with what each policy
 *  would set right now, plus the policies' track record so far. Nothing is applied here.
 *  Spec: docs/specs/2026-09-28-weight-shadow-calculator.md */
export async function GET(req: NextRequest) {
  const mode = modeOr(new URL(req.url).searchParams.get('mode'))
  const snap = buildSnapshot(mode)
  const cfg = readRiskConfig(mode)
  const disabled = Object.keys(readSymbolState(mode).disabled ?? {})
  const history = evaluateShadow(mode, 7, 'history')
  const tmw = Number(cfg.tats_min_weight ?? 0)
  const current = Object.fromEntries(Object.entries(snap?.symbols ?? {}).map(([s, r]) => [s, r.w]))
  const anchored = anchors(mode, current)
  const suggestions = Object.fromEntries(Object.entries(snap?.symbols ?? {}).map(([sym, row]) => [
    sym, panelSuggestions(row, {
      disabled: disabled.includes(sym), baseWeight: tmw > 0 ? tmw : 1, history, anchor: anchored[sym],
    }),
  ]))
  return NextResponse.json({
    mode,
    day: snap?.day ?? null,
    tats_min_weight: tmw,
    symbols: snap?.symbols ?? {},
    suggestions,
    disabled,
    track: evaluateShadow(mode),
    history,
  })
}
