// Shared datetime helpers for <input type="datetime-local"> fields.
// Extracted from app/page.tsx in session 63 so the Learning Mode start modal maps
// dates to candle indexes with exactly the same rules as the page's date filters.

import type { Kline } from '@/lib/types'

/** Unix seconds → "YYYY-MM-DDTHH:mm" in the browser's local timezone. */
export function tsToDatetimeLocal(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Snap a datetime-local string down to the enclosing 15-minute candle boundary. */
export function snapTo15Min(dt: string): string {
  if (!dt) return dt
  const ms = new Date(dt).getTime()
  if (isNaN(ms)) return dt
  const snapped = Math.floor(ms / 900_000) * 900_000
  const d = new Date(snapped)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * Map a datetime-local string to the index of the candle that was open at that
 * moment — i.e. the last candle whose open time is <= the chosen time.
 *
 * Returns -1 for an empty/unparseable value or empty klines. A time before the
 * first candle clamps to 0; a time after the last clamps to the last index, so
 * the picker can never produce an out-of-range index.
 * klines are assumed ascending by time (as written by bot/exporter.py).
 */
export function datetimeLocalToCandleIndex(dt: string, klines: Kline[]): number {
  if (!dt || klines.length === 0) return -1
  const target = new Date(dt).getTime() / 1000
  if (isNaN(target)) return -1
  if (target <= klines[0].time) return 0

  // binary search for the last candle with time <= target
  let lo = 0
  let hi = klines.length - 1
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2)
    if (klines[mid].time <= target) lo = mid
    else hi = mid - 1
  }
  return lo
}
