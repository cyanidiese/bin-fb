import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../_utils'

function readJson(filePath: string, fallback: unknown) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return fallback
  }
}

function currentMode(): string {
  const modePath = path.join(BOT_ROOT, 'data', 'bot_mode.json')
  const data = readJson(modePath, {})
  return (data as Record<string, string>).mode ?? 'test'
}

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const mode = searchParams.get('mode') ?? currentMode()
  const limit = Math.max(1, parseInt(searchParams.get('limit') ?? '500', 10))

  const filePath = path.join(BOT_ROOT, 'data', `balance_history_${mode}.json`)
  const entries = readJson(filePath, []) as unknown[]

  const recent = entries.slice(-limit).reverse()
  return NextResponse.json({ entries: recent })
}
