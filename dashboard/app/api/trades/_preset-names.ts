// The presets the Trades page's Preset Efficiency table lists for a symbol.
//
// Shared by /api/trades (the table) and /api/trades/symbol-scores (the picker sort),
// because the sort key is "the table's top row" — it must choose among the same rows.

import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../_utils'

function readJson<T>(filePath: string, fallback: T): T {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')) as T } catch { return fallback }
}

export function currentMode(): string {
  return readJson<{ mode?: string }>(path.join(BOT_ROOT, 'data', 'bot_mode.json'), {}).mode ?? 'test'
}

/** Backtest preset names ∪ efficiency keys, in that order. The shadow instance's
 *  backtest lives under a mode-suffixed name — see bot/instance_paths.py. */
export function presetNamesFor(
  symbol: string, mode: string, symbolEfficiency: Record<string, unknown>,
): string[] {
  const isShadow = mode !== currentMode()
  const backtestPath = path.join(BOT_ROOT, 'dashboard', 'public',
    isShadow ? `backtest_results_${symbol}_${mode}.json` : `backtest_results_${symbol}.json`)
  const backtest = readJson<{ presets?: Record<string, unknown> } | null>(backtestPath, null)
  return backtest?.presets
    ? Array.from(new Set([...Object.keys(backtest.presets), ...Object.keys(symbolEfficiency)]))
    : Object.keys(symbolEfficiency)
}
