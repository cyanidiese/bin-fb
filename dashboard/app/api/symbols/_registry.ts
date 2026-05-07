// Server-only helpers shared between /api/symbols route handlers.
import fs from 'fs'
import path from 'path'
import { BOT_ROOT, isAlive } from '../_utils'

export { BOT_ROOT, isAlive }

export const REGISTRY_PATH = path.join(BOT_ROOT, 'symbol_registry.json')
const SYMBOLS_JSON = path.join(BOT_ROOT, 'dashboard', 'public', 'symbols.json')

export type BacktestStatus = 'none' | 'running' | 'complete' | 'error' | 'cancelled'

export interface SymbolStatus {
  backtest: BacktestStatus
  pid: number | null
}

export interface RegistryFile {
  symbols: string[]
  updated_at: string
  status: Record<string, SymbolStatus>
}

export function readRegistry(): RegistryFile {
  try {
    return JSON.parse(fs.readFileSync(REGISTRY_PATH, 'utf8')) as RegistryFile
  } catch {
    return { symbols: [], updated_at: '', status: {} }
  }
}

export function writeRegistry(data: RegistryFile): void {
  data.updated_at = new Date().toISOString()
  fs.writeFileSync(REGISTRY_PATH, JSON.stringify(data, null, 2))
  // Keep dashboard/public/symbols.json in sync so the nav switcher updates.
  fs.writeFileSync(SYMBOLS_JSON, JSON.stringify({ symbols: data.symbols }, null, 2))
}

