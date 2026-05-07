import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT, readRegistry, writeRegistry, isAlive } from './_registry'

function getPython(): string {
  const venvPy = path.join(BOT_ROOT, '.venv', 'bin', 'python3')
  return fs.existsSync(venvPy) ? venvPy : 'python3'
}

/** GET /api/symbols — return registry, reconciling any stale "running" statuses. */
export async function GET() {
  const reg = readRegistry()

  let dirty = false
  for (const [sym, st] of Object.entries(reg.status)) {
    if (st.backtest === 'running' && !isAlive(st.pid)) {
      // Process died without updating the file (server restart, crash, etc.)
      reg.status[sym] = { backtest: 'error', pid: null }
      dirty = true
    }
  }
  if (dirty) writeRegistry(reg)

  return NextResponse.json(reg)
}

/** POST /api/symbols — add a symbol and immediately start a backtest for it. */
export async function POST(req: NextRequest) {
  let symbol = ''
  let klinesCount = 1500
  try {
    const body = await req.json()
    symbol = (body.symbol ?? '').toString().trim().toUpperCase()
    if (typeof body.klines_count === 'number' && body.klines_count > 0) {
      klinesCount = body.klines_count
    }
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
  }

  if (!symbol) {
    return NextResponse.json({ error: 'symbol is required' }, { status: 400 })
  }
  if (!/^[A-Z0-9]{2,10}USDT$/.test(symbol)) {
    return NextResponse.json(
      { error: `"${symbol}" does not look like a valid USD-M futures symbol (e.g. ETHUSDT)` },
      { status: 400 },
    )
  }

  const reg = readRegistry()
  if (reg.symbols.includes(symbol)) {
    return NextResponse.json({ error: `${symbol} is already active` }, { status: 409 })
  }

  reg.symbols.push(symbol)
  reg.status[symbol] = { backtest: 'running', pid: null }
  writeRegistry(reg)

  // Spawn backtest subprocess in the background — do NOT await it.
  const python = getPython()
  const child = spawn(
    python,
    ['backtest.py', '--symbols', symbol, '--klines-count', String(klinesCount)],
    { cwd: BOT_ROOT, detached: false, stdio: 'ignore' },
  )

  // Store PID immediately so DELETE can kill the process if needed.
  if (child.pid) {
    const reg2 = readRegistry()
    if (reg2.symbols.includes(symbol)) {
      reg2.status[symbol] = { backtest: 'running', pid: child.pid }
      writeRegistry(reg2)
    }
  }

  // Update status when the process finishes.
  child.on('close', (code: number | null) => {
    const current = readRegistry()
    if (!current.symbols.includes(symbol)) return // was removed while running
    if (current.status[symbol]?.backtest === 'running') {
      current.status[symbol] = { backtest: code === 0 ? 'complete' : 'error', pid: null }
      writeRegistry(current)
    }
  })

  child.unref()

  return NextResponse.json({ ok: true, symbol, status: 'running', pid: child.pid ?? null })
}
