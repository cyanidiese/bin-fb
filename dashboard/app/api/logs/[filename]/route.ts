import { NextRequest, NextResponse } from 'next/server'
import path from 'path'
import fs from 'fs'
import { BOT_ROOT } from '../../_utils'

const LOGS_DIR = path.join(BOT_ROOT, 'logs')

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ filename: string }> }
) {
  const { filename } = await params

  // Safety: only allow .log files, no path traversal
  if (!filename.endsWith('.log') || filename.includes('/') || filename.includes('..')) {
    return NextResponse.json({ error: 'Invalid filename' }, { status: 400 })
  }

  const filePath = path.join(LOGS_DIR, filename)
  if (!fs.existsSync(filePath)) {
    return NextResponse.json({ error: 'Not found' }, { status: 404 })
  }

  const content = fs.readFileSync(filePath)
  return new NextResponse(content, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Content-Disposition': `attachment; filename="${filename}"`,
    },
  })
}
