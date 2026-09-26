/**
 * Runs once per dashboard server start.
 *
 * Keeps the preset Profit% store current (docs/specs/2026-09-26-preset-profit-store.md):
 * every 30 s, for both modes, recompute the symbols whose orders closed, whose "today"
 * rolled over, or whose numbers are older than 10 min. The check itself is a stat per
 * order file; the work stays out of the trading bot, on a 1-core server.
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== 'nodejs') return
  const { refresh, MODES } = await import('./app/api/trades/_preset-profit-store')

  let running = false
  const tick = async () => {
    if (running) return   // a slow pass must not stack up behind itself
    running = true
    try {
      for (const mode of MODES) {
        const r = await refresh(mode)
        if (r.recomputed.length > 0) {
          console.log(`[preset-profit] ${mode}: ${r.recomputed.length} symbol(s) in ${r.ms} ms`)
        }
      }
    } catch (err) {
      console.error('[preset-profit] refresh failed:', err)
    } finally {
      running = false
    }
  }
  setTimeout(tick, 5_000).unref()
  setInterval(tick, 30_000).unref()
}
