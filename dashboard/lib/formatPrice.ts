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

/**
 * Raw price → a plain number string suitable for an <input type="number">.
 *
 * Unlike formatPrice this must NOT group digits or pad — the result is parsed back
 * with Number(). Precision scales with magnitude so sub-cent symbols keep meaningful
 * digits (MEME ~0.00056) while large ones stay readable (SOL ~68).
 */
export function priceToInputValue(p: number): string {
  if (!isFinite(p)) return ''
  const abs = Math.abs(p)
  const decimals = abs >= 10000 ? 1 : abs >= 100 ? 2 : abs >= 1 ? 4 : abs >= 0.01 ? 6 : 8
  return String(Number(p.toFixed(decimals)))
}
