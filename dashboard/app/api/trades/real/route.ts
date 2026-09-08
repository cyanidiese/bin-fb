import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../../_utils'

export const dynamic = 'force-dynamic'

const DEFAULT_LIMIT = 25
const MAX_LIMIT = 200

function currentMode(): string {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(BOT_ROOT, 'data', 'bot_mode.json'), 'utf8'))
    return d.mode ?? 'test'
  } catch { return 'test' }
}

function registeredSymbols(): string[] {
  try {
    const d = JSON.parse(fs.readFileSync(
      path.join(BOT_ROOT, 'dashboard', 'public', 'symbols.json'), 'utf8'))
    return Array.isArray(d.symbols) ? d.symbols : []
  } catch { return [] }
}

function readJson<T>(p: string, fallback: T): T {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')) as T } catch { return fallback }
}

type Row = {
  symbol: string
  preset_name: string
  side: string
  entry_price: number
  close_price: number | null
  quantity: number | null
  leverage: number | null
  pnl_usdt: number | null
  result: string | null
  open_time: string | null
  close_time: string | null
  /** Seconds the order was (or has been) open. Null when open_time is unusable. */
  duration_s: number | null
  is_open: boolean
  unrealized_pnl_usdt?: number | null
}

function seconds(from?: string | null, to?: string | null): number | null {
  if (!from) return null
  const a = Date.parse(from)
  if (Number.isNaN(a)) return null
  const b = to ? Date.parse(to) : Date.now()
  if (Number.isNaN(b)) return null
  return Math.max(0, Math.round((b - a) / 1000))
}

/**
 * Real orders across ALL symbols, newest first — open ones first, then closed.
 *
 * Symbol-scoped is what /api/trades already gives; this exists for the at-a-glance
 * widget, because real orders are few (79 in a month) and are the only ones that move
 * money, so seeing every symbol's at once is the useful view.
 *
 * `?mode=` picks the instance, matching /api/trades and /api/trades/symbols.
 */
export async function GET(req: NextRequest) {
  const params = new URL(req.url).searchParams
  const requested = params.get('mode')
  const mode = requested === 'test' || requested === 'live' ? requested : currentMode()
  const limit = Math.min(
    Math.max(parseInt(params.get('limit') ?? String(DEFAULT_LIMIT), 10) || DEFAULT_LIMIT, 1),
    MAX_LIMIT,
  )

  const dir = path.join(BOT_ROOT, 'data')
  const rows: Row[] = []

  // Closed real orders, per symbol file.
  for (const sym of registeredSymbols()) {
    const raw = readJson<unknown>(path.join(dir, `real_orders_${sym}_${mode}.json`), [])
    const orders = Array.isArray(raw) ? raw : []
    for (const o of orders as Record<string, unknown>[]) {
      if (o.result === null || o.result === undefined) continue
      const open = (o.open_time as string) ?? null
      const close = (o.close_time as string) ?? null
      rows.push({
        symbol: sym,
        preset_name: (o.preset_name as string) ?? '',
        side: (o.side as string) ?? '',
        entry_price: Number(o.entry_price ?? 0),
        close_price: o.close_price != null ? Number(o.close_price) : null,
        quantity: o.quantity != null ? Number(o.quantity) : null,
        leverage: o.leverage != null ? Number(o.leverage) : null,
        pnl_usdt: o.pnl_usdt != null ? Number(o.pnl_usdt) : null,
        result: (o.result as string) ?? null,
        open_time: open,
        close_time: close,
        duration_s: seconds(open, close),
        is_open: false,
      })
    }
  }

  // Positions open right now come from the snapshot, not the per-symbol files — those
  // are only written on close.
  const snap = readJson<{ real?: Record<string, unknown>[]; updated_at?: string }>(
    path.join(dir, `open_positions_${mode}.json`), {})
  for (const o of snap.real ?? []) {
    const open = (o.open_time as string) ?? null
    rows.push({
      symbol: (o.symbol as string) ?? '',
      preset_name: (o.preset_name as string) ?? '',
      side: (o.side as string) ?? '',
      entry_price: Number(o.entry_price ?? 0),
      close_price: null,
      quantity: o.quantity != null ? Number(o.quantity) : null,
      leverage: o.leverage != null ? Number(o.leverage) : null,
      pnl_usdt: null,
      result: null,
      open_time: open,
      close_time: null,
      duration_s: seconds(open, null),
      is_open: true,
      unrealized_pnl_usdt: o.unrealized_pnl_usdt != null
        ? Number(o.unrealized_pnl_usdt) : null,
    })
  }

  // Open first — they are the ones that can still be acted on — then newest closed.
  rows.sort((a, b) => {
    if (a.is_open !== b.is_open) return a.is_open ? -1 : 1
    const at = Date.parse(a.close_time ?? a.open_time ?? '') || 0
    const bt = Date.parse(b.close_time ?? b.open_time ?? '') || 0
    return bt - at
  })

  const shown = rows.slice(0, limit)
  const closed = rows.filter(r => !r.is_open)
  return NextResponse.json({
    mode,
    open_updated_at: snap.updated_at ?? null,
    total: rows.length,
    open_count: rows.length - closed.length,
    // Totals span every real order on disk, not just the page shown, so the widget can
    // say what the book has actually done rather than what fits in it.
    net_pnl_usdt: closed.reduce((s, r) => s + (r.pnl_usdt ?? 0), 0),
    wins: closed.filter(r => ['win', 'partial', 'trail'].includes(r.result ?? '')).length,
    closed_count: closed.length,
    orders: shown,
  })
}
