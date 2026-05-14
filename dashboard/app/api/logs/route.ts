import { NextResponse } from 'next/server'
import path from 'path'
import fs from 'fs'
import { BOT_ROOT } from '../_utils'

const LOGS_DIR = path.join(BOT_ROOT, 'logs')

export async function GET() {
  try {
    const files = fs.readdirSync(LOGS_DIR)
      .filter(f => f.endsWith('.log'))
      .map(f => {
        const stat = fs.statSync(path.join(LOGS_DIR, f))
        return { name: f, size: stat.size, modified: stat.mtime.toISOString() }
      })
      .sort((a, b) => b.modified.localeCompare(a.modified))
    return NextResponse.json({ files })
  } catch {
    return NextResponse.json({ files: [] })
  }
}
