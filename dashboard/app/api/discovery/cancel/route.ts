import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT, isAlive } from '../../_utils'

const STATE_PATH = path.join(BOT_ROOT, 'dashboard', 'public', 'discovery_state.json')

function readState(): Record<string, unknown> {
  try {
    return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'))
  } catch {
    return { status: 'idle', pid: null }
  }
}

export async function POST() {
  const state = readState()

  if (state.status !== 'running') {
    return NextResponse.json({ error: 'No active discovery run' }, { status: 409 })
  }

  const pid = state.pid as number | null
  if (!isAlive(pid)) {
    return NextResponse.json({ error: 'Process is not alive' }, { status: 409 })
  }

  try {
    process.kill(pid!, 'SIGTERM')
  } catch (e) {
    return NextResponse.json({ error: `Failed to send SIGTERM: ${e}` }, { status: 500 })
  }

  return NextResponse.json({ ok: true })
}
