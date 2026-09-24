import fs from 'fs'

/** Highest rank pool that exists for this mode, from the virtual_balance_rank{N} files.
 *  Falls back to 6 when none are found. Shared by /api/trades and symbol-scores. */
export function detectRankMax(dataDir: string, mode: string): number {
  try {
    const re = new RegExp(`^virtual_balance_rank(\\d+)_${mode}\\.json$`)
    const ranks = fs.readdirSync(dataDir)
      .map(f => { const m = f.match(re); return m ? parseInt(m[1], 10) : 0 })
      .filter(n => n > 0)
    return ranks.length > 0 ? Math.max(...ranks) : 6
  } catch { return 6 }
}
