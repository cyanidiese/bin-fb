import { NextResponse } from 'next/server'
import { randomUUID } from 'crypto'
import { BOT_ROOT, isAlive } from '../../_utils'
import path from 'path'
import fs from 'fs'

const COMMAND_PATH = path.join(BOT_ROOT, 'data', 'bot_command.json')
const RESULT_PATH = path.join(BOT_ROOT, 'data', 'bot_command_result.json')
const PID_PATH = path.join(BOT_ROOT, 'data', 'bot_pid.json')
const COMMAND_TIMEOUT_MS = 10_000

function writeCommand(id: string) {
  const tmp = COMMAND_PATH + '.tmp'
  fs.writeFileSync(tmp, JSON.stringify({ id, type: 'stop_bot', payload: {}, issued_at: new Date().toISOString() }))
  fs.renameSync(tmp, COMMAND_PATH)
}

async function waitForResult(id: string): Promise<boolean> {
  const deadline = Date.now() + COMMAND_TIMEOUT_MS
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 500))
    if (!fs.existsSync(RESULT_PATH)) continue
    try {
      const result = JSON.parse(fs.readFileSync(RESULT_PATH, 'utf8'))
      if (result.id === id && result.ok) return true
    } catch {}
  }
  return false
}

/** Try to SIGTERM the bot. Returns 'killed' | 'already_stopped' | 'no_pid'. */
function sigterm(): 'killed' | 'already_stopped' | 'no_pid' {
  if (!fs.existsSync(PID_PATH)) return 'no_pid'
  try {
    const { pid } = JSON.parse(fs.readFileSync(PID_PATH, 'utf8'))
    if (!pid) return 'no_pid'
    if (!isAlive(pid)) return 'already_stopped'
    process.kill(pid, 'SIGTERM')
    return 'killed'
  } catch {
    return 'no_pid'
  }
}

export async function POST() {
  const id = randomUUID()
  try {
    writeCommand(id)
  } catch (err) {
    return NextResponse.json({ ok: false, error: `Failed to write command: ${err}` }, { status: 500 })
  }
  const responded = await waitForResult(id)
  if (responded) return NextResponse.json({ ok: true })

  // Bot didn't respond in time — fall back to SIGTERM
  const result = sigterm()
  if (result === 'killed') return NextResponse.json({ ok: true, note: 'Bot killed with SIGTERM (did not respond to stop command in time)' })
  if (result === 'already_stopped') return NextResponse.json({ ok: true, note: 'Bot was already stopped' })
  // No PID at all — treat as already stopped (goal achieved)
  return NextResponse.json({ ok: true, note: 'Bot was not running' })
}
