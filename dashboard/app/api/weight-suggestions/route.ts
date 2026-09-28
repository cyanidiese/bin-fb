import { NextRequest, NextResponse } from 'next/server'
import { modeOr, readRiskConfig } from '../_risk-config'
import { buildSnapshot, evaluateShadow } from '../trades/_weight-shadow'

/** GET /api/weight-suggestions?mode=test|live — current weights with what each policy
 *  would set right now, plus the policies' track record so far. Nothing is applied here.
 *  Spec: docs/specs/2026-09-28-weight-shadow-calculator.md */
export async function GET(req: NextRequest) {
  const mode = modeOr(new URL(req.url).searchParams.get('mode'))
  const snap = buildSnapshot(mode)
  const cfg = readRiskConfig(mode)
  return NextResponse.json({
    mode,
    day: snap?.day ?? null,
    tats_min_weight: Number(cfg.tats_min_weight ?? 0),
    symbols: snap?.symbols ?? {},
    track: evaluateShadow(mode),
    history: evaluateShadow(mode, 7, 'history'),
  })
}
