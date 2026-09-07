/** Locked presets are stored per trading mode.
 *
 *  Shape: { test: { SYMBOL: preset }, live: { ... } }. The mirror instance mounts the
 *  same risk_config.json read-only, so a single shared dict forced the testnet locks
 *  onto the live market — the very thing the mirror exists to compare against.
 *
 *  A legacy flat { SYMBOL: preset } dict is read as the TEST set, so existing config
 *  keeps working and live starts empty. Mirrors config/risk_config.py:locked_presets_for.
 */
export type LockMap = Record<string, string>

export function lockedPresetsFor(config: unknown, mode: string): LockMap {
  const raw = (config as Record<string, unknown> | undefined)?.locked_presets
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const obj = raw as Record<string, unknown>
  if ('test' in obj || 'live' in obj) {
    const got = obj[mode]
    return got && typeof got === 'object' && !Array.isArray(got) ? { ...(got as LockMap) } : {}
  }
  return mode === 'test' ? { ...(obj as LockMap) } : {}
}

/** Write `locks` back for one mode, migrating a legacy flat dict on the way. */
export function withLockedPresets(
  config: Record<string, unknown>, mode: string, locks: LockMap,
): Record<string, unknown> {
  const raw = config.locked_presets
  let next: Record<string, LockMap>
  if (raw && typeof raw === 'object' && !Array.isArray(raw) &&
      ('test' in (raw as object) || 'live' in (raw as object))) {
    next = { ...(raw as Record<string, LockMap>) }
  } else {
    // legacy flat dict belonged to test; live starts clean
    next = { test: (raw as LockMap) ?? {}, live: {} }
  }
  next[mode] = locks
  return { ...config, locked_presets: next }
}
