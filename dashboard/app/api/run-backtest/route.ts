import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'
import fs from 'fs'

const BOT_ROOT = path.resolve(process.cwd(), '..')
const MODE_PATH = path.join(BOT_ROOT, 'data', 'bot_mode.json')

function getPython(): string {
  const venvPy = path.join(BOT_ROOT, '.venv', 'bin', 'python3')
  return fs.existsSync(venvPy) ? venvPy : 'python3'
}

function readCurrentMode(): string {
  try {
    return JSON.parse(fs.readFileSync(MODE_PATH, 'utf8')).mode ?? 'test'
  } catch {
    return 'test'
  }
}

export async function POST(req: NextRequest) {
  let klinesCount = 1500
  let symbol = ''
  try {
    const body = await req.json()
    if (typeof body.klines_count === 'number' && body.klines_count > 0) {
      klinesCount = body.klines_count
    }
    if (typeof body.symbol === 'string' && body.symbol.trim()) {
      symbol = body.symbol.trim().toUpperCase()
    }
  } catch {
    // use defaults
  }

  const python = getPython()
  const mode = readCurrentMode()
  const args = [
    'backtest.py',
    '--klines-count', String(klinesCount),
    '--mode', mode,
    ...(symbol ? ['--symbols', symbol] : []),
  ]

  // Spawn and return immediately — the frontend polls the results JSON file.
  // Waiting for backtest.py to finish (can take 30 s+) would block the browser.
  return new Promise<NextResponse>(resolve => {
    const child = spawn(python, args, { cwd: BOT_ROOT, detached: true, stdio: 'ignore' })
    child.on('error', err => {
      resolve(NextResponse.json({ error: `Failed to start backtest: ${err.message}` }, { status: 500 }))
    })
    child.on('spawn', () => {
      child.unref()
      resolve(NextResponse.json({ ok: true, klines_count: klinesCount, symbol, pid: child.pid }))
    })
  })
}
