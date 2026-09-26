// Backtest results are keyed by MARKET (mode): public/backtest_results_{SYM}_{mode}.json.
// Spec: docs/specs/2026-09-26-mode-switch-restart-and-per-mode-backtests.md — mirrors
// bot/instance_paths.py::backtest_results_path. The legacy unsuffixed file is read as a
// fallback for test only (it was always the testnet primary's) — never across markets.
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from './_utils'
import type { Mode } from './_risk-config'

export const PUBLIC_DIR = path.join(BOT_ROOT, 'dashboard', 'public')

export function backtestResultsName(symbol: string, mode: Mode): string {
  return `backtest_results_${symbol}_${mode}.json`
}

/** The file to READ for a symbol and mode, or null when there is none. */
export function backtestResultsFile(symbol: string, mode: Mode): string | null {
  const own = path.join(PUBLIC_DIR, backtestResultsName(symbol, mode))
  if (fs.existsSync(own)) return own
  if (mode === 'test') {
    const legacy = path.join(PUBLIC_DIR, `backtest_results_${symbol}.json`)
    if (fs.existsSync(legacy)) return legacy
  }
  return null
}

export function readBacktestResults<T = Record<string, unknown>>(symbol: string, mode: Mode): T | null {
  const f = backtestResultsFile(symbol, mode)
  if (!f) return null
  try { return JSON.parse(fs.readFileSync(f, 'utf8')) as T } catch { return null }
}

/** Symbols that have results for `mode` (own file, or the legacy file for test). */
export function symbolsWithBacktest(mode: Mode): string[] {
  let files: string[] = []
  try { files = fs.readdirSync(PUBLIC_DIR) } catch { return [] }
  const out = new Set<string>()
  const own = new RegExp(`^backtest_results_([A-Z0-9]+)_${mode}\\.json$`)
  const legacy = /^backtest_results_([A-Z0-9]+)\.json$/
  for (const f of files) {
    const m = f.match(own) ?? (mode === 'test' ? f.match(legacy) : null)
    if (m) out.add(m[1])
  }
  return [...out].sort()
}
