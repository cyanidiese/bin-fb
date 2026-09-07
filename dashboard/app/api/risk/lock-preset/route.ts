import { NextRequest, NextResponse } from 'next/server'
import { lockedPresetsFor, withLockedPresets } from '../../_locked-presets'
import fs from 'fs'
import path from 'path'

const BOT_ROOT = path.resolve(process.cwd(), '..')
const CONFIG_PATH = path.join(BOT_ROOT, 'risk_config.json')

/** POST { symbol: string, preset: string | null }
 *  preset=null  → unlock (remove entry)
 *  preset=name  → lock symbol to that preset
 */
function currentMode(): string {
  try {
    const p = CONFIG_PATH.replace(/risk_config\.json$/, 'data/bot_mode.json')
    return JSON.parse(fs.readFileSync(p, 'utf8')).mode ?? 'test'
  } catch { return 'test' }
}

export async function POST(req: NextRequest) {
  let body: { symbol: string; preset: string | null }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 })
  }
  const { symbol, preset } = body
  if (!symbol) return NextResponse.json({ error: 'symbol required' }, { status: 400 })

  let config: Record<string, unknown> = {}
  try { config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8')) } catch {}

  // Lock into the mode the bot is actually running. Writing a flat dict here would
  // apply the lock to both instances, including the live-market mirror.
  const mode = currentMode()
  const locked = lockedPresetsFor(config, mode)
  if (preset) {
    locked[symbol] = preset
  } else {
    delete locked[symbol]
  }
  config = withLockedPresets(config, mode, locked)

  try {
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2))
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
  return NextResponse.json({ ok: true, mode, locked_presets: locked })
}
