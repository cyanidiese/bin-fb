import { NextResponse } from 'next/server'
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

function sigterm(): boolean {
  if (!fs.existsSync(PID_PATH)) return false
  try {
    const { pid } = JSON.parse(fs.readFileSync(PID_PATH, 'utf8'))
    if (pid && isAlive(pid)) {
      process.kill(pid, 'SIGTERM')
      return true
    }
  } catch {}
  return false
}

export async function POST() {
  const id = crypto.randomUUID()
  writeCommand(id)
  const ok = await waitForResult(id)
  if (!ok) {
    const killed = sigterm()
    if (!killed) return NextResponse.json({ ok: false, error: 'Bot not responding and no PID found' }, { status: 500 })
  }
  return NextResponse.json({ ok: true })
}
