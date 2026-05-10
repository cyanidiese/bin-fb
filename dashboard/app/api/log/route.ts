import { NextRequest, NextResponse } from 'next/server'
import { BOT_ROOT } from '../_utils'
import path from 'path'
import fs from 'fs'

const LOG_PATH = path.join(BOT_ROOT, 'data', 'system_log.json')

export async function GET() {
  if (!fs.existsSync(LOG_PATH)) return NextResponse.json([])
  try {
    const entries = JSON.parse(fs.readFileSync(LOG_PATH, 'utf8'))
    return NextResponse.json(entries.reverse())
  } catch {
    return NextResponse.json([])
  }
}

export async function DELETE(req: NextRequest) {
  const keep = parseInt(req.nextUrl.searchParams.get('keep') ?? '0', 10)
  if (isNaN(keep) || keep < 0) {
    return NextResponse.json({ error: 'keep must be a non-negative integer' }, { status: 400 })
  }
  try {
    const existing: unknown[] = fs.existsSync(LOG_PATH)
      ? JSON.parse(fs.readFileSync(LOG_PATH, 'utf8'))
      : []
    const trimmed = keep === 0 ? [] : existing.slice(-keep)
    const tmp = LOG_PATH + '.tmp'
    fs.writeFileSync(tmp, JSON.stringify(trimmed, null, 2))
    fs.renameSync(tmp, LOG_PATH)
    return NextResponse.json({ kept: trimmed.length })
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
}
