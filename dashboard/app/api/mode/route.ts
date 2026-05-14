import { NextResponse } from 'next/server'
import { randomUUID } from 'crypto'
import { BOT_ROOT, isAlive } from '../_utils'
import path from 'path'
import fs from 'fs'

const MODE_PATH = path.join(BOT_ROOT, 'data', 'bot_mode.json')
const COMMAND_PATH = path.join(BOT_ROOT, 'data', 'bot_command.json')
const RESULT_PATH = path.join(BOT_ROOT, 'data', 'bot_command_result.json')
const BOT_STATE_PATH = path.join(BOT_ROOT, 'dashboard', 'public', 'bot_state.json')

function isBotAlive(): boolean {
  try {
    const state = JSON.parse(fs.readFileSync(BOT_STATE_PATH, 'utf8'))
    if (!state.running || !state.pid) return false
    // Also check heartbeat freshness (30s)
    if (state.last_heartbeat) {
      const age = Date.now() - new Date(state.last_heartbeat).getTime()
      if (age > 30_000) return false
    }
    return isAlive(state.pid)
  } catch {
    return false
  }
}

function writeModeFile(mode: string): void {
  fs.mkdirSync(path.dirname(MODE_PATH), { recursive: true })
  const tmp = MODE_PATH + '.tmp'
  fs.writeFileSync(tmp, JSON.stringify({
    mode,
    switched_at: new Date().toISOString(),
  }))
  fs.renameSync(tmp, MODE_PATH)
}

export async function GET() {
  try {
    const data = fs.existsSync(MODE_PATH)
      ? JSON.parse(fs.readFileSync(MODE_PATH, 'utf8'))
      : { mode: 'test' }
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ mode: 'test' })
  }
}

export async function POST(req: Request) {
  const { target_mode } = await req.json()
  if (!['test', 'live'].includes(target_mode)) {
    return NextResponse.json({ ok: false, error: 'Invalid mode' }, { status: 400 })
  }

  // If the bot is not running, write the mode file directly — no bot coordination needed.
  if (!isBotAlive()) {
    writeModeFile(target_mode)
    return NextResponse.json({ ok: true, via: 'direct' })
  }

  // Bot is running — send a command and wait for it to acknowledge.
  const id = randomUUID()
  const tmp = COMMAND_PATH + '.tmp'
  fs.mkdirSync(path.dirname(COMMAND_PATH), { recursive: true })
  fs.writeFileSync(tmp, JSON.stringify({
    id, type: 'switch_mode',
    payload: { target_mode },
    issued_at: new Date().toISOString(),
  }))
  fs.renameSync(tmp, COMMAND_PATH)

  // Poll for result (60s timeout)
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 1000))
    if (!fs.existsSync(RESULT_PATH)) continue
    try {
      const result = JSON.parse(fs.readFileSync(RESULT_PATH, 'utf8'))
      if (result.id === id) {
        return NextResponse.json(result)
      }
    } catch {}
  }
  return NextResponse.json({ ok: false, error: 'Timeout waiting for mode switch' }, { status: 504 })
}
