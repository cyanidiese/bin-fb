// Server-only helpers shared between /api/symbols route handlers.
//
// The roster is shared by both modes: symbol_registry_shared.json (symbols, status).
// What is decided about a symbol is per trading mode: symbol_registry_{mode}.json
// (disabled, paused, disabled_ranks, weights, leverage_overrides).
// Spec: docs/specs/2026-09-26-shared-settings-and-per-mode-registry.md — mirrors
// bot/symbol_registry.py. The legacy symbol_registry.json is only read as a seed.
import fs from 'fs'
import path from 'path'
import { BOT_ROOT, isAlive } from '../_utils'
import { MODES, botMode, isMode, type Mode } from '../_risk-config'

export { BOT_ROOT, isAlive, MODES, botMode, isMode, type Mode }

export const LEGACY_PATH = path.join(BOT_ROOT, 'symbol_registry.json')
export const ROSTER_PATH = path.join(BOT_ROOT, 'symbol_registry_shared.json')
export function statePath(mode: Mode): string {
  return path.join(BOT_ROOT, `symbol_registry_${mode}.json`)
}
const SYMBOLS_JSON = path.join(BOT_ROOT, 'dashboard', 'public', 'symbols.json')

export type BacktestStatus = 'none' | 'running' | 'complete' | 'error' | 'cancelled'

export interface SymbolStatus {
  backtest: BacktestStatus
  pid: number | null
}

export interface DisabledEntry {
  reason: string
  disabled_at: string
  prev_weight?: number
}

export interface Roster {
  symbols: string[]
  updated_at: string
  status: Record<string, SymbolStatus>
}

export interface SymbolState {
  mode?: Mode
  updated_at?: string
  disabled?: Record<string, DisabledEntry>
  disabled_ranks?: Record<string, number[]>
  weights?: Record<string, number>
  paused?: Record<string, unknown>
  leverage_overrides?: Record<string, number>
}

/** Roster ⊕ one mode's state — the shape /api/symbols has always returned. */
export type RegistryFile = Roster & SymbolState

const STATE_KEYS = ['disabled', 'disabled_ranks', 'weights', 'paused', 'leverage_overrides'] as const

function readFile(p: string): Record<string, unknown> | null {
  try {
    const d = JSON.parse(fs.readFileSync(p, 'utf8'))
    return d && typeof d === 'object' && !Array.isArray(d) ? d : null
  } catch { return null }
}

export function readRoster(): Roster {
  const d = readFile(ROSTER_PATH) ?? readFile(LEGACY_PATH) ?? {}
  return {
    symbols: Array.isArray(d.symbols) ? d.symbols as string[] : [],
    updated_at: typeof d.updated_at === 'string' ? d.updated_at : '',
    status: (d.status && typeof d.status === 'object' ? d.status : {}) as Record<string, SymbolStatus>,
  }
}

export function writeRoster(roster: Roster): void {
  roster.updated_at = new Date().toISOString()
  fs.writeFileSync(ROSTER_PATH, JSON.stringify({
    symbols: roster.symbols, updated_at: roster.updated_at, status: roster.status,
  }, null, 2))
  // Keep dashboard/public/symbols.json in sync so the nav switcher updates.
  fs.writeFileSync(SYMBOLS_JSON, JSON.stringify({ symbols: roster.symbols }, null, 2))
}

/** A mode's state as stored, seeded in memory if the file is missing (live ← test ←
 *  legacy) — scripts/split_shared_and_registry.py's rules. */
export function readSymbolState(mode: Mode): SymbolState {
  const own = readFile(statePath(mode))
  const src = own ?? (mode === 'live' ? readSymbolState('test') as Record<string, unknown> : readFile(LEGACY_PATH) ?? {})
  const out: SymbolState = { mode }
  for (const k of STATE_KEYS) if (k in src) (out as Record<string, unknown>)[k] = src[k]
  if (typeof src.updated_at === 'string') out.updated_at = src.updated_at
  return out
}

/** Read-modify-write one mode's state file. Direct write: the files are single-file bind
 *  mounts, where tmp+rename fails (EBUSY). */
export function updateSymbolState(mode: Mode, fn: (s: SymbolState) => SymbolState): SymbolState {
  const next = { ...fn({ ...readSymbolState(mode) }), mode, updated_at: new Date().toISOString() }
  fs.writeFileSync(statePath(mode), JSON.stringify(next, null, 2))
  return next
}

export function readRegistry(mode: Mode): RegistryFile {
  return { ...readRoster(), ...readSymbolState(mode) }
}

/** `?mode=` when valid, else the bot's mode. */
export function modeParam(url: string): Mode {
  const m = new URL(url).searchParams.get('mode')
  return isMode(m) ? m : botMode()
}
