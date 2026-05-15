import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT } from '../../_utils'

function readJson(p: string, fallback: unknown) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')) } catch { return fallback }
}

function currentMode(): string {
  const data = readJson(path.join(BOT_ROOT, 'data', 'bot_mode.json'), {}) as Record<string, string>
  return data.mode ?? 'test'
}

function activeSymbols(): string[] {
  const data = readJson(path.join(BOT_ROOT, 'symbol_registry.json'), { symbols: [] }) as { symbols: string[] }
  return data.symbols ?? []
}

export async function POST() {
  try {
    const mode = currentMode()
    const symbols = activeSymbols()
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    const dataDir = path.join(BOT_ROOT, 'data')
    const archived: string[] = []
    const errors: string[] = []

    // Archive real orders per symbol
    for (const sym of symbols) {
      const src = path.join(dataDir, `real_orders_${sym}_${mode}.json`)
      if (!fs.existsSync(src)) continue
      const dst = path.join(dataDir, `real_orders_${sym}_${mode}_archive_${ts}.json`)
      try {
        fs.renameSync(src, dst)
        archived.push(`real_orders_${sym}_${mode}.json`)
      } catch (e) {
        errors.push(`${sym}: ${e}`)
      }
    }

    // Clear virtual efficiency (reset trade counts)
    const effPath = path.join(dataDir, `preset_efficiency_${mode}.json`)
    if (fs.existsSync(effPath)) {
      const dst = path.join(dataDir, `preset_efficiency_${mode}_archive_${ts}.json`)
      try {
        fs.renameSync(effPath, dst)
        archived.push(`preset_efficiency_${mode}.json`)
      } catch (e) {
        errors.push(`efficiency: ${e}`)
      }
    }

    // Clear virtual orders per symbol
    for (const sym of symbols) {
      const src = path.join(dataDir, `virtual_orders_${sym}_${mode}.json`)
      if (!fs.existsSync(src)) continue
      const dst = path.join(dataDir, `virtual_orders_${sym}_${mode}_archive_${ts}.json`)
      try {
        fs.renameSync(src, dst)
        archived.push(`virtual_orders_${sym}_${mode}.json`)
      } catch (e) {
        errors.push(`virtual ${sym}: ${e}`)
      }
    }

    if (errors.length > 0) {
      return NextResponse.json({ ok: false, error: errors.join('; '), archived }, { status: 500 })
    }
    return NextResponse.json({ ok: true, archived })
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 500 })
  }
}
