import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT, readRegistry, writeRegistry, isAlive } from '../_registry'

function deleteSymbolFiles(symbol: string): void {
  const publicDir = path.join(BOT_ROOT, 'dashboard', 'public')
  const dataDir = path.join(BOT_ROOT, 'data')

  const candidates = [
    path.join(publicDir, `backtest_results_${symbol}.json`),
    path.join(publicDir, `results_${symbol}.json`),
    path.join(dataDir, `${symbol}_15m_test.json`),
    path.join(dataDir, `${symbol}_15m.json`),
  ]

  // All timestamped backtest runs: data/backtest_{SYMBOL}_*.json
  try {
    const dataFiles = fs.readdirSync(dataDir)
    for (const f of dataFiles) {
      if (f.startsWith(`backtest_${symbol}_`) && f.endsWith('.json')) {
        candidates.push(path.join(dataDir, f))
      }
    }
  } catch { /* data dir missing — skip */ }

  for (const filePath of candidates) {
    try {
      fs.unlinkSync(filePath)
    } catch {
      // File doesn't exist or can't be deleted — not an error.
    }
  }
}

/** DELETE /api/symbols/[symbol] — remove a symbol, kill its backtest if running, and delete its data files. */
export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: raw } = await params
  const symbol = raw.toUpperCase()

  const reg = readRegistry()
  if (!reg.symbols.includes(symbol)) {
    return NextResponse.json({ error: `${symbol} is not active` }, { status: 404 })
  }

  const st = reg.status[symbol]
  if (st?.backtest === 'running' && isAlive(st.pid)) {
    try {
      process.kill(st.pid!, 'SIGTERM')
    } catch {
      // Process already gone — fine.
    }
  }

  reg.symbols = reg.symbols.filter(s => s !== symbol)
  delete reg.status[symbol]
  writeRegistry(reg)

  deleteSymbolFiles(symbol)

  return NextResponse.json({ ok: true, symbol })
}
