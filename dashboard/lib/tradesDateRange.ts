// Shared date-range helpers for the Trades page.
//
// One range drives EVERY widget on the page. The filtering is applied once to the
// loaded TradesData (see filterTradesData) rather than inside each widget, so a
// widget added later inherits the range automatically instead of silently showing
// unfiltered data.
//
// Deliberately NOT filtered — these are "current state", not history:
//   preset_ranks   the Rank column is a live ranking, not a windowed statistic
//   rank_balances  current virtual pool balances
//   best_preset    the preset in force right now
//   open_real / open_virtual   positions that are open *now*

import type { TradesData, RealOrder, RankOrder, Kline } from '@/lib/types'

/** Local (not UTC) "YYYY-MM-DDTHH:mm" — what <input type="datetime-local"> expects. */
export function toDatetimeLocal(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

/** Epoch seconds for a datetime-local string, or null when empty/unparseable. */
export function toEpochSeconds(v: string): number | null {
  if (!v) return null
  const ms = new Date(v).getTime()
  return Number.isNaN(ms) ? null : ms / 1000
}

/** Epoch seconds for an ISO order timestamp, or null. */
function isoToEpochSeconds(iso: string | null | undefined): number | null {
  if (!iso) return null
  const ms = new Date(iso).getTime()
  return Number.isNaN(ms) ? null : ms / 1000
}

/**
 * Earliest and latest data actually available for this symbol, across klines and
 * every order list. Used to bound the pickers so a range with no data cannot be
 * chosen. Returns nulls when there is nothing loaded yet.
 */
export function dataBounds(
  data: TradesData | null,
  klines: Kline[],
): { minMs: number | null; maxMs: number | null } {
  const times: number[] = []
  for (const k of klines) times.push(k.time * 1000)
  const push = (iso: string | null | undefined) => {
    const s = isoToEpochSeconds(iso)
    if (s !== null) times.push(s * 1000)
  }
  if (data) {
    for (const o of data.real_orders) { push(o.open_time); push(o.close_time) }
    for (const list of Object.values(data.rank_orders ?? {})) {
      for (const o of list as RankOrder[]) { push(o.open_time); push(o.close_time) }
    }
  }
  if (times.length === 0) return { minMs: null, maxMs: null }
  return { minMs: Math.min(...times), maxMs: Math.max(...times) }
}

/**
 * Default range: from = max(now - 1 month, earliest data for the symbol), to = now.
 *
 * Clamping the start to the earliest available data means a symbol with only a
 * week of history opens showing that week rather than three empty weeks before it.
 */
export function defaultRange(
  minMs: number | null,
  now: Date = new Date(),
): { from: string; to: string } {
  const monthAgo = new Date(now)
  monthAgo.setMonth(monthAgo.getMonth() - 1)
  const fromMs = minMs === null ? monthAgo.getTime() : Math.max(monthAgo.getTime(), minMs)
  return { from: toDatetimeLocal(new Date(fromMs)), to: toDatetimeLocal(now) }
}

/** True when the order overlaps [from, to]. An order counts if it was OPEN at any
 *  point in the window — using open_time alone would drop a trade that opened
 *  before the window and closed inside it. */
function orderInRange(
  o: { open_time?: string | null; close_time?: string | null },
  fromS: number | null,
  toS: number | null,
): boolean {
  const open = isoToEpochSeconds(o.open_time)
  const close = isoToEpochSeconds(o.close_time)
  const start = open ?? close
  const end = close ?? open
  if (start === null || end === null) return true   // undated: never hide it
  if (fromS !== null && end < fromS) return false
  if (toS !== null && start > toS) return false
  return true
}

/** Apply the range to every historical list in TradesData. Current-state fields
 *  (preset_ranks, rank_balances, best_preset, open positions) pass through. */
export function filterTradesData(
  data: TradesData,
  fromS: number | null,
  toS: number | null,
): TradesData {
  if (fromS === null && toS === null) return data
  const rankOrders: Record<string, RankOrder[]> = {}
  for (const [rank, list] of Object.entries(data.rank_orders ?? {})) {
    rankOrders[rank] = (list as RankOrder[]).filter(o => orderInRange(o, fromS, toS))
  }
  return {
    ...data,
    real_orders: (data.real_orders as RealOrder[]).filter(o => orderInRange(o, fromS, toS)),
    rank_orders: rankOrders,
  }
}

export function filterKlines(klines: Kline[], fromS: number | null, toS: number | null): Kline[] {
  if (fromS === null && toS === null) return klines
  return klines.filter(k => (fromS === null || k.time >= fromS) && (toS === null || k.time <= toS))
}
