import { NextResponse } from 'next/server'
import { randomUUID } from 'crypto'
import { BOT_ROOT } from '../_utils'
import path from 'path'
import fs from 'fs'

const MODE_PATH = path.join(BOT_ROOT, 'data', 'bot_mode.json')
const COMMAND_PATH = path.join(BOT_ROOT, 'data', 'bot_command.json')
const RESULT_PATH = path.join(BOT_ROOT, 'data', 'bot_command_result.json')

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
