// Preset ranking for one symbol, as the Trades page's Rank column shows it.
//
// Shared by /api/trades (the table) and /api/trades/symbol-scores (the picker sort), so
// "the top preset" means the same thing in both places.

export interface PresetEfficiency {
  total_winning_usdt: number
  trade_count: number
  seeded_winning_usdt?: number
  recent_trades?: number[]
}

/** Mirror VirtualTracker scoring: window-based once warmed up, cumulative fallback while
 *  filling, seeded score (Tier 0) before min_trades are reached. */
function makeEffectiveScore(riskConfig: Record<string, unknown>) {
  const minTrades = (riskConfig?.min_trades_for_ranking as number) ?? 3
  const windowSize = (riskConfig?.ranking_window_size as number) ?? 10
  return (stats: PresetEfficiency): number => {
    if ((stats.trade_count ?? 0) >= minTrades) {
      const recent = stats.recent_trades ?? []
      if (recent.length >= windowSize) {
        return recent.slice(-windowSize).reduce((a, b) => a + b, 0)
      }
      return stats.total_winning_usdt
    }
    return stats.seeded_winning_usdt ?? 0
  }
}

/**
 * Best preset and per-preset ranks for one symbol.
 *
 * best: locked preset wins; otherwise highest effective score > 0 (null if none).
 * ranks: when locked, locked = 1 and the rest by score from 2; otherwise by score from 1.
 */
export function rankPresets(
  symbolEfficiency: Record<string, PresetEfficiency>,
  lockedPreset: string | null,
  riskConfig: Record<string, unknown>,
): { bestPreset: string | null; presetRanks: Record<string, number> } {
  const effectiveScore = makeEffectiveScore(riskConfig)

  let bestPreset: string | null = lockedPreset
  if (!bestPreset) {
    let bestScore = 0
    for (const [name, stats] of Object.entries(symbolEfficiency)) {
      const score = effectiveScore(stats)
      if (score > bestScore) {
        bestScore = score
        bestPreset = name
      }
    }
  }

  const presetRanks: Record<string, number> = {}
  const sortedByEff = Object.entries(symbolEfficiency)
    .sort(([, a], [, b]) => effectiveScore(b) - effectiveScore(a))
  if (lockedPreset) {
    presetRanks[lockedPreset] = 1
    sortedByEff.filter(([name]) => name !== lockedPreset)
      .forEach(([name], idx) => { presetRanks[name] = idx + 2 })
  } else {
    sortedByEff.forEach(([name], idx) => { presetRanks[name] = idx + 1 })
  }
  return { bestPreset, presetRanks }
}

/** The Rank-1 preset — the row the picker sorts by. Rank 1 rather than bestPreset,
 *  because bestPreset is null when every score is <= 0 but the table still has a top row. */
export function topPreset(presetRanks: Record<string, number>): string | null {
  for (const [name, rank] of Object.entries(presetRanks)) if (rank === 1) return name
  return null
}
