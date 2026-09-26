import { NextRequest, NextResponse } from 'next/server'
import { lockedPresetsFor, withLockedPresets } from '../../_locked-presets'
import { modeOr, updateRiskConfig } from '../../_risk-config'

/** POST { symbol: string, preset: string | null }
 *  preset=null  → unlock (remove entry)
 *  preset=name  → lock symbol to that preset
 */
export async function POST(req: NextRequest) {
  let body: { symbol: string; preset: string | null; mode?: string }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }
  const { symbol, preset, mode: requestedMode } = body
  if (!symbol) return NextResponse.json({ error: 'symbol required' }, { status: 400 })

  // Lock into the instance the caller is LOOKING AT, not whichever mode the bot
  // happens to run. The Trades page reads locks for the viewed instance, so writing to
  // the bot's mode meant clicking the padlock in the Shadow view silently locked the
  // preset for test — the wrong instance — and the icon never updated because the page
  // re-read live. Falls back to the bot's mode when no instance is specified.
  // The locks live in that mode's own file, risk_config_{mode}.json (per-mode config).
  const mode = modeOr(requestedMode)
  let locked: Record<string, string> = {}
  try {
    updateRiskConfig(mode, config => {
      locked = lockedPresetsFor(config, mode)
      if (preset) locked[symbol] = preset
      else delete locked[symbol]
      return withLockedPresets(config, mode, locked)
    })
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
  return NextResponse.json({ ok: true, mode, locked_presets: locked })
}
