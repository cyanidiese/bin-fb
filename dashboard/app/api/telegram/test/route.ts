import { NextResponse } from 'next/server'
import { BOT_ROOT } from '../../_utils'
import path from 'path'
import fs from 'fs'

const CONFIG_PATH = path.join(BOT_ROOT, 'risk_config.json')
const PUBLIC_DIR = path.join(BOT_ROOT, 'dashboard', 'public')

interface Preset {
  total_profit_pct: number
  total_trades: number
  win_rate: number
  balance_start: number
  balance_end: number
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
  balanceStart: number
  balanceEnd: number
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function sign(n: number): string {
  return n >= 0 ? `+${n.toFixed(1)}%` : `${n.toFixed(1)}%`
}

function buildMessage(summaries: SymbolSummary[]): string {
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

  const lines: string[] = [
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
  ]

  return lines.join('\n')
}

function loadSummaries(): SymbolSummary[] {
  const summaries: SymbolSummary[] = []
  let files: string[]
  try {
    files = fs.readdirSync(PUBLIC_DIR).filter(f => f.startsWith('backtest_results_') && f.endsWith('.json'))
  } catch {
    return summaries
  }

  for (const file of files) {
    try {
      const data: BacktestFile = JSON.parse(fs.readFileSync(path.join(PUBLIC_DIR, file), 'utf8'))
      const presets = Object.values(data.presets ?? {})
      if (presets.length === 0) continue
      const best = presets.reduce((a, b) => b.total_profit_pct > a.total_profit_pct ? b : a)
      summaries.push({
        symbol: data.symbol,
        profit: best.total_profit_pct,
        trades: best.total_trades,
        winRate: best.win_rate,
        balanceStart: best.balance_start,
        balanceEnd: best.balance_end,
      })
    } catch {
      // skip corrupt file
    }
  }
  return summaries
}

export async function POST() {
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

  const summaries = loadSummaries()
  const text = summaries.length > 0
    ? buildMessage(summaries)
    : '🤖 <b>Binance Futures Bot</b>\n\n✅ Notifier connected — no backtest data yet.'

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
