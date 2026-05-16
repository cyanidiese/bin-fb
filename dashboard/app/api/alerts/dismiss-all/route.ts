import { NextResponse } from 'next/server'
import { BOT_ROOT } from '../../_utils'
import path from 'path'
import fs from 'fs'

const ALERT_PATH = path.join(BOT_ROOT, 'dashboard', 'public', 'alert_state.json')

export async function POST() {
  let state: { alerts: { id: string }[]; dismissed_ids: string[] } = { alerts: [], dismissed_ids: [] }
  if (fs.existsSync(ALERT_PATH)) {
    try { state = JSON.parse(fs.readFileSync(ALERT_PATH, 'utf8')) } catch {}
  }

  const allIds = state.alerts.map(a => a.id)
  const merged = Array.from(new Set([...state.dismissed_ids, ...allIds]))
  state.dismissed_ids = merged

  const tmp = ALERT_PATH + '.tmp'
  fs.mkdirSync(path.dirname(ALERT_PATH), { recursive: true })
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2))
  fs.renameSync(tmp, ALERT_PATH)

  return NextResponse.json({ ok: true, dismissed: allIds.length })
}
