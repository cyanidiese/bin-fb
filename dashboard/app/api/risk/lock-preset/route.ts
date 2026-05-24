import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const BOT_ROOT = path.resolve(process.cwd(), '..')
const CONFIG_PATH = path.join(BOT_ROOT, 'risk_config.json')

/** POST { symbol: string, preset: string | null }
 *  preset=null  → unlock (remove entry)
 *  preset=name  → lock symbol to that preset
 */
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

  const locked: Record<string, string> = { ...(config.locked_presets as Record<string, string> ?? {}) }
  if (preset) {
    locked[symbol] = preset
  } else {
    delete locked[symbol]
  }
  config.locked_presets = locked

  try {
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2))
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
  return NextResponse.json({ ok: true, locked_presets: locked })
}
