import { NextRequest, NextResponse } from 'next/server'
import { BOT_ROOT } from '../../_utils'
import path from 'path'
import fs from 'fs'

const CONFIG_PATH = path.join(BOT_ROOT, 'risk_config.json')
const PUBLIC_DIR = path.join(BOT_ROOT, 'dashboard', 'public')

interface Preset {
  total_profit_pct: number
  total_trades: number
  win_rate: number
}

interface BacktestFile {
  symbol: string
  presets: Record<string, Preset>
}

interface SymbolSummary {
  symbol: string
  profit: number
  trades: number
  winRate: number
}

const SAMPLE_MESSAGES: Record<string, { text: string; mention: boolean }> = {
  trade_win: {
    text: '✅ <b>BTCUSDT BUY — Win</b>\nPnL: <b>+12.34 USDT</b>\nBalance: 1,234.56 USDT\nEntry: 68,000.00 → Close: 68,450.00\nPreset: trail_15_from_30_full',
    mention: false,
  },
  trade_loss: {
    text: '❌ <b>ETHUSDT SELL — Loss</b>\nPnL: <b>-5.20 USDT</b>\nBalance: 1,229.36 USDT\nEntry: 3,200.00 → Close: 3,218.50\nPreset: trail_15_from_30_full',
    mention: false,
  },
  emergency: {
    text: '🚨 <b>Test emergency alert</b>\nThis is a test of the emergency notification.',
    mention: true,
  },
  balance_warning: {
    text: '⚠️ <b>Low balance warning</b>\nBalance 42.10 USDT is below threshold 50.00 USDT.',
    mention: false,
  },
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function sign(n: number): string {
  return n >= 0 ? `+${n.toFixed(1)}%` : `${n.toFixed(1)}%`
}

function buildConnectionMessage(): string {
  let files: string[]
  try {
    files = fs.readdirSync(PUBLIC_DIR).filter(f => f.startsWith('backtest_results_') && f.endsWith('.json'))
  } catch {
    return '🤖 <b>Binance Futures Bot</b>\n\n✅ Notifier connected — no backtest data yet.'
  }

  const summaries: SymbolSummary[] = []
  for (const file of files) {
    try {
      const data: BacktestFile = JSON.parse(fs.readFileSync(path.join(PUBLIC_DIR, file), 'utf8'))
      const presets = Object.values(data.presets ?? {})
      if (presets.length === 0) continue
      const best = presets.reduce((a, b) => b.total_profit_pct > a.total_profit_pct ? b : a)
      summaries.push({ symbol: data.symbol, profit: best.total_profit_pct, trades: best.total_trades, winRate: best.win_rate })
    } catch { /* skip corrupt file */ }
  }

  if (summaries.length === 0) {
    return '🤖 <b>Binance Futures Bot</b>\n\n✅ Notifier connected — no backtest data yet.'
  }

  const sorted = [...summaries].sort((a, b) => b.profit - a.profit)
  const profitable = summaries.filter(s => s.profit > 0).length
  const avgProfit = summaries.reduce((a, s) => a + s.profit, 0) / summaries.length
  const totalTrades = summaries.reduce((a, s) => a + s.trades, 0)
  const medals = ['🥇', '🥈', '🥉']

  const rows = sorted.map((s, i) => {
    const medal = medals[i] ?? '  '
    const sym = esc(s.symbol.replace('USDT', ''))
    const profit = sign(s.profit)
    const wr = Math.round(s.winRate * 100)
    const profitTag = s.profit >= 0 ? `<b>${profit}</b>` : profit
    return `${medal} <b>${sym}</b>  ${profitTag}  ${s.trades}T  WR ${wr}%`
  })

  return [
    '🤖 <b>Binance Futures Bot — Backtest Highlights</b>',
    '',
    `Best preset profit per symbol (${summaries.length} active):`,
    '',
    ...rows,
    '',
    `📈 Avg profit: <b>${sign(avgProfit)}</b>   Total trades: <b>${totalTrades}</b>`,
    `✅ <b>${profitable}/${summaries.length}</b> symbols profitable`,
    '',
    '<i>Notifier connected — alerts are live.</i>',
  ].join('\n')
}

export async function POST(req: NextRequest) {
  let token = ''
  let chatId = ''
  try {
    const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'))
    token = cfg?.telegram?.token?.trim() ?? ''
    chatId = String(cfg?.telegram?.chat_id ?? '').trim()
  } catch {
    return NextResponse.json({ ok: false, error: 'Could not read risk_config.json' }, { status: 500 })
  }

  if (!token || !chatId) {
    return NextResponse.json(
      { ok: false, error: 'Telegram token or chat ID not configured. Save them in Settings first.' },
      { status: 400 },
    )
  }

  let msgType = 'connection'
  try {
    const body = await req.json()
    if (body?.type && typeof body.type === 'string') msgType = body.type
  } catch { /* default to connection */ }

  let text: string
  let mention = false

  if (msgType === 'connection') {
    text = buildConnectionMessage()
  } else if (msgType in SAMPLE_MESSAGES) {
    ;({ text, mention } = SAMPLE_MESSAGES[msgType])
  } else {
    return NextResponse.json({ ok: false, error: `Unknown message type: ${msgType}` }, { status: 400 })
  }

  if (mention) text = `@bo_pal ${text}`

  try {
    const url = `https://api.telegram.org/bot${token}/sendMessage`
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text, parse_mode: 'HTML' }),
    })
    const data = await res.json()
    if (!res.ok || !data.ok) {
      return NextResponse.json({ ok: false, error: data.description ?? 'Telegram API error' }, { status: 502 })
    }
    return NextResponse.json({ ok: true })
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 502 })
  }
}
