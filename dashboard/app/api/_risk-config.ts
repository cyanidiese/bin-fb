// Per-mode risk config: risk_config_test.json / risk_config_live.json, plus
// risk_config_shared.json for the keys that are the same in both modes.
// Specs: docs/specs/2026-09-26-per-mode-risk-config.md,
//        docs/specs/2026-09-26-shared-settings-and-per-mode-registry.md
// Mirrors config/risk_config.py.
//
// Every dashboard reader/writer of the risk config goes through here, with a mode.

import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from './_utils'

export type Mode = 'test' | 'live'
export const MODES: Mode[] = ['test', 'live']
export type Config = Record<string, unknown>

/** Keys with one value for both modes, kept in risk_config_shared.json. The rule: shared =
 *  anything that shapes signals, preset ranking or virtual/backtest accounting, plus process
 *  settings. Per mode = everything that decides real orders and money.
 *  Must match SHARED_KEYS in config/risk_config.py (tests/test_shared_settings.py checks). */
export const SHARED_KEYS = [
  // process
  'telegram', 'telegram_notify_interval_s', 'emergency_repeat_interval_s',
  'warning_repeat_interval_s', 'analysis_log_enabled', 'analysis_log_max_mb',
  'analysis_log_backups',
  // backtest method
  'startup_backtest', 'backtest_klines', 'backtest_initial_balance_usdt',
  'backtest_seed_leverage_factor', 'backtest_entry_slippage_pct',
  // virtual accounting
  'virtual_max_age_candles', 'slippage_model_enabled', 'slippage_default_pct',
  'slippage_min_samples', 'slippage_per_symbol',
  // signal filters
  'global_min_rr', 'global_max_rr', 'global_min_sl_pct', 'entry_zone_max_pct',
  'global_trend_regime_filter', 'global_trend_regime_lookback',
  'global_blocked_signal_types', 'global_max_level', 'global_correction_weight',
  'global_enforce_parent_alignment', 'per_symbol_settings',
  // preset ranking
  'preset_blocklist', 'ranking_window_size', 'min_trades_for_ranking',
  'min_trades_for_ranking_per_symbol', 'preset_hysteresis_pct', 'preset_cooldown_trades',
  // trend bootstrap depth
  'analyzer_history_candles',
] as const

export function isSharedKey(k: string): boolean {
  return (SHARED_KEYS as readonly string[]).includes(k)
}

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

export const SHARED_PATH = path.join(BOT_ROOT, 'risk_config_shared.json')

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

/** The shared file's SHARED_KEYS. Missing → seeded in memory from the test file's copies. */
function sharedOverlay(): Config {
  const src = readFile(SHARED_PATH) ?? ownFile('test')
  const out: Config = {}
  for (const k of SHARED_KEYS) if (k in src) out[k] = src[k]
  return out
}

/** Effective config for a mode: live falls back to test key by key (the user's rule:
 *  missing live config comes from test). Locks are nested per mode, so test's never
 *  reach live. SHARED_KEYS come from risk_config_shared.json and win over any mode-file
 *  copy. Defaults are the caller's (the /api/risk route owns DEFAULT_CONFIG). */
export function readRiskConfig(mode: Mode): Config {
  const own = mode === 'live' ? { ...ownFile('test'), ...ownFile('live') } : ownFile('test')
  return { ...own, ...sharedOverlay() }
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

/** Apply the same change to both modes' files — e.g. the roster's weight entries. */
export function updateBothModes(fn: (cfg: Config, mode: Mode) => Config): void {
  for (const mode of MODES) updateRiskConfig(mode, cfg => fn(cfg, mode))
}

/** Merge a patch for `mode`: shared keys go to risk_config_shared.json (and are mirrored
 *  into both mode files, so each stays a complete snapshot for a rollback to the previous
 *  image); every other key goes to that mode's file only. Returns what was written. */
export function saveRiskPatch(mode: Mode, patch: Config): { shared: string[]; own: string[] } {
  const shared: Config = {}
  const own: Config = {}
  for (const [k, v] of Object.entries(patch)) (isSharedKey(k) ? shared : own)[k] = v
  const sharedKeys = Object.keys(shared)
  if (sharedKeys.length) {
    const base = readFile(SHARED_PATH) ?? sharedOverlay()
    fs.writeFileSync(SHARED_PATH, JSON.stringify({ ...base, ...shared }, null, 2))
    updateBothModes(cfg => ({ ...cfg, ...shared }))
  }
  if (Object.keys(own).length) updateRiskConfig(mode, cfg => ({ ...cfg, ...own }))
  return { shared: sharedKeys, own: Object.keys(own) }
}
