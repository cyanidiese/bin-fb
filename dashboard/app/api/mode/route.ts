import { NextResponse } from 'next/server'
import { BOT_ROOT } from '../_utils'
import path from 'path'
import fs from 'fs'

const MODE_PATH = path.join(BOT_ROOT, 'data', 'bot_mode.json')

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
  writeModeFile(target_mode)
  return NextResponse.json({ ok: true, via: 'direct' })
}
