// Per-mode risk config: risk_config_test.json / risk_config_live.json.
// Spec: docs/specs/2026-09-26-per-mode-risk-config.md — mirrors config/risk_config.py.
//
// Every dashboard reader/writer of the risk config goes through here, with a mode.

import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from './_utils'

export type Mode = 'test' | 'live'
export const MODES: Mode[] = ['test', 'live']
export type Config = Record<string, unknown>

/** About the bot process, not a trading mode: stored in both files, written to both. */
export const BOT_WIDE_KEYS = [
  'telegram', 'telegram_notify_interval_s', 'emergency_repeat_interval_s',
  'warning_repeat_interval_s', 'startup_backtest', 'backtest_klines',
] as const

const LEGACY_PATH = path.join(BOT_ROOT, 'risk_config.json')

export function isMode(m: unknown): m is Mode {
  return m === 'test' || m === 'live'
}

/** The mode the trading bot runs (data/bot_mode.json). */
export function botMode(): Mode {
  try {
    const m = JSON.parse(fs.readFileSync(path.join(BOT_ROOT, 'data', 'bot_mode.json'), 'utf8')).mode
    return isMode(m) ? m : 'test'
  } catch { return 'test' }
}

/** `?mode=` when valid, else the bot's mode — what every existing caller meant. */
export function modeOr(requested: unknown): Mode {
  return isMode(requested) ? requested : botMode()
}

export function riskConfigPath(mode: Mode): string {
  return path.join(BOT_ROOT, `risk_config_${mode}.json`)
}

function readFile(p: string): Config | null {
  try {
    const d = JSON.parse(fs.readFileSync(p, 'utf8'))
    return d && typeof d === 'object' && !Array.isArray(d) ? d as Config : null
  } catch { return null }
}

/** The mode file as stored, seeded if missing (test ← legacy file, live ← test), with
 *  locked_presets reduced to this mode's entry — scripts/split_risk_config.py's rules. */
function ownFile(mode: Mode): Config {
  const own = readFile(riskConfigPath(mode))
  if (own) return own
  const src = mode === 'live' ? ownFile('test') : (readFile(LEGACY_PATH) ?? {})
  const legacy = readFile(LEGACY_PATH) ?? {}
  return { ...src, locked_presets: { [mode]: legacyLocks(legacy, mode) } }
}

function legacyLocks(cfg: Config, mode: Mode): Record<string, string> {
  const raw = cfg.locked_presets
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const obj = raw as Record<string, unknown>
  if ('test' in obj || 'live' in obj) {
    const got = obj[mode]
    return got && typeof got === 'object' && !Array.isArray(got) ? { ...got as Record<string, string> } : {}
  }
  return mode === 'test' ? { ...obj as Record<string, string> } : {}
}

/** Effective config for a mode: live falls back to test key by key (the user's rule:
 *  missing live config comes from test). Locks are nested per mode, so test's never
 *  reach live. Defaults are the caller's (the /api/risk route owns DEFAULT_CONFIG). */
export function readRiskConfig(mode: Mode): Config {
  return mode === 'live' ? { ...ownFile('test'), ...ownFile('live') } : ownFile('test')
}

/** Direct write: the files are single-file bind mounts, where tmp+rename fails (EBUSY). */
function writeFile(mode: Mode, cfg: Config): void {
  fs.writeFileSync(riskConfigPath(mode), JSON.stringify(cfg, null, 2))
}

/** Read-modify-write one mode's file. `fn` gets the stored file (not the fallback view),
 *  so only what it changes lands in the file. */
export function updateRiskConfig(mode: Mode, fn: (cfg: Config) => Config): Config {
  const next = fn({ ...ownFile(mode) })
  writeFile(mode, next)
  return next
}

/** Apply the same change to both modes' files — bot-wide keys, the shared symbol roster. */
export function updateBothModes(fn: (cfg: Config, mode: Mode) => Config): void {
  for (const mode of MODES) updateRiskConfig(mode, cfg => fn(cfg, mode))
}

export function isBotWidePatch(body: Config): boolean {
  const keys = Object.keys(body)
  return keys.length > 0 && keys.every(k => (BOT_WIDE_KEYS as readonly string[]).includes(k))
}
