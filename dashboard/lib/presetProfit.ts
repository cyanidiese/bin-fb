// Profit% of one preset — the figure in the Trades page's Profit% column.
//
// Shared by the page (buildPresetRows) and /api/trades/symbol-scores, which sorts the
// symbol picker by this number. One implementation, so the sort key can never drift
// from what the table shows.

interface PnlOrder {
  entry_price: number
  quantity: number
  leverage: number
  pnl_usdt: number | null
}

/** Sum of each order's return on its own margin, in percent. Orders are summed rather
 *  than averaged so a preset that trades more earns more — matching how the pool grows. */
export function sumMarginPct(orders: PnlOrder[]): number {
  return orders.reduce((s, o) => {
    const margin = o.leverage > 0 ? (o.entry_price * o.quantity) / o.leverage : 0
    return s + (margin > 0 ? ((o.pnl_usdt ?? 0) / margin) * 100 : 0)
  }, 0)
}

/** Profit% for a preset, given its real orders and its CLOSED virtual orders.
 *  null when it has no trades at all — "no data", distinct from 0%. */
export function presetProfitPct(real: PnlOrder[], virt: PnlOrder[]): number | null {
  if (real.length + virt.length === 0) return null
  return sumMarginPct(real) + sumMarginPct(virt)
}
