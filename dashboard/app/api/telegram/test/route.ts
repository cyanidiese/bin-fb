import { NextResponse } from 'next/server'
import { randomUUID } from 'crypto'
import { BOT_ROOT } from '../../_utils'
import path from 'path'
import fs from 'fs'

const COMMAND_PATH = path.join(BOT_ROOT, 'data', 'bot_command.json')
const RESULT_PATH = path.join(BOT_ROOT, 'data', 'bot_command_result.json')

export async function POST() {
  const id = randomUUID()
  try {
    const tmp = COMMAND_PATH + '.tmp'
    fs.mkdirSync(path.dirname(COMMAND_PATH), { recursive: true })
    fs.writeFileSync(tmp, JSON.stringify({
      id, type: 'test_telegram', payload: {}, issued_at: new Date().toISOString(),
    }))
    fs.renameSync(tmp, COMMAND_PATH)
  } catch (err) {
    return NextResponse.json({ ok: false, error: `Failed to write command: ${err}` }, { status: 500 })
  }

  const deadline = Date.now() + 15_000
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 500))
    if (!fs.existsSync(RESULT_PATH)) continue
    try {
      const result = JSON.parse(fs.readFileSync(RESULT_PATH, 'utf8'))
      if (result.id === id) return NextResponse.json(result)
    } catch {}
  }
  return NextResponse.json({ ok: false, error: 'Bot not responding (is it running?)' }, { status: 504 })
}
