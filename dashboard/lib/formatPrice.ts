/**
 * Format a price for display, adapting decimal places to the magnitude of the value.
 * - >= 1      : 2 decimal places  (e.g. 65000.00)
 * - >= 0.01   : 4 decimal places  (e.g. 0.0123)
 * - < 0.01    : enough decimals to show ~4 significant figures
 *               (e.g. 0.000012 → 8 dp, 0.00001234 → 0.00001234)
 */
export function formatPrice(price: number | null): string {
  if (price === null) return '—'
  if (price === 0) return '0.00'
  const abs = Math.abs(price)
  let decimals: number
  if (abs >= 1) {
    decimals = 2
  } else if (abs >= 0.01) {
    decimals = 4
  } else {
    decimals = -Math.floor(Math.log10(abs)) + 3
  }
  return price.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}
