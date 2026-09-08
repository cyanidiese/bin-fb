/**
 * Weight assignment after a drag in the per-symbol allocation table.
 *
 * The old rule was `newWeights[sym] = n - i` for every row, which rewrote the whole
 * table by position. With 17 symbols the bottom row got weight 1, so a single drag
 * silently activated every zero-weight symbol for real orders and discarded the
 * hand-picked weights above.
 *
 * Weight 0 is a deliberate off switch, so dragging is now asymmetric: it can
 * deactivate a symbol, but it can never activate one.
 */

/** Weight of `s`, treating missing and negative as 0. */
function weightOf(weights: Record<string, number>, s: string): number {
  const w = weights[s]
  return typeof w === 'number' && w > 0 ? w : 0
}

/**
 * True when `moved` was dropped into the inactive region — the run of zero-weight
 * symbols at the bottom of the table.
 *
 * "Between two zeros" also covers the last position, where there is no row below: the
 * natural gesture for deactivating a symbol is dragging it to the end of the list.
 */
function droppedAmongZeros(
  newOrder: string[],
  weights: Record<string, number>,
  moved: string,
): boolean {
  const i = newOrder.indexOf(moved)
  if (i <= 0) return false                     // top row is never "between" anything
  const above = newOrder[i - 1]
  const below = i < newOrder.length - 1 ? newOrder[i + 1] : undefined
  return weightOf(weights, above) === 0
    && (below === undefined || weightOf(weights, below) === 0)
}

/**
 * The new weight table after `moved` was dragged to its position in `newOrder`.
 *
 * - a symbol at 0 stays at 0 — drag never activates
 * - `moved` drops to 0 if it landed among the zeros
 * - the remaining active symbols keep the *set* of weights already chosen, reassigned
 *   in the new display order, so reordering changes priority without inventing numbers
 */
export function reweightAfterDrag(
  newOrder: string[],
  weights: Record<string, number>,
  moved: string,
): Record<string, number> {
  const deactivated = weightOf(weights, moved) > 0
    && droppedAmongZeros(newOrder, weights, moved)

  // Deactivation touches one symbol only. Reassigning the pool by position here would
  // shift every symbol below it up a notch -- switching SOLUSDT off would have moved
  // TIAUSDT from 4 to 6, changing six allocations the user never dragged.
  if (deactivated) {
    const out: Record<string, number> = {}
    for (const s of newOrder) out[s] = weightOf(weights, s)
    out[moved] = 0
    return out
  }

  const active = newOrder.filter(s =>
    s === moved ? weightOf(weights, moved) > 0 && !deactivated : weightOf(weights, s) > 0,
  )

  // The values the user actually picked, largest first — handed out by new position.
  const pool = newOrder
    .filter(s => weightOf(weights, s) > 0)
    .map(s => weightOf(weights, s))
    .sort((a, b) => b - a)

  const out: Record<string, number> = {}
  for (const s of newOrder) out[s] = 0
  active.forEach((s, i) => {
    out[s] = pool[i] ?? pool[pool.length - 1] ?? 1
  })
  return out
}
