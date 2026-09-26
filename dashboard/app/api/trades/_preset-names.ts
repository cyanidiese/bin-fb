// The presets the Trades page's Preset Efficiency table lists for a symbol.
//
// Shared by /api/trades (the table) and /api/trades/symbol-scores (the picker sort),
// because the sort key is "the table's top row" — it must choose among the same rows.

import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../_utils'
import { readBacktestResults } from '../_backtest-results'

function readJson<T>(filePath: string, fallback: T): T {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')) as T } catch { return fallback }
}

export function currentMode(): string {
  return readJson<{ mode?: string }>(path.join(BOT_ROOT, 'data', 'bot_mode.json'), {}).mode ?? 'test'
}

/** Backtest preset names ∪ efficiency keys, in that order. Backtest results are keyed
 *  by mode (bot/instance_paths.py, _backtest-results.ts). */
export function presetNamesFor(
  symbol: string, mode: string, symbolEfficiency: Record<string, unknown>,
): string[] {
  const backtest = readBacktestResults<{ presets?: Record<string, unknown> }>(
    symbol, mode === 'live' ? 'live' : 'test')
  return backtest?.presets
    ? Array.from(new Set([...Object.keys(backtest.presets), ...Object.keys(symbolEfficiency)]))
    : Object.keys(symbolEfficiency)
}
