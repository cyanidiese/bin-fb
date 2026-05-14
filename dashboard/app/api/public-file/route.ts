import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const PUBLIC_DIR = path.resolve(process.cwd(), 'public')

export async function GET(req: NextRequest) {
  const file = req.nextUrl.searchParams.get('f')
  if (!file) return new NextResponse('Missing f param', { status: 400 })

  const resolved = path.resolve(PUBLIC_DIR, file)
  if (!resolved.startsWith(PUBLIC_DIR + path.sep) && resolved !== PUBLIC_DIR) {
    return new NextResponse('Forbidden', { status: 403 })
  }

  try {
    const content = fs.readFileSync(resolved)
    return new NextResponse(content, {
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    })
  } catch {
    return new NextResponse('Not found', { status: 404 })
  }
}
