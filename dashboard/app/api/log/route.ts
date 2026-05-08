import { NextResponse } from 'next/server'
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
