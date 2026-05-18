import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT, readRegistry, writeRegistry, isAlive } from './_registry'

const CONFIG_PATH = path.join(BOT_ROOT, 'risk_config.json')

function addToSymbolWeights(symbol: string): void {
  try {
    const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'))
    if (!cfg.symbol_weights) cfg.symbol_weights = {}
    if (!(symbol in cfg.symbol_weights)) {
      cfg.symbol_weights[symbol] = 1
      fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2))
    }
  } catch { /* config not yet created — bot will add it on first write */ }
}

const MODE_PATH = path.join(BOT_ROOT, 'data', 'bot_mode.json')

function readCurrentMode(): string {
  try {
    return JSON.parse(fs.readFileSync(MODE_PATH, 'utf8')).mode ?? 'test'
  } catch {
    return 'test'
  }
}

function getPython(): string {
  const venvPy = path.join(BOT_ROOT, '.venv', 'bin', 'python3')
  return fs.existsSync(venvPy) ? venvPy : 'python3'
}

const PUBLIC_DIR = path.join(BOT_ROOT, 'dashboard', 'public')

/**
 * Writes a minimal results_{symbol}.json so the Strategy page renders
 * immediately after a symbol is added, without waiting for the bot to run.
 * Tries to fetch the current price from Binance; falls back to 0.
 */
async function writePlaceholderResults(symbol: string): Promise<void> {
  // Inherit mode + timeframe from an existing results file if one is present.
  let mode = 'test'
  let timeframe = '15m'
  try {
    const existing = fs.readdirSync(PUBLIC_DIR).find(f => f.startsWith('results_') && f.endsWith('.json') && !f.includes(symbol))
    if (existing) {
      const d = JSON.parse(fs.readFileSync(path.join(PUBLIC_DIR, existing), 'utf8'))
      if (d.mode) mode = d.mode
      if (d.timeframe) timeframe = d.timeframe
    }
  } catch { /* use defaults */ }

  let currentPrice = 0
  try {
    const r = await fetch(`https://fapi.binance.com/fapi/v1/ticker/price?symbol=${symbol}`, { signal: AbortSignal.timeout(4000) })
    if (r.ok) {
      const d = await r.json() as { price?: string }
      currentPrice = parseFloat(d.price ?? '0') || 0
    }
  } catch { /* use 0 */ }

  const placeholder = {
    symbol,
    timeframe,
    mode,
    generated_at: new Date().toISOString(),
    current_price: currentPrice,
    trend_levels: [],
    all_points: [],
    klines: [],
    signals: [],
  }

  const dest = path.join(PUBLIC_DIR, `results_${symbol}.json`)
  // Don't overwrite a real results file if somehow one already exists.
  if (!fs.existsSync(dest)) {
    fs.writeFileSync(dest, JSON.stringify(placeholder, null, 2))
  }
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
  addToSymbolWeights(symbol)

  // Write a placeholder results file immediately so the Strategy page shows the
  // symbol straight away rather than the "no data yet" message.
  void writePlaceholderResults(symbol)

  // Spawn backtest subprocess in the background — do NOT await it.
  const python = getPython()
  const mode = readCurrentMode()
  const child = spawn(
    python,
    ['backtest.py', '--symbols', symbol, '--klines-count', String(klinesCount), '--mode', mode],
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
